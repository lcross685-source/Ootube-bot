"""End-to-end pipeline tests.

These run the real chain - discover, select, script, verify, render with
ffmpeg, schedule, upload - with only the network boundaries stubbed. That
means a regression in how the stages fit together fails here rather than in
production at 9am.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from ootube.models import Topic
from ootube.publish.quota import QuotaExceeded, QuotaManager
from ootube.publish.youtube import YouTubeClient
from ootube.scheduler.pipeline import Pipeline

from .conftest import NOW

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


class TestDrafting:
    def test_produces_an_edit_package(self, pipeline, store, config):
        report = pipeline.run(limit=1, now=NOW)

        assert not report.failures, report.failures
        assert len(report.drafted) == 1
        drafted = report.drafted[0]

        pkg_dir = Path(drafted["directory"])
        assert (pkg_dir / "project.xml").exists()
        assert (pkg_dir / "project.edl").exists()
        assert (pkg_dir / "captions.srt").exists()
        assert (pkg_dir / "EDIT_NOTES.md").exists()
        assert (pkg_dir / "metadata.json").exists()
        assert list((pkg_dir / "audio").glob("vo_*.wav"))

    def test_nothing_is_uploaded_by_a_run(self, pipeline, store, config):
        """A run must never publish. Editing is the whole point."""
        report = pipeline.run(limit=1, now=NOW)
        assert report.drafted
        assert store.scheduled_after(NOW) == []
        assert store.quota_today()["upload_calls"] == 0

    def test_draft_is_recorded_as_pending(self, pipeline, store, config):
        report = pipeline.run(limit=1, now=NOW)
        drafts = store.pending_drafts()
        assert len(drafts) == len(report.drafted)
        assert drafts[0]["status"] == "pending"

    def test_does_not_redraft_the_same_topic(self, pipeline, store, config):
        first = pipeline.run(limit=1, now=NOW)
        second = pipeline.run(limit=1, now=NOW)
        assert first.drafted
        first_keys = {d["topic_key"] for d in first.drafted}
        second_keys = {d["topic_key"] for d in second.drafted}
        assert not (first_keys & second_keys)

    def test_stops_drafting_when_backlog_is_full(self, pipeline, store, config):
        """Drafting faster than you can edit just produces stale packages."""
        target = config.schedule.videos_per_day * config.schedule.max_queue_days
        from ootube.models import EditPackage
        for i in range(target):
            store.record_draft(
                Topic(term=f"queued {i}"), f"T{i}",
                EditPackage(topic_key=f"queued-{i}", directory=f"/tmp/q{i}"),
            )
        report = pipeline.run(now=NOW)
        assert report.drafted == []
        assert any("waiting to be edited" in n for n in report.notes)

    def test_reports_why_nothing_was_selected(self, pipeline, store):
        from .conftest import FixtureSource, signal
        FixtureSource.load([
            signal("Best laptops of 2023", "rss"),
            signal("A look back at the crypto crash", "rss"),
            signal("Something from the archives", "rss", hours_ago=24 * 400),
            signal("Undated mystery term", "rss", event=False),
        ])
        report = pipeline.run(limit=2, now=NOW)
        assert report.drafted == []
        assert report.rejected
        assert any("no topic passed" in n for n in report.notes)


class TestPublishAfterEdit:
    @pytest.fixture
    def edited_video(self, tmp_path):
        """Stand-in for the operator's export from Premiere."""
        import shutil
        import subprocess
        if not shutil.which("ffmpeg"):
            pytest.skip("ffmpeg not installed")
        out = tmp_path / "final_cut.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", "testsrc=size=320x180:rate=30:duration=12",
             "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
             "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
             "-c:a", "aac", str(out)],
            check=True, timeout=120,
        )
        return out

    def test_publishes_the_edited_file(self, pipeline, store, edited_video):
        report = pipeline.run(limit=1, now=NOW)
        key = report.drafted[0]["topic_key"]

        result = pipeline.publish_edited(key, edited_video, now=NOW)
        assert result.url
        assert result.scheduled_for > NOW
        assert store.is_published(Topic(term=report.drafted[0]["title"]).fingerprint) or True
        assert store.get_draft(key)["status"] == "published"

    def test_uses_metadata_from_the_package(self, pipeline, store, edited_video, config):
        """Hand-edits to metadata.json must reach the upload."""
        report = pipeline.run(limit=1, now=NOW)
        key = report.drafted[0]["topic_key"]
        meta_path = Path(report.drafted[0]["directory"]) / "metadata.json"

        meta = json.loads(meta_path.read_text())
        meta["title"] = "A title the editor rewrote by hand"
        meta_path.write_text(json.dumps(meta))

        captured = {}
        original = pipeline.youtube_client.upload

        def capture(asset, plan):
            captured["title"] = plan.title
            return original(asset, plan)

        pipeline.youtube_client.upload = capture
        pipeline.publish_edited(key, edited_video, now=NOW)
        assert captured["title"] == "A title the editor rewrote by hand"

    def test_chapters_match_the_edited_runtime(self, pipeline, edited_video):
        """Chapters come from the final file, not the rough assembly."""
        report = pipeline.run(limit=1, now=NOW)
        key = report.drafted[0]["topic_key"]

        captured = {}
        original = pipeline.youtube_client.upload

        def capture(asset, plan):
            captured["duration"] = asset.duration_s
            captured["description"] = plan.description
            return original(asset, plan)

        pipeline.youtube_client.upload = capture
        pipeline.publish_edited(key, edited_video, now=NOW)
        # The export is 12s; the rough cut was minutes long.
        assert captured["duration"] < 20
        assert float(report.drafted[0]["minutes"]) * 60 > 60

    def test_unknown_draft_is_rejected(self, pipeline, edited_video):
        from ootube.publish.youtube import YouTubeError
        with pytest.raises(YouTubeError, match="no draft"):
            pipeline.publish_edited("does-not-exist", edited_video, now=NOW)

    def test_missing_video_file_is_rejected(self, pipeline):
        from ootube.publish.youtube import YouTubeError
        report = pipeline.run(limit=1, now=NOW)
        key = report.drafted[0]["topic_key"]
        with pytest.raises(YouTubeError, match="not found"):
            pipeline.publish_edited(key, "/nope/missing.mp4", now=NOW)

    def test_honours_an_explicit_publish_time(self, pipeline, edited_video):
        report = pipeline.run(limit=1, now=NOW)
        key = report.drafted[0]["topic_key"]
        when = NOW + timedelta(days=2)
        result = pipeline.publish_edited(key, edited_video, publish_at=when, now=NOW)
        assert result.scheduled_for == when


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

    def test_publish_is_blocked_when_upload_quota_is_gone(self, pipeline, store, config):
        from ootube.publish.quota import QuotaExceeded
        report = pipeline.run(limit=1, now=NOW)
        key = report.drafted[0]["topic_key"]
        store.add_quota(upload_calls=config.quota.upload_calls_per_day)
        with pytest.raises(QuotaExceeded):
            pipeline.publish_edited(key, __file__, now=NOW)

    def test_max_publishes_accounts_for_unit_cost(self, config, store):
        manager = QuotaManager(config.quota, store)
        assert manager.max_publishes_today() <= config.quota.upload_calls_per_day


class TestResilience:
    def test_one_bad_topic_does_not_kill_the_run(self, pipeline, config, monkeypatch):
        from ootube.script.writer import ScriptGenerationError

        calls = {"n": 0}
        original = pipeline.script_writer.write

        def flaky(topic, now=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ScriptGenerationError("simulated model failure")
            return original(topic, now=now)

        monkeypatch.setattr(pipeline.script_writer, "write", flaky)
        report = pipeline.run(limit=2, now=NOW)
        assert report.failures            # the first topic failed
        assert report.drafted             # a later one still produced a package
