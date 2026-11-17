"""Turn a spike into a 1080x1920 Short: blurred fill, punch-in zoom, hook card, word captions, storyline, credit."""
import os
import re
from pathlib import Path

from .common import ROOT, env_bool, log, run

W, H = 1080, 1920
FG_H = 608              # 16:9 gameplay at full width
FG_Y = 640              # top of gameplay window
FONT = os.environ.get("CLIP_FONT", "Anton")
FONT_DIR = ROOT / "fonts"

_model = None


def whisper():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        name = os.environ.get("WHISPER_MODEL", "base")
        log(f"[whisper] loading {name} (cpu int8)")
        _model = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 2)
    return _model


def transcribe(src, start, dur, workdir, words=True):
    wav = Path(workdir) / "a.wav"
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.2f}", "-t", f"{dur:.2f}", "-i", str(src),
         "-vn", "-ac", "1", "-ar", "16000", str(wav)])
    import wave
    import numpy as np
    with wave.open(str(wav)) as wf:
        audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    segs, _info = whisper().transcribe(audio, language=os.environ.get("WHISPER_LANG", "en"), word_timestamps=words,
                                       vad_filter=True, beam_size=1, condition_on_previous_text=False)
    out = []
    for s in segs:
        words = [{"w": w.word.strip(), "start": w.start, "end": w.end} for w in (s.words or []) if w.word.strip()]
        out.append({"start": s.start, "end": s.end, "text": s.text.strip(), "words": words})
    wav.unlink(missing_ok=True)
    return out


def _ts(t):
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _clean(s):
    return re.sub(r"[{}\\]", "", s).replace("\n", " ").strip()


_font_cache = {}


def text_width(text, size):
    """Rendered width in px (PIL + the bundled font when available, else a conservative estimate)."""
    try:
        from PIL import ImageFont
        fp = FONT_DIR / "Anton-Regular.ttf"
        if FONT == "Anton" and fp.exists():
            if size not in _font_cache:
                _font_cache[size] = ImageFont.truetype(str(fp), size)
            return _font_cache[size].getlength(text) * 1.04
    except Exception:
        pass
    return len(text) * size * 0.56


def fit_text(text, size, max_w, max_lines, min_size=34):
    """Break text into <= max_lines balanced lines at word boundaries, shrinking the font until it fits.
    Returns (ass_text_with_\\N, font_size)."""
    words = text.split()
    if not words:
        return "", size
    for sz in range(size, min_size - 1, -2):
        if text_width(text, sz) <= max_w:
            return text, sz
        if max_lines >= 2 and len(words) > 1:
            best = None
            for i in range(1, len(words)):
                a, b = " ".join(words[:i]), " ".join(words[i:])
                w = max(text_width(a, sz), text_width(b, sz))
                if w <= max_w and (best is None or w < best[0]):
                    best = (w, a + "\\N" + b)
            if best:
                return best[1], sz
    # still too long at min size: hard-trim the second line
    out = text
    while len(out) > 4 and text_width(out, min_size) > max_w * max_lines * 0.95:
        out = out[:-2]
    return out.rstrip() + ("…" if out != text else ""), min_size


def build_ass(path, hook, storyline, credit, words, dur, context_card=""):
    hdr = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Hook,{FONT},128,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,1,0,1,10,5,5,60,60,0,1
