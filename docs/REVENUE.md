# Revenue

## The honest version

This pipeline optimises the levers software can actually move: topic
selection, freshness, packaging, cadence, consistency. It cannot manufacture
an audience, and it does not make monetisation automatic.

To earn ad revenue at all, a channel must be accepted into the **YouTube
Partner Program**, which has subscriber and watch-time thresholds. Nothing
here shortcuts that. Expect the first stretch to earn nothing while the
channel builds the history that qualifies it.

What automation genuinely buys you here is *time per video*. Research,
scripting, narration, footage gathering and rough assembly are the slow,
repetitive parts; the pipeline does those and hands you an edit. What is left
is the part that actually differentiates a video, and it is the part worth
spending your hours on.

The realistic constraint becomes your editing throughput, not idea supply.
That is why drafting throttles itself against your backlog: a queue of stale
packages is not progress.

## Why niche choice dominates

RPM (revenue per 1000 monetised playbacks) varies by roughly an order of
magnitude across verticals, because advertisers bid far more to reach someone
researching business software than someone watching entertainment.

| Vertical | Planning RPM | Why |
|---|---|---|
| Personal finance | $15–30 | Brokerages, banks, tax software bid hard |
| Business software | $12–25 | B2B customer lifetime value is enormous |
| AI tools | $8–18 | Heavy SaaS advertiser competition |
| Software development | $8–15 | Developer tools, cloud platforms |
| Consumer tech | $5–12 | Retail advertising, seasonal |
| Entertainment / gaming | $1–4 | Broad, low commercial intent |

The practical consequence: **8,000 views in business software can out-earn
100,000 views in gaming.** This is why `scoring.py` ranks by expected revenue
rather than projected views, and why the per-niche `weight` exists — to bias
selection toward verticals worth showing up in.

Figures above are planning estimates for a US-majority audience. Replace them
with your own analytics once you have three months of history; real RPM
depends on your audience geography, season, and video length.

## Three revenue streams, not one

Ad revenue is the slowest to switch on, so the description template supports
all three from video one:

1. **AdSense.** Requires Partner Program acceptance. Slowest, most passive.
2. **Affiliate links.** Works from day one with no threshold. For
   software and hardware niches this often exceeds ad revenue early on. Set
   `affiliate_block` per niche in `niches.yaml`. Disclose the relationship —
   it is legally required in most jurisdictions and it is in the template.
3. **Sponsorships.** Needs a real audience, but pays a flat rate independent
   of RPM. A consistent publishing record is what makes a channel pitchable.

## What the levers actually do

**Freshness** compounds harder than it looks. Being early on a story means
competing with fewer videos for the same demand spike, so the same content
earns more. It is also why the gate is strict: a late video on a saturated
topic loses twice over.

**Click-through rate multiplies everything downstream.** A thumbnail taking
CTR from 4% to 6% is a 50% traffic increase with no change to the video. This
is why the writer produces dedicated thumbnail text rather than truncating a
title — see [`media/thumbnail.py`](../src/ootube/media/thumbnail.py).

**Watch time decides whether you get distribution at all.** Chapters help
viewers reach what they came for instead of bouncing. The hook is written to
state the specific news in the first ten seconds, with no greeting.

**Consistency is the compounding one.** Two videos a week for a year beats
fourteen in a burst and then silence. The scheduling queue exists specifically
to protect this against infrastructure failures.

## Setting expectations

A channel like this, run well, in a high-RPM niche, publishing consistently,
plausibly reaches Partner Program thresholds in something like six to twelve
months — and plenty never do. Anyone quoting you faster numbers is selling
something.

The highest-leverage decisions are made before any code runs: which niche,
which angle, and whether the output is genuinely worth a viewer's time. The
pipeline executes; it does not substitute for that judgement.
