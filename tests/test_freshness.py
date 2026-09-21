"""Tests for the anti-stale gate - the rules that keep old news off the channel."""

from __future__ import annotations

from datetime import timedelta

import pytest

from ootube.freshness import (
    FreshnessGate,
    extract_generations,
    history_frame,
    learn_generations,
    stale_year_reference,
)
from ootube.models import Topic

from .conftest import NOW, signal


class TestGenerationExtraction:
    @pytest.mark.parametrize(
        "text,family,number",
        [
            ("Samsung Galaxy S23 Ultra review", "galaxy s", 23),
            ("iPhone 17 Pro Max hands on", "iphone", 17),
            ("GPT-5 is available today", "gpt", 5),
            ("Pixel 10 leak", "pixel", 10),
            ("RTX 5090 benchmarks", "rtx", 5090),
            ("Windows 11 update", "windows", 11),
        ],
    )
    def test_extracts_known_families(self, text, family, number):
        gens = {g.family: g.number for g in extract_generations(text)}
        assert gens[family] == number

    @pytest.mark.parametrize(
        "text",
        [
            "iPhone in 2026 will be different",   # a year, not a generation
            "Galaxy S Pen support",                # no number at all
            "",
        ],
    )
    def test_ignores_non_generations(self, text):
        assert extract_generations(text) == []

    def test_learn_takes_the_newest(self):
        latest = learn_generations([
            signal("Galaxy S23 retrospective"),
            signal("Galaxy S25 Ultra launch"),
            signal("Galaxy S24 deals"),
        ])
        assert latest["galaxy s"] == 25


class TestYearAndFraming:
    def test_flags_old_year(self):
        assert stale_year_reference("Best laptops of 2023", NOW, 1) == 2023

    def test_allows_current_and_future_years(self):
        assert stale_year_reference("Best laptops of 2026", NOW, 1) is None
        assert stale_year_reference("2027 outlook", NOW, 1) is None

    def test_allows_last_year_within_lag(self):
        assert stale_year_reference("changes since 2025", NOW, 1) is None

    def test_detects_history_framing(self):
        assert history_frame("A look back at the crash") == "a look back"
        assert history_frame("Fed cuts rates today") is None


class TestGate:
    @pytest.fixture
    def gate(self, config):
        g = FreshnessGate(config.freshness)
        g.learn([signal("Samsung Galaxy S25 Ultra launch")])
        return g

    def test_passes_genuinely_fresh_topic(self, gate):
        topic = Topic(term="Fed cuts rates by 50 basis points",
                      signals=[signal("Fed cuts rates", hours_ago=3)])
        assert gate.evaluate(topic, now=NOW).ok

    def test_blocks_superseded_product_even_when_freshly_published(self, gate):
        """The hard case: a brand-new article about a dead product.

        Both timestamps look current, so only supersession catches it.
        """
        topic = Topic(term="Samsung Galaxy S23 launch: what to know",
                      signals=[signal("Galaxy S23 launch", hours_ago=1)])
        verdict = gate.evaluate(topic, now=NOW)
        assert not verdict.ok
        assert verdict.rule == "superseded"
        assert "25" in verdict.reason

    def test_blocks_old_event(self, gate):
        topic = Topic(term="Something that happened ages ago",
                      signals=[signal("old", hours_ago=24 * 400)])
        verdict = gate.evaluate(topic, now=NOW)
        assert not verdict.ok
        assert verdict.rule == "event_age"

    def test_blocks_stale_signal(self, gate):
        stale = signal("x", hours_ago=1)
        stale.observed_at = NOW - timedelta(days=5)
        topic = Topic(term="Fresh event, stale observation", signals=[stale])
        verdict = gate.evaluate(topic, now=NOW)
        assert not verdict.ok
        assert verdict.rule == "signal_age"

    def test_blocks_year_anchored_title(self, gate):
        topic = Topic(term="Best laptops of 2023", signals=[signal("x")])
        assert gate.evaluate(topic, now=NOW).rule == "stale_year"

    def test_blocks_history_framing(self, gate):
        topic = Topic(term="A look back at the crypto crash", signals=[signal("x")])
        assert gate.evaluate(topic, now=NOW).rule == "history_frame"

    def test_blocks_undated_news(self, gate):
        topic = Topic(term="Mystery term", signals=[signal("x", event=False)])
        assert gate.evaluate(topic, now=NOW).rule == "no_event_date"

    def test_allows_undated_topic_for_evergreen_niche(self, gate):
        topic = Topic(term="How index funds work", signals=[signal("x", event=False)])
        assert gate.evaluate(topic, evergreen=True, now=NOW).ok

    def test_rejects_future_dated_source(self, gate):
        topic = Topic(term="Tomorrow's news", signals=[signal("x", hours_ago=-24 * 5)])
        assert gate.evaluate(topic, now=NOW).rule == "bad_date"

    def test_empty_topic_is_rejected(self, gate):
        assert not gate.evaluate(Topic(term="nothing"), now=NOW).ok

    def test_score_decays_with_age(self, gate):
        recent = Topic(term="a", signals=[signal("a", hours_ago=1)])
        older = Topic(term="b", signals=[signal("b", hours_ago=24 * 10)])
        assert gate.score(recent, NOW) > gate.score(older, NOW)
        assert 0.0 <= gate.score(older, NOW) <= 1.0

    def test_learning_never_regresses(self, gate):
        gate.learn([signal("Galaxy S23 nostalgia post")])
        assert gate.known_generations["galaxy s"] == 25
