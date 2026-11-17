"""python -m clipper check|run --creator ID [--source-url URL --max-minutes 10]"""
import argparse
import csv
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from . import detect, live, publish, story
from .common import (channel_url, env_bool, get_creator, handle, is_twitch_url, live_url, log, now_local,
                     platform_name, run, set_output, summary)

SEG = int(os.environ.get("SEGMENT_SECONDS", "600"))


# ---------------------------------------------------------------- check
def cmd_check(a):
    c = get_creator(a.creator)
    if a.source_url:
        set_output("go", "true")
        summary(f"**{c['name']}** — test run on `{a.source_url}`")
        return
    info = live.check(c)
    require = env_bool("REQUIRE_GTA6", True)
    go = info["live"] and (info["gta6"] or not require)
    set_output("go", "true" if go else "false")
    state = "LIVE" if info["live"] else "offline"
    summary(f"**{c['name']}**: {state} · GTA VI={info['gta6']} · {info.get('category', '')[:60]} · {info.get('title', '')[:100]} → {'recording' if go else 'skip'}"
            + (f" ({info['reason']})" if not info["live"] else ""))


# ---------------------------------------------------------------- recording
def _secs(hms):
    try:
        parts = [float(x) for x in (hms or "0").split(":")]
    except ValueError:
        return 0
    v = 0
    for x in parts:
        v = v * 60 + x
    return int(v)


def start_recorder(c, src, outdir, max_seconds, test):
    """Returns (producer_proc or None, ffmpeg_segmenter_proc)."""
    seg_cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-fflags", "+genpts+discardcorrupt", "-i", "pipe:0",
               "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", "-f", "segment", "-segment_time", str(SEG),
               "-reset_timestamps", "1", "-segment_list", str(outdir / "segs.csv"), "-segment_list_type", "csv",
               "-segment_format", "mpegts", str(outdir / "seg_%04d.ts")]
    if is_twitch_url(src):
        prod = ["streamlink", "--stdout", "--retry-open", "3", "--hls-live-edge", "6"]
        if test:
            off = os.environ.get("START_AT", "").strip()
            if off and off not in ("0", "00:00:00"):
                prod += ["--hls-start-offset", off]
            h, m = divmod(int(max_seconds), 3600)
            prod += ["--hls-duration", f"{h:02d}:{m // 60:02d}:{m % 60:02d}"]
        prod += [src, "720p60,720p,720p30,best"]
        p1 = subprocess.Popen(prod, stdout=subprocess.PIPE)
        p2 = subprocess.Popen(seg_cmd, stdin=p1.stdout)
        p1.stdout.close()
        return p1, p2
    if test:  # YouTube / other VOD: download the first N minutes, then segment
        tmpl = str(outdir / "src.%(ext)s")
        r = run(["yt-dlp", "--no-warnings", "--no-playlist", "-f", "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720]/b",
                 "--merge-output-format", "mp4", "--download-sections", f"*{_secs(os.environ.get('START_AT', ''))}-{_secs(os.environ.get('START_AT', '')) + int(max_seconds)}", "--force-keyframes-at-cuts",
                 "-o", tmpl, src], check=False, timeout=1800)
        files = [p for p in outdir.glob("src.*") if p.suffix in (".mp4", ".mkv", ".webm")]
        if r.returncode != 0 or not files:
            raise RuntimeError(f"yt-dlp failed: {(r.stderr or '')[-800:]}")
        cmd = seg_cmd.copy()
        i = cmd.index("pipe:0")
        cmd[i] = str(files[0])
        cmd[i + 1:i + 1] = ["-t", str(int(max_seconds))]
        return None, subprocess.Popen(cmd)
    # YouTube live
    p1 = subprocess.Popen(["yt-dlp", "--no-warnings", "-q", "-f", "b[height<=720]/b", "--hls-use-mpegts", "-o", "-", src],
                          stdout=subprocess.PIPE)
    p2 = subprocess.Popen(seg_cmd, stdin=p1.stdout)
    p1.stdout.close()
    return p1, p2


def read_segments(csv_path):
    if not csv_path.exists():
        return []
    rows = []
    with open(csv_path) as f:
        for r in csv.reader(f):
            if len(r) >= 3:
                rows.append((r[0], float(r[1]), float(r[2])))
    return rows


