"""Per-stream-session memory: rolling transcript (last few minutes) + 1-line summaries of earlier clips.

Lives in a small JSON in the job workdir (out/<creator>/session.json) so every clip knows what led up to it.
All times are stream wall-clock seconds (time.time() basis).
"""
import json
import os
import time
from pathlib import Path

from .common import log

CONTEXT_S = float(os.environ.get("AI_CONTEXT_SECONDS", "300"))   # rolling transcript window before a spike
AI_MIN_SCORE = int(os.environ.get("AI_MIN_SCORE", "60"))
AI_TOP_PER_HOUR = int(os.environ.get("AI_TOP_PER_HOUR", "3"))
AI_MAX_PER_HOUR = int(os.environ.get("AI_MAX_PER_HOUR", "12"))  # hard quota guard per creator


class Session:
    def __init__(self, path):
        self.path = Path(path)
        self.d = {"lines": [], "clips": [], "storyline": "", "hours": {}}
        try:
            if self.path.exists():
                self.d.update(json.loads(self.path.read_text()))
        except Exception as e:
            log(f"[session] could not read {self.path}: {e!r}")

    # ---------------- rolling transcript
    def add_lines(self, segs, t0):
        """segs: whisper segments with start/end relative to t0 (wall clock)."""
        have = {(round(x["t"], 1), x["text"]) for x in self.d["lines"]}
        for s in segs:
            txt = (s.get("text") or "").strip()
            t = round(t0 + float(s["start"]), 1)
            if txt and (t, txt) not in have:
                self.d["lines"].append({"t": t, "e": round(t0 + float(s["end"]), 1), "text": txt})
        self.d["lines"].sort(key=lambda x: x["t"])
        # keep ~2 context windows of history
        if self.d["lines"]:
            newest = self.d["lines"][-1]["e"]
            self.d["lines"] = [x for x in self.d["lines"] if x["e"] >= newest - 2 * CONTEXT_S]

    def covered_until(self, after):
        """Latest transcript end time that is >= `after` (so lead-in transcription can skip what we already have)."""
        ends = [x["e"] for x in self.d["lines"] if x["e"] >= after]
        return max(ends) if ends else after

    def lead_text(self, t_from, t_to):
        return " ".join(x["text"] for x in self.d["lines"] if x["e"] > t_from and x["t"] < t_to)

    # ---------------- clip log
    def summaries(self, n=6):
        return [c["summary"] for c in self.d["clips"][-n:] if c.get("summary")]

    @property
    def storyline(self):
        return self.d.get("storyline", "")

    def add_clip(self, t, stem, score, summary, storyline="", ai=False):
        self.d["clips"].append({"t": round(t, 1), "id": stem, "score": score, "summary": summary[:160], "ai": ai})
        self.d["clips"] = self.d["clips"][-40:]
        if storyline:
            self.d["storyline"] = storyline

    # ---------------- AI quota gate
    def ai_eligible(self, score, t):
        """AI only for clips likely to be posted: score >= AI_MIN_SCORE, or in the top N so far this hour."""
        hk = time.strftime("%Y%m%d%H", time.localtime(t))
        h = self.d["hours"].setdefault(hk, {"scores": [], "ai": 0})
        h["scores"].append(score)
        if h["ai"] >= AI_MAX_PER_HOUR:
            return False
        top = sorted(h["scores"], reverse=True)[:AI_TOP_PER_HOUR]
        return score >= AI_MIN_SCORE or score >= min(top)

    def ai_used(self, t):
        hk = time.strftime("%Y%m%d%H", time.localtime(t))
        self.d["hours"].setdefault(hk, {"scores": [], "ai": 0})["ai"] += 1

    def save(self):
        try:
            hours = dict(sorted(self.d["hours"].items())[-6:])
            self.d["hours"] = hours
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.d, ensure_ascii=False, indent=1))
            tmp.replace(self.path)
        except Exception as e:
            log(f"[session] save failed: {e!r}")
