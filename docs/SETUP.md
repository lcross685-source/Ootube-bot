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
ootube run --limit 1          # draft one edit package
```

Then open `out/<topic>/project.xml` in Premiere and read
`out/<topic>/EDIT_NOTES.md`. See [EDITING.md](EDITING.md) for the full
workflow.

Nothing has touched YouTube at this point — `run` never uploads. When you have
cut and exported something you are happy with:

```bash
ootube publish <topic-key> --video ~/exports/final.mp4
```

It uploads *private* with a scheduled publish time, so you can still review it
in YouTube Studio before it goes public.

## 6. Automate

Automation is **optional here**, and less useful than it was for a fully
automated channel: if you are editing in Premiere on your own machine, running
the bot there puts the footage straight where you need it.

CI is for keeping drafts flowing while you are away from your edit machine.
[`.github/workflows/draft.yml`](../.github/workflows/draft.yml) drafts packages
daily and uploads them as build artifacts to download. It never publishes.

Add secrets under **Settings → Secrets and variables → Actions**. Note that
`YOUTUBE_*` secrets are not needed for drafting — only for `ootube publish`,
which you run locally.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `ootube plan` selects nothing | Normal on a quiet day. The rejection breakdown says which rule fired. |
| `run` drafts nothing, no rejections | Backlog is full. Edit or `ootube discard` what is waiting. |
| XML will not import | See [EDITING.md](EDITING.md#if-the-xml-will-not-import). |
| Everything rejected `no_event_date` | Sources are not returning dates. Check `ootube trends` — undated feeds cannot pass the news gate. |
| `quotaExceeded` | Check `ootube status`. Reduce `videos_per_day` or request more quota. |
| Token expires weekly | OAuth consent screen is in *Testing*. Publish it, or add yourself as a test user. |
| Renders fail | `ffmpeg` missing — `ootube doctor` confirms. |
| Scripts rejected repeatedly | Read the errors. Usually stale sources or missing original analysis; both are working as intended. |