# ---------------------------------------------------------------- per segment
class Clipper:
    def __init__(self, c, test_url, chat):
        self.c = c
        self.test_url = test_url
        self.chat = chat
        self.made = []
        self.rel = None
        d = now_local()
        self.tag = f"{c['id']}-{d:%Y%m%d}"
        self.part = 0
        self.out = Path("out") / c["id"]
        self.out.mkdir(parents=True, exist_ok=True)
        self.max_per_seg = int(os.environ.get("CLIPS_PER_SEGMENT", "2"))

    def release(self):
        if self.rel is None and publish.enabled():
            self.rel = publish.get_or_create_release(self.tag, f"{self.c['name']} — {now_local():%d %b %Y}")
            self.part = sum(1 for x in publish.list_assets(self.rel["id"]) if x["name"].endswith(".mp4"))
        return self.rel

    def process(self, seg_path, seg_wall_start):
        from . import edit
        t0 = time.time()
        loud = detect.loudness_per_second(seg_path)
        if len(loud) < 25:
            log(f"[seg] {seg_path.name}: too short, skip")
            return
        chat = self.chat.counts(seg_wall_start, len(loud)) if self.chat else None
        curve = detect.score_curve(loud, chat)
        peaks = detect.pick_peaks(curve, max_n=self.max_per_seg)
        log(f"[seg] {seg_path.name}: {len(loud)}s, chat msgs={sum(chat) if chat else 'n/a'}, peaks={peaks}")
        for peak, score in peaks:
            try:
                self.make_clip(edit, seg_path, len(loud), peak, score, seg_wall_start)
            except Exception as e:
                log(f"[clip] failed at {peak}s: {e!r}")
        log(f"[seg] processed in {time.time() - t0:.0f}s")

    def make_clip(self, edit, seg, seglen, peak, score, seg_wall_start):
        c = self.c
        start = max(0.0, peak - 20.0)
        win_end = min(float(seglen), start + 55.0)
        if win_end - start < 25:
            start = max(0.0, win_end - 25.0)
        if win_end - start < 25:
            return
        work = self.out / "tmp"
        work.mkdir(exist_ok=True)
        segs = edit.transcribe(seg, start, win_end - start, work)
        p_rel = peak - start
        target = p_rel + 10.0
        end_rel = target
        for s in segs:
            if s["end"] >= target:
                end_rel = s["end"] + 0.4
                break
        end_rel = max(25.0, min(end_rel, win_end - start, 55.0))
        segs = [s for s in segs if s["start"] < end_rel]
        words = [w for s in segs for w in s["words"] if w["start"] < end_rel]
        speech = [(s["start"], min(s["end"], end_rel)) for s in segs]
        if len(words) >= 12:
            score = min(99, score + 5)
        self.release()
        self.part += 1
        clip_time = time.localtime(seg_wall_start + peak) if not self.test_url else time.localtime()
        stamp = time.strftime("%Y%m%d-%H%M", clip_time)
        seed = f"{c['id']}{stamp}{peak}"
        st = story.build(c, segs, p_rel, self.part, seed)
        credit = f"Clip: {handle(c)} live on {platform_name(c)}"
        stem = f"{stamp}_s{score:03d}_{c['id']}_p{self.part:02d}"
        mp4 = self.out / f"{stem}.mp4"
        edit.render(seg, start, end_rel, mp4, work, st["hook_card"], st["storyline"], credit, words, p_rel, speech)
        meta = self.metadata(st, score, stem, start, end_rel, peak, segs)
        (self.out / f"{stem}.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
        (self.out / f"{stem}.txt").write_text(meta["paste"], encoding="utf-8")
        log(f"[clip] {stem}  {meta['title']}  ({end_rel:.0f}s, {mp4.stat().st_size / 1e6:.1f} MB)")
        if self.release():
            url = publish.upload(self.rel, mp4, label=f"[{score}] {meta['title']}")
            publish.upload(self.rel, self.out / f"{stem}.txt")
            publish.upload(self.rel, self.out / f"{stem}.json")
            meta["download"] = url
            publish.refresh_release_body(self.tag)
            publish.refresh_index()
            for p in (mp4,):
                p.unlink(missing_ok=True)
        self.made.append(meta)

    def metadata(self, st, score, stem, start, dur, peak, segs):
        c = self.c
        perm = str(c.get("permission", "none"))
        program = c.get("program") or perm
        lines = [st["title"], "", f"{c['name']} in GTA 6 — {st['chapter']} ({st['storyline']})",
                 f"🎮 Clipped from {c['name']}'s live stream on {platform_name(c)}.",
                 f"👉 Watch {c['name']} live: {channel_url(c)}"]
        if perm.lower() not in ("none", "check-campaign"):
            lines.append(f"✅ Posted under the {program} clipping program.")
        if perm.lower() == "check-campaign":
            lines.append(f"⚠️ Only post this if you are accepted into the active {program} for {c['name']}.")
        if c.get("sponsored"):
            lines.append("💲 Paid promotion: tick “Includes paid promotion” in YouTube Studio before publishing.")
        lines += ["", " ".join(c["hashtags"])]
        desc = "\n".join(lines[2:])
        paste = f"TITLE:\n{st['title']}\n\nALT TITLE:\n{st['alt_title']}\n\nDESCRIPTION:\n{desc}\n\nSCORE: {score}/100\n"
        return {"id": stem, "creator": c["name"], "creator_id": c["id"], "platform": c["platform"],
                "channel_url": channel_url(c), "permission": perm, "program": program, "sponsored": bool(c.get("sponsored")),
                "score": score, "title": st["title"], "alt_title": st["alt_title"], "hook_card": st["hook_card"],
                "storyline": st["storyline"], "theme": st["theme"], "description": desc, "hashtags": c["hashtags"],
                "duration_s": round(dur, 1), "source_offset_s": round(start, 1), "spike_at_s": peak,
                "source": self.test_url or live_url(c),
                "transcript": " ".join(s["text"] for s in segs)[:2000], "paste": paste}


# ---------------------------------------------------------------- run
def cmd_run(a):
    c = get_creator(a.creator)
    test = bool(a.source_url)
    src = a.source_url or live_url(c)
    if not test and c["platform"] == "youtube":
        info = live.check(c)
        src = info.get("video_url") or src
    budget = (float(a.max_minutes) * 60) if test else float(os.environ.get("RECORD_MINUTES", "320")) * 60
    rec = Path("rec") / c["id"]
    shutil.rmtree(rec, ignore_errors=True)
    rec.mkdir(parents=True)
    chat = None
    if not test and c["platform"] == "twitch":
        from .chat import ChatLogger
        chat = ChatLogger(c["channel"])
        chat.start()
    clipper = Clipper(c, a.source_url, chat)
    wall0 = time.time()
    prod, seg = start_recorder(c, src, rec, budget, test)
    done = set()
    stopping = False
    recheck_at = time.time() + 1800
    while True:
        finished = seg.poll() is not None
        rows = read_segments(rec / "segs.csv")
        for name, s0, _s1 in rows:
            if name in done:
                continue
            done.add(name)
            p = rec / name
            clipper.process(p, wall0 + s0)
            p.unlink(missing_ok=True)
        if finished:
            break
        if not stopping and time.time() - wall0 > budget + (60 if test else 0):
            log("[run] time budget reached, stopping recorder")
            stopping = True
            (prod or seg).send_signal(signal.SIGINT)
        if not test and not stopping and time.time() > recheck_at and env_bool("REQUIRE_GTA6", True):
            recheck_at = time.time() + 1800
            info = live.check(c)
            if info["live"] and not info["gta6"]:
                log("[run] no longer GTA VI, stopping")
                stopping = True
                (prod or seg).send_signal(signal.SIGINT)
        time.sleep(5)
    if prod and prod.poll() is None:
        prod.terminate()
    # leftover segments written after last poll
    for name, s0, _s1 in read_segments(rec / "segs.csv"):
        if name not in done:
            done.add(name)
            clipper.process(rec / name, wall0 + s0)
    lines = [f"### {c['name']}: {len(clipper.made)} clips"]
    for m in sorted(clipper.made, key=lambda m: -m["score"]):
        lines.append(f"- **{m['score']}** {m['title']} ({m['duration_s']}s) {m.get('download', '')}")
    summary("\n".join(lines))
    if test and not clipper.made:
        raise SystemExit("test run produced no clips")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check", "run", "index"])
    ap.add_argument("--creator", default="")
    ap.add_argument("--source-url", default="")
    ap.add_argument("--max-minutes", default="10")
    a = ap.parse_args()
    a.source_url = (a.source_url or "").strip()
    if a.cmd == "check":
        cmd_check(a)
    elif a.cmd == "run":
        cmd_run(a)
    else:
        publish.refresh_index()


if __name__ == "__main__":
    main()