Style: Story,{FONT},54,&H00FFFFFF,&H00FFFFFF,&H40000000,&H40000000,1,0,0,0,100,100,1,0,3,16,0,5,60,60,0,1
Style: Cap,{FONT},92,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,1,0,1,8,3,5,60,60,0,1
Style: Ctx,{FONT},60,&H00FFFFFF,&H00FFFFFF,&H28000000,&H28000000,0,0,0,0,100,100,1,0,3,18,0,2,60,60,0,1
Style: Credit,{FONT},40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,1,0,1,4,1,5,40,40,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    # Hook title card: first 2 s, pops in, yellow, over a dark band
    htxt, hsz = fit_text(_clean(hook), 128, W - 120, 2, min_size=80)
    ev.append(f"Dialogue: 2,{_ts(0)},{_ts(2.0)},Hook,,0,0,0,,{{\\pos(540,{FG_Y - 210})\\fs{hsz}\\c&H00F0FF&\\fscx60\\fscy60\\t(0,160,\\fscx100\\fscy100)}}{htxt}")
    # Context card (AI): plain setup of what is happening, ~2-5 s, max two lines on a dark band,
    # bottom-anchored above the storyline banner so it never collides with it
    if context_card and dur > 3.0:
        ctxt, csz = fit_text(_clean(context_card), 60, W - 180, 2, min_size=40)
        ev.append(f"Dialogue: 2,{_ts(2.0)},{_ts(min(5.0, dur))},Ctx,,0,0,0,,{{\\an2\\pos(540,{FG_Y - 165})\\fs{csz}\\fad(150,200)}}{ctxt}")
    # Storyline banner (persistent after the hook)
    stxt, ssz = fit_text(_clean(storyline), 54, W - 160, 1, min_size=36)
    ev.append(f"Dialogue: 1,{_ts(2.0)},{_ts(dur)},Story,,0,0,0,,{{\\pos(540,{FG_Y - 90})\\fs{ssz}\\fad(200,0)}}{stxt}")
    # Credit
    ev.append(f"Dialogue: 1,{_ts(0)},{_ts(dur)},Credit,,0,0,0,,{{\\pos(540,{H - 230})\\alpha&H30&}}{_clean(credit)}")
    # Word-by-word captions in chunks of up to 3 words
    chunks, cur = [], []
    for w in words:
        if cur and (len(cur) >= 3 or w["start"] - cur[-1]["end"] > 0.6):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    cy = FG_Y + FG_H + 190
    for ci, ch in enumerate(chunks):
        nxt = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else dur
        for i, w in enumerate(ch):
            st = w["start"]
            en = ch[i + 1]["start"] if i + 1 < len(ch) else min(nxt, w["end"] + 0.35)
            if en - st < 0.05:
                en = st + 0.05
            parts = []
            for j, x in enumerate(ch):
                txt = _clean(re.sub(r"[^\w'?!]", "", x["w"]).upper())
                if not txt:
                    continue
                if j == i:
                    parts.append(f"{{\\c&H00F0FF&\\fscx112\\fscy112}}{txt}{{\\r}}")
                else:
                    parts.append(txt)
            if parts:
                ev.append(f"Dialogue: 0,{_ts(st)},{_ts(en)},Cap,,0,0,0,,{{\\pos(540,{cy})}}{' '.join(parts)}")
    Path(path).write_text(hdr + "\n".join(ev) + "\n", encoding="utf-8")


def render(src, start, dur, out_mp4, workdir, hook, storyline, credit, words, zoom_at, speech=None, context_card=""):
    workdir = Path(workdir)
    ass = workdir / "c.ass"
    build_ass(ass, hook, storyline, credit, words, dur, context_card)
    z0, z1 = max(0.0, zoom_at - 0.25), min(dur, zoom_at + 2.4)
    z2 = min(dur - 0.5, z1 + 6.0)  # second, smaller punch-in a few seconds later
    fontsdir = FONT_DIR if FONT_DIR.exists() else workdir
    vf = (
        f"[0:v]fps=30,setsar=1,split=4[a][b][c][d];"
        f"[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=8:2,scale={W}:{H},eq=brightness=-0.18:saturation=1.2[bg];"
        f"[b]scale={W}:{FG_H}:force_original_aspect_ratio=increase,crop={W}:{FG_H}[fg];"
        f"[c]scale={int(W*1.28)//2*2}:{int(FG_H*1.28)//2*2},crop={W}:{FG_H}[z1];"
        f"[d]scale={int(W*1.12)//2*2}:{int(FG_H*1.12)//2*2},crop={W}:{FG_H}[z2];"
        f"[bg][fg]overlay=0:{FG_Y}[v1];"
        f"[v1][z1]overlay=0:{FG_Y}:enable='between(t,{z0:.2f},{z1:.2f})'[v2];"
        f"[v2][z2]overlay=0:{FG_Y}:enable='between(t,{z1+3.0:.2f},{z2:.2f})'[v3];"
        f"[v3]subtitles=filename='{ass}':fontsdir='{fontsdir}'[v]"
    )
    af = "[0:a]aresample=48000"
    if env_bool("MUTE_MUSIC") and speech:
        terms = "+".join(f"between(t,{max(0, a - 0.2):.2f},{b + 0.25:.2f})" for a, b in speech[:80])
        af += f",volume='0.15+0.85*gt({terms},0)':eval=frame"
    af += ",loudnorm=I=-14:TP=-1.5:LRA=11[a]"
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.2f}", "-t", f"{dur:.2f}", "-i", str(src),
         "-filter_complex", vf + ";" + af, "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", os.environ.get("X264_PRESET", "veryfast"), "-crf", "21", "-pix_fmt", "yuv420p",
         "-r", "30", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", "-shortest", str(out_mp4)],
        timeout=900)
    ass.unlink(missing_ok=True)
