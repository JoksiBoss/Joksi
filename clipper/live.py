"""Live / GTA VI detection."""
import json

from .common import GTA_RE, live_url, log, run


def check_twitch(c):
    url = live_url(c)
    r = run(["streamlink", "--json", url], check=False, timeout=90)
    try:
        data = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        data = {"error": (r.stderr or "")[-300:]}
    if "error" in data or not data.get("streams"):
        return {"live": False, "title": "", "category": "", "reason": str(data.get("error", "offline"))[:200]}
    md = data.get("metadata") or {}
    return {"live": True, "title": md.get("title") or "", "category": md.get("category") or "", "reason": "live"}


def check_youtube(c):
    url = live_url(c)
    r = run(["yt-dlp", "-J", "--no-warnings", "--skip-download", "--playlist-items", "1", url], check=False, timeout=120)
    if r.returncode != 0:
        err = (r.stderr or "").strip().splitlines()
        err = err[-1] if err else "error"
        blocked = "confirm you" in err.lower() or "bot" in err.lower()
        return {"live": False, "title": "", "category": "", "reason": ("BLOCKED: " if blocked else "") + err[:200]}
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"live": False, "title": "", "category": "", "reason": "bad json"}
    if d.get("_type") == "playlist" and d.get("entries"):
        d = d["entries"][0]
    live = d.get("live_status") == "is_live" or d.get("is_live") is True
    cat = " ".join((d.get("tags") or [])[:30])
    return {"live": live, "title": d.get("title") or "", "category": cat, "reason": d.get("live_status") or "",
            "video_url": d.get("webpage_url") or url}


def check(c):
    info = check_twitch(c) if c["platform"] == "twitch" else check_youtube(c)
    info["gta6"] = bool(GTA_RE.search(f"{info.get('title', '')} {info.get('category', '')}"))
    log(f"[{c['id']}] live={info['live']} gta6={info['gta6']} category={info.get('category')!r} title={info.get('title')!r} ({info.get('reason')})")
    return info
