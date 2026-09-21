"""Per-niche news feeds.

Google Trends tells you what is popular; these feeds tell you what actually
happened and when. They are the primary source of ``event_at``, which is the
field the freshness gate leans on hardest.
"""

from __future__ import annotations

from ..feeds import parse_feed
from ..http import fetch_many, get
from ..models import TrendSignal, utcnow
from .base import TrendSource


class RssNewsSource(TrendSource):
    name = "rss"

    def _fetch(self) -> list[TrendSignal]:
        max_items = int(self.settings.get("max_items_per_feed", 20))
        extra_feeds = list(self.settings.get("feeds", []) or [])

        feeds: list[tuple[str, str]] = [(url, "") for url in extra_feeds]
        for niche in self.config.channel.niches:
            feeds.extend((url, niche.name) for url in niche.rss_feeds)

        # Dedupe feeds shared between niches (several list The Verge).
        unique: list[tuple[str, str]] = []
        seen_urls: set[str] = set()
        for url, niche_name in feeds:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            unique.append((url, niche_name))

        now = utcnow()

        def fetch_feed(entry: tuple[str, str]) -> list[TrendSignal]:
            url, niche_name = entry
            resp = get(url)
            if resp is None:
                return []
            return [
                TrendSignal(
                    source=self.name,
                    term=item["title"],
                    url=item.get("link", ""),
                    observed_at=now,
                    event_at=item.get("published"),
                    # News items have no volume of their own; demand comes
                    # from corroboration by other sources at merge time.
                    volume=float(self.settings.get("base_volume", 50)),
                    summary=item.get("summary", ""),
                    raw={"feed": url, "niche_hint": niche_name},
                )
                for item in parse_feed(resp.text, max_items=max_items)
            ]

        batches = fetch_many(unique, fetch_feed, workers=8)
        return [sig for batch in batches for sig in batch]
