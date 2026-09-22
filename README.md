# ootube

Press a button, get the first 80% of a video edit. It finds what is trending
right now, rejects anything stale, writes and fact-checks a script, narrates
it, pulls footage, and hands you a **Premiere project with the timeline already
built**. You cut it down and publish.

```
trends ──▶ freshness gate ──▶ revenue scoring ──▶ script + verify ──▶ EDIT PACKAGE
 5 sources   4 blocking rules    RPM-weighted       originality       ↓
                                                              you cut it in Premiere
                                                                      ↓
                                                        ootube publish ──▶ scheduled
```

**It never publishes on its own.** `ootube run` stops at a project file.
Uploading is a separate command that takes *your* export. That split is the
design: the tedious work is automated, the judgement stays yours.

## What you get per video

```
out/<topic>/
  project.xml      <- import into Premiere (File > Import). Resolve reads it too
  project.edl      <- fallback if the XML misbehaves
  captions.srt     <- drag onto the timeline
  EDIT_NOTES.md    <- shot list, sources, what to fix first
  metadata.json    <- title/tags/description, editable, used at publish
  thumbnail.jpg
  audio/vo_*.wav   <- narration, one file per section
  broll/*.mp4
```

Open `project.xml` and the sequence is already laid out:

- **A1** — narration, **one clip per section**, so you can move, trim or drop
  blocks independently rather than fighting one immovable audio blob.
- **V1** — b-roll cut to the narration. A section with no usable clip is left
  as a **visible gap**, not padded, because an empty span is the clearest
  possible instruction.
- **A2** — empty, reserved for music.
- **Markers** — section starts, a `cut point` at every sentence boundary,
  `CITE` where a claim needs an on-screen source, `NO B-ROLL` where footage is
  missing, `B-ROLL REPEATS` where a short clip is looping.

`EDIT_NOTES.md` opens with a "do these first" list, then the shot list with
timecodes, then every factual claim with its dated source so you can verify
anything before your name goes on it.

> The format matters: Premiere imports `.xml` (the FCP7 schema) and does **not**
> import `.fcpxml`. This writes the one both Premiere and Resolve read.

## Quick start

```bash
pip install -e ".[publish,script,media]" edge-tts
sudo apt-get install ffmpeg        # brew install ffmpeg on macOS

ootube doctor          # what is configured, what is missing
ootube plan            # what it would draft, and why it rejected the rest
ootube run --limit 1   # draft one edit package
ootube drafts          # list packages waiting to be cut
```

Then open `out/<topic>/project.xml` in Premiere, cut it, export, and:

```bash
ootube publish <topic-key> --video ~/exports/final.mp4
```

It uploads private with a scheduled `publishAt`, so the channel keeps a steady
public cadence regardless of when you finished editing.

## Why the rest of the pipeline exists

**Stale topics.** "Trending" and "current" are different questions — a
publisher can run a fresh article about a two-year-old product, giving a recent
signal with a dead subject. The [freshness gate](src/ootube/freshness.py)
applies four independent rules and a topic must pass all of them:

| Rule | Catches |
|---|---|
| Signal age | Demand we noticed days ago and are only acting on now |
| Event age | A story whose underlying event is older than the window |
| Supersession | A subject that has since been replaced |
| Anchor text | Titles pointing at an old year, or framed as history |

Supersession does the real work: the bot learns product generations from what
it sees, so one "Galaxy S25" headline makes every S23 topic rejectable — *even
a brand-new article about the S23*, where both timestamps look current. That
knowledge persists and never moves backwards.

**Views are not revenue.** RPM varies roughly 10x across verticals, so a B2B
software video at 8k views can out-earn a gaming video at 100k.
[Scoring](src/ootube/scoring.py) ranks by expected dollars:

```
expected_revenue = projected_views × monetized_rate × (RPM / 1000)
```

**Thin scripts.** Every script must carry a specific, falsifiable original
claim and date every factual assertion to a source. The
[verifier](src/ootube/script/verify.py) fails the draft rather than handing you
something not worth cutting — it also rejects templated filler ("in today's
video", "let's dive in") outright.

## Commands

| Command | Does |
|---|---|
| `ootube trends` | Current signals, plus learned product generations |
| `ootube plan` | What it would draft, projected revenue, rejection reasons |
| `ootube run` | Draft edit packages. Never uploads |
| `ootube drafts` | Packages waiting to be cut, flagged if going stale |
| `ootube publish <key> --video <file>` | Upload your edited export |
| `ootube discard <key>` | Drop a draft you will not use |
| `ootube status` | Drafts, scheduled queue, quota, recent runs |
| `ootube doctor` | Check config and credentials without spending quota |
| `ootube auth` | One-time OAuth; prints the refresh token for CI |

## Operational notes

- **Drafting is throttled by your editing.** A run counts unedited drafts and
  stops when the backlog is full. Drafting faster than you cut just produces
  stale packages — a topic current on Monday is not on Friday, and `ootube
  drafts` flags anything over 72 hours old.
- **A topic is never drafted twice.** Dedupe covers published *and* drafted
  topics, including ones you discarded.
- **Chapters come from your final cut**, not the rough assembly, so they match
  whatever you actually exported.
- **Degrades instead of failing.** A dead trend source returns nothing rather
  than raising; missing footage becomes a marked gap; one bad topic fails alone.
- Costs and quota are config, not constants, because Google has changed them.

## Documentation

- [Editing](docs/EDITING.md) — the Premiere workflow in detail
- [Setup](docs/SETUP.md) — API keys, OAuth, first run
- [Revenue](docs/REVENUE.md) — how monetisation actually works
- [Compliance](docs/COMPLIANCE.md) — YouTube's AI content policy

## Tests

```bash
pytest -q     # 175 tests, fully offline
```

## Honest limitations

- **The XML has not been opened in Premiere by me.** It is validated
  structurally — integer frames, file dedup, audio sourcetracks, encoded path
  URLs — and written to the schema Premiere documents, but I have no Premiere
  licence to confirm the import. The EDL exists as a fallback for exactly this
  reason. Please report what happens.
- **Caption and cut-point timings are interpolated** within each measured
  section, since most TTS providers do not return word boundaries. Close, not
  frame-accurate.
- **Topic clustering is lexical.** It merges rephrasings but not rewordings:
  "Fed cuts rates by 50 basis points" and "Fed delivers 50bp cut" are one story
  to a reader and two here.
- **Competition is estimated, not measured** — measuring it means `search.list`
  at 100 quota units per call against a 100-call daily cap.
- **RPM figures are planning estimates** until you have your own analytics.
