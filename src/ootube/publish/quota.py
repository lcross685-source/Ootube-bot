"""YouTube Data API quota accounting.

Quota exhaustion is the classic silent failure for an unattended uploader:
calls start returning ``quotaExceeded``, the bot logs an error nobody reads,
and the channel goes quiet for a day. So spend is tracked locally and checked
*before* each call, leaving a reserve so a partially-completed video can still
finish uploading rather than stranding a rendered file.

Costs come from config because Google has changed them - uploads historically
cost 1600 units from the shared daily pool and are now billed per call against
a separate cap.
"""

from __future__ import annotations

import logging

from ..config import QuotaConfig
from ..store import Store

log = logging.getLogger(__name__)


class QuotaExceeded(RuntimeError):
    pass


class QuotaManager:
    def __init__(self, config: QuotaConfig, store: Store):
        self.config = config
        self.store = store

    def usage(self) -> dict[str, int]:
        return self.store.quota_today()

    def remaining_units(self) -> int:
        used = self.usage()["units"]
        return max(0, self.config.daily_units - self.config.reserve_units - used)

    def remaining_uploads(self) -> int:
        return max(0, self.config.upload_calls_per_day - self.usage()["upload_calls"])

    def remaining_searches(self) -> int:
        return max(0, self.config.search_calls_per_day - self.usage()["search_calls"])

    # ------------------------------------------------------------------
    def check(self, units: int = 0, uploads: int = 0, searches: int = 0) -> None:
        """Raise :class:`QuotaExceeded` if a call would not fit."""
        if uploads and self.remaining_uploads() < uploads:
            raise QuotaExceeded(
                f"upload cap reached: {self.usage()['upload_calls']}/"
                f"{self.config.upload_calls_per_day} calls used today"
            )
        if searches and self.remaining_searches() < searches:
            raise QuotaExceeded(
                f"search cap reached: {self.usage()['search_calls']}/"
                f"{self.config.search_calls_per_day} calls used today"
            )
        if units and self.remaining_units() < units:
            raise QuotaExceeded(
                f"unit budget exhausted: need {units}, "
                f"{self.remaining_units()} left (reserve {self.config.reserve_units})"
            )

    def charge(self, units: int = 0, uploads: int = 0, searches: int = 0) -> None:
        self.store.add_quota(units=units, upload_calls=uploads, search_calls=searches)

    # ------------------------------------------------------------------
    def cost_of_publish(self, *, thumbnail: bool = True, playlist: bool = True) -> int:
        cfg = self.config
        cost = cfg.cost_videos_insert
        if thumbnail:
            cost += cfg.cost_thumbnails_set
        if playlist:
            cost += cfg.cost_playlist_items_insert
        return cost

    def can_publish(self, *, thumbnail: bool = True, playlist: bool = True) -> bool:
        try:
            self.check(
                units=self.cost_of_publish(thumbnail=thumbnail, playlist=playlist),
                uploads=1,
            )
            return True
        except QuotaExceeded:
            return False

    def max_publishes_today(self, *, thumbnail: bool = True, playlist: bool = True) -> int:
        per = max(1, self.cost_of_publish(thumbnail=thumbnail, playlist=playlist))
        return min(self.remaining_uploads(), self.remaining_units() // per)
