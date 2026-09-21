"""Hacker News front page via the Algolia search API.

Free, no key, and it carries an explicit ``created_at`` per story, which makes
it one of the few sources where the event date is reliable rather than
inferred.
"""

from __future__ import annotations

from datetime import timedelta

from ..feeds import parse_date
from ..http import get_json
from ..models import TrendSignal, utcnow
from .base import TrendSource

ENDPOINT = "https://hn.algolia.com/api/v1/search_by_date"


class HackerNewsSource(TrendSource):
    name = "hackernews"

    def _fetch(self) -> list[TrendSignal]:
        min_points = int(self.settings.get("min_points", 80))
        window_hours = float(self.settings.get("window_hours", 48))
        now = utcnow()
        since = int((now - timedelta(hours=window_hours)).timestamp())

        data = get_json(
            ENDPOINT,
            params={
                "tags": "story",
                "numericFilters": f"created_at_i>{since},points>{min_points}",
                "hitsPerPage": int(self.settings.get("limit", 50)),
            },
        )
        if not data:
            return []

        signals: list[TrendSignal] = []
        for hit in data.get("hits", []) or []:
            title = (hit.get("title") or "").strip()
            if not title:
                continue
            event_at = parse_date(hit.get("created_at"))
            points = int(hit.get("points", 0) or 0)
            comments = int(hit.get("num_comments", 0) or 0)
            signals.append(
                TrendSignal(
                    source=self.name,
                    term=title,
                    url=hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                    observed_at=now,
                    event_at=event_at,
                    volume=float(points + 2 * comments),
                    summary=(hit.get("story_text") or "")[:500],
                    raw={"points": points, "comments": comments},
                )
            )
        return signals
