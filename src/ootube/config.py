"""Configuration loading.

Everything an operator would want to tune lives in YAML so the channel can be
re-aimed at a different niche without touching code. Secrets never live here -
they come from the environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_DIR = REPO_ROOT / "config"


@dataclass
class FreshnessConfig:
    """Thresholds for the anti-stale gate.

    Defaults are deliberately strict. A channel that publishes a "breaking"
    video about a three-week-old event reads as a content farm to both viewers
    and YouTube's inauthentic-content review.
    """

    max_signal_age_hours: float = 48.0
    max_event_age_days: float = 14.0
    evergreen_max_event_age_days: float = 365.0
    max_claim_age_days: float = 30.0
    require_event_date_for_news: bool = True
    reject_superseded_generations: bool = True
    max_year_reference_lag: int = 1
    min_sources_for_news: int = 1
    stale_terms: list[str] = field(default_factory=list)


@dataclass
class NicheConfig:
    """A content vertical with its revenue characteristics.

    ``rpm_usd`` is revenue per 1000 monetized playbacks. It varies by more than
    10x across verticals, which is why niche choice dominates every other
    revenue lever in this pipeline.
    """

    name: str
    rpm_usd: float = 4.0
    keywords: list[str] = field(default_factory=list)
    subreddits: list[str] = field(default_factory=list)
    rss_feeds: list[str] = field(default_factory=list)
    youtube_category_id: str = "28"
    weight: float = 1.0
    evergreen: bool = False
    affiliate_block: str = ""
    # Verticals where YouTube restricts synthetic presenters discussing
    # sensitive subject matter. Flagged so the script stage can add a
    # human-reviewed disclaimer instead of an AI persona giving advice.
    sensitive: bool = False


@dataclass
class ScoringConfig:
    weight_demand: float = 1.0
    weight_rpm: float = 1.2
    weight_freshness: float = 1.5
    weight_saturation: float = 0.8
    baseline_views: int = 1200
    monetized_playback_rate: float = 0.55
    min_score: float = 0.25


@dataclass
class ScheduleConfig:
    """Publishing cadence.

    Uploads go up as ``private`` with a ``publishAt`` timestamp so the channel
    keeps a steady public cadence even when a run fails or a batch lands at an
    odd hour.
    """

    timezone: str = "America/New_York"
    videos_per_day: int = 2
    publish_hours_local: list[int] = field(default_factory=lambda: [9, 17])
    min_gap_hours: float = 4.0
    lead_time_hours: float = 2.0
    max_queue_days: int = 5
    backfill_when_short: bool = True


@dataclass
class QuotaConfig:
    """YouTube Data API budget.

    Costs are configurable because Google has changed them: uploads
    historically cost 1600 units from the shared 10,000/day pool, and are now
    billed to a separate per-call bucket. Setting these from YAML means a
    future change is a config edit, not a code change.
    """

    daily_units: int = 10000
    upload_calls_per_day: int = 100
    search_calls_per_day: int = 100
    cost_videos_insert: int = 1
    cost_search_list: int = 100
    cost_videos_list: int = 1
    cost_playlist_items_insert: int = 50
    cost_thumbnails_set: int = 50
    reserve_units: int = 500


@dataclass
class MediaConfig:
    tts_provider: str = "edge"
    tts_voice: str = "en-US-AndrewMultilingualNeural"
    words_per_minute: int = 155
    width: int = 1920
    height: int = 1080
    fps: int = 30
    target_duration_s: float = 480.0
    min_duration_s: float = 180.0
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    output_dir: str = "out"
    broll_provider: str = "pexels"
    music_volume: float = 0.06
    # --- edit-package settings ---
    #: Pad between narration sections on the timeline. Small on purpose: a gap
    #: that already exists is easier to close than one you have to create.
    section_gap_s: float = 0.35
    #: Drop a marker at each sentence boundary as a suggested cut point.
    sentence_markers: bool = True
    #: Render a watchable rough cut alongside the project file.
    render_preview: bool = False


@dataclass
class ScriptConfig:
    provider: str = "anthropic"
    model: str = "claude-opus-5"
    max_tokens: int = 8000
    temperature: float = 0.7
    min_words: int = 450
    max_words: int = 1400
    require_original_analysis: bool = True
    min_claims_with_sources: int = 3
    disclosure_line: str = (
        "This video uses AI-assisted narration and visuals. "
        "Research, analysis, and editorial judgement are reviewed by a human editor."
    )


@dataclass
class ChannelConfig:
    name: str = "ootube"
    default_language: str = "en"
    made_for_kids: bool = False
    contains_synthetic_media: bool = True
    description_footer: str = ""
    niches: list[NicheConfig] = field(default_factory=list)


@dataclass
class Config:
    channel: ChannelConfig = field(default_factory=ChannelConfig)
    freshness: FreshnessConfig = field(default_factory=FreshnessConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    quota: QuotaConfig = field(default_factory=QuotaConfig)
    media: MediaConfig = field(default_factory=MediaConfig)
    script: ScriptConfig = field(default_factory=ScriptConfig)
    sources: dict[str, Any] = field(default_factory=dict)
    db_path: str = "data/ootube.db"

    def niche(self, name: str) -> NicheConfig:
        for n in self.channel.niches:
            if n.name == name:
                return n
        return NicheConfig(name=name)

    @property
    def niche_names(self) -> list[str]:
        return [n.name for n in self.channel.niches]


def _filter_kwargs(cls: type, data: dict[str, Any]) -> dict[str, Any]:
    """Drop unknown keys so an extra YAML field is a warning, not a crash."""
    valid = {f for f in getattr(cls, "__dataclass_fields__", {})}
    return {k: v for k, v in (data or {}).items() if k in valid}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(config_dir: str | Path | None = None) -> Config:
    """Load ``channel.yaml`` and ``niches.yaml`` into a :class:`Config`."""
    cdir = Path(config_dir or os.environ.get("OOTUBE_CONFIG_DIR", DEFAULT_CONFIG_DIR))
    raw: dict[str, Any] = {}

    channel_file = cdir / "channel.yaml"
    if channel_file.exists():
        raw = yaml.safe_load(channel_file.read_text()) or {}

    local_file = cdir / "channel.local.yaml"
    if local_file.exists():
        raw = _deep_merge(raw, yaml.safe_load(local_file.read_text()) or {})

    niches_raw: list[dict[str, Any]] = []
    niches_file = cdir / "niches.yaml"
    if niches_file.exists():
        loaded = yaml.safe_load(niches_file.read_text()) or {}
        niches_raw = loaded.get("niches", []) or []

    channel_data = _filter_kwargs(ChannelConfig, raw.get("channel", {}))
    channel = ChannelConfig(**channel_data)
    channel.niches = [NicheConfig(**_filter_kwargs(NicheConfig, n)) for n in niches_raw]

    cfg = Config(
        channel=channel,
        freshness=FreshnessConfig(**_filter_kwargs(FreshnessConfig, raw.get("freshness", {}))),
        scoring=ScoringConfig(**_filter_kwargs(ScoringConfig, raw.get("scoring", {}))),
        schedule=ScheduleConfig(**_filter_kwargs(ScheduleConfig, raw.get("schedule", {}))),
        quota=QuotaConfig(**_filter_kwargs(QuotaConfig, raw.get("quota", {}))),
        media=MediaConfig(**_filter_kwargs(MediaConfig, raw.get("media", {}))),
        script=ScriptConfig(**_filter_kwargs(ScriptConfig, raw.get("script", {}))),
        sources=raw.get("sources", {}) or {},
        db_path=raw.get("db_path", "data/ootube.db"),
    )

    # Env overrides for the handful of things CI needs to flip per-run.
    if os.environ.get("OOTUBE_DB_PATH"):
        cfg.db_path = os.environ["OOTUBE_DB_PATH"]
    if os.environ.get("OOTUBE_VIDEOS_PER_DAY"):
        cfg.schedule.videos_per_day = int(os.environ["OOTUBE_VIDEOS_PER_DAY"])
    if os.environ.get("OOTUBE_MODEL"):
        cfg.script.model = os.environ["OOTUBE_MODEL"]
    return cfg
