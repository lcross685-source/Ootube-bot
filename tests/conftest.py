"""Shared test fixtures.

The whole suite runs offline. Network sources are replaced with a recorded
fixture source, script generation with a canned writer, and uploads with the
client's dry-run mode - so CI verifies the pipeline's logic rather than the
availability of Reddit.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ootube.config import load_config
from ootube.models import Script, Topic, TrendSignal
from ootube.script.writer import ScriptWriter
from ootube.store import Store
from ootube.trends.base import TrendSource

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 21, 13, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def config(tmp_path):
    cfg = load_config()
    cfg.db_path = str(tmp_path / "test.db")
    cfg.media.output_dir = str(tmp_path / "out")
    cfg.media.tts_provider = "silent"
    # Small frames keep the render test to a few seconds.
    cfg.media.width, cfg.media.height, cfg.media.fps = 640, 360, 12
    cfg.media.target_duration_s = 120
    cfg.media.min_duration_s = 30
    return cfg


@pytest.fixture
def store(config):
    s = Store(config.db_path)
    yield s
    s.close()


def signal(term, source="rss", *, hours_ago=2, volume=100.0, summary="", event=True):
    return TrendSignal(
        source=source,
        term=term,
        url=f"https://example.com/{abs(hash(term)) % 10000}",
        observed_at=NOW,
        event_at=NOW - timedelta(hours=hours_ago) if event else None,
        volume=volume,
        summary=summary,
    )


@pytest.fixture
def sample_signals() -> list[TrendSignal]:
    """A realistic mixed batch: corroborated news, singletons, and stale traps."""
    return [
        signal("Fed cuts rates by 50 basis points", "google_trends", volume=180000,
               summary="interest rates inflation report"),
        signal("Fed cuts rates by 50 basis points as inflation cools", "rss",
               summary="interest rates stock market"),
        signal("Fed cut rates by 50 basis points, larger than expected", "hackernews",
               volume=600, summary="interest rates"),
        signal("Salesforce launches new AI agent pricing tier", "rss", volume=90,
               summary="saas b2b software enterprise ai crm"),
        signal("Salesforce AI agent pricing draws backlash", "reddit", volume=800,
               summary="saas enterprise ai"),
        signal("Postgres 18 ships async IO", "hackernews", volume=700,
               summary="database developer tools"),
        # Traps the freshness gate must catch:
        signal("Samsung Galaxy S23 launch: everything you need to know", "rss",
               summary="smartphone launch"),
        signal("Best laptops of 2023", "rss", summary="laptop review"),
        signal("A look back at the crypto crash", "rss", summary="stock market"),
        signal("Ancient news nobody wants", "rss", hours_ago=24 * 400,
               summary="smartphone launch"),
        # Establishes that the S25 generation exists, which is what makes the
        # S23 topic above rejectable.
        signal("Samsung Galaxy S25 Ultra camera update rolls out", "rss",
               summary="smartphone launch chipset"),
    ]


class FixtureSource(TrendSource):
    """A trend source that replays a recorded batch instead of calling out."""

    name = "fixture"
    _signals: list[TrendSignal] = []

    @classmethod
    def load(cls, signals: list[TrendSignal]) -> None:
        cls._signals = list(signals)

    def _fetch(self) -> list[TrendSignal]:
        return list(self._signals)


@pytest.fixture
def fixture_source(sample_signals):
    FixtureSource.load(sample_signals)
    return FixtureSource


class StubScriptWriter(ScriptWriter):
    """Returns a valid canned script without calling the Claude API."""

    def write(self, topic: Topic, now: datetime | None = None) -> Script:
        now = now or NOW
        body = " ".join(["analysis"] * 170)
        payload = {
            "title": f"{topic.term}: what actually changes",
            "hook": "The decision landed this morning and the details matter more than the headline.",
            "thumbnail_text": "WHAT CHANGES",
            "sections": [
                {"heading": "what happened", "voiceover": body,
                 "b_roll_query": "office skyline", "on_screen_text": "The decision"},
                {"heading": "who it hits", "voiceover": body,
                 "b_roll_query": "city street", "on_screen_text": "Who it hits"},
                {"heading": "my read", "voiceover": body,
                 "b_roll_query": "data charts", "on_screen_text": "My read"},
            ],
            "original_analysis": (
                "The consensus read misses that the second-order effect lands on "
                "refinancing volume rather than on new originations, which is where "
                "the revenue actually moves."
            ),
            "claims": [
                {"text": "The change was announced today.",
                 "source_url": "https://example.com/a",
                 "as_of": now.strftime("%Y-%m-%d")},
                {"text": "Two independent outlets corroborated it.",
                 "source_url": "https://example.com/b",
                 "as_of": now.strftime("%Y-%m-%d")},
                {"text": "The effective date is next quarter.",
                 "source_url": "https://example.com/c",
                 "as_of": (now - timedelta(days=1)).strftime("%Y-%m-%d")},
            ],
            "description": "A short, specific summary of what changed and why it matters.",
            "tags": ["news", "analysis", topic.niche],
        }
        return self.parse(json.dumps(payload), topic, model="stub")


@pytest.fixture
def stub_writer(config):
    return StubScriptWriter(config)
