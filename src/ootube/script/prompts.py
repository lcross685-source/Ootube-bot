"""Prompts for the script writer.

These prompts carry most of the channel's compliance risk. YouTube's
inauthentic-content policy (the rename of "repetitious content") demonetises
mass-produced, templated, low-effort output, and enforcement has included
outright channel termination. A generator that emits the same five-section
shell with the nouns swapped is precisely the pattern that gets caught.

So the prompt does three things beyond "write a script":

1. Demands a specific, falsifiable original claim - the "so what" that is not
   in any of the source articles.
2. Requires every factual statement to carry a source URL and a date, which
   the verifier then enforces mechanically.
3. Forbids the structural tics of templated content and varies the structure
   per video.
"""

from __future__ import annotations

import json
from datetime import datetime

from ..config import Config, NicheConfig
from ..models import Topic

SYSTEM_PROMPT = """\
You are the writer and analyst for a YouTube channel that covers {niche} for a \
US audience. You are not a summariser. Your value is judgement: what the news \
means, who it affects, what to do about it, and what everyone else reporting \
it is getting wrong.

Non-negotiable rules:

1. CURRENCY. Today is {today}. Write only about what is true today. Never \
describe a superseded product, price, version, or policy as current. If a \
fact could have changed since your training data, either source it from the \
supplied evidence or leave it out. Do not invent version numbers, dates, \
prices, or statistics.

2. ORIGINAL VALUE. The script must contain at least one specific, falsifiable \
claim or piece of analysis that does not appear in the supplied sources - a \
consequence, a comparison, a number you derive, a prediction with a stated \
mechanism. Generic commentary ("this is a big deal for the industry") does \
not count.

3. SOURCING. Every factual assertion goes in the `claims` array with a real \
source URL drawn from the evidence provided and the date that source is from. \
If you cannot source it, cut it.

4. NO TEMPLATE FEEL. Do not open with "In today's video". Do not say "Let's \
dive in", "buckle up", "game-changer", or "revolutionary". Do not number the \
sections out loud. Vary sentence length. Write the way a well-briefed person \
talks, not the way SEO copy reads.

5. HONEST FRAMING. If the story is incremental, say so. Do not manufacture \
stakes. A title the video does not deliver on costs more in retention than it \
gains in clicks.
{sensitive_clause}
Return ONLY valid JSON matching the schema given. No markdown fence, no commentary.
"""

SENSITIVE_CLAUSE = """
6. REGULATED SUBJECT MATTER. This niche touches money, health, or law. State \
plainly that the video is information, not advice. Do not tell the viewer what \
to buy, sell, or take. Attribute every recommendation to a named source rather \
than asserting it in the channel's own voice.
"""

SCHEMA = {
    "title": "YouTube title, <=70 chars, specific and honest, no clickbait punctuation",
    "hook": "First 10 seconds of voiceover. State the specific news and the stake. No greeting.",
    "sections": [
        {
            "heading": "Short internal label (not read aloud)",
            "voiceover": "What the narrator says. Conversational. 60-150 words.",
            "b_roll_query": "3-6 word visual search query for this section",
            "on_screen_text": "Optional short caption, <=60 chars",
        }
    ],
    "original_analysis": "1-3 sentences: the specific insight that is yours, not the sources'",
    "thumbnail_text": "2-4 words for the thumbnail. A complete phrase, not a truncated title.",
    "claims": [
        {
            "text": "A factual assertion made in the script",
            "source_url": "URL from the supplied evidence supporting it",
            "as_of": "YYYY-MM-DD date of that source",
        }
    ],
    "description": "YouTube description: 2-3 sentence summary, then Sources with dated links",
    "tags": ["8-15 lowercase search tags"],
}


def build_user_prompt(
    topic: Topic,
    niche: NicheConfig,
    config: Config,
    now: datetime,
) -> str:
    """Assemble the evidence pack the model must write from."""
    evidence_lines: list[str] = []
    for item in topic.evidence:
        when = item.get("event_at") or "date unknown"
        evidence_lines.append(
            f"- [{item.get('source')}] {item.get('title')}\n"
            f"  url: {item.get('url')}\n"
            f"  dated: {when}"
        )
    for signal in topic.signals[:8]:
        if signal.summary:
            evidence_lines.append(f"  context: {signal.summary[:300]}")

    target_words = int(
        config.media.target_duration_s / 60.0 * config.media.words_per_minute
    )
    target_words = max(config.script.min_words, min(config.script.max_words, target_words))

    return f"""\
TOPIC: {topic.term}

NICHE: {niche.name}
TODAY: {now.strftime('%Y-%m-%d')}
TARGET LENGTH: about {target_words} words of voiceover \
(~{config.media.target_duration_s / 60:.0f} minutes at {config.media.words_per_minute} wpm)

EVIDENCE (this is your only source of current fact - do not go beyond it for
anything dated, and do not treat your training data as current):
{chr(10).join(evidence_lines) if evidence_lines else '- (no evidence supplied)'}

WHY THIS TOPIC WAS SELECTED:
- corroborating sources: {', '.join(topic.sources)}
- demand score: {topic.demand:.2f}
- estimated competition: {topic.saturation:.2f} (higher means more creators have covered it;
  at high competition, lead with the angle others are missing, not the basic facts)

REQUIRED JSON SCHEMA:
{json.dumps(SCHEMA, indent=2)}

Write at least {config.script.min_claims_with_sources} sourced claims. Every
claim's `as_of` must be within {config.freshness.max_claim_age_days} days of
today, or the script will be rejected automatically.
"""


def build_system_prompt(niche: NicheConfig, now: datetime) -> str:
    return SYSTEM_PROMPT.format(
        niche=niche.name.replace("-", " "),
        today=now.strftime("%Y-%m-%d"),
        sensitive_clause=SENSITIVE_CLAUSE if niche.sensitive else "",
    )
