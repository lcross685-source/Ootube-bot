"""The anti-stale gate.

The failure mode this module exists to prevent: a channel that publishes
"Samsung Galaxy S23 launch - everything you need to know" two years after the
S23 launched. That happens because "is this trending?" and "is this current?"
are different questions, and scrapers only answer the first one.

Four independent rules run, and a topic must pass all of them:

1. **Signal age** - we saw the demand recently.
2. **Event age** - the thing itself happened recently.
3. **Supersession** - no newer generation of the subject exists.
4. **Anchor text** - the wording does not point at an old year or frame the
   subject as history.

Rules 1 and 2 differ in a way that matters: a publisher can run a fresh article
about an ancient phone, which gives a recent signal age and an old event age.
Rule 3 catches the inverse - genuinely new coverage of a product that has since
been replaced, where both dates look fine but the subject is dead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from .config import FreshnessConfig
from .models import Topic, TrendSignal, utcnow

# Product families whose generation number is worth tracking. Each entry is
# matched case-insensitively with flexible spacing, so "Galaxy S23",
# "galaxy s 23" and "GALAXY-S23" all resolve to ("galaxy s", 23).
FAMILY_HINTS: tuple[str, ...] = (
    "galaxy s", "galaxy z fold", "galaxy z flip", "galaxy note", "galaxy tab",
    "iphone", "ipad pro", "ipad air", "apple watch series", "airpods pro",
    "pixel", "oneplus", "xperia", "nothing phone",
    "gpt", "claude opus", "claude sonnet", "claude haiku", "gemini", "llama",
    "mistral", "qwen", "grok", "deepseek",
    "rtx", "gtx", "radeon rx", "ryzen", "core i", "snapdragon", "m",
    "playstation", "xbox series", "steam deck",
    "android", "ios", "ipados", "macos", "windows",
    "python", "java", "php", "angular", "react", "vue", "node", "rust edition",
    "kubernetes", "postgresql", "mysql",
)

# Families where a four-digit generation number is normal, so it must not be
# mistaken for a year.
FOUR_DIGIT_FAMILIES = {"rtx", "gtx", "radeon rx", "ryzen", "snapdragon", "core i"}

_HISTORY_FRAMES = (
    "years ago", "year ago", "back in 20", "retrospective", "throwback",
    "a look back", "looking back", "anniversary of", "remember when",
    "revisited", "in hindsight", "what happened to", "whatever happened",
)

_YEAR_RE = re.compile(r"\b(19[89]\d|20\d{2})\b")
_MONEY_RE = re.compile(r"[$€£]\s*\d")


@dataclass
class Generation:
    family: str
    number: float
    label: str = ""

    def __hash__(self) -> int:
        return hash((self.family, self.number))


@dataclass
class Verdict:
    """Outcome of the gate. ``ok=False`` carries a human-readable reason."""

    ok: bool
    reason: str = ""
    rule: str = ""
    details: dict[str, object] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.ok


def _family_pattern(hint: str) -> re.Pattern[str]:
    parts = [re.escape(p) for p in hint.split()]
    body = r"\s*".join(parts)
    return re.compile(rf"\b{body}\s*-?\s*(\d{{1,4}}(?:\.\d{{1,2}})?)\b", re.IGNORECASE)


_COMPILED_FAMILIES: list[tuple[str, re.Pattern[str]]] = [
    (hint, _family_pattern(hint)) for hint in FAMILY_HINTS
]


def extract_generations(text: str) -> list[Generation]:
    """Pull ``(family, generation)`` pairs out of free text.

    Precision matters more than recall here: a false positive silently blocks a
    publishable topic, so only curated families are matched and year-like
    numbers are rejected unless the family legitimately uses them.
    """
    if not text:
        return []
    found: dict[str, Generation] = {}
    for hint, pattern in _COMPILED_FAMILIES:
        for match in pattern.finditer(text):
            raw = match.group(1)
            try:
                number = float(raw)
            except ValueError:
                continue
            # "iPhone 2026" is a year, not a generation.
            if 1990 <= number <= 2100 and hint not in FOUR_DIGIT_FAMILIES:
                continue
            # Skip a price or percentage that happens to follow a family word.
            start = max(0, match.start() - 2)
            if _MONEY_RE.search(text[start : match.end()]):
                continue
            key = hint.lower()
            if key not in found or number > found[key].number:
                found[key] = Generation(
                    family=key, number=number, label=match.group(0).strip()
                )
    return list(found.values())


def learn_generations(signals: Iterable[TrendSignal]) -> dict[str, float]:
    """Build a family -> newest-generation map from the current signal set.

    This is how the bot finds out an S25 exists without anyone telling it: if
    today's feeds mention one, every older sibling becomes rejectable.
    """
    latest: dict[str, float] = {}
    for signal in signals:
        blob = f"{signal.term} {signal.summary}"
        for gen in extract_generations(blob):
            if gen.number > latest.get(gen.family, 0.0):
                latest[gen.family] = gen.number
    return latest


def stale_year_reference(text: str, now: datetime, max_lag: int) -> int | None:
    """Return the offending year if the text anchors to an outdated one."""
    cutoff = now.year - max_lag
    for match in _YEAR_RE.finditer(text or ""):
        year = int(match.group(1))
        # Future years are fine - "2027 forecast" is forward-looking content.
        if year < cutoff:
            return year
    return None


def history_frame(text: str) -> str | None:
    lowered = (text or "").lower()
    for frame in _HISTORY_FRAMES:
        if frame in lowered:
            return frame
    return None


class FreshnessGate:
    """Applies the freshness rules to a :class:`Topic`."""

    def __init__(
        self,
        config: FreshnessConfig,
        known_generations: dict[str, float] | None = None,
    ):
        self.config = config
        self.known_generations = dict(known_generations or {})

    def learn(self, signals: Iterable[TrendSignal]) -> None:
        """Fold newly observed generations into the known map (forward only)."""
        for family, number in learn_generations(signals).items():
            if number > self.known_generations.get(family, 0.0):
                self.known_generations[family] = number

    def evaluate(
        self, topic: Topic, *, evergreen: bool = False, now: datetime | None = None
    ) -> Verdict:
        now = now or utcnow()
        cfg = self.config
        text = " ".join([topic.term] + [s.summary for s in topic.signals])

        # --- Rule 1: did we see this recently? --------------------------
        newest = topic.newest_observation()
        if newest is None:
            return Verdict(False, "topic has no signals", "no_signal")
        age_hours = (now - newest).total_seconds() / 3600.0
        if age_hours > cfg.max_signal_age_hours:
            return Verdict(
                False,
                f"signal is {age_hours:.1f}h old (limit {cfg.max_signal_age_hours:.0f}h)",
                "signal_age",
                {"age_hours": round(age_hours, 2)},
            )

        # --- Rule 2: did the thing itself happen recently? --------------
        limit_days = (
            cfg.evergreen_max_event_age_days if evergreen else cfg.max_event_age_days
        )
        newest_event = topic.newest_event()
        if newest_event is None:
            if cfg.require_event_date_for_news and not evergreen:
                return Verdict(
                    False,
                    "no dateable event behind this topic",
                    "no_event_date",
                )
        else:
            event_age_days = (now - newest_event).total_seconds() / 86400.0
            if event_age_days > limit_days:
                return Verdict(
                    False,
                    f"event is {event_age_days:.1f}d old (limit {limit_days:.0f}d)",
                    "event_age",
                    {"event_age_days": round(event_age_days, 2)},
                )
            # A future-dated feed item means a broken upstream clock.
            if event_age_days < -2:
                return Verdict(
                    False, "event date is in the future; source clock suspect", "bad_date"
                )

        # --- Rule 3: has the subject been replaced? ---------------------
        if cfg.reject_superseded_generations:
            for gen in extract_generations(text):
                newest_known = self.known_generations.get(gen.family)
                if newest_known is not None and newest_known > gen.number:
                    return Verdict(
                        False,
                        (
                            f"{gen.label} is superseded; "
                            f"{gen.family} is now at {newest_known:g}"
                        ),
                        "superseded",
                        {
                            "family": gen.family,
                            "topic_generation": gen.number,
                            "latest_generation": newest_known,
                        },
                    )

        # --- Rule 4: does the wording point backwards? ------------------
        bad_year = stale_year_reference(topic.term, now, cfg.max_year_reference_lag)
        if bad_year is not None:
            return Verdict(
                False,
                f"title anchors to {bad_year}",
                "stale_year",
                {"year": bad_year},
            )

        frame = history_frame(topic.term)
        if frame:
            return Verdict(False, f"framed as history ('{frame}')", "history_frame")

        for term in cfg.stale_terms:
            if term.lower() in topic.term.lower():
                return Verdict(False, f"matches stale term '{term}'", "stale_term")

        # --- Rule 5: is one flaky source enough? ------------------------
        if not evergreen and len(topic.sources) < cfg.min_sources_for_news:
            return Verdict(
                False,
                f"only {len(topic.sources)} source(s), need {cfg.min_sources_for_news}",
                "thin_sourcing",
            )

        return Verdict(True, "fresh", "pass", {"age_hours": round(age_hours, 2)})

    def score(self, topic: Topic, now: datetime | None = None) -> float:
        """Continuous freshness score in [0, 1] used for ranking.

        Decays linearly with event age so that a six-hour-old story outranks a
        six-day-old one even when both pass the gate.
        """
        now = now or utcnow()
        newest_event = topic.newest_event() or topic.newest_observation()
        if newest_event is None:
            return 0.0
        age_days = max(0.0, (now - newest_event).total_seconds() / 86400.0)
        limit = max(self.config.max_event_age_days, 0.5)
        return max(0.0, min(1.0, 1.0 - (age_days / limit)))
