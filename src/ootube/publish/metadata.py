"""Title, description and tag construction.

The description is where a surprising amount of the revenue lives. Chapters
lift watch time by letting viewers skip to what they came for; dated source
links are the channel's defence if a video is ever reviewed for authenticity;
and the affiliate block is a revenue stream that does not depend on the
Partner Program at all, which matters because AdSense is the slowest of the
three to switch on.
"""

from __future__ import annotations

from datetime import datetime

from ..config import Config
from ..models import PublishPlan, Script, Topic, utcnow

# YouTube's hard limits.
MAX_TITLE = 100
MAX_DESCRIPTION = 5000
MAX_TAG_CHARS = 500
MAX_TAG_LEN = 30


def clamp_title(title: str) -> str:
    title = " ".join((title or "").split())
    if len(title) <= MAX_TITLE:
        return title
    # Cut at a word boundary rather than mid-word.
    cut = title[: MAX_TITLE - 1].rsplit(" ", 1)[0]
    return (cut or title[: MAX_TITLE - 1]) + "…"


def clamp_tags(tags: list[str]) -> list[str]:
    """Trim to YouTube's 500-character aggregate tag budget."""
    out: list[str] = []
    used = 0
    seen: set[str] = set()
    for tag in tags:
        tag = " ".join((tag or "").strip().lower().split())
        if not tag or tag in seen or len(tag) > MAX_TAG_LEN:
            continue
        # Tags are comma-joined by the API, so each costs its length plus one.
        cost = len(tag) + 1
        if used + cost > MAX_TAG_CHARS:
            break
        out.append(tag)
        seen.add(tag)
        used += cost
    return out


def _timestamp(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def build_chapters(script: Script, total_duration: float) -> list[tuple[float, str]]:
    """Proportional chapter marks.

    YouTube only renders chapters when the first is at 0:00 and there are at
    least three, each at least ten seconds long; a list that violates those
    rules is silently ignored, so return nothing rather than a broken list.
    """
    weights = [max(1, len(script.hook.split()))] + [
        max(1, len(s.voiceover.split())) for s in script.sections
    ]
    labels = ["Intro"] + [
        (s.heading or f"Part {i + 1}").strip().title()
        for i, s in enumerate(script.sections)
    ]
    total_words = sum(weights)
    if total_words <= 0 or total_duration <= 0:
        return []

    chapters: list[tuple[float, str]] = []
    elapsed = 0.0
    for weight, label in zip(weights, labels):
        chapters.append((elapsed, label))
        elapsed += total_duration * weight / total_words

    if len(chapters) < 3:
        return []
    for (start, _), (nxt, _) in zip(chapters, chapters[1:]):
        if nxt - start < 10.0:
            return []
    return chapters


def build_description(
    script: Script,
    topic: Topic,
    config: Config,
    *,
    total_duration: float = 0.0,
    now: datetime | None = None,
) -> str:
    now = now or utcnow()
    niche = config.niche(topic.niche)
    parts: list[str] = []

    summary = script.description.strip() or script.hook.strip()
    if summary:
        parts.append(summary)

    if script.original_analysis:
        parts.append(script.original_analysis.strip())

    chapters = build_chapters(script, total_duration)
    if chapters:
        lines = ["Chapters:"] + [
            f"{_timestamp(start)} {label}" for start, label in chapters
        ]
        parts.append("\n".join(lines))

    # Dated sources: the authenticity record, and a genuine viewer service.
    sourced = [c for c in script.claims if c.source_url]
    if sourced:
        lines = ["Sources:"]
        seen: set[str] = set()
        for claim in sourced:
            if claim.source_url in seen:
                continue
            seen.add(claim.source_url)
            date = f" ({claim.as_of:%Y-%m-%d})" if claim.as_of else ""
            lines.append(f"- {claim.source_url}{date}")
        parts.append("\n".join(lines))

    if niche.affiliate_block:
        parts.append(niche.affiliate_block.strip())

    # Required disclosure when narration or visuals are synthetic.
    if config.channel.contains_synthetic_media and config.script.disclosure_line:
        parts.append(config.script.disclosure_line.strip())

    parts.append(f"Published {now:%B %d, %Y}. Facts stated are current as of that date.")

    if config.channel.description_footer:
        parts.append(config.channel.description_footer.strip())

    description = "\n\n".join(p for p in parts if p)
    if len(description) > MAX_DESCRIPTION:
        description = description[: MAX_DESCRIPTION - 1].rsplit("\n", 1)[0]
    return description


def build_publish_plan(
    script: Script,
    topic: Topic,
    config: Config,
    publish_at: datetime,
    *,
    total_duration: float = 0.0,
    playlist_id: str = "",
    now: datetime | None = None,
) -> PublishPlan:
    niche = config.niche(topic.niche)
    tags = clamp_tags(list(script.tags) + niche.keywords[:5])

    return PublishPlan(
        topic_key=topic.key,
        title=clamp_title(script.title),
        description=build_description(
            script, topic, config, total_duration=total_duration, now=now
        ),
        tags=tags,
        publish_at=publish_at,
        category_id=niche.youtube_category_id,
        # Uploaded private and flipped public by YouTube at publish_at. This
        # is what keeps a steady public cadence even when runs are bursty.
        privacy_status="private",
        made_for_kids=config.channel.made_for_kids,
        contains_synthetic_media=config.channel.contains_synthetic_media,
        playlist_id=playlist_id,
        language=config.channel.default_language,
    )
