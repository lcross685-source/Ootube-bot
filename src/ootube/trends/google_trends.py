"""Google Trends daily trending searches.

The realtime RSS endpoint needs no API key and no quota, which makes it the
cheapest high-signal source available. It reports what people are searching
*right now*, so ``observed_at`` is the fetch time; ``event_at`` comes from the
news articles Google attaches to each spike.
"""

from __future__ import annotations

from ..feeds import parse_feed, parse_traffic
from ..http import get
from ..models import TrendSignal, utcnow
from .base import TrendSource

ENDPOINT = "https://trends.google.com/trending/rss"


class GoogleTrendsSource(TrendSource):
    name = "google_trends"

    def _fetch(self) -> list[TrendSignal]:
        geo = self.settings.get("geo", "US")
        resp = get(ENDPOINT, params={"geo": geo})
        if resp is None:
            return []

        now = utcnow()
        signals: list[TrendSignal] = []
        for item in parse_feed(resp.text, max_items=int(self.settings.get("limit", 40))):
            extra = item.get("extra", {})
            signals.append(
                TrendSignal(
                    source=self.name,
                    term=item["title"],
                    url=item.get("link", ""),
                    observed_at=now,
                    # A search spike is happening now, but the story behind it
                    # may be older - prefer the attached article date.
                    event_at=item.get("published") or now,
                    volume=parse_traffic(extra.get("approx_traffic")),
                    region=geo,
                    summary=item.get("summary", ""),
                    raw=extra,
                )
            )
        return signals
