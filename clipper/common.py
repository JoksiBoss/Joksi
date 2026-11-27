import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo(os.environ.get("CLIP_TZ", "Asia/Kolkata"))
LAUNCH = datetime.date(2026, 11, 19)
GTA_RE = re.compile(r"grand\s*theft\s*auto\s*(vi|6)\b|\bgta\s*-?\s*(vi|6)\b", re.I)


def log(*a):
    print(*a, flush=True, file=sys.stderr)


def env_bool(name, default=False):
    v = os.environ.get(name, "").strip().lower()
    if v == "":
        return default
    return v in ("1", "true", "yes", "on")


def now_local():
    return datetime.datetime.now(TZ)


def load_config():
    with open(ROOT / "streamers.yml") as f:
        cfg = yaml.safe_load(f)
    defaults = cfg.get("defaults", {}) or {}
    marks = {str(k): str(v) for k, v in (defaults.get("account_watermarks", {}) or {}).items()}
    styles = {str(k): dict(v or {}) for k, v in (defaults.get("account_styles", {}) or {}).items()}
    out = []
    for c in cfg.get("creators", []):
        c = dict(c)
        # watermark: the creator's own, else the watermark of the GTA 6 account the creator belongs to
        c["watermark"] = str(c.get("watermark") or marks.get(str(c.get("account", "")), "") or "")
        c.setdefault("short", c["name"])
        # look of the GTA 6 account the creator belongs to (badge name + accent colour)
        c["style"] = dict(styles.get(str(c.get("account", "")), {}), **(c.get("style") or {}))
        # language: force captions + on-screen text language for this creator (e.g. "de"); "" = auto/English
        c["language"] = str(c.get("language") or "").strip().lower()
        c.setdefault("enabled", False)
        c.setdefault("sponsored", False)
        c.setdefault("permission", "none")
        tags = list(defaults.get("hashtags", [])) + list(c.get("hashtags", []) or [])
        seen, clean = set(), []
        for t in tags:
            t = "#" + str(t).lstrip("#").replace(" ", "")
            if t.lower() not in seen:
                seen.add(t.lower())
                clean.append(t)
        c["hashtags"] = clean
        out.append(c)
    return out


def get_creator(cid):
    for c in load_config():
        if c["id"].lower() == cid.lower() or str(c["channel"]).lower().lstrip("@") == cid.lower().lstrip("@"):
            return c
    raise SystemExit(f"creator '{cid}' not in streamers.yml")


def channel_url(c):
    if c["platform"] == "twitch":
        return f"https://www.twitch.tv/{c['channel']}"
    ch = c["channel"]
    return f"https://www.youtube.com/{ch if ch.startswith(('@', 'channel/', 'c/')) else '@' + ch}"


def live_url(c):
    if c["platform"] == "twitch":
        return channel_url(c)
    return channel_url(c) + "/live"


def handle(c):
    return "@" + str(c["channel"]).lstrip("@")


def platform_name(c):
    return "Twitch" if c["platform"] == "twitch" else "YouTube"


def run(cmd, check=True, capture=True, timeout=None):
    r = subprocess.run(cmd, capture_output=capture, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"command failed ({r.returncode}): {' '.join(map(str, cmd))[:300]}\n{(r.stderr or '')[-2000:]}")
    return r


def set_output(key, value):
    p = os.environ.get("GITHUB_OUTPUT")
    if p:
        with open(p, "a") as f:
            f.write(f"{key}={value}\n")
    log(f"output {key}={value}")


def summary(md):
    p = os.environ.get("GITHUB_STEP_SUMMARY")
    if p:
        with open(p, "a") as f:
            f.write(md + "\n")
    log(md)


def is_twitch_url(u):
    return "twitch.tv" in (u or "")


def ytdlp():
    """yt-dlp command, with cookies from the optional YT_COOKIES secret (helps when YouTube says 'not a bot')."""
    cmd = ["yt-dlp"]
    ck = os.environ.get("YT_COOKIES", "").strip()
    if ck:
        p = Path("/tmp/yt_cookies.txt")
        if not p.exists():
            p.write_text(ck + "\n")
        cmd += ["--cookies", str(p)]
    # web_safari/tv still return live HLS formats when the default web client gets none
    cmd += ["--extractor-args", "youtube:player_client=" + os.environ.get("YT_CLIENTS", "default,web_safari,tv")]
    return cmd
