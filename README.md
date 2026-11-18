# AI Clipping Engine (profile-driven; first profile: GTA 6)

One authorized VOD/stream URL in → full-stream analysis → ranked moments → polished 9:16 shorts with
animated captions → platform-specific metadata → rights-gated scheduling → YouTube Shorts + Instagram
Reels uploads → analytics that feed back into clip selection.

Everything runs locally and free: FFmpeg (NVENC + libass), faster-whisper, OpenCV, yt-dlp, SQLite, and
your existing **Claude Code CLI** as the "editor brain" (review + metadata). No OpusClip/Vizard, no paid API keys.

```
python main.py --profile gta6     --url "VOD_URL_OR_FILE" --streamer NAME
python main.py --profile valorant --url "VOD_URL_OR_FILE" --streamer NAME
```

## Clip Studio dashboard

```powershell
start_dashboard.bat                      # or: .venv\Scripts\python -m web.server
```
Opens http://127.0.0.1:8765. On localhost there is no login. To reach it from another device, run
`python -m web.server --host 0.0.0.0` with `DASHBOARD_PASSWORD` (10+ characters) set in `.env`. The
server refuses to start on a network interface without a password. Put HTTPS in front of it before
exposing it beyond your LAN.

Current pages:
- **Overview**: start a job, stats, review queue, recent jobs, auto-publish toggle.
- **Jobs**: live progress and logs; pause, resume, cancel and retry.
- **Clips**: filter by review state.
- **Clip editor**: watch the clip, approve or reject it, edit YouTube/Instagram titles, descriptions
  and hashtags, see the AI's reasoning and scores.

With `publishing.require_approval: true` (the default), only clips a human approved get scheduled.
The approve step also works from the CLI: `main.py approve <clip_id>`.

The backend API already covers re-render (trim, hook, caption fixes), rendering a rejected moment,
narration upload, per-clip scheduling, platform connections, analytics, and profile/settings
editing. These have no UI pages yet.

## How it works

| Stage | What happens | Module |
|---|---|---|
| Ingest | yt-dlp download (≤1080p) or local creator-provided file; source registered in DB | `src/ingestion/download.py` |
| Rights | rights status, commercial permission, date, reference, restrictions. **No rights → no publishing** | `src/ingestion/rights.py` |
| Transcribe | faster-whisper `large-v3-turbo` on GPU, word timestamps, profile vocabulary prompt, chunked for long streams | `src/transcription/` |
| Signals | per-second loudness vs. local baseline (screams, gunfire), speech rate, hype/laugh lexicon, pause→burst reactions, visual change, flashes; facecam detection | `src/detection/signals.py` |
| Candidates | peak picking + setup/payoff windows snapped to sentence boundaries | `src/detection/candidates.py` |
| AI review | Claude judges each candidate from transcript + signals, fixes start/end, categorizes, scores 8 criteria; local audio/visual quality added → 0–100 virality; rejection rules + dedup | `src/scoring/review.py` |
| Metadata | 3 YouTube titles + best, description, tags, hashtags; IG caption/hashtags/CTA; on-screen hook; caption spelling fixes; original commentary script | `src/metadata/generate.py` |
| Render | dead-air smart cuts, facecam-stack or gameplay-focus layout with motion-aware crop, reaction punch-ins, word-highlight captions, hook text, −14 LUFS audio, 1080×1920 H.264/AAC | `src/editing/` |
| Schedule | US-Eastern slots per platform, daily limits, min gap, category rotation, duplicate (sha256) protection | `src/publishing/scheduler.py` |
| Publish | YouTube Data API v3 (resumable, `publishAt`), Instagram API (resumable Reels upload → status poll → publish) with retries | `src/publishing/` |
| Analytics | YouTube stats + Analytics API retention, IG insights; per category/duration/hour/streamer report; learned category multipliers feed scoring | `src/analytics/collect.py` |

## Profiles (`profiles/`)

The core engine has no game-specific logic. A profile defines: game label, audience & language style,
Whisper vocabulary, hype/laughter lexicon, signal weights, clip length & context windows, moment
categories, scoring weights + editorial brief, crop/punch-in style, metadata voice, hashtags, glossary.
`_base.yaml` holds defaults; each profile overrides only what differs. Shipped: `gta6`, `valorant`,
`cs2`, `minecraft`, `irl`. New category = copy a profile and edit it. `python main.py profiles` lists them.

