"""Trend discovery sources."""

from .base import TrendSource, registry
from .google_trends import GoogleTrendsSource
from .hackernews import HackerNewsSource
from .rss_news import RssNewsSource
from .reddit import RedditSource
from .youtube_charts import YouTubeChartsSource

__all__ = [
    "TrendSource",
    "registry",
    "GoogleTrendsSource",
    "HackerNewsSource",
    "RssNewsSource",
    "RedditSource",
    "YouTubeChartsSource",
]
