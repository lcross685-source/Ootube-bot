"""Tests for durable state: dedupe, quota, and the generation registry."""

from __future__ import annotations

from datetime import timedelta

from ootube.models import Topic

from .conftest import NOW


def topic(term="Fed cuts rates", niche="personal-finance"):
    return Topic(term=term, niche=niche, score=0.8)


class TestTopics:
    def test_record_and_read_back(self, store):
        t = topic()
        store.record_topic(t, status="selected")
        assert store.topic_status(t.fingerprint) == "selected"

    def test_upsert_updates_status(self, store):
        t = topic()
        store.record_topic(t, status="seen")
        store.record_topic(t, status="rejected", reason="stale")
        assert store.topic_status(t.fingerprint) == "rejected"

    def test_fingerprint_is_stable_across_instances(self):
        assert topic().fingerprint == topic().fingerprint


class TestPublished:
    def test_publishing_marks_topic_published(self, store):
        t = topic()
        assert not store.is_published(t.fingerprint)
        store.record_published(video_id="v1", topic=t, title="T", url="u",
                               scheduled_for=NOW + timedelta(hours=4))
        assert store.is_published(t.fingerprint)
        assert store.topic_status(t.fingerprint) == "published"

    def test_scheduled_after_returns_future_slots_only(self, store):
        store.record_published(video_id="past", topic=topic("old story"), title="T",
                               url="u", scheduled_for=NOW - timedelta(days=1))
        store.record_published(video_id="future", topic=topic("new story"), title="T",
                               url="u", scheduled_for=NOW + timedelta(days=1))
        slots = store.scheduled_after(NOW)
        assert len(slots) == 1
        assert slots[0] > NOW

    def test_recent_terms_used_for_dedupe(self, store):
        store.record_published(video_id="v1", topic=topic("Fed cuts rates"), title="T",
                               url="u", scheduled_for=NOW)
        assert "fed-cuts-rates" in store.recent_terms(days=30)


class TestDrafts:
    def test_records_and_finds_a_draft(self, store):
        from ootube.models import EditPackage
        t = topic()
        store.record_draft(t, "A title", EditPackage(topic_key=t.key, directory="/tmp/x"))
        assert store.pending_draft_count() == 1
        assert store.get_draft(t.key)["title"] == "A title"
        assert store.topic_status(t.fingerprint) == "drafted"

    def test_has_draft_blocks_redrafting(self, store):
        """A pending draft must stop the topic being selected again."""
        from ootube.models import EditPackage
        t = topic()
        assert not store.has_draft(t.fingerprint)
        store.record_draft(t, "T", EditPackage(topic_key=t.key, directory="/tmp/x"))
        assert store.has_draft(t.fingerprint)

    def test_discarded_draft_still_blocks(self, store):
        """The operator already said no; do not re-offer the topic."""
        from ootube.models import EditPackage
        t = topic()
        store.record_draft(t, "T", EditPackage(topic_key=t.key, directory="/tmp/x"))
        store.discard_draft(t.key)
        assert store.pending_draft_count() == 0
        assert store.has_draft(t.fingerprint)

    def test_publishing_clears_it_from_pending(self, store):
        from ootube.models import EditPackage
        t = topic()
        store.record_draft(t, "T", EditPackage(topic_key=t.key, directory="/tmp/x"))
        store.mark_draft_published(t.key)
        assert store.pending_draft_count() == 0
        assert store.get_draft(t.key)["status"] == "published"


class TestQuota:
    def test_accumulates(self, store):
        store.add_quota(units=100, upload_calls=1)
        store.add_quota(units=50, search_calls=2)
        usage = store.quota_today()
        assert usage == {"units": 150, "upload_calls": 1, "search_calls": 2}

    def test_starts_empty(self, store):
        assert store.quota_today()["units"] == 0


class TestGenerations:
    def test_records_newest(self, store):
        store.observe_generation("galaxy s", 25)
        assert store.latest_generation("galaxy s") == 25

    def test_never_regresses(self, store):
        """Seeing an old article must not un-learn a newer generation."""
        store.observe_generation("galaxy s", 25)
        store.observe_generation("galaxy s", 23)
        assert store.latest_generation("galaxy s") == 25

    def test_unknown_family_is_none(self, store):
        assert store.latest_generation("nonexistent") is None

    def test_survives_reopen(self, config, store):
        from ootube.store import Store
        store.observe_generation("iphone", 17)
        store.close()
        reopened = Store(config.db_path)
        assert reopened.latest_generation("iphone") == 17
        reopened.close()


def test_run_log(store):
    store.log_run("run", "ok", "3 published")
    runs = store.recent_runs()
    assert runs[0]["status"] == "ok"
