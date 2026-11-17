"""Optional AI "context provider": hook / context card / title / storyline / summary from the transcript.

Groq (OpenAI-compatible) with model rotation, optional Gemini fallback. Any problem (no key, 429, timeout,
bad JSON, unsafe text) returns None and the caller silently uses the template engine in story.py.
Never raises.
"""
import json
import os
import re
import time

import requests

from .common import log

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
GROQ_MODELS = [m.strip() for m in os.environ.get(
    "GROQ_MODELS", "openai/gpt-oss-120b,openai/gpt-oss-20b,qwen/qwen3.8-27b").split(",") if m.strip()]
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "").strip() or "gemini-flash-latest"
TIMEOUT = float(os.environ.get("AI_TIMEOUT", "10"))       # total wall budget per clip, all attempts
MAX_OUT = int(os.environ.get("AI_MAX_TOKENS", "450"))     # completion cap (incl. low reasoning)

# Prompt budget (~4 chars/token): system ~330 tok + context ~650 tok + output <=450 => < 1.5K per request
LEAD_CHARS = 1500
CLIP_CHARS = 900
SUMMARY_CHARS = 450

LIMITS = {"context_card": 60, "hook": 40, "title": 90, "storyline": 45, "summary": 140}

_cooldown = {}  # model -> unix time until which we skip it (after a 429)

# Conservative slur / hate-term screen (any hit => discard the AI result entirely)
_BAD = re.compile(
    r"n[i1!|]+gg+(a|er|uh|ah)?s?|\bf[a@4]g+(s|ot|ots|got)?\b|\bretard(s|ed)?\b|\btr[a@4]nn(y|ie|ies)\b|"
    r"\bch[i1]nks?\b|\bsp[i1]cs?\b|\bk[i1]kes?\b|\bwetbacks?\b|\bbeaners?\b|\bcoons?\b|\bdykes?\b|\bgooks?\b|"
    r"\bpakis?\b|\bshemales?\b|\bjigaboos?\b|\braghead|\btowelhead",
    re.I)

SYSTEM = (
    "You write on-screen text for a YouTube Short clipped from a GTA 6 live stream. Use ONLY facts stated in "
    "the transcript. Never invent events, places, numbers, wanted levels, names or outcomes that are not said. "
    "If the transcript is unclear, stay vague and honest (e.g. 'X reacts to a wild moment'). No slurs, no "
    "hate, no sexual content, no profanity in the text. Write original text, do not copy the streamer's "
    "words verbatim except a short quote. Reply with ONE JSON object only, keys:\n"
    '"context_card": <=60 chars, plain setup of what is happening right before/at the moment, third person, '
    'e.g. "Speed just robbed a gas station with 4 stars on him";\n'
    '"hook": <=40 chars, punchy, original, makes people keep watching;\n'
    '"title": <=90 chars, must include #GTA6;\n'
    '"storyline": <=45 chars, ongoing arc label for this stream session, e.g. "Day 1: Kai\'s first heist" '
    "(keep the current arc label if the story has not clearly moved on);\n"
    '"summary": one short line (<=140 chars) describing this clip, for the session log.'
)


def available():
    return bool(os.environ.get("GROQ_API_KEY", "").strip() or os.environ.get("GEMINI_API_KEY", "").strip())


def _tail(text, n):
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= n:
        return text
    cut = text[-n:]
    return "…" + cut[cut.find(" ") + 1:] if " " in cut else cut


def _head(text, n):
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= n:
        return text
    cut = text[:n]
    return cut[:cut.rfind(" ")] + "…" if " " in cut else cut