## Install

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
```
Needs `ffmpeg` on PATH (built with libass; NVENC optional) and, for AI review/metadata, the `claude` CLI
logged in. Without it, use `--no-llm` (signal-only scoring, template metadata — noticeably weaker).

## Daily use

```powershell
# 1) process a stream you have permission for
.venv\Scripts\python main.py --profile gta6 --url "https://www.twitch.tv/videos/123" --streamer NAME `
    --rights permission_granted --commercial --auth-ref "Email from NAME, 2026-11-19" --authorized-on 2026-11-19

# outputs: candidate_clips/<src>.md (ranked list incl. rejects), final_clips/*.mp4, metadata/*.json

# 2) optional originality: record narration from metadata/<clip>.json -> commentary.script, then
.venv\Scripts\python main.py narrate <clip_id> my_voice.wav --at 0

# 3) publishing runs automatically once the scheduler is installed (one time):
powershell -ExecutionPolicy Bypass -File scripts\install_scheduler.ps1

.venv\Scripts\python main.py status            # sources, rights, schedule, errors
.venv\Scripts\python main.py analytics report   # performance + learned weights
```
Rights can also be set later: `main.py rights set <source_id> --status permission_granted --commercial --ref "..."`,
then `main.py schedule`. Restrictions: `--restrictions no-instagram` / `no-youtube` / `no-publish`.

## One-time platform setup

### YouTube
1. Google Cloud Console → new project → enable **YouTube Data API v3** and **YouTube Analytics API**.
2. OAuth consent screen → External → add yourself as a test user → then **Publish app** ("In production").
   In "Testing" mode Google refresh tokens expire after 7 days and automation silently stops.
3. Credentials → OAuth client ID → **Desktop app** → download JSON to `secrets/client_secret.json`.
4. `python main.py auth youtube` (browser opens once) → `python main.py test-upload youtube --clip <clip_id>`
   uploads a **private** test video.

**Important limits:**
- Videos uploaded through an unverified API project are **locked to private**. You must pass the
  YouTube API Services audit (free; form in Google's YouTube API docs) before scheduled public
  uploads work.
- The default quota is 10,000 units a day. An upload costs about 1,600, so that is roughly 6 uploads
  a day (`quota_uploads_per_day`).

### Instagram
1. Convert the IG account to **Professional (Creator or Business)**.
2. developers.facebook.com → create app (Business) → add **Instagram** product → *API setup with Instagram login*.
3. Add your IG account as an Instagram Tester (App roles) and accept it in Instagram → Settings → Website permissions/Apps.
4. Generate a token with `instagram_business_basic`, `instagram_business_content_publish`,
   `instagram_business_manage_insights`. Put it in `.env` as `IG_ACCESS_TOKEN` (and `IG_APP_SECRET`).
5. `python main.py auth instagram` (stores a 60-day token; it is auto-refreshed) →
   `python main.py test-upload instagram --clip <clip_id>`. This uploads and processes a Reel container
   but does **not** publish it. Instagram has no private Reels, so the first real publish is public.
- The Instagram API allows 100 published posts per 24h. The engine's defaults stay far below that.

## Scheduling defaults (US Eastern)
- YouTube 12:30 / 17:00 / 20:00.
- Instagram 11:00 / 15:00 / 19:30.
- At most 2 per platform per day, at least 3h apart.

12:30 ET is 17:30 in the UK, and 20:00 ET is 11:00 next morning on the Australian east coast, so these
slots cover US lunch and evening, UK evening, and the AU morning. They are sensible starting points, not
proven optimal. Check the analytics report's "upload hour" table after a few weeks and adjust
`config/settings.yaml`. Raise `daily_limit` only when clips are performing well.

## Copyright & monetization (read this)
- Publishing is hard-blocked unless the source is `owned` or `permission_granted`, **and** has explicit
  commercial-use permission (configurable). Every clip credits the streamer.
- Cropping, captions and zooms do **not** make someone else's stream yours. Get written permission
  (many streamers run official clipping programs). Store where the permission lives in `--auth-ref`.
- YouTube's reused-content policy targets channels that only re-upload others' content. The best lever
  here is your own narration or commentary (`commentary.script` in each metadata file plus `main.py narrate`).
- Permission to use clips and YouTube Partner Program or Instagram monetization eligibility are separate
  decisions. Nothing here guarantees approval.

## Folder layout
`config/` global settings · `profiles/` content profiles · `src/` engine · `downloads/ transcripts/
candidate_clips/ final_clips/ subtitles/ metadata/ analytics/ logs/ database/` data (git-ignored) ·
`secrets/` tokens (git-ignored) · `scripts/` scheduler installer.

## Known limitations
- The AI judges moments from transcript and audio/visual signals. It does not watch the video, so
  purely visual moments with no audio reaction score lower.
- Facecam detection uses a face detector over sampled frames. Unusual overlays can be missed, and then
  the clip falls back to the gameplay-focus layout.
- No sound effects or music are added. You would need properly licensed assets for that.
