# ootube

An autonomous YouTube channel operator: it finds what is trending right now,
rejects anything stale, writes and renders a video, and schedules it to
publish on a fixed cadence. It optimises for **revenue**, not views, and it is
built to run unattended on a cron.

```
trends ──▶ freshness gate ──▶ revenue scoring ──▶ script + verify ──▶ render ──▶ schedule
 5 sources   4 blocking rules    RPM-weighted       originality        ffmpeg     publishAt
```

## The problem this is built around

Most auto-upload channels fail in one of three ways. Each has a specific
countermeasure here.

**They publish stale content.** "Trending" and "current" are different
questions. A publisher can run a fresh article about a two-year-old phone,
giving a recent signal with an ancient subject. The
[freshness gate](src/ootube/freshness.py) applies four independent rules and a
topic must pass all of them:

| Rule | Catches |
|---|---|
| Signal age | Demand we noticed days ago and are only acting on now |
| Event age | A story whose underlying event is older than the window |
| Supersession | A subject that has since been replaced |
| Anchor text | Titles pointing at an old year, or framed as history |

Supersession is the one that does the real work. The bot learns product
generations from the signals it sees — spot one "Galaxy S25" headline and
every S23 topic becomes rejectable, *even a brand-new article about the S23*,
where both timestamps look perfectly current. That knowledge persists across
runs and never moves backwards.

**They get demonetised.** YouTube's inauthentic-content policy (the July 2025
rename of "repetitious content") targets mass-produced, templated, low-effort
output, and enforcement has included channel termination. A generator that
emits the same shell with the nouns swapped is exactly that pattern. So every
script must carry a specific, falsifiable original claim, every factual
assertion needs a dated source, and the
[verifier](src/ootube/script/verify.py) fails the video rather than publishing
one that does not. Uploads set `status.containsSyntheticMedia`, the API
equivalent of the "altered or synthetic content" toggle.

**They optimise for views.** Views are not revenue. RPM varies roughly 10x
across verticals, so a B2B software video at 8k views can out-earn a gaming
video at 100k. [Scoring](src/ootube/scoring.py) ranks by expected dollars:

```
expected_revenue = projected_views × monetized_rate × (RPM / 1000)
```

## Quick start

```bash
pip install -e ".[publish,script,media]" edge-tts
sudo apt-get install ffmpeg

ootube doctor                 # what is configured, what is missing
ootube trends                 # what is trending right now
ootube plan                   # what it would make, and why it rejected the rest
ootube run --dry-run          # build the videos without uploading
ootube run                    # build, upload, schedule
```

`ootube plan` is the one to read first. It shows the selected topics with
their projected revenue *and* a breakdown of every rejection by rule, which
answers the question that otherwise needs a debugging session: why didn't it
publish anything today?

## Commands

| Command | Does |
|---|---|
| `ootube trends` | Fetch and display current signals, plus learned product generations |
| `ootube plan` | Dry planning: selections, projected revenue, rejection reasons |
| `ootube run` | Full pipeline. `--dry-run` to skip upload, `--limit N` to cap |
| `ootube status` | Scheduled queue, quota spend, recent runs |
| `ootube approve` | Review videos held for human approval |
| `ootube doctor` | Check config, credentials and binaries without spending quota |
| `ootube auth` | One-time OAuth; prints the refresh token for CI |

## How it stays low-maintenance

- **Scheduled, not immediate.** Videos upload private with a future
  `publishAt`. The public cadence is independent of when the bot runs.
- **A queue buffer.** It keeps several days of videos scheduled ahead. If a run
  fails or a key expires, the channel keeps publishing while you fix it.
- **Quota-aware.** Spend is tracked locally and checked before each call, with
  a reserve so a part-finished video can still complete. Costs are config, not
  constants, because Google has changed them.
- **Degrades instead of failing.** A dead trend source returns nothing rather
  than raising; a missing b-roll clip becomes a generated card; one bad topic
  fails alone and the run continues.
- **Fails closed on quality.** A script that cannot be verified is dropped. A
  skipped video costs one slot; a wrong one costs channel trust.
- **Self-reporting.** Failed runs open (and reuse) a single GitHub issue.

## Configuration

Everything tunable is in [`config/`](config/) — no code changes to re-aim the
channel:

- [`niches.yaml`](config/niches.yaml) — verticals, RPM estimates, keywords,
  source feeds. Niche choice dominates every other revenue lever.
- [`channel.yaml`](config/channel.yaml) — freshness thresholds, scoring
  weights, publishing cadence, quota budget, voice and render settings.

Secrets come from the environment and are never stored in config.

## Automation

[`.github/workflows/publish.yml`](.github/workflows/publish.yml) runs twice
daily. State — what has been published, quota spent, generations learned —
persists on an orphan `bot-state` branch between runs.

## Documentation

- [Setup](docs/SETUP.md) — API keys, OAuth, first run
- [Revenue](docs/REVENUE.md) — how monetisation actually works, niche economics
- [Compliance](docs/COMPLIANCE.md) — staying inside YouTube's AI content policy

## Tests

```bash
pytest -q     # 134 tests, fully offline
```

The suite runs the real chain end to end — including actual ffmpeg renders —
with only network boundaries stubbed.

## Honest limitations

- **Topic clustering is lexical.** It merges rephrasings but not rewordings:
  "Fed cuts rates by 50 basis points" and "Fed delivers 50bp cut" are one story
  to a reader and two here. The cost is a duplicate topic, mostly absorbed by
  the near-duplicate check. Sentence embeddings would close it.
- **Competition is estimated, not measured.** Measuring it properly means
  `search.list` at 100 quota units per call against a 100-call daily cap, which
  would spend the entire search budget on ranking.
- **RPM figures are planning estimates** until the channel has its own
  analytics history to calibrate against.
- **This does not make monetisation automatic.** See
  [docs/REVENUE.md](docs/REVENUE.md) — the Partner Program has thresholds, and
  a fully hands-off channel is the exact profile YouTube scrutinises.
