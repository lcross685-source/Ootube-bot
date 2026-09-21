"""Reddit rising posts.

Reddit surfaces demand earlier than Google Trends for technical niches: a
framework release trends on r/programming hours before it shows up in general
search. The public JSON listing needs no key.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..http import fetch_many, get_json
from ..models import TrendSignal, utcnow
from .base import TrendSource


class RedditSource(TrendSource):
    name = "reddit"

    def _fetch(self) -> list[TrendSignal]:
        listing = self.settings.get("listing", "rising")
        limit = int(self.settings.get("limit", 25))
        min_score = int(self.settings.get("min_score", 25))

        subreddits: list[str] = []
        for niche in self.config.channel.niches:
            subreddits.extend(niche.subreddits)
        subreddits = list(dict.fromkeys(subreddits))
        if not subreddits:
            return []

        now = utcnow()

        def fetch_sub(sub: str) -> list[TrendSignal]:
            data = get_json(
                f"https://www.reddit.com/r/{sub}/{listing}.json",
                params={"limit": limit},
            )
            if not data:
                return []
            out: list[TrendSignal] = []
            for child in (data.get("data", {}) or {}).get("children", []) or []:
                post = child.get("data", {}) or {}
                if post.get("stickied") or post.get("over_18"):
                    continue
                score = int(post.get("score", 0) or 0)
                if score < min_score:
                    continue
                created = post.get("created_utc")
                event_at = (
                    datetime.fromtimestamp(float(created), tz=timezone.utc)
                    if created
                    else None
                )
                out.append(
                    TrendSignal(
                        source=self.name,
                        term=post.get("title", "").strip(),
                        url="https://www.reddit.com" + post.get("permalink", ""),
                        observed_at=now,
                        event_at=event_at,
                        # Comments indicate argument, which converts to watch
                        # time better than upvotes alone.
                        volume=float(score + 3 * int(post.get("num_comments", 0) or 0)),
                        summary=(post.get("selftext", "") or "")[:500],
                        raw={"subreddit": sub, "score": score},
                    )
                )
            return out

        # Roughly 25 subreddits at a second each is a minute of pure waiting.
        batches = fetch_many(subreddits, fetch_sub, workers=8)
        signals = [sig for batch in batches for sig in batch]
        return [s for s in signals if s.term]
