# Setup

Roughly 30 minutes, most of it waiting on Google's OAuth consent screen.

## 1. Install

```bash
git clone https://github.com/lcross685-source/Ootube-bot
cd Ootube-bot
pip install -e ".[publish,script,media]" edge-tts
sudo apt-get install ffmpeg        # brew install ffmpeg on macOS
ootube doctor                      # lists exactly what is still missing
```

## 2. Keys

| Variable | Required | For | Where |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | yes | Script writing | console.anthropic.com |
| `YOUTUBE_CLIENT_ID` | yes | Upload | Google Cloud Console |
| `YOUTUBE_CLIENT_SECRET` | yes | Upload | Google Cloud Console |
| `YOUTUBE_REFRESH_TOKEN` | yes | Unattended upload | `ootube auth` |
| `YOUTUBE_API_KEY` | no | Trending-chart source | Google Cloud Console |
| `PEXELS_API_KEY` | no | B-roll footage | pexels.com/api |
| `ELEVENLABS_API_KEY` | no | Premium voice | elevenlabs.io |

Without the optional keys the bot still runs: no b-roll means generated
gradient cards, and the default voice is the free `edge-tts`.

## 3. YouTube OAuth

1. In [Google Cloud Console](https://console.cloud.google.com), create a
   project and enable **YouTube Data API v3**.
2. Configure the OAuth consent screen. While it is in *Testing*, add your own
   Google account under **Test users** — otherwise the token expires in seven
   days and the bot silently stops uploading.
3. Create an **OAuth client ID** of type *Desktop app*. Download it as
   `client_secret.json`.
4. Run the one-time consent flow:

```bash
ootube auth
```

This opens a browser, writes `token.json`, and prints the three values to
store as CI secrets. Both files are gitignored — never commit them.

> **Quota note.** A new project gets a default daily allocation. Google has
> changed how uploads are billed (they previously cost 1600 units from the
> shared pool and are now billed per call against a separate cap), so check
> the current [quota documentation](https://developers.google.com/youtube/v3/determine_quota_cost)
> and set the values under `quota:` in `config/channel.yaml` to match. No code
> change is needed.

## 4. Aim the channel

Edit [`config/niches.yaml`](../config/niches.yaml). Delete what you will not
cover — a channel spanning five unrelated verticals confuses both the
recommendation system and the audience. One or two adjacent niches is the
right starting shape.

For each niche set `rpm_usd` (see [REVENUE.md](REVENUE.md)), `keywords` used
for classification, and `subreddits` / `rss_feeds` as sources.

Then set cadence in [`config/channel.yaml`](../config/channel.yaml):

```yaml
schedule:
  timezone: America/New_York
  videos_per_day: 2
  publish_hours_local: [9, 17]
```

## 5. First run

Work up in stages rather than going straight to `run`:

```bash
ootube trends                 # are the sources returning anything?
ootube plan                   # what would it pick, and why reject the rest?
ootube run --dry-run --limit 1   # build one video, no upload
```

`--dry-run` writes to `out/<topic>/`. **Watch the video before you let it
upload anything.** Check that the narration is accurate, the pacing works, and
nothing reads as templated.

When you are satisfied:

```bash
ootube run --limit 1
```

It uploads as *private* with a scheduled `publishAt`, so you can still review
it in YouTube Studio before it goes public.

## 6. Automate

Add the secrets under **Settings → Secrets and variables → Actions**, then
enable [`.github/workflows/publish.yml`](../.github/workflows/publish.yml).
It runs twice daily and persists state on a `bot-state` branch.

Keep the approval gate on until you trust the output:

```yaml
channel:
  require_human_approval: true
```

Then `ootube approve --list` shows what is waiting, and
`ootube approve --fingerprint <id>` releases it.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `ootube plan` selects nothing | Normal on a quiet day. The rejection breakdown says which rule fired. |
| Everything rejected `no_event_date` | Sources are not returning dates. Check `ootube trends` — undated feeds cannot pass the news gate. |
| `quotaExceeded` | Check `ootube status`. Reduce `videos_per_day` or request more quota. |
| Token expires weekly | OAuth consent screen is in *Testing*. Publish it, or add yourself as a test user. |
| Renders fail | `ffmpeg` missing — `ootube doctor` confirms. |
| Scripts rejected repeatedly | Read the errors. Usually stale sources or missing original analysis; both are working as intended. |
