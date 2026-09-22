# Compliance

The risk to an automated channel is not a copyright strike — it is
demonetisation or termination under YouTube's **inauthentic content** policy.

**This workflow is in good shape on that front, and the reason is the human
edit.** The policy targets content that can be replicated at scale with little
human input. A pipeline that drafts a timeline and stops, leaving a person to
cut, restructure and approve every video, is not that. The editing step is not
just a quality gate — it is the single thing that most clearly distinguishes
this from what gets channels terminated.

That protection only holds if the edit is real. Importing the project and
exporting it unchanged puts you straight back in the target category.

## What the policy targets

In July 2025 YouTube renamed its "repetitious content" policy to
**inauthentic content** and clarified what it covers. The target is
mass-produced, templated, low-effort content that can be replicated at scale
with little human input — the category commonly called AI slop. Reported
enforcement includes channel terminations in batches, and a three-strike
progression: warning, suspension, then removal from the Partner Program.

Three flagged categories matter here:

1. **Generic, repetitive or template-based content.** The same structural
   shell with the nouns swapped.
2. **Off-putting or distressing content.**
3. **AI personas discussing sensitive topics** such as health and finance.

## What is explicitly still allowed

AI-assisted and AI-generated content remains eligible for the Partner Program,
provided it offers original value, is not mass-produced or repetitive, and the
altered-or-synthetic-content disclosure is set where relevant.

The distinction is not "was AI involved" — it is **whether the video is worth
watching and adds something**.

## How this repo is built against that

| Requirement | Mechanism |
|---|---|
| Original value | Every script must carry a specific, falsifiable original claim. The [verifier](../src/ootube/script/verify.py) rejects the video if it is missing or generic. |
| Not templated | Filler phrases ("in today's video", "let's dive in") are hard failures. The prompt forbids the structural tics of SEO copy and varies structure per video. |
| Sourced and current | Every factual claim needs a source URL and a date inside the freshness window. Undated or stale claims fail the build. |
| Not repetitive | Dedupe by topic fingerprint plus a near-duplicate check against 30 days of published titles. |
| Disclosure | Uploads set `status.containsSyntheticMedia` — the API equivalent of the Studio toggle — and the description carries a plain-language disclosure line. |
| Sensitive topics | Niches marked `sensitive: true` (finance, health) carry a not-advice disclaimer. Since you edit every video, you are the reviewer. |
| Human input | Structural. The pipeline cannot publish; `ootube publish` only accepts a file you exported. |

The verifier **fails closed**: a script that cannot be verified is dropped
rather than published. A skipped video costs one slot; a wrong one costs
channel trust, and potentially monetisation.

## What the code cannot do for you

This is the honest part.

**The disclosure line must be true.** The default says research and editorial
judgement are human-reviewed. With this workflow that is accurate — provided
you actually review the claims in `EDIT_NOTES.md` rather than just trimming
the timeline. Every claim is listed with its dated source precisely so that
checking them is quick.

**An unchanged export is still mass-produced content.** The protection comes
from the edit, not from the existence of an editing step. If a draft is not
worth cutting, discard it; publishing it unchanged is worse than publishing
nothing.

**Volume is still a risk multiplier.** `videos_per_day` defaults to 2, and the
backlog throttle stops drafting when unedited packages pile up. Both are
deliberate. If you find yourself exporting drafts with minimal changes to keep
up, the cadence is too high.

**You are responsible for the facts.** The verifier checks that claims are
sourced and current; it cannot check that they are *true*. The model can be
confidently wrong. Your name is on the video.

## Recommended posture

**First 30 videos** — cut each draft properly and verify every claim against
its source. You are calibrating whether the niche, prompts and sources produce
material worth your editing time. If they do not, change the niche rather than
lowering the bar.

**Ongoing** — verify claims in regulated niches every time, not just at the
start. Read the rejection log in `ootube plan` occasionally; a sudden spike in
one rule usually means a source broke. Discard drafts freely — `ootube
discard` exists so that "this one is not worth it" is a cheap decision.

**Always** — respond to comments correcting factual errors, and pin
corrections. Keep the sourced-claims archive (`out/<topic>/script.json`);
it is written on every run and is your record if authenticity is ever
questioned.

## Other rules worth knowing

- **Copyright.** B-roll comes from Pexels and Pixabay, both of which permit
  commercial use without attribution. If you add sources, verify the licence —
  a content-ID claim redirects revenue to the claimant.
- **Made for kids.** Misdeclaring has legal consequences under COPPA, not just
  platform ones. Default is `false`; change it only if it is genuinely true.
- **Impersonation.** Do not synthesise a real person's voice or likeness.
- **Financial and medical advice.** Information, not advice. The disclaimer is
  in the description footer; keep it there.

## If you get a strike

1. Stop publishing — set `videos_per_day: 0` or disable the workflow.
2. Read the specific policy cited; do not guess.
3. Audit recent videos against it honestly.
4. Fix the root cause — usually thin originality or excessive volume — before
   appealing or resuming.

Do not simply resume on a new channel. That pattern is itself enforced against.
