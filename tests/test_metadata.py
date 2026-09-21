"""Tests for title, description, tags and the API request body."""

from __future__ import annotations

from datetime import timedelta

from ootube.models import Claim, Script, ScriptSection, Topic
from ootube.publish.metadata import (
    MAX_TAG_CHARS, build_chapters, build_description, build_publish_plan,
    clamp_tags, clamp_title,
)
from ootube.publish.youtube import YouTubeClient

from .conftest import NOW


def make_script(sections=3, claims=2):
    return Script(
        topic_key="fed-cuts",
        title="Fed cuts rates by 50 basis points",
        hook=" ".join(["hook"] * 40),
        sections=[
            ScriptSection(f"section {i}", " ".join(["word"] * 120),
                          on_screen_text=f"Point {i}")
            for i in range(sections)
        ],
        claims=[
            Claim(f"claim {i}", f"https://example.com/{i}", NOW - timedelta(days=i))
            for i in range(claims)
        ],
        description="What changed and why it matters.",
        original_analysis="The second-order effect lands on refinancing volume.",
        tags=["fed", "rates"],
    )


class TestTitle:
    def test_short_title_untouched(self):
        assert clamp_title("Fed cuts rates") == "Fed cuts rates"

    def test_long_title_clamped_at_word_boundary(self):
        out = clamp_title("word " * 60)
        assert len(out) <= 100
        assert not out.rstrip("…").endswith("wor")

    def test_whitespace_normalised(self):
        assert clamp_title("  Fed   cuts \n rates ") == "Fed cuts rates"


class TestTags:
    def test_respects_aggregate_budget(self):
        tags = clamp_tags([f"tag-number-{i}" for i in range(200)])
        assert len(",".join(tags)) <= MAX_TAG_CHARS

    def test_deduplicates_and_lowercases(self):
        assert clamp_tags(["Fed", "fed", "FED"]) == ["fed"]

    def test_drops_overlong_and_empty(self):
        assert clamp_tags(["x" * 50, "", "  ", "ok"]) == ["ok"]


class TestChapters:
    def test_first_chapter_starts_at_zero(self):
        chapters = build_chapters(make_script(), 600)
        assert chapters[0][0] == 0.0

    def test_omitted_when_too_few_sections(self):
        """YouTube ignores a chapter list with fewer than three entries."""
        assert build_chapters(make_script(sections=1), 600) == []

    def test_omitted_when_chapters_too_short(self):
        assert build_chapters(make_script(), total_duration=5) == []

    def test_chapters_are_increasing(self):
        starts = [c[0] for c in build_chapters(make_script(sections=4), 900)]
        assert starts == sorted(starts)


class TestDescription:
    def test_includes_dated_sources(self, config):
        desc = build_description(make_script(), Topic(term="Fed cuts"), config,
                                 total_duration=600, now=NOW)
        assert "Sources:" in desc
        assert "https://example.com/0" in desc
        assert NOW.strftime("%Y-%m-%d") in desc

    def test_includes_synthetic_media_disclosure(self, config):
        desc = build_description(make_script(), Topic(term="Fed cuts"), config,
                                 total_duration=600, now=NOW)
        assert "AI-assisted" in desc

    def test_includes_chapters(self, config):
        desc = build_description(make_script(), Topic(term="Fed cuts"), config,
                                 total_duration=600, now=NOW)
        assert "Chapters:" in desc and "0:00" in desc

    def test_respects_length_limit(self, config):
        script = make_script(sections=40, claims=60)
        desc = build_description(script, Topic(term="x"), config,
                                 total_duration=6000, now=NOW)
        assert len(desc) <= 5000

    def test_includes_niche_affiliate_block(self, config):
        desc = build_description(make_script(),
                                 Topic(term="Fed cuts", niche="personal-finance"),
                                 config, total_duration=600, now=NOW)
        assert "Resources referenced" in desc


class TestPublishPlan:
    def test_uploads_private_for_scheduling(self, config):
        plan = build_publish_plan(make_script(), Topic(term="Fed cuts"), config,
                                  NOW + timedelta(hours=6), total_duration=600, now=NOW)
        assert plan.privacy_status == "private"
        assert plan.publish_at > NOW

    def test_uses_niche_category(self, config):
        plan = build_publish_plan(make_script(),
                                  Topic(term="Fed cuts", niche="personal-finance"),
                                  config, NOW + timedelta(hours=6), now=NOW)
        assert plan.category_id == "25"


class TestApiBody:
    def test_sets_synthetic_media_and_publish_at(self, config):
        plan = build_publish_plan(make_script(), Topic(term="Fed cuts"), config,
                                  NOW + timedelta(hours=6), total_duration=600, now=NOW)
        body = YouTubeClient(dry_run=True)._body(plan)
        status = body["status"]
        assert status["containsSyntheticMedia"] is True
        assert status["selfDeclaredMadeForKids"] is False
        assert status["privacyStatus"] == "private"
        assert status["publishAt"].endswith("Z")

    def test_no_publish_at_when_public(self, config):
        plan = build_publish_plan(make_script(), Topic(term="Fed cuts"), config,
                                  NOW + timedelta(hours=6), now=NOW)
        plan.privacy_status = "public"
        assert "publishAt" not in YouTubeClient(dry_run=True)._body(plan)["status"]
