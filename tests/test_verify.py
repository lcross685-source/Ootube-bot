"""Tests for script verification - the gate that fails closed on bad scripts."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from ootube.models import Topic
from ootube.script.verify import ScriptVerifier
from ootube.script.writer import ScriptGenerationError, ScriptWriter

from .conftest import NOW


@pytest.fixture
def writer(config):
    return ScriptWriter(config)


@pytest.fixture
def verifier(config):
    return ScriptVerifier(config, known_generations={"galaxy s": 25.0})


def payload(**overrides):
    base = {
        "title": "Fed cuts rates: what changes for your mortgage",
        "hook": "The Fed moved this morning and the details matter.",
        "sections": [{"heading": "a", "voiceover": " ".join(["word"] * 500)}],
        "original_analysis": (
            "The dot plot implies two more cuts than futures price, "
            "which is the part nobody is covering."
        ),
        "claims": [
            {"text": f"claim {i}", "source_url": f"https://example.com/{i}",
             "as_of": NOW.strftime("%Y-%m-%d")}
            for i in range(3)
        ],
        "description": "d",
        "tags": ["fed"],
    }
    base.update(overrides)
    return json.dumps(base)


class TestParsing:
    def test_parses_plain_json(self, writer):
        assert writer.parse(payload(), Topic(term="t")).title.startswith("Fed cuts")

    def test_tolerates_markdown_fence(self, writer):
        raw = "```json\n" + payload() + "\n```"
        assert writer.parse(raw, Topic(term="t")).sections

    def test_tolerates_preamble_text(self, writer):
        raw = "Here is the script:\n" + payload()
        assert writer.parse(raw, Topic(term="t")).sections

    def test_rejects_unparseable(self, writer):
        with pytest.raises(ScriptGenerationError):
            writer.parse("not json at all", Topic(term="t"))

    def test_rejects_script_with_no_sections(self, writer):
        with pytest.raises(ScriptGenerationError):
            writer.parse(payload(sections=[]), Topic(term="t"))

    def test_falls_back_to_topic_term_for_missing_title(self, writer):
        assert writer.parse(payload(title=""), Topic(term="Fallback")).title == "Fallback"


class TestVerification:
    def test_accepts_a_good_script(self, writer, verifier):
        assert verifier.verify(writer.parse(payload(), Topic(term="t")), now=NOW).ok

    @pytest.mark.parametrize("phrase", ["In today's video", "Let's dive in", "buckle up"])
    def test_rejects_templated_filler(self, writer, verifier, phrase):
        script = writer.parse(payload(hook=f"{phrase} we cover the Fed."), Topic(term="t"))
        result = verifier.verify(script, now=NOW)
        assert not result.ok
        assert any("filler" in e for e in result.errors)

    def test_rejects_stale_sourced_claim(self, writer, verifier):
        old = (NOW - timedelta(days=200)).strftime("%Y-%m-%d")
        script = writer.parse(
            payload(claims=[{"text": "c", "source_url": "https://e.com", "as_of": old}] * 3),
            Topic(term="t"),
        )
        assert not verifier.verify(script, now=NOW).ok

    def test_rejects_outdated_year_in_prose(self, writer, verifier):
        script = writer.parse(
            payload(sections=[{"heading": "a",
                               "voiceover": "Back in 2021 things were different. "
                                            + " ".join(["word"] * 500)}]),
            Topic(term="t"),
        )
        result = verifier.verify(script, now=NOW)
        assert not result.ok
        assert any("2021" in e for e in result.errors)

    def test_rejects_superseded_product(self, writer, verifier):
        script = writer.parse(
            payload(sections=[{"heading": "a",
                               "voiceover": "The Galaxy S23 is the current flagship. "
                                            + " ".join(["word"] * 500)}]),
            Topic(term="t"),
        )
        result = verifier.verify(script, now=NOW)
        assert not result.ok
        assert any("S23" in e for e in result.errors)

    def test_rejects_missing_original_analysis(self, writer, verifier):
        script = writer.parse(payload(original_analysis="Big deal."), Topic(term="t"))
        assert not verifier.verify(script, now=NOW).ok

    def test_rejects_too_short(self, writer, verifier):
        script = writer.parse(
            payload(sections=[{"heading": "a", "voiceover": "too short"}]), Topic(term="t")
        )
        assert not verifier.verify(script, now=NOW).ok

    def test_rejects_unsourced_claims(self, writer, verifier):
        script = writer.parse(
            payload(claims=[{"text": "c", "source_url": "", "as_of": ""}]), Topic(term="t")
        )
        assert not verifier.verify(script, now=NOW).ok

    def test_rejects_future_dated_claim(self, writer, verifier):
        future = (NOW + timedelta(days=30)).strftime("%Y-%m-%d")
        script = writer.parse(
            payload(claims=[{"text": "c", "source_url": "https://e.com", "as_of": future}] * 3),
            Topic(term="t"),
        )
        assert not verifier.verify(script, now=NOW).ok

    def test_rejects_overlong_title(self, writer, verifier):
        script = writer.parse(payload(title="x " * 80), Topic(term="t"))
        assert not verifier.verify(script, now=NOW).ok

    def test_warns_but_passes_on_hype(self, writer, verifier):
        script = writer.parse(
            payload(sections=[{"heading": "a",
                               "voiceover": "This is revolutionary and insane. "
                                            + " ".join(["word"] * 500)}]),
            Topic(term="t"),
        )
        result = verifier.verify(script, now=NOW)
        assert result.ok
        assert result.warnings
