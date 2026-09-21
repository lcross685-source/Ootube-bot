"""End-to-end orchestration.

One run does: discover -> select -> produce -> publish. Each stage is
independently callable so the CLI can dry-run any prefix of it, and every
failure is contained to a single video: one bad topic must never take down the
whole run, because an unattended channel that stops publishing is the failure
mode that actually costs money.
"""

from __future__ import annotations

import json
import logging
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..aggregate import build_topics
from ..config import Config
from ..freshness import FreshnessGate
from ..media.broll import fetch_broll
from ..media.render import RenderError, Renderer, ffmpeg_available
from ..media.thumbnail import make_thumbnail
from ..models import PublishResult, Script, Topic, TrendSignal, VideoAsset, utcnow
from ..publish.metadata import build_publish_plan
from ..publish.quota import QuotaExceeded, QuotaManager
from ..publish.youtube import YouTubeClient, YouTubeError
from ..scoring import ScoredTopic, TopicSelector
from ..script.verify import ScriptVerifier
from ..script.writer import ScriptGenerationError, ScriptWriter
from ..store import Store
from ..trends.base import build_sources
from .slots import SlotPlanner

log = logging.getLogger(__name__)


@dataclass
class RunReport:
    """What a run did, and why it did not do more.

    Deliberately verbose: the single most common question about an unattended
    bot is "why didn't it publish anything today?", and the answer has to be
    in the logs without a debugging session.
    """

    started_at: datetime = field(default_factory=utcnow)
    signals: int = 0
    topics: int = 0
    selected: list[str] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)
    published: list[dict[str, str]] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)
    queued_for_approval: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "signals": self.signals,
            "topics": self.topics,
            "selected": self.selected,
            "rejected": self.rejected,
            "published": self.published,
            "failures": self.failures,
            "queued_for_approval": self.queued_for_approval,
            "notes": self.notes,
        }

    def summary(self) -> str:
        return (
            f"{self.signals} signals -> {self.topics} topics -> "
            f"{len(self.selected)} selected -> {len(self.published)} published "
            f"({len(self.rejected)} rejected, {len(self.failures)} failed)"
        )


