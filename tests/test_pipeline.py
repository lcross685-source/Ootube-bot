"""End-to-end pipeline tests.

These run the real chain - discover, select, script, verify, render with
ffmpeg, schedule, upload - with only the network boundaries stubbed. That
means a regression in how the stages fit together fails here rather than in
production at 9am.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from ootube.media.render import ffmpeg_available
from ootube.models import Topic
from ootube.publish.quota import QuotaExceeded, QuotaManager
from ootube.publish.youtube import YouTubeClient
from ootube.scheduler.pipeline import Pipeline

from .conftest import NOW

@pytest.fixture
def auto_publish_config(config):
    """Config with the human-approval gate off.

    ``personal-finance`` ships with ``sensitive: true``, so by default its
    videos are held for review rather than published. Tests that exercise the
    produce-and-upload path need that gate open; the gate itself is covered
    separately in :class:`TestApprovalGate`.
    """
    for niche in config.channel.niches:
        niche.sensitive = False
    config.channel.require_human_approval = False
    return config


@pytest.fixture
def publishing_pipeline(auto_publish_config, store, stub_writer, fixture_source):
    return Pipeline(
        auto_publish_config, store,
        dry_run=True,
        workdir=auto_publish_config.media.output_dir,
        script_writer=stub_writer,
        youtube_client=YouTubeClient(dry_run=True),
        sources=["fixture"],
    )


@pytest.fixture
def pipeline(config, store, stub_writer, fixture_source):
    return Pipeline(
        config, store,
        dry_run=True,
        workdir=config.media.output_dir,
        script_writer=stub_writer,
        youtube_client=YouTubeClient(dry_run=True),
        sources=["fixture"],
    )


class TestDiscovery:
    def test_discovers_fixture_signals(self, pipeline, sample_signals):
        assert len(pipeline.discover(only=["fixture"])) == len(sample_signals)

    def test_learns_and_persists_generations(self, pipeline, store):
        pipeline.discover(only=["fixture"])
        # The S25 headline in the fixtures teaches it the current generation.
        assert pipeline.gate.known_generations["galaxy s"] == 25
        assert store.latest_generation("galaxy s") == 25


class TestSelection:
    def test_rejects_the_stale_traps(self, pipeline):
        signals = pipeline.discover(only=["fixture"])
        selected, rejected = pipeline.select(signals, limit=5, now=NOW)

        selected_terms = " ".join(s.topic.term for s in selected)
        assert "S23" not in selected_terms
        assert "2023" not in selected_terms
        assert "look back" not in selected_terms

        rules = {v.rule for _, v in rejected}
        assert {"superseded", "stale_year", "history_frame"} <= rules

    def test_selects_by_expected_revenue(self, pipeline):
        signals = pipeline.discover(only=["fixture"])
        selected, _ = pipeline.select(signals, limit=5, now=NOW)
        assert selected
        revenues = [s.expected_revenue_usd for s in selected]
        assert revenues == sorted(revenues, reverse=True)


class TestFullRun:
    def test_produces_and_schedules_videos(self, publishing_pipeline, store, config):
        if not ffmpeg_available(config.media):
            pytest.skip("ffmpeg not installed")

        report = publishing_pipeline.run(limit=1, now=NOW)

        assert not report.failures, report.failures
        assert len(report.published) == 1
        published = report.published[0]
        assert published["url"]
        assert published["scheduled_for"] > NOW.isoformat()

        # The video really exists on disk and has real duration.
        from pathlib import Path
        assert store.scheduled_after(NOW), "publish slot not recorded"
        outputs = list(Path(config.media.output_dir).rglob("video.mp4"))
        assert outputs and outputs[0].stat().st_size > 1000

    def test_does_not_republish_the_same_topic(self, publishing_pipeline, store, config):
        if not ffmpeg_available(config.media):
            pytest.skip("ffmpeg not installed")

        first = publishing_pipeline.run(limit=1, now=NOW)
        assert len(first.published) == 1

        second = publishing_pipeline.run(limit=1, now=NOW)
        assert first.published[0]["title"] not in [
            p["title"] for p in second.published
        ]

    def test_writes_script_and_sources_to_disk(self, publishing_pipeline, config):
        if not ffmpeg_available(config.media):
            pytest.skip("ffmpeg not installed")
        publishing_pipeline.run(limit=1, now=NOW)
        from pathlib import Path
        scripts = list(Path(config.media.output_dir).rglob("script.json"))
        assert scripts, "script was not archived for auditing"
        import json
        data = json.loads(scripts[0].read_text())
        assert data["claims"] and all(c["source_url"] for c in data["claims"])

    def test_skips_when_queue_is_full(self, pipeline, store, config):
        target = config.schedule.videos_per_day * config.schedule.max_queue_days
        for i in range(target):
            store.record_published(video_id=f"v{i}", topic=Topic(term=f"queued {i}"),
                                   title="T", url="u",
                                   scheduled_for=NOW + timedelta(hours=i + 1))
        report = pipeline.run(now=NOW)
        assert report.published == []
        assert any("queue already holds" in n for n in report.notes)

    def test_reports_why_nothing_was_selected(self, pipeline, store):
        from .conftest import FixtureSource, signal
        # Only stale traps remain.
        FixtureSource.load([
            signal("Best laptops of 2023", "rss"),
            signal("A look back at the crypto crash", "rss"),
            signal("Something from the archives", "rss", hours_ago=24 * 400),
            signal("Undated mystery term", "rss", event=False),
        ])
        report = pipeline.run(limit=2, now=NOW)
        assert report.published == []
        assert report.rejected
        assert any("no topic passed" in n for n in report.notes)


class TestQuota:
    def test_blocks_upload_when_cap_reached(self, config, store):
        manager = QuotaManager(config.quota, store)
        store.add_quota(upload_calls=config.quota.upload_calls_per_day)
        assert manager.remaining_uploads() == 0
        with pytest.raises(QuotaExceeded):
            manager.check(uploads=1)

    def test_reserve_is_withheld(self, config, store):
        manager = QuotaManager(config.quota, store)
        store.add_quota(units=config.quota.daily_units - config.quota.reserve_units)
        assert manager.remaining_units() == 0

    def test_run_stops_when_quota_exhausted(self, pipeline, store, config):
        store.add_quota(upload_calls=config.quota.upload_calls_per_day)
        report = pipeline.run(limit=2, now=NOW)
        assert report.published == []
        assert any("quota" in n.lower() for n in report.notes)

    def test_max_publishes_accounts_for_unit_cost(self, config, store):
        manager = QuotaManager(config.quota, store)
        assert manager.max_publishes_today() <= config.quota.upload_calls_per_day


class TestApprovalGate:
    def test_sensitive_niche_is_held_for_review(self, pipeline, store, config):
        if not ffmpeg_available(config.media):
            pytest.skip("ffmpeg not installed")
        from .conftest import FixtureSource, signal
        # personal-finance is marked sensitive, so it must not auto-publish.
        FixtureSource.load([
            signal("Fed cuts rates by 50 basis points", s,
                   summary="interest rates inflation report stock market")
            for s in ("rss", "reddit", "hackernews")
        ])
        report = pipeline.run(limit=1, now=NOW)
        assert report.queued_for_approval
        assert report.published == []
        assert store.pending_approvals()

    def test_global_approval_flag_holds_everything(self, pipeline, store, config):
        if not ffmpeg_available(config.media):
            pytest.skip("ffmpeg not installed")
        config.channel.require_human_approval = True
        report = pipeline.run(limit=1, now=NOW)
        assert report.published == []
        assert report.queued_for_approval


class TestResilience:
    def test_one_bad_topic_does_not_kill_the_run(self, publishing_pipeline, config, monkeypatch):
        if not ffmpeg_available(config.media):
            pytest.skip("ffmpeg not installed")
        from ootube.script.writer import ScriptGenerationError

        calls = {"n": 0}
        original = publishing_pipeline.script_writer.write

        def flaky(topic, now=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ScriptGenerationError("simulated model failure")
            return original(topic, now=now)

        monkeypatch.setattr(publishing_pipeline.script_writer, "write", flaky)
        report = publishing_pipeline.run(limit=2, now=NOW)
        assert report.failures            # the first topic failed
        assert report.published           # a later one still published
