"""Tests for signal clustering and niche classification."""

from __future__ import annotations

from ootube.aggregate import (
    build_topics, classify_niche, cluster_signals, demand_score, similarity, tokenize,
)

from .conftest import signal


class TestSimilarity:
    def test_identical_terms_match(self):
        assert similarity("Fed cuts rates", "Fed cuts rates") == 1.0

    def test_headline_length_does_not_break_matching(self):
        """Plain Jaccard scores this pair ~0.25 despite it being one story."""
        assert similarity(
            "Nvidia earnings",
            "Nvidia earnings beat estimates as data center revenue surges",
        ) >= 0.9

    def test_single_shared_word_does_not_match(self):
        assert similarity("Nvidia earnings", "Nvidia announces new GPU") < 0.6

    def test_unrelated_terms_do_not_match(self):
        assert similarity("Fed cuts rates", "Postgres 18 released") == 0.0

    def test_stopwords_are_ignored(self):
        assert "the" not in tokenize("the Fed and the rates")

    def test_plural_and_singular_forms_match(self):
        assert similarity("Fed cuts rates", "Fed cut rate") == 1.0

    def test_stemming_preserves_words_ending_in_is_ss_us(self):
        assert "basis" in tokenize("basis points")
        assert "access" in tokenize("access control")

    def test_known_limitation_abbreviations_do_not_merge(self):
        """Documents a real gap rather than pretending it is closed.

        A reader sees one story; lexical overlap sees two. The cost is a
        duplicate topic, which the near-duplicate check usually absorbs.
        Closing this properly needs sentence embeddings.
        """
        assert similarity(
            "Fed cuts rates by 50 basis points", "Fed delivers 50bp cut"
        ) < 0.6


class TestClustering:
    def test_merges_all_phrasings_of_one_story(self):
        clusters = cluster_signals([
            signal("Nvidia earnings", "google_trends"),
            signal("Nvidia earnings beat estimates as revenue surges", "rss"),
            signal("Nvidia earnings report smashes expectations", "hackernews"),
            signal("Nvidia stock jumps after earnings", "reddit"),
            signal("Postgres 18 ships async IO", "rss"),
        ])
        sizes = sorted(len(c) for c in clusters)
        assert sizes == [1, 4]

    def test_empty_input(self):
        assert cluster_signals([]) == []

    def test_blank_terms_are_dropped(self):
        assert cluster_signals([signal("")]) == []


class TestNicheClassification:
    def test_matches_keywords(self, config):
        name, conf = classify_niche(
            "new saas crm pricing for b2b software", config.channel.niches
        )
        assert name == "business-software"
        assert conf > 0

    def test_unmatched_text_is_general(self, config):
        name, conf = classify_niche("zzz nothing here", config.channel.niches)
        assert name == "general"
        assert conf == 0.0


class TestDemand:
    def test_corroboration_raises_demand(self):
        one = demand_score([signal("x", "rss", volume=100)], 0.5)
        many = demand_score(
            [signal("x", s, volume=100) for s in ("rss", "reddit", "hackernews", "google_trends")],
            0.5,
        )
        assert many > one

    def test_bounded_to_unit_interval(self):
        assert 0.0 <= demand_score([signal("x", volume=10**9)], 1.0) <= 1.0


def test_build_topics_assigns_niche_and_demand(config, sample_signals):
    topics = build_topics(sample_signals, config)
    assert topics
    fed = next(t for t in topics if "Fed" in t.term)
    assert fed.niche == "personal-finance"
    assert len(fed.signals) == 3          # all three phrasings merged
    assert len(fed.sources) == 3          # from three distinct sources
    assert topics == sorted(topics, key=lambda t: -t.demand)
