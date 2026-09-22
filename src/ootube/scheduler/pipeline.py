"""Orchestration.

The pipeline deliberately stops short of publishing. A run does:

    discover -> select -> write -> verify -> assemble edit package

and then hands over to a human, who opens the project in Premiere or Resolve,
cuts it down, and exports. Publishing is a separate command taking that export.

That split is the whole design. Fully automated upload produces the kind of
templated output YouTube's inauthentic-content policy targets, and it produces
videos nobody wants to watch. Generating the first 80% of an edit - narration
laid out, b-roll matched, cut points and citations marked - keeps the tedious
part automated and the part that needs judgement human.

Every failure is contained to a single topic: one bad script must never take
down the whole run.
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
from ..edit.package import build_package
from ..media.broll import fetch_broll
from ..media.render import RenderError, Renderer, ffmpeg_available
from ..media.thumbnail import make_thumbnail
from ..feeds import parse_date
from ..media.tts import probe_duration
from ..models import (
    Claim, EditPackage, PublishResult, Script, ScriptSection, Topic, TrendSignal,
    VideoAsset, utcnow,
)
from ..publish.metadata import build_publish_plan
from ..publish.quota import QuotaManager
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
    drafted: list[dict[str, str]] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "signals": self.signals,
            "topics": self.topics,
            "selected": self.selected,
            "rejected": self.rejected,
            "drafted": self.drafted,
            "failures": self.failures,
            "notes": self.notes,
        }

    def summary(self) -> str:
        return (
            f"{self.signals} signals -> {self.topics} topics -> "
            f"{len(self.selected)} selected -> {len(self.drafted)} edit packages "
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
    def produce(self, scored: ScoredTopic, now: datetime | None = None) -> EditPackage:
        """Write, verify and assemble one edit package.

        Stops at a project file. Nothing here renders a final video, because
        the final cut is the human's call.
        """
        now = now or utcnow()
        topic = scored.topic

        script = self.script_writer.write(topic, now=now)

        verifier = ScriptVerifier(self.config, self.gate.known_generations)
        result = verifier.verify(script, now=now)
        for warning in result.warnings:
            log.warning("[%s] %s", topic.key, warning)
        if not result.ok:
            raise ScriptGenerationError(f"script rejected: {'; '.join(result.errors)}")

        work = self.workdir / topic.key
        work.mkdir(parents=True, exist_ok=True)

        broll = fetch_broll(
            [s.b_roll_query or topic.term for s in script.sections],
            work / "broll",
            provider=self.config.media.broll_provider,
            min_width=self.config.media.width,
        )

        thumb = make_thumbnail(
            script.thumbnail_text or script.title,
            work / "thumbnail.jpg",
            accent_index=abs(hash(topic.key)) % 4,
        )

        package = build_package(
            script, topic, self.config, work,
            broll=broll,
            thumbnail=thumb,
            verification_warnings=result.warnings,
            now=now,
        )

        # A rough render is optional: useful to check pacing without opening
        # an NLE, but it is not the deliverable and it is slow.
        if self.config.media.render_preview and ffmpeg_available(self.config.media):
            try:
                asset = Renderer(self.config.media).render(script, work, broll=broll)
                package.preview_path = asset.video_path
            except RenderError as exc:
                log.warning("preview render failed (project file is unaffected): %s", exc)

        self.store.record_draft(topic, script.title, package)
        return package

    # -------------------------------------------------------------- publish
    def publish_edited(
        self,
        topic_key: str,
        video_path: str | Path,
        *,
        publish_at: datetime | None = None,
        client: YouTubeClient | None = None,
        now: datetime | None = None,
    ) -> PublishResult:
        """Upload the human-edited export for a drafted topic.

        Metadata was prepared when the package was drafted and lives in the
        package's ``metadata.json``, which the editor is free to have changed.
        Chapters and duration are recomputed from the *final* file, so they
        match whatever was actually cut rather than the rough assembly.
        """
        now = now or utcnow()
        video = Path(video_path)
        if not video.exists():
            raise YouTubeError(f"edited video not found: {video}")

        draft = self.store.get_draft(topic_key)
        if draft is None:
            raise YouTubeError(
                f"no draft named {topic_key!r}. Run `ootube drafts` to list them."
            )

        package_dir = Path(draft["directory"])
        meta_path = package_dir / "metadata.json"
        if not meta_path.exists():
            raise YouTubeError(f"metadata.json missing from {package_dir}")
        meta = json.loads(meta_path.read_text())

        script = self._script_from_metadata(meta)
        topic = Topic(term=meta.get("topic_term", topic_key), niche=meta.get("niche", "general"))

        duration = probe_duration(video, self.config.media) or 0.0
        if publish_at is None:
            publish_at = self.planner.plan(1, now=now)[0]

        plan = build_publish_plan(
            script, topic, self.config, publish_at,
            total_duration=duration, now=now,
        )

        thumbnail = package_dir / "thumbnail.jpg"
        asset = VideoAsset(
            topic_key=topic_key,
            video_path=str(video),
            thumbnail_path=str(thumbnail) if thumbnail.exists() else "",
            duration_s=duration,
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
        self.store.mark_draft_published(topic_key)
        log.info("published %s -> %s (publishes %s)", plan.title, result.url, publish_at)
        return result

    @staticmethod
    def _script_from_metadata(meta: dict[str, Any]) -> Script:
        """Rebuild a Script from a package's metadata.json.

        Read back at publish time rather than carried in memory, so hand-edits
        to the title, tags or sources are picked up.
        """
        return Script(
            topic_key=meta.get("topic_key", ""),
            title=meta.get("title", ""),
            hook=meta.get("hook", ""),
            sections=[
                ScriptSection(heading=s.get("heading", ""), voiceover=s.get("voiceover", ""))
                for s in meta.get("sections", []) or []
            ],
            claims=[
                Claim(
                    text=c.get("text", ""),
                    source_url=c.get("source_url", ""),
                    as_of=parse_date(c.get("as_of")),
                )
                for c in meta.get("claims", []) or []
            ],
            description=meta.get("summary", ""),
            tags=list(meta.get("tags", []) or []),
            original_analysis=meta.get("original_analysis", ""),
            thumbnail_text=meta.get("thumbnail_text", ""),
        )

    # ------------------------------------------------------------------ run
    def run(self, limit: int | None = None, now: datetime | None = None) -> RunReport:
        """Discover, select and draft edit packages. Never uploads."""
        now = now or utcnow()
        report = RunReport(started_at=now)

        if limit is None:
            limit = self.drafts_needed(now)
        if limit <= 0:
            report.notes.append(
                f"{self.store.pending_draft_count()} draft(s) already waiting to be "
                f"edited and {self.planner.queue_depth(now)} scheduled; "
                "not drafting more"
            )
            self.store.log_run("run", "skipped", report.notes[-1])
            return report

        if not ffmpeg_available(self.config.media):
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
                "drafting nothing is the correct outcome here"
            )
            self.store.log_run("run", "no_topics", report.notes[-1])
            return report

        for scored in selected:
            topic = scored.topic
            try:
                package = self.produce(scored, now=now)
                report.drafted.append(
                    {
                        "topic_key": topic.key,
                        "title": topic.term,
                        "directory": package.directory,
                        "project": package.project_xml,
                        "minutes": f"{package.duration_s / 60:.1f}",
                        "sections": str(package.section_count),
                        "missing_broll": str(len(package.missing_broll)),
                        "expected_revenue_usd": f"{scored.expected_revenue_usd:.2f}",
                    }
                )
                log.info("drafted %s -> %s", topic.term, package.directory)
            except (ScriptGenerationError, RenderError) as exc:
                report.failures.append({"topic": topic.term, "error": str(exc)[:400]})
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

    def drafts_needed(self, now: datetime | None = None) -> int:
        """How many new drafts to make.

        Counts unedited drafts as well as scheduled videos: drafting faster
        than you can edit just creates a backlog of stale packages, since a
        topic that was current on Monday is not on Friday.
        """
        now = now or utcnow()
        pending = self.store.pending_draft_count()
        scheduled = self.planner.queue_depth(now)
        target = self.config.schedule.videos_per_day * self.config.schedule.max_queue_days
        return max(0, min(self.config.schedule.videos_per_day, target - pending - scheduled))
