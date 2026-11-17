# GTA 6 Live-Stream Clipper (free, GitHub Actions)

Watches your chosen streamers every 30 minutes. When one is live **playing Grand Theft Auto VI**, it records the stream in 10-minute pieces, finds the hype moments (loudness spikes + Twitch chat explosions), and turns each into a **YouTube Shorts–ready reel**:

- 25–55 s, 1080×1920, blurred-background fill, punch-in zooms on the moment
- bold **hook title card** for the first 2 s
- **word-by-word burned captions** (faster-whisper, runs free on the runner CPU)
- original **storyline banner** (e.g. *Speed vs Vice City – Day 1, Part 7*)
- small credit: *Clip: @creator live on Twitch*
- a `.txt` with a ready-to-paste **title + description** (credit, channel link, program, hashtags, paid-promotion reminder) and a **score**

Everything lands in **Releases** (one per creator per day), and **[clips/index.md](clips/index.md)** lists the newest clips, best first, with the top 2–3 per hour marked **🔥 POST THESE**.

> Nothing is ever posted for you. You download and upload yourself.

---

## Using it from a tablet

**Get clips**
1. Open **[clips/index.md](clips/index.md)** (or the **Releases** page: repo home → *Releases* on the right / bottom).
2. Tap **MP4** to download the video, tap **TXT** to copy the title + description.
3. Post the **🔥 POST THESE** ones only.

**Run it now (instead of waiting for the 30-min timer)**
1. Repo → **Actions** tab → **Clip GTA 6 streams** → **Run workflow**.
2. Leave everything blank to check all enabled creators, or type one id (e.g. `kaicenat`) in *channel*.
3. Untick *Only clip when … Grand Theft Auto VI* to clip a creator playing anything else.
4. *TEST ONLY* boxes: paste a Twitch VOD / YouTube video link to try the editor on a past stream (e.g. 10 minutes of it).

**Turn creators on/off**
1. Open `streamers.yml` → tap the ✏️ pencil.
2. Set `enabled: true` or `enabled: false` for the creator → **Commit changes**.
3. Fill in each program's **required hashtags** under `hashtags:` (check the program brief — the seeded ones are placeholders).
4. Set `sponsored: true` for a creator whose campaign pays you (adds a “tick Paid promotion” line).

Optional settings (Settings → Secrets and variables → Actions → **Variables**):
- `MUTE_MUSIC` = `true` → turn game audio down whenever nobody is talking (helps against Content ID claims from in-game radio).

---

## YouTube rules you must respect

- **Permission:** only clip creators whose **clipping program** you're accepted into (Content Rewards, Reach, CORE, etc.). xQc, CaseOh and Stable Ronaldo are **disabled** because no permission is known. IShowSpeed is marked `check-campaign` — only post if you're in an active Reach/Expedia campaign for him.
- **Reused content:** YouTube can demonetize channels that just re-upload other people's streams. These edits add a hook, captions, a storyline and zooms — keep your titles original and don't post every clip.
- **Paid promotion:** if a brand or creator pays you for the views, tick **“Includes paid promotion”** in YouTube Studio. The `.txt` reminds you when `sponsored: true`.
- **Music / Content ID:** GTA's in-game radio is licensed music and can trigger Content ID claims. Use `MUTE_MUSIC`, or skip clips with loud radio.
- **Volume:** post the **best 2–3 per hour**, not all of them. Mass-posting near-identical clips gets flagged as spam.

---

## How it works

| Piece | File |
|---|---|
| Creator list | `streamers.yml` |
| Schedule (every 30 min) + matrix job per creator, no overlapping runs | `.github/workflows/clip.yml` |
| Live + GTA VI check (streamlink for Twitch, yt-dlp for YouTube) | `clipper/live.py` |
| Recording in 10-min segments at 720p | `clipper/__main__.py` |
| Chat velocity (anonymous Twitch IRC) | `clipper/chat.py` |
| Spike detection (EBU R128 loudness + chat) | `clipper/detect.py` |
| Hook / storyline / title templates | `clipper/story.py` |
| Vertical edit + captions | `clipper/edit.py` |
| Releases + index | `clipper/publish.py` |

Limits: each GitHub runner lasts at most 6 h, so a long stream is recorded in ~5 h 20 min chunks (the next scheduled run picks up the rest). Clips come out about 10–15 min after the moment happens. YouTube sometimes blocks downloads from GitHub's servers ("confirm you're not a bot"); Twitch is reliable. Public repo = free, unlimited Actions minutes.
