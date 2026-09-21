"""Tests for revenue-weighted scoring and selection."""

from __future__ import annotations

from ootube.aggregate import build_topics
from ootube.models import Topic
from ootube.scoring import TopicSelector, estimate_saturation, normalize_rpm

from .conftest import NOW, signal


class TestRpmNormalization:
    def test_monotonic(self):
        assert normalize_rpm(4) < normalize_rpm(12) < normalize_rpm(22)

    def test_bounded(self):
        assert normalize_rpm(0) == 0.0
        assert normalize_rpm(10_000) <= 1.0


class TestSaturation:
    def test_youtube_chart_presence_raises_saturation(self):
        plain = Topic(term="a specific niche story", signals=[signal("a")])
        charted = Topic(term="a specific niche story",
                        signals=[signal("a", "youtube_charts")])
        assert estimate_saturation(charted, NOW) > estimate_saturation(plain, NOW)

    def test_older_stories_are_more_saturated(self):
        fresh = Topic(term="story about something", signals=[signal("a", hours_ago=1)])
        older = Topic(term="story about something", signals=[signal("a", hours_ago=60)])
        assert estimate_saturation(older, NOW) > estimate_saturation(fresh, NOW)

    def test_bounded(self):
        t = Topic(term="ai", signals=[signal("ai", "youtube_charts", hours_ago=500)])
        assert 0.0 <= estimate_saturation(t, NOW) <= 1.0


class TestSelection:
    def test_high_rpm_niche_wins_at_similar_demand(self, config, store):
        """The core revenue behaviour: dollars decide, not raw views."""
        selector = TopicSelector(config, store)

        def make(term, niche):
            sigs = [signal(term, s, volume=500) for s in ("rss", "reddit", "hackernews")]
            t = Topic(term=term, niche=niche, signals=sigs)
            t.demand = 0.7
            return t

        finance = make("rates decision lands today", "personal-finance")   # $22 RPM
        tech = make("new handset launches today", "consumer-tech")         # $9 RPM
        selected, _ = selector.select([tech, finance], limit=2, now=NOW)
        assert selected[0].topic.niche == "personal-finance"
        assert selected[0].expected_revenue_usd > selected[1].expected_revenue_usd

    def test_stale_topics_are_rejected_not_scored(self, config, store, sample_signals):
        selector = TopicSelector(config, store)
        topics = build_topics(sample_signals, config)
        selected, rejected = selector.select(topics, limit=10, now=NOW)

        rejected_terms = " ".join(t.term for t, _ in rejected)
        assert "S23" in rejected_terms
        assert "2023" in rejected_terms
        assert all("S23" not in s.topic.term for s in selected)

        rules = {v.rule for _, v in rejected}
        assert "superseded" in rules
        assert "stale_year" in rules

    def test_already_published_topic_is_skipped(self, config, store):
        selector = TopicSelector(config, store)
        topic = Topic(term="Fed cuts rates today", niche="personal-finance",
                      signals=[signal("Fed cuts rates today")])
        topic.demand = 0.8
        store.record_published(video_id="v1", topic=topic, title="t", url="u",
                               scheduled_for=NOW)
        verdict = selector.filter_topic(topic, now=NOW)
        assert not verdict.ok
        assert verdict.rule == "duplicate"

    def test_near_duplicate_of_recent_video_is_skipped(self, config, store):
        selector = TopicSelector(config, store)
        published = Topic(term="Fed cuts rates by 50 basis points",
                          niche="personal-finance")
        store.record_published(video_id="v1", topic=published, title="t", url="u",
                               scheduled_for=NOW)
        similar = Topic(term="Fed cuts rates by 50 basis points today",
                        niche="personal-finance", signals=[signal("x")])
        verdict = selector.filter_topic(similar, now=NOW)
        assert not verdict.ok
        assert verdict.rule == "near_duplicate"

    def test_respects_limit(self, config, store, sample_signals):
        selector = TopicSelector(config, store)
        topics = build_topics(sample_signals, config)
        selected, _ = selector.select(topics, limit=2, now=NOW)
        assert len(selected) <= 2

    def test_empty_input_is_safe(self, config, store):
        selected, rejected = TopicSelector(config, store).select([], limit=3, now=NOW)
        assert selected == [] and rejected == []
