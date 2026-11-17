"""GitHub Releases upload + clips/index.md (uses the workflow's GITHUB_TOKEN)."""
import base64
import mimetypes
import os
import re
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

import requests

from .common import load_config, log, now_local

API = "https://api.github.com"
NAME_RE = re.compile(r"^(\d{8})-(\d{4})_s(\d{3})_([a-z0-9_]+)_p(\d+)\.mp4$")


def repo():
    return os.environ.get("GITHUB_REPOSITORY", "")


def enabled():
    return bool(os.environ.get("GITHUB_TOKEN")) and bool(repo()) and os.environ.get("NO_PUBLISH", "") != "1"


def _h(extra=None):
    h = {"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}
    h.update(extra or {})
    return h


def gh(method, path, **kw):
    url = path if path.startswith("http") else f"{API}{path}"
    for attempt in range(5):
        r = requests.request(method, url, headers=_h(kw.pop("headers", None)), timeout=300, **kw)
        if r.status_code in (502, 503, 504) or (r.status_code == 403 and "rate" in r.text.lower()):
            time.sleep(5 * (attempt + 1))
            continue
        return r
    return r


def get_or_create_release(tag, title, body=""):
    r = gh("GET", f"/repos/{repo()}/releases/tags/{tag}")
    if r.status_code == 200:
        return r.json()
    r = gh("POST", f"/repos/{repo()}/releases", json={"tag_name": tag, "name": title, "body": body or "Clips",
                                                       "target_commitish": os.environ.get("GITHUB_SHA") or "main"})
    if r.status_code == 422:  # created concurrently
        return gh("GET", f"/repos/{repo()}/releases/tags/{tag}").json()
    r.raise_for_status()
    log(f"[publish] created release {tag}")
    return r.json()


def list_assets(rel_id):
    out, page = [], 1
    while True:
        r = gh("GET", f"/repos/{repo()}/releases/{rel_id}/assets", params={"per_page": 100, "page": page})
        r.raise_for_status()
        batch = r.json()
        out += batch
        if len(batch) < 100:
            return out
        page += 1


def upload(rel, path, label=None):
    path = Path(path)
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    url = f"https://uploads.github.com/repos/{repo()}/releases/{rel['id']}/assets?name={quote(path.name)}"
    if label:
        label = " ".join("".join(ch for ch in label if ord(ch) < 0x10000).split())  # GitHub labels reject emoji (4-byte)
        url += f"&label={quote(label[:120])}"
    with open(path, "rb") as f:
        data = f.read()
    r = gh("POST", url, headers={"Content-Type": ctype}, data=data)
    if r.status_code >= 300:
        raise RuntimeError(f"upload {path.name} failed {r.status_code}: {r.text[:300]}")
    return r.json()["browser_download_url"]


def _clip_rows(release):
    names = {c["id"]: c["name"] for c in load_config()}
    assets = release.get("assets") or []
    by_name = {a["name"]: a for a in assets}
    rows = []
    for a in assets:
        m = NAME_RE.match(a["name"])
        if not m:
            continue
        d, hm, sc, cid, part = m.groups()
        stem = a["name"][:-4]
        txt = by_name.get(stem + ".txt")
        rows.append({
            "hour": f"{d}-{hm[:2]}", "time": f"{d}-{hm}", "score": int(sc), "creator": names.get(cid, cid),
            "title": re.sub(r"^\[\d+\]\s*", "", a.get("label") or stem), "mp4": a["browser_download_url"],
            "txt": txt["browser_download_url"] if txt else "", "size": a.get("size", 0),
        })
    return rows


def _hour_label(h):
    d, hh = h.split("-")
    return f"{d[6:8]}/{d[4:6]}/{d[:4]} {hh}:00–{(int(hh) + 1) % 24:02d}:00 CET"


def _table(rows):
    by_hour = defaultdict(list)
    for r in rows:
        by_hour[r["hour"]].append(r)
    lines = []
    for h in sorted(by_hour, reverse=True):
        rs = sorted(by_hour[h], key=lambda r: -r["score"])
        lines.append(f"\n### {_hour_label(h)}\n")
        lines.append("| | Score | Creator | Title | Video | Title + description |")
        lines.append("|---|---|---|---|---|---|")
        for i, r in enumerate(rs):
            hot = i < 2 or (i == 2 and r["score"] >= 60)
            flag = "🔥 **POST THESE**" if hot else ""
            title = r["title"].replace("|", "/")
            txt = f"[TXT]({r['txt']})" if r["txt"] else ""
            lines.append(f"| {flag} | {r['score']} | {r['creator']} | {title} | [MP4]({r['mp4']}) | {txt} |")
    return "\n".join(lines)


HEADER = ("Download on your tablet: tap **MP4** to save the video, tap **TXT** for the ready-to-paste title + description.\n"
          "Post only the 🔥 **POST THESE** clips (best 2–3 per hour), not all of them.\n")


def refresh_release_body(tag):
    rel = gh("GET", f"/repos/{repo()}/releases/tags/{tag}").json()
    body = HEADER + _table(_clip_rows(rel))
    gh("PATCH", f"/repos/{repo()}/releases/{rel['id']}", json={"body": body[:120000]})


def refresh_index(max_releases=40, hours=72):
    r = gh("GET", f"/repos/{repo()}/releases", params={"per_page": max_releases})
    r.raise_for_status()
    rows = []
    for rel in r.json():
        rows += _clip_rows(rel)
    rows.sort(key=lambda x: x["time"], reverse=True)
    keep_hours = sorted({x["hour"] for x in rows}, reverse=True)[:hours]
    rows = [x for x in rows if x["hour"] in keep_hours]
    md = (f"# GTA 6 clips — newest first\n\n_Updated {now_local():%d %b %Y %H:%M} CET · "
          f"[All releases](https://github.com/{repo()}/releases)_\n\n" + HEADER + _table(rows) + "\n")
    path = "clips/index.md"
    for attempt in range(6):
        cur = gh("GET", f"/repos/{repo()}/contents/{path}")
        sha = cur.json().get("sha") if cur.status_code == 200 else None
        payload = {"message": "Update clips index", "content": base64.b64encode(md.encode()).decode()}
        if sha:
            payload["sha"] = sha
        put = gh("PUT", f"/repos/{repo()}/contents/{path}", json=payload)
        if put.status_code in (200, 201):
            log("[publish] index updated")
            return
        time.sleep(3 + attempt * 2)
    log(f"[publish] index update failed: {put.status_code} {put.text[:200]}")
