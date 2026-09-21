"""Revenue-weighted topic scoring and selection.

The channel's goal is revenue, not views, and those diverge sharply. A
gaming video at 100k views can earn less than a B2B software video at 8k
views because RPM differs by an order of magnitude between verticals. So the
scorer optimises expected dollars:

    expected_revenue = projected_views x monetized_rate x (RPM / 1000)

Everything else - demand, freshness, saturation - feeds ``projected_views``.
RPM enters the ranking directly, which is what makes the bot prefer a modest
finance story over a bigger but cheaper entertainment one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from .config import Config
from .freshness import FreshnessGate, Verdict
from .models import Topic, utcnow
from .store import Store


@dataclass
class ScoredTopic:
    topic: Topic
    score: float
    projected_views: int
    expected_revenue_usd: float
    freshness: float
    rpm: float
    saturation: float


def estimate_saturation(topic: Topic, now: datetime | None = None) -> float:
    """Guess how crowded this topic already is on YouTube, in [0, 1].

    Deliberately inferred rather than measured: measuring it properly means
    ``search.list`` at 100 quota units per call against a 100-call daily cap,
    which would consume the entire search budget on ranking. These proxies are
    free and directionally right.
    """
    now = now or utcnow()
    saturation = 0.25  # nothing is truly uncontested

    # Already on the most-popular chart means big channels have covered it.
    if "youtube_charts" in topic.sources:
        saturation += 0.35

    # Every hour after a story breaks, more creators publish on it.
    newest_event = topic.newest_event()
    if newest_event is not None:
        age_hours = max(0.0, (now - newest_event).total_seconds() / 3600.0)
        saturation += min(0.3, age_hours / 72.0 * 0.3)

    # Very broad one- or two-word terms compete with everything.
    from .aggregate import tokenize

    if len(tokenize(topic.term)) <= 2:
        saturation += 0.1

    return round(min(1.0, saturation), 4)


def normalize_rpm(rpm: float, reference: float = 25.0) -> float:
    """Map an RPM onto [0, 1] with diminishing returns above the reference."""
    if rpm <= 0:
        return 0.0
    return min(1.0, math.log1p(rpm) / math.log1p(reference))


class TopicSelector:
    """Filters, scores and picks the topics a run will actually produce."""

    def __init__(self, config: Config, store: Store, gate: FreshnessGate | None = None):
        self.config = config
        self.store = store
        self.gate = gate or FreshnessGate(
            config.freshness, known_generations=store.all_generations()
        )

    # ------------------------------------------------------------------
    def score_topic(self, topic: Topic, now: datetime | None = None) -> ScoredTopic:
        now = now or utcnow()
        cfg = self.config.scoring
        niche = self.config.niche(topic.niche)

        freshness = self.gate.score(topic, now)
        saturation = estimate_saturation(topic, now)
        rpm_norm = normalize_rpm(niche.rpm_usd)

        weighted = (
            cfg.weight_demand * topic.demand
            + cfg.weight_rpm * rpm_norm
            + cfg.weight_freshness * freshness
            - cfg.weight_saturation * saturation
        )
        total_weight = (
            cfg.weight_demand + cfg.weight_rpm + cfg.weight_freshness
        ) or 1.0
        score = max(0.0, weighted / total_weight)

        # Projected views scale with demand and decay with saturation.
        projected = (
            cfg.baseline_views
            * (0.4 + 2.2 * topic.demand)
            * (0.5 + 0.8 * freshness)
            * (1.0 - 0.5 * saturation)
            * niche.weight
        )
        projected_views = int(max(0, projected))
        revenue = (
            projected_views * cfg.monetized_playback_rate * (niche.rpm_usd / 1000.0)
        )

        topic.score = round(score, 4)
        topic.saturation = saturation
        topic.projected_views = projected_views
        topic.expected_revenue_usd = round(revenue, 2)

        return ScoredTopic(
            topic=topic,
            score=topic.score,
            projected_views=projected_views,
            expected_revenue_usd=topic.expected_revenue_usd,
            freshness=round(freshness, 4),
            rpm=niche.rpm_usd,
            saturation=saturation,
        )

    # ------------------------------------------------------------------
    def filter_topic(self, topic: Topic, now: datetime | None = None) -> Verdict:
        """Apply dedupe and freshness. Returns the first failing rule."""
        if self.store.is_published(topic.fingerprint):
            return Verdict(False, "already published", "duplicate")

        # Near-duplicate of something recent, even if the wording differs.
        from .aggregate import similarity

        for key in self.store.recent_terms(days=30):
            if similarity(topic.key.replace("-", " "), key.replace("-", " ")) >= 0.75:
                return Verdict(
                    False, f"near-duplicate of recent video '{key}'", "near_duplicate"
                )

        niche = self.config.niche(topic.niche)
        return self.gate.evaluate(topic, evergreen=niche.evergreen, now=now)

    # ------------------------------------------------------------------
    def select(
        self,
        topics: list[Topic],
        limit: int,
        *,
        now: datetime | None = None,
        max_per_niche: int | None = None,
    ) -> tuple[list[ScoredTopic], list[tuple[Topic, Verdict]]]:
        """Return ``(selected, rejected)``.

        Rejections are returned rather than dropped so the operator can see
        *why* a run produced nothing - the most common support question for an
        unattended bot.
        """
        now = now or utcnow()
        self.gate.learn([s for t in topics for s in t.signals])

        scored: list[ScoredTopic] = []
        rejected: list[tuple[Topic, Verdict]] = []

        for topic in topics:
            verdict = self.filter_topic(topic, now=now)
            if not verdict.ok:
                topic.rejected_reason = verdict.reason
                rejected.append((topic, verdict))
                self.store.record_topic(topic, status="rejected", reason=verdict.reason)
                continue
            st = self.score_topic(topic, now=now)
            if st.score < self.config.scoring.min_score:
                verdict = Verdict(
                    False,
                    f"score {st.score:.3f} below minimum {self.config.scoring.min_score}",
                    "low_score",
                )
                topic.rejected_reason = verdict.reason
                rejected.append((topic, verdict))
                self.store.record_topic(topic, status="rejected", reason=verdict.reason)
                continue
            scored.append(st)

        # Rank by expected revenue, not by score: score is a quality signal,
        # dollars are the objective.
        scored.sort(key=lambda s: (-s.expected_revenue_usd, -s.score))

        # Cap per niche so one busy vertical cannot fill the whole schedule and
        # make the channel look like a single-topic feed.
        if max_per_niche is None:
            max_per_niche = max(1, limit // 2) if limit > 2 else limit

        selected: list[ScoredTopic] = []
        per_niche: dict[str, int] = {}
        for st in scored:
            niche = st.topic.niche
            if per_niche.get(niche, 0) >= max_per_niche:
                continue
            selected.append(st)
            per_niche[niche] = per_niche.get(niche, 0) + 1
            if len(selected) >= limit:
                break

        # If diversity capping left slots empty, refill with the best remainder.
        if len(selected) < limit:
            chosen = {id(s.topic) for s in selected}
            for st in scored:
                if id(st.topic) in chosen:
                    continue
                selected.append(st)
                if len(selected) >= limit:
                    break

        for st in selected:
            self.store.record_topic(st.topic, status="selected")

        return selected, rejected
