"""Unit tests for clipper/ai.py + session gate + overlay layout (mocked HTTP, no keys needed).
Run: python -m pytest -q tests  (or python tests/test_ai.py)"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from clipper import ai, story  # noqa: E402
from clipper.session import Session  # noqa: E402

GOOD = {"context_card": "Speed just robbed a gas station with 4 stars on him",
        "hook": "He did NOT think this through", "title": "Speed's first Vice City robbery goes wrong",
        "storyline": "Day 1: Speed's first heist", "summary": "Speed robs a gas station and gets 4 stars."}


class R:
    def __init__(self, code, body=None, headers=None):
        self.status_code, self._b, self.headers = code, body, headers or {}

    def json(self):
        return self._b


def ok(d, think=False):
    c = json.dumps(d)
    if think:
        c = "<think>hmm</think>\n" + c
    return R(200, {"choices": [{"message": {"content": c}}]})


def env(**kw):
    base = {"GROQ_API_KEY": "", "GEMINI_API_KEY": ""}
    base.update(kw)
    return mock.patch.dict(os.environ, base)


def setup_function(_f=None):
    ai._cooldown.clear()


def test_no_key_returns_none_without_http():
    with env(), mock.patch("requests.post") as p:
        assert not ai.available()
        assert ai.generate("Speed", 1, "text") is None
        p.assert_not_called()


def test_success_and_title_tag():
    setup_function()
    with env(GROQ_API_KEY="x"), mock.patch("requests.post", return_value=ok(GOOD)) as p:
        r = ai.generate("Speed", 1, "I just robbed the gas station, four stars bro", "earlier text", ["a"], "")
    assert r and r["title"].endswith("#GTA6") and r["model"] == "groq:openai/gpt-oss-120b"
    body = p.call_args.kwargs["json"]
    assert body["model"] == "openai/gpt-oss-120b" and body["response_format"]["type"] == "json_object"
    assert body["reasoning_effort"] == "low"
    assert p.call_args.kwargs["timeout"] <= 10.0


def test_rotation_on_429_and_errors():
    setup_function()
    seq = [R(429, {}, {"retry-after": "30"}), R(500, {}), ok(GOOD, think=True)]
    with env(GROQ_API_KEY="x"), mock.patch("requests.post", side_effect=seq) as p:
        r = ai.generate("Speed", 1, "t")
    assert [c.kwargs["json"]["model"] for c in p.call_args_list] == [
        "openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"]
    assert "reasoning_effort" not in p.call_args_list[2].kwargs["json"]
    assert r["model"] == "groq:qwen/qwen3.8-27b"
    # 120b is cooling down after the 429 -> next call starts at 20b
    with env(GROQ_API_KEY="x"), mock.patch("requests.post", return_value=ok(GOOD)) as p:
        ai.generate("Speed", 1, "t")
    assert p.call_args_list[0].kwargs["json"]["model"] == "openai/gpt-oss-20b"


def test_gemini_fallback():
    setup_function()
    seq = [R(429, {}), R(429, {}), R(429, {}), ok(GOOD)]
    with env(GROQ_API_KEY="x", GEMINI_API_KEY="y"), mock.patch("requests.post", side_effect=seq) as p:
        r = ai.generate("Speed", 1, "t")
    assert r["model"].startswith("gemini:") and "generativelanguage" in p.call_args_list[3].args[0]


def test_all_fail_and_timeout_returns_none():
    setup_function()
    import requests
    with env(GROQ_API_KEY="x"), mock.patch("requests.post", side_effect=requests.Timeout()):
        assert ai.generate("Speed", 1, "t") is None
    setup_function()
    with env(GROQ_API_KEY="x"), mock.patch("requests.post", return_value=R(200, {"choices": [{"message": {"content": "not json"}}]})):
        assert ai.generate("Speed", 1, "t") is None
    setup_function()
    with env(GROQ_API_KEY="x"), mock.patch("requests.post", side_effect=RuntimeError("boom")):
        assert ai.generate("Speed", 1, "t") is None  # never raises


def test_deadline_stops_rotation():
    setup_function()
    def slow(*a, **k):
        time.sleep(0.3)
        return R(500, {})
    with env(GROQ_API_KEY="x"), mock.patch.object(ai, "TIMEOUT", 1.2), mock.patch("requests.post", side_effect=slow) as p:
        t = time.time()
        assert ai.generate("Speed", 1, "t") is None
    assert time.time() - t < 1.5 and p.call_count <= 2


def test_lengths_and_slur_screen():
    long = {k: "word " * 40 for k in GOOD}
    c = ai.clean(long)
    assert c and all(len(c[k]) <= n for k, n in ai.LIMITS.items() if k != "title") and len(c["title"]) <= 90
    bad = dict(GOOD, hook="that f4ggot cop")
    assert ai.clean(bad) is None
    assert ai.clean(dict(GOOD, hook="")) is None


def test_prompt_size_under_budget():
    big = "blah " * 3000
    msgs = ai.build_messages("Kai", 1, big, big, ["summary " * 20] * 6, "Day 1: Kai's first heist")
    chars = sum(len(m["content"]) for m in msgs)
    assert chars / 4 + ai.MAX_OUT < 1500, chars


def test_story_overlay():
    c = {"short": "Speed", "name": "IShowSpeed"}
    segs = [{"start": 0, "end": 2, "text": "Bro the cops are chasing me!", "words": []}]
    st = story.build(c, segs, 1.0, 3, "seed")
    assert st["context_card"] == "" and st["summary"]
    res = dict(GOOD, model="groq:m", title=GOOD["title"] + " #GTA6")
    out = story.apply_ai(st, res)
    assert out["hook_card"] == GOOD["hook"].upper() and out["storyline"] == GOOD["storyline"]
    assert out["alt_title"] == st["title"] and out["ai_model"] == "groq:m"


def test_session_gate_and_log():
    with tempfile.TemporaryDirectory() as d:
        s = Session(Path(d) / "session.json")
        t = time.time()
        assert s.ai_eligible(30, t)        # 1st of hour: top 3 so far
        assert s.ai_eligible(20, t)
        assert s.ai_eligible(10, t)
        assert not s.ai_eligible(5, t)      # not top 3, below 60
        assert s.ai_eligible(70, t)         # >= 60 always
        s.add_lines([{"start": 0, "end": 3, "text": "we're robbing the bank"}], t - 200)
        s.add_lines([{"start": 0, "end": 3, "text": "we're robbing the bank"}], t - 200)  # de-dup
        assert s.lead_text(t - 300, t) == "we're robbing the bank"
        s.add_clip(t, "id1", 70, "Kai robs a bank.", "Day 1: Kai's first heist", ai=True)
        s.save()
        s2 = Session(Path(d) / "session.json")
        assert s2.summaries() == ["Kai robs a bank."] and s2.storyline == "Day 1: Kai's first heist"


def test_ass_layout_fits():
    from clipper import edit
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "c.ass"
        edit.build_ass(p, "THE COPS WERE NOT READY FOR SPEED AT ALL", "Day 1: Kai's first heist goes really wrong ok",
                       "Clip: @x live on Twitch", [], 30, context_card=GOOD["context_card"])
        txt = p.read_text()
    ctx = [l for l in txt.splitlines() if ",Ctx," in l][0]
    assert "0:00:02.00,0:00:05.00" in ctx and ctx.count("\\N") <= 1


if __name__ == "__main__":
    fails = 0
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            setup_function()
            try:
                f()
                print("ok  ", k)
            except Exception as e:
                fails += 1
                print("FAIL", k, repr(e))
    sys.exit(1 if fails else 0)
