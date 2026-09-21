"""Core domain objects passed between pipeline stages.

The pipeline is a pure-ish chain:

    TrendSignal[]  ->  Topic[]  ->  Script  ->  VideoAsset  ->  PublishPlan

Every object carries the timestamps needed by the freshness gate, so a stale
item can be rejected at any stage rather than only at ingestion.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:80] or "untitled"


@dataclass
class TrendSignal:
    """One observation of demand from a single source.

    ``observed_at`` is when *we* saw the signal; ``event_at`` is when the
    underlying thing actually happened. They differ constantly and the
    difference is what keeps two-year-old news out of the channel: a blog can
    publish a fresh article about an old phone, giving a recent ``observed_at``
    but an old ``event_at``.
    """

    source: str
    term: str
    url: str = ""
    observed_at: datetime = field(default_factory=utcnow)
    event_at: datetime | None = None
    volume: float = 0.0
    region: str = "US"
    summary: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return _slug(self.term)

    def age_hours(self, now: datetime | None = None) -> float:
        now = now or utcnow()
        return (now - self.observed_at).total_seconds() / 3600.0

    def event_age_days(self, now: datetime | None = None) -> float | None:
        if self.event_at is None:
            return None
        now = now or utcnow()
        return (now - self.event_at).total_seconds() / 86400.0


@dataclass
class Topic:
    """A candidate video subject, merged from one or more signals."""

    term: str
    niche: str = "general"
    signals: list[TrendSignal] = field(default_factory=list)
    demand: float = 0.0
    saturation: float = 0.5
    score: float = 0.0
    projected_views: int = 0
    expected_revenue_usd: float = 0.0
    rejected_reason: str = ""
    angle: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)

    @property
    def key(self) -> str:
        return _slug(self.term)

    @property
    def fingerprint(self) -> str:
        """Stable id used for cross-run dedupe."""
        return hashlib.sha256(self.key.encode("utf-8")).hexdigest()[:16]

    @property
    def sources(self) -> list[str]:
        return sorted({s.source for s in self.signals})

    def newest_observation(self) -> datetime | None:
        if not self.signals:
            return None
        return max(s.observed_at for s in self.signals)

    def oldest_event(self) -> datetime | None:
        events = [s.event_at for s in self.signals if s.event_at is not None]
        return min(events) if events else None

    def newest_event(self) -> datetime | None:
        events = [s.event_at for s in self.signals if s.event_at is not None]
        return max(events) if events else None


@dataclass
class ScriptSection:
    heading: str
    voiceover: str
    b_roll_query: str = ""
    on_screen_text: str = ""


@dataclass
class Claim:
    """A factual assertion with the source that backs it.

    ``as_of`` is mandatory in practice: the verifier rejects claims whose
    supporting source predates the freshness window, which stops the writer
    from padding a 2026 video with 2024 specifications.
    """

    text: str
    source_url: str = ""
    as_of: datetime | None = None


@dataclass
class Script:
    topic_key: str
    title: str
    hook: str
    sections: list[ScriptSection] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    description: str = ""
    tags: list[str] = field(default_factory=list)
    original_analysis: str = ""
    # Short phrase for the thumbnail. Kept separate from the title because
    # truncating a title mid-phrase produces unreadable thumbnails.
    thumbnail_text: str = ""
    created_at: datetime = field(default_factory=utcnow)
    model: str = ""

    @property
    def voiceover_text(self) -> str:
        parts = [self.hook] + [s.voiceover for s in self.sections]
        return "\n\n".join(p.strip() for p in parts if p and p.strip())

    def word_count(self) -> int:
        return len(self.voiceover_text.split())

    def estimated_duration_s(self, words_per_minute: int = 150) -> float:
        if words_per_minute <= 0:
            return 0.0
        return self.word_count() / words_per_minute * 60.0


@dataclass
class VideoAsset:
    topic_key: str
    video_path: str
    thumbnail_path: str = ""
    audio_path: str = ""
    duration_s: float = 0.0
    width: int = 1920
    height: int = 1080

    @property
    def is_short(self) -> bool:
        """YouTube treats vertical videos of 3 minutes or less as Shorts."""
        return self.height > self.width and self.duration_s <= 180


@dataclass
class PublishPlan:
    topic_key: str
    title: str
    description: str
    tags: list[str]
    publish_at: datetime
    category_id: str = "28"
    privacy_status: str = "private"
    made_for_kids: bool = False
    contains_synthetic_media: bool = True
    playlist_id: str = ""
    language: str = "en"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["publish_at"] = self.publish_at.isoformat()
        return d


@dataclass
class PublishResult:
    topic_key: str
    video_id: str
    url: str
    scheduled_for: datetime
    dry_run: bool = False