class Pipeline:
    def __init__(
        self,
        config: Config,
        store: Store,
        *,
        dry_run: bool = False,
        workdir: str | Path | None = None,
        script_writer: ScriptWriter | None = None,
        youtube_client: YouTubeClient | None = None,
        sources: list[str] | None = None,
    ):
        self.config = config
        self.store = store
        self.dry_run = dry_run
        self.workdir = Path(workdir or config.media.output_dir)
        # Injected so tests can run the whole chain without an API key, and so
        # an operator can swap the writer or uploader without forking this.
        self.script_writer = script_writer or ScriptWriter(config)
        self.youtube_client = youtube_client
        self.sources = sources
        self.gate = FreshnessGate(
            config.freshness, known_generations=store.all_generations()
        )
        self.selector = TopicSelector(config, store, gate=self.gate)
        self.planner = SlotPlanner(config.schedule, store)
        self.quota = QuotaManager(config.quota, store)

    # ------------------------------------------------------------- discover
    def discover(self, only: list[str] | None = None) -> list[TrendSignal]:
        signals: list[TrendSignal] = []
        for source in build_sources(self.config, only=only):
            found = source.fetch()
            if source.name == "youtube_charts" and found:
                self.quota.charge(units=self.config.quota.cost_videos_list)
            signals.extend(found)

        # Learn product generations from everything seen, then persist so the
        # knowledge survives into tomorrow's run.
        self.gate.learn(signals)
        for family, number in self.gate.known_generations.items():
            self.store.observe_generation(family, number)

        log.info("discovered %d signals from %d sources", len(signals),
                 len({s.source for s in signals}))
        return signals

    # --------------------------------------------------------------- select
    def select(
        self, signals: list[TrendSignal], limit: int, now: datetime | None = None
    ) -> tuple[list[ScoredTopic], list[tuple[Topic, Any]]]:
        topics = build_topics(signals, self.config)
        return self.selector.select(topics, limit, now=now)

    # -------------------------------------------------------------- produce
    def produce(self, scored: ScoredTopic, now: datetime | None = None) -> tuple[Script, VideoAsset]:
        """Write, verify and render one video. Raises on unrecoverable failure."""
        now = now or utcnow()
        topic = scored.topic

        script = self.script_writer.write(topic, now=now)

        verifier = ScriptVerifier(self.config, self.gate.known_generations)
        result = verifier.verify(script, now=now)
        for warning in result.warnings:
            log.warning("[%s] %s", topic.key, warning)
        if not result.ok:
            raise ScriptGenerationError(
                f"script rejected: {'; '.join(result.errors)}"
            )

        work = self.workdir / topic.key
        work.mkdir(parents=True, exist_ok=True)
        (work / "script.json").write_text(
            json.dumps(
                {
                    "title": script.title,
                    "hook": script.hook,
                    "original_analysis": script.original_analysis,
                    "sections": [
                        {"heading": s.heading, "voiceover": s.voiceover}
                        for s in script.sections
                    ],
                    "claims": [
                        {
                            "text": c.text,
                            "source_url": c.source_url,
                            "as_of": c.as_of.isoformat() if c.as_of else None,
                        }
                        for c in script.claims
                    ],
                },
                indent=2,
            )
        )

        broll = fetch_broll(
            [s.b_roll_query or topic.term for s in script.sections],
            work / "broll",
            provider=self.config.media.broll_provider,
            min_width=self.config.media.width,
        )

        asset = Renderer(self.config.media).render(script, work, broll=broll)

        thumb = make_thumbnail(
            script.thumbnail_text or script.title,
            work / "thumbnail.jpg",
            accent_index=abs(hash(topic.key)) % 4,
        )
        if thumb:
            asset.thumbnail_path = str(thumb)

        return script, asset

    # -------------------------------------------------------------- publish
    def publish(
        self,
        scored: ScoredTopic,
        script: Script,
        asset: VideoAsset,
        publish_at: datetime,
        *,
        client: YouTubeClient | None = None,
        now: datetime | None = None,
    ) -> PublishResult:
        topic = scored.topic
        plan = build_publish_plan(
            script, topic, self.config, publish_at,
            total_duration=asset.duration_s, now=now,
        )

        self.quota.check(
            units=self.quota.cost_of_publish(
                thumbnail=bool(asset.thumbnail_path), playlist=False
            ),
            uploads=1,
        )

        client = client or self.youtube_client or YouTubeClient(dry_run=self.dry_run)
        result = client.upload(asset, plan)
        self.quota.charge(units=self.config.quota.cost_videos_insert, uploads=1)

        if asset.thumbnail_path and client.set_thumbnail(
            result.video_id, asset.thumbnail_path
        ):
            self.quota.charge(units=self.config.quota.cost_thumbnails_set)

        self.store.record_published(
            video_id=result.video_id,
            topic=topic,
            title=plan.title,
            url=result.url,
            scheduled_for=publish_at,
            dry_run=result.dry_run,
        )
        return result

    # ------------------------------------------------------------------ run
    def run(self, limit: int | None = None, now: datetime | None = None) -> RunReport:
        now = now or utcnow()
        report = RunReport(started_at=now)

        if limit is None:
            limit = self.planner.videos_needed(now)
        if limit <= 0:
            report.notes.append(
                f"queue already holds {self.planner.queue_depth(now)} scheduled "
                "videos; nothing to produce"
            )
            self.store.log_run("run", "skipped", report.notes[-1])
            return report

        # Never start more videos than today's quota can actually publish.
        affordable = self.quota.max_publishes_today(playlist=False)
        if affordable < limit:
            report.notes.append(
                f"quota allows {affordable} more uploads today (wanted {limit})"
            )
            limit = affordable
        if limit <= 0:
            self.store.log_run("run", "quota_exhausted", report.notes[-1])
            return report

        if not self.dry_run and not ffmpeg_available(self.config.media):
            report.failures.append({"stage": "preflight", "error": "ffmpeg not found"})
            self.store.log_run("run", "failed", "ffmpeg not found")
            return report

        signals = self.discover(only=self.sources)
        report.signals = len(signals)

        selected, rejected = self.select(signals, limit, now=now)
        report.topics = len(selected) + len(rejected)
        report.selected = [s.topic.term for s in selected]
        report.rejected = [
            {"term": t.term, "rule": v.rule, "reason": v.reason}
            for t, v in rejected[:40]
        ]

        if not selected:
            report.notes.append(
                "no topic passed the freshness and revenue filters; "
                "publishing nothing is the correct outcome here"
            )
            self.store.log_run("run", "no_topics", report.notes[-1])
            return report

        slots = self.planner.plan(len(selected), now=now)

        for scored, slot in zip(selected, slots):
            topic = scored.topic
            try:
                script, asset = self.produce(scored, now=now)

                if self.config.channel.require_human_approval or self.config.niche(
                    topic.niche
                ).sensitive:
                    self.store.queue_approval(
                        topic, script.title,
                        {
                            "video_path": asset.video_path,
                            "thumbnail_path": asset.thumbnail_path,
                            "publish_at": slot.isoformat(),
                            "niche": topic.niche,
                        },
                    )
                    report.queued_for_approval.append(script.title)
                    log.info("queued for approval: %s", script.title)
                    continue

                result = self.publish(scored, script, asset, slot, now=now)
                report.published.append(
                    {
                        "title": script.title,
                        "video_id": result.video_id,
                        "url": result.url,
                        "scheduled_for": slot.isoformat(),
                        "expected_revenue_usd": f"{scored.expected_revenue_usd:.2f}",
                    }
                )
                log.info("published %s -> %s", script.title, result.url)

            except QuotaExceeded as exc:
                report.notes.append(f"stopped on quota: {exc}")
                log.warning("quota stop: %s", exc)
                break
            except (ScriptGenerationError, RenderError, YouTubeError) as exc:
                report.failures.append(
                    {"topic": topic.term, "error": str(exc)[:400]}
                )
                log.error("failed %s: %s", topic.key, exc)
                self.store.record_topic(topic, status="failed", reason=str(exc)[:200])
            except Exception as exc:  # noqa: BLE001
                report.failures.append(
                    {"topic": topic.term, "error": f"{type(exc).__name__}: {exc}"[:400]}
                )
                log.error("unexpected failure on %s:\n%s", topic.key, traceback.format_exc())
                self.store.record_topic(topic, status="failed", reason=str(exc)[:200])

        self.store.log_run("run", "ok", report.summary())
        return report
