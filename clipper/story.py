"""Tiny template engine: hook titles + storyline overlay from the transcript (no paid APIs)."""
import hashlib
import re

from .common import LAUNCH, now_local

THEMES = [
    ("cops", "Cop Chase", ["cop", "cops", "police", "wanted", "star", "stars", "chase", "chasing", "siren", "arrest", "jail", "swat", "helicopter", "pull over"],
     ["{NAME} vs THE VICE CITY COPS", "{NAME} JUST WENT 5 STARS", "THE COPS WERE NOT READY FOR {NAME}", "{NAME} TRIED TO OUTRUN THE COPS"], "🚔"),
    ("heist", "The Heist", ["bank", "heist", "rob", "robbing", "robbery", "money", "cash", "safe", "vault", "store", "register", "steal", "stole"],
     ["{NAME}'S FIRST HEIST WENT WRONG", "{NAME} ROBBED THE WRONG PLACE", "{NAME} PULLED OFF THE HEIST?!", "THIS HEIST WAS A DISASTER FOR {NAME}"], "💰"),
    ("fight", "Shootout", ["shoot", "shooting", "gun", "guns", "kill", "killed", "dead", "die", "died", "fight", "punch", "shot", "sniper", "bullet"],
     ["{NAME} STARTED A SHOOTOUT IN VICE CITY", "{NAME} DID NOT SURVIVE THIS", "{NAME} PICKED THE WRONG FIGHT", "{NAME} WENT FULL GTA MODE"], "🔫"),
    ("drive", "Joyride", ["car", "cars", "drive", "driving", "crash", "crashed", "flip", "plane", "boat", "bike", "jump", "ramp", "race", "speed"],
     ["{NAME} CANNOT DRIVE IN GTA 6", "{NAME}'S CRAZIEST CRASH YET", "{NAME} SENT IT IN VICE CITY", "{NAME} TOOK THE WORST SHORTCUT"], "🚗"),
    ("story", "Jason & Lucia", ["lucia", "jason", "girlfriend", "boyfriend", "mission", "cutscene", "story"],
     ["{NAME} REACTS TO THIS GTA 6 MOMENT", "{NAME} WAS NOT READY FOR THIS MISSION", "THIS GTA 6 MISSION BROKE {NAME}"], "🎬"),
    ("wild", "Unhinged Moments", ["what", "bro", "no way", "yo", "oh my god", "wait", "nah", "crazy", "insane", "why"],
     ["{NAME} CAN'T BELIEVE WHAT JUST HAPPENED", "{NAME} LOST IT IN GTA 6", "GTA 6 BROKE {NAME}", "{NAME}'S REACTION IS EVERYTHING", "{NAME} WAS NOT READY"], "😭"),
]


def _pick(options, seed):
    h = int(hashlib.md5(seed.encode()).hexdigest(), 16)
    return options[h % len(options)]


def theme_for(text):
    t = " " + re.sub(r"[^a-z0-9' ]", " ", text.lower()) + " "
    best, best_n = THEMES[-1], 0
    for th in THEMES[:-1]:
        n = sum(t.count(f" {k} ") for k in th[2])
        if n > best_n:
            best, best_n = th, n
    return best


def quote_near(segments, t_rel):
    """A short, complete sentence said right around the spike (or '' if none fits)."""
    cands = []
    for sg in segments:
        d = 0 if sg["start"] <= t_rel + 1 and sg["end"] >= t_rel - 3 else min(abs(sg["start"] - t_rel), abs(sg["end"] - t_rel))
        if d > 5:
            continue
        for sent in re.split(r"(?<=[.!?])\s+", sg["text"].strip()):
            words = re.sub(r"[^\w' ?!]", "", sent).split()
            if 2 <= len(words) <= 8:
                bonus = 2 if sent.rstrip().endswith(("!", "?")) else 0
                cands.append((d - bonus - 0.1 * len(words), " ".join(words)))
    if not cands:
        return ""
    return sorted(cands)[0][1]


def day_number():
    d = (now_local().date() - LAUNCH).days + 1
    return max(1, d)


def build(creator, segments, t_rel, part, seed):
    full = " ".join(s["text"] for s in segments)
    key, chapter, _kw, hooks, emoji = theme_for(full)
    name = creator["short"].upper()
    tmpl = _pick(hooks, seed)
    hook = tmpl.format(NAME=name)
    quote = quote_near(segments, t_rel)
    title_hook = tmpl.title().replace("{Name}", creator["short"]).replace("Gta", "GTA").replace("Vs ", "vs ").replace("'S ", "'s ")
    if quote and len(quote) <= 45:
        yt_title = f'{creator["short"]}: "{quote[0].upper() + quote[1:]}" {emoji} #GTA6'
    else:
        yt_title = f"{title_hook} {emoji} #GTA6"
    storyline = f"{creator['short']} vs Vice City – Day {day_number()}, Part {part}"
    return {
        "theme": key,
        "chapter": chapter,
        "hook_card": hook,  # burned in for first 2s
        "storyline": storyline,
        "title": yt_title[:100],
        "alt_title": f"{title_hook} {emoji}"[:100],
        "quote": quote,
    }