def build_messages(creator_name, day, clip_text, lead_text, summaries, storyline):
    prev = "\n".join(f"- {s}" for s in summaries) if summaries else "(none yet)"
    prev = _tail(prev, SUMMARY_CHARS)
    user = (
        f"Streamer: {creator_name}. Game: GTA 6 (Vice City), launch day {day}.\n"
        f"Current arc label: {storyline or '(none yet)'}\n"
        f"Earlier clips this stream:\n{prev}\n\n"
        f"Transcript of the minutes BEFORE the clip:\n{_tail(lead_text, LEAD_CHARS) or '(not available)'}\n\n"
        f"Transcript of the CLIP itself (the moment):\n{_head(clip_text, CLIP_CHARS) or '(silent)'}\n\n"
        "Return the JSON object now."
    )
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def _providers():
    out = []
    gk = os.environ.get("GROQ_API_KEY", "").strip()
    if gk:
        for m in GROQ_MODELS:
            extra = {"reasoning_effort": "low"} if m.startswith("openai/gpt-oss") else {}
            out.append(("groq", GROQ_URL, gk, m, extra))
    ek = os.environ.get("GEMINI_API_KEY", "").strip()
    if ek:
        out.append(("gemini", GEMINI_URL, ek, GEMINI_MODEL, {"reasoning_effort": "low"}))
    return out


def _parse(content):
    if not content:
        return None
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return d if isinstance(d, dict) else None


def _fit(s, n):
    s = re.sub(r"\s+", " ", str(s or "")).strip().strip('"').strip()
    s = s.replace("\\", "").replace("{", "(").replace("}", ")")
    if len(s) <= n:
        return s
    cut = s[:n + 1]
    cut = cut[:cut.rfind(" ")] if " " in cut else s[:n]
    return cut.rstrip(",;:-–— ")


def clean(d):
    """Validate + trim a raw model dict. Returns cleaned dict or None if unusable/unsafe."""
    if not isinstance(d, dict):
        return None
    out = {k: _fit(d.get(k, ""), n) for k, n in LIMITS.items()}
    if not out["hook"] or not out["title"] or not out["context_card"]:
        return None
    if _BAD.search(" ".join(out.values())):
        log("[ai] result failed safety screen, using templates")
        return None
    if "#gta6" not in out["title"].lower():
        out["title"] = _fit(out["title"], LIMITS["title"] - 6) + " #GTA6"
    return out


def generate(creator_name, day, clip_text, lead_text="", summaries=(), storyline=""):
    """Returns {context_card, hook, title, storyline, summary, model} or None. Never raises."""
    try:
        provs = _providers()
        if not provs:
            return None
        msgs = build_messages(creator_name, day, clip_text, lead_text, list(summaries), storyline)
        deadline = time.time() + TIMEOUT
        for name, url, key, model, extra in provs:
            left = deadline - time.time()
            if left < 1.0:
                log("[ai] time budget used up, using templates")
                return None
            if _cooldown.get(model, 0) > time.time():
                continue
            body = {"model": model, "messages": msgs, "temperature": 0.7, "max_tokens": MAX_OUT,
                    "response_format": {"type": "json_object"}}
            body.update(extra)
            try:
                r = requests.post(url, json=body, timeout=left,
                                  headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            except requests.RequestException as e:
                log(f"[ai] {name}:{model} {type(e).__name__}")
                continue
            if r.status_code == 429:
                try:
                    wait = float(r.headers.get("retry-after", "60"))
                except ValueError:
                    wait = 60.0
                _cooldown[model] = time.time() + min(max(wait, 5.0), 3600.0)
                log(f"[ai] {name}:{model} rate-limited ({wait:.0f}s), rotating")
                continue
            if r.status_code != 200:
                log(f"[ai] {name}:{model} HTTP {r.status_code}, rotating")
                if r.status_code in (400, 404):
                    _cooldown[model] = time.time() + 3600  # bad model id / params: stop retrying it
                continue
            try:
                content = r.json()["choices"][0]["message"].get("content") or ""
            except (ValueError, KeyError, IndexError, TypeError):
                content = ""
            res = clean(_parse(content))
            if res:
                res["model"] = f"{name}:{model}"
                log(f"[ai] {name}:{model} ok")
                return res
            log(f"[ai] {name}:{model} unusable output, rotating")
        return None
    except Exception as e:  # pipeline must never break because of AI
        log(f"[ai] unexpected {e!r}")
        return None
