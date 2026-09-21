# Compliance

Read this before turning on unattended publishing. The risk to an automated
channel is not a copyright strike — it is demonetisation or termination under
YouTube's **inauthentic content** policy.

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
| Sensitive topics | Niches marked `sensitive: true` (finance, health) route to human approval and carry a not-advice disclaimer, rather than an AI persona giving advice. |

The verifier **fails closed**: a script that cannot be verified is dropped
rather than published. A skipped video costs one slot; a wrong one costs
channel trust, and potentially monetisation.

## What the code cannot do for you

This is the honest part.

**Volume is a risk multiplier.** Ten thoughtful videos a week is a different
risk profile from a hundred. `videos_per_day` defaults to 2 deliberately.
Raising it materially raises your exposure.

**"Reviewed by a human editor" must be true.** The default disclosure line
says research and editorial judgement are human-reviewed. If you publish fully
hands-off, that line is false — either review the output or change the line.

**A fully unattended channel is the exact profile under scrutiny.** The
realistic posture is not "never look at it" but "look at it briefly and
regularly". Watch the first thirty videos end to end. Afterwards, spot-check.

## Recommended posture

**First 30 videos** — `require_human_approval: true`, watch each one fully,
delete anything you would not put your name on. You are calibrating whether
the niche, prompts and sources produce something worth publishing.

**Ongoing** — keep approval on for `sensitive` niches permanently. Spot-check
a couple per week. Read the rejection log in `ootube plan` occasionally; a
sudden spike in one rule usually means a source broke.

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
