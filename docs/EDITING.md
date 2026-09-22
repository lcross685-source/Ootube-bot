# The editing workflow

## The loop

```bash
ootube run --limit 2                          # draft packages
# ... open, cut, export in Premiere ...
ootube publish <topic-key> --video final.mp4  # upload yours
```

`ootube run` never uploads. Nothing reaches YouTube until you run `publish`
with a file you exported.

## Importing into Premiere

**File → Import**, select `out/<topic>/project.xml`. Premiere creates a bin
and a sequence with everything laid out.

Premiere imports `.xml` — the FCP7 schema — and does **not** import
`.fcpxml`. This writes the former, which Resolve also reads. If the import
misbehaves, `project.edl` reconstructs the cut order in any NLE ever made,
though EDLs carry no markers or multiple tracks.

**Do not move the package folder before importing.** The project references
`audio/` and `broll/` by absolute path. Move it afterwards and Premiere will
ask you to relink; move it before and it has nothing to find. If you need it
elsewhere, move it first and re-run `ootube run`, or relink once in Premiere.

## What the timeline gives you

| Track | Contents |
|---|---|
| **V1** | B-roll cut to section boundaries. Gaps are deliberate. |
| **A1** | Narration, one clip per section |
| **A2** | Empty, for music |

Narration is **one clip per section** rather than one long file specifically
so you can reorder, trim or delete a block without ripple-editing everything
downstream. Dropping a weak section is a single delete.

### Markers

| Marker | Means |
|---|---|
| *Section name* | Start of a narration block |
| `cut point` | Sentence boundary — lift a line here without cutting mid-sentence |
| `CITE` | A factual claim that should carry an on-screen source |
| `NO B-ROLL` | V1 is empty here; drop footage in |
| `B-ROLL REPEATS` | A short clip is looping to fill; consider more coverage |

Open the Markers panel (**Window → Markers**) and work down the list.

## A sensible first pass

1. **Read `EDIT_NOTES.md` first.** It opens with what is weak about this
   specific draft — missing footage, verification warnings — then the shot
   list and every claim with its source.
2. **Cut length hard.** Scripts are written to a target duration, which means
   they are long. The `cut point` markers are safe places to lift a sentence.
   Most drafts improve by 30–40%.
3. **Fill the `NO B-ROLL` gaps**, or cut those sections if you have nothing.
4. **Fix the open.** The hook states the news, but the first five seconds
   decide retention and are worth re-recording or re-cutting by hand.
5. **Add citations at `CITE` markers.** Lower-thirds with the source name.
6. **Close the gaps.** There is a 0.35s pad between sections. Tighten where
   the pacing should be quick.
7. **Music on A2**, ducked under narration.
8. **Captions** — drag `captions.srt` onto the timeline. Timings are
   interpolated, so scan them; text is exact.

## Publishing

Export however you like, then:

```bash
ootube publish fed-cuts-rates --video ~/exports/fed-final.mp4
```

This uploads **private** with a scheduled `publishAt` (the next free slot from
your configured cadence), sets the synthetic-media disclosure, attaches the
thumbnail, and builds the description — chapters recomputed from your final
runtime, so they match what you actually cut, plus the dated source list.

Pick a time explicitly with `--at 2026-10-02T09:00:00-04:00`.

### Changing the metadata

`metadata.json` in the package is read at publish time, so edit it freely
first — title, tags, summary. A hand-written title always beats a generated
one; the generated one is a starting point.

## Keeping the backlog sane

```bash
ootube drafts     # what is waiting, and how old
ootube discard <topic-key>
```

A run counts unedited drafts and stops when the backlog is full, because
drafting faster than you edit just accumulates stale packages. Anything over
72 hours old is flagged — news topics decay fast, and a three-day-old take is
usually no longer worth cutting. Discard freely; a discarded topic is never
offered again.

## Tuning the drafts

In `config/channel.yaml`:

```yaml
media:
  target_duration_s: 480     # script length target (before your cuts)
  section_gap_s: 0.35        # pad between narration blocks
  sentence_markers: true     # per-sentence cut points — off if too noisy
  render_preview: false      # also render a watchable rough cut (slow)
  fps: 30
  width: 1920
  height: 1080
```

Set `render_preview: true` to get a `preview.mp4` alongside the project. It is
worth it while you are calibrating whether a niche produces usable drafts —
you can judge pacing without opening an NLE — and worth turning off afterwards,
since it is the slowest step and is not the deliverable.

Match `fps` and dimensions to your Premiere sequence preset to avoid Premiere
offering to change the sequence settings on import.

## If the XML will not import

1. Try `project.edl` instead — no markers, but the cut order survives.
2. Check the package folder has not moved since drafting.
3. Import into DaVinci Resolve (free) and re-export as FCP7 XML.
4. Worst case, everything is loose in the folder: `audio/vo_*.wav` are numbered
   in order and `EDIT_NOTES.md` has the full shot list with timecodes, so the
   sequence can be rebuilt by hand in a few minutes.
