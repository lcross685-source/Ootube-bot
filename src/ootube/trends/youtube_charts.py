"""YouTube's own most-popular chart.

``videos.list`` with ``chart=mostPopular`` costs a single quota unit, unlike
``search.list`` at 100 units against a 100-call daily cap. It shows what is
already winning on the platform, which is the closest available proxy for
"this format converts here".
"""

from __future__ import annotations

import os

from ..feeds import parse_date
from ..http import get_json
from ..models import TrendSignal, utcnow
from .base import TrendSource

ENDPOINT = "https://www.googleapis.com/youtube/v3/videos"


class YouTubeChartsSource(TrendSource):
    name = "youtube_charts"

    def _fetch(self) -> list[TrendSignal]:
        api_key = os.environ.get("YOUTUBE_API_KEY", "")
        if not api_key:
            return []

        regions = self.settings.get("regions", ["US"]) or ["US"]
        max_results = int(self.settings.get("max_results", 50))
        now = utcnow()
        signals: list[TrendSignal] = []

        for region in regions:
            data = get_json(
                ENDPOINT,
                params={
                    "part": "snippet,statistics",
                    "chart": "mostPopular",
                    "regionCode": region,
                    "maxResults": min(max_results, 50),
                    "key": api_key,
                },
            )
            if not data:
                continue
            for item in data.get("items", []) or []:
                snippet = item.get("snippet", {}) or {}
                stats = item.get("statistics", {}) or {}
                title = (snippet.get("title") or "").strip()
                if not title:
                    continue
                signals.append(
                    TrendSignal(
                        source=self.name,
                        term=title,
                        url=f"https://www.youtube.com/watch?v={item.get('id')}",
                        observed_at=now,
                        event_at=parse_date(snippet.get("publishedAt")),
                        volume=float(stats.get("viewCount", 0) or 0) / 1000.0,
                        region=region,
                        summary=(snippet.get("description") or "")[:500],
                        raw={
                            "channel": snippet.get("channelTitle", ""),
                            "category_id": snippet.get("categoryId", ""),
                            "views": stats.get("viewCount"),
                        },
                    )
                )
        return signals

    def quota_cost(self, regions: int = 1) -> int:
        return self.config.quota.cost_videos_list * max(1, regions)
