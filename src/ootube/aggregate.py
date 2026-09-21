"""Merge raw signals into candidate topics.

Five sources reporting the same story produce five signals with different
wording. Treating them as five topics would waste a publishing slot on
duplicates and understate real demand, so signals are clustered by token
overlap and a topic's demand rises with the number of independent sources
that corroborate it.
"""

from __future__ import annotations

import re
from typing import Iterable

from .config import Config, NicheConfig
from .models import Topic, TrendSignal

_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "of", "to", "in", "on", "for", "with",
    "is", "are", "was", "were", "be", "been", "it", "its", "this", "that",
    "as", "at", "by", "from", "how", "what", "why", "you", "your", "we",
    "new", "now", "says", "said", "after", "over", "into", "more", "than",
    "will", "can", "has", "have", "just", "about", "up", "out", "not",
}

_TOKEN_RE = re.compile(r"[a-z0-9']+")


def _stem(token: str) -> str:
    """Strip a trailing plural/third-person 's'.

    Enough to merge "Fed cuts rates" with "Fed cut rate" without pulling in a
    real stemmer. Words ending in -ss, -is, -us keep their s ("basis" must not
    become "basi").
    """
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "is", "us")):
        return token[:-1]
    return token


def tokenize(text: str) -> set[str]:
    tokens = _TOKEN_RE.findall((text or "").lower())
    return {_stem(t) for t in tokens if t not in _STOPWORDS and len(t) > 1}


def similarity(a: str, b: str) -> float:
    """Token overlap between two headlines, in [0, 1].

    Plain Jaccard is wrong here because headline lengths differ wildly:
    "Nvidia earnings" against "Nvidia earnings beat estimates as data center
    revenue surges" scores 0.25 despite being the same story, purely because
    the publisher wrote a longer headline. Containment (overlap / smaller set)
    handles that, guarded by a minimum of two shared tokens so a single common
    word like "Nvidia" cannot merge two unrelated stories.
    """
    ta, tb = tokenize(a), tokenize(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    if not inter:
        return 0.0
    jaccard = inter / len(ta | tb)
    if inter < 2:
        return jaccard
    containment = inter / min(len(ta), len(tb))
    return max(jaccard, containment)


def classify_niche(text: str, niches: list[NicheConfig]) -> tuple[str, float]:
    """Pick the best-matching niche by keyword hits.

    Returns ``(niche_name, confidence)``. Confidence is used downstream to
    discount topics that only barely belong to a monetizable vertical.
    """
    blob = (text or "").lower()
    best_name, best_hits = "", 0.0
    for niche in niches:
        hits = 0.0
        for keyword in niche.keywords:
            kw = keyword.lower()
            if kw in blob:
                # Multi-word keyword matches are stronger evidence.
                hits += 1.0 + 0.5 * kw.count(" ")
        hits *= niche.weight
        if hits > best_hits:
            best_name, best_hits = niche.name, hits
    if not best_name:
        return "general", 0.0
    return best_name, min(1.0, best_hits / 3.0)


def _pick_title(signals: list[TrendSignal]) -> str:
    """Use the shortest clear title in the cluster as the topic term.

    Short titles from Google Trends are usually the bare entity ("Nvidia
    earnings"), which makes a better video subject than a publisher's
    headline, but a one-word term is too thin to write against.
    """
    candidates = sorted(signals, key=lambda s: (len(s.term), -s.volume))
    for signal in candidates:
        if len(tokenize(signal.term)) >= 2:
            return signal.term.strip()
    return candidates[0].term.strip()


def cluster_signals(
    signals: Iterable[TrendSignal], threshold: float = 0.6
) -> list[list[TrendSignal]]:
    """Greedy single-pass clustering.

    Known limitation: this is lexical, so it merges rephrasings but not
    re-wordings. "Fed cuts rates by 50 basis points" and "Fed delivers 50bp
    cut" are the same story to a reader and different stories here, because
    they share only one significant token. The cost is a duplicate topic
    rather than a wrong one, and the near-duplicate check in
    ``TopicSelector.filter_topic`` catches most of those before they reach a
    second video. Swapping in sentence embeddings would close the gap.

    Each candidate is compared against every member of an existing cluster
    rather than one representative headline. Comparing against a single
    representative loses matches: once a long publisher headline becomes the
    representative, a third phrasing of the same story no longer contains
    enough of it to match, and the story fragments into separate topics.
    """
    clusters: list[list[TrendSignal]] = []
    for signal in signals:
        if not signal.term.strip():
            continue
        best_idx, best_score = -1, 0.0
        for idx, cluster in enumerate(clusters):
            score = max(similarity(signal.term, member.term) for member in cluster)
            if score > best_score:
                best_idx, best_score = idx, score
        if best_score >= threshold and best_idx >= 0:
            clusters[best_idx].append(signal)
        else:
            clusters.append([signal])
    return clusters


def build_topics(signals: Iterable[TrendSignal], config: Config) -> list[Topic]:
    """Cluster signals, then attach niche and demand to each cluster."""
    signals = [s for s in signals if s.term and s.term.strip()]
    clusters = cluster_signals(signals)

    topics: list[Topic] = []
    for cluster in clusters:
        term = _pick_title(cluster)
        blob = " ".join([term] + [s.summary for s in cluster])
        niche, confidence = classify_niche(blob, config.channel.niches)

        topic = Topic(term=term, niche=niche, signals=list(cluster))
        topic.demand = demand_score(cluster, confidence)
        topic.evidence = [
            {
                "source": s.source,
                "url": s.url,
                "title": s.term,
                "event_at": s.event_at.isoformat() if s.event_at else None,
            }
            for s in sorted(cluster, key=lambda x: -x.volume)[:6]
        ]
        topics.append(topic)

    return sorted(topics, key=lambda t: -t.demand)


def demand_score(cluster: list[TrendSignal], niche_confidence: float) -> float:
    """Normalized demand in roughly [0, 1].

    Corroboration is weighted heavily: one Reddit post is noise, while the same
    story on Reddit, Hacker News and two news feeds is a real event.
    """
    import math

    raw_volume = sum(s.volume for s in cluster)
    # Log compression keeps one viral outlier from dominating the ranking.
    volume_component = math.log10(1.0 + raw_volume) / 6.0

    distinct_sources = len({s.source for s in cluster})
    corroboration = min(1.0, (distinct_sources - 1) / 3.0)

    score = 0.5 * min(1.0, volume_component) + 0.35 * corroboration + 0.15 * niche_confidence
    return round(min(1.0, score), 4)
