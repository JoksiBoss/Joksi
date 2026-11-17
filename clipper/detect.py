"""Spike detection: EBU R128 momentary loudness jumps + chat velocity."""
import math
import re
import subprocess

import numpy as np

from .common import log

EBU_RE = re.compile(r"t:\s*([\d.]+)\s+TARGET:.*?M:\s*(-?[\d.]+|-inf|nan)")


def loudness_per_second(path):
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-v", "verbose", "-i", str(path), "-map", "0:a:0", "-af", "ebur128=framelog=verbose", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    vals = {}
    for m in EBU_RE.finditer(p.stderr):
        t = float(m.group(1))
        try:
            v = float(m.group(2))
        except ValueError:
            v = -120.0
        if not math.isfinite(v):
            v = -120.0
        s = int(t)
        vals[s] = max(vals.get(s, -120.0), v)
    if not vals:
        log("[detect] no loudness data (no audio?)")
        return np.zeros(0)
    n = max(vals) + 1
    arr = np.full(n, -120.0)
    for k, v in vals.items():
        arr[k] = v
    return np.maximum(arr, -70.0)


def _rolling(arr, win, fn):
    out = np.empty_like(arr, dtype=float)
    h = win // 2
    for i in range(len(arr)):
        out[i] = fn(arr[max(0, i - h): i + h + 1])
    return out


def score_curve(loud, chat=None):
    """Return per-second combined spike score (roughly 0..2)."""
    if len(loud) == 0:
        return np.zeros(0)
    base = _rolling(loud, 90, np.median)
    peak = _rolling(loud, 3, np.max)
    audio = np.clip((peak - base) / 7.0, 0, 2)
    # bonus for absolute loudness (yelling) relative to the segment
    hi = np.percentile(loud, 95)
    audio += np.clip((peak - hi) / 6.0 + 0.3, 0, 0.5)
    if chat is not None and sum(chat) >= 30:
        c = np.array(chat[: len(loud)] + [0] * max(0, len(loud) - len(chat)), dtype=float)
        c5 = np.convolve(c, np.ones(5), mode="same")
        cbase = _rolling(c5, 120, np.median)
        cs = np.clip(np.log2((c5 + 2) / (cbase + 2)), 0, 2)
        # chat reacts a few seconds after the moment: pull it back
        cs = np.concatenate([cs[3:], np.zeros(3)])
        return 0.55 * audio + 0.45 * cs * 1.2
    return audio


def pick_peaks(curve, max_n=2, min_gap=45, min_score2=40, edge_lead=8):
    """Top peaks, de-duplicated so clip windows don't overlap. Returns [(t, score0_100)]."""
    if len(curve) < 25:
        return []
    order = np.argsort(-curve)
    picks = []
    for t in order:
        t = int(t)
        if t < edge_lead:
            continue
        if any(abs(t - p) < min_gap for p, _ in picks):
            continue
        sc = int(round(min(99, 100 * curve[t] / 3.0)))
        if picks and sc < min_score2:
            break
        picks.append((t, sc))
        if len(picks) >= max_n:
            break
    return picks
