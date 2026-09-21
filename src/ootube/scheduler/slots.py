"""Publish-time planning.

Upload time and publish time are decoupled on purpose. The bot may run once a
day in CI at whatever minute the cron fires, but the channel should publish at
the same local hours every day: consistency is what trains the algorithm and
the audience. So videos upload private with a ``publishAt`` in the future.

Slots are computed in the channel's local timezone so daylight-saving shifts
do not silently move the schedule by an hour.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None  # type: ignore[assignment]

from ..config import ScheduleConfig
from ..models import utcnow

log = logging.getLogger(__name__)


def _tz(name: str):
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - bad tz name must not break scheduling
        log.warning("unknown timezone %r; falling back to UTC", name)
        return timezone.utc


def next_slots(
    config: ScheduleConfig,
    count: int,
    *,
    now: datetime | None = None,
    taken: list[datetime] | None = None,
) -> list[datetime]:
    """Return the next ``count`` free publish times, in UTC.

    Respects the configured local publish hours, a minimum gap between
    videos, a lead time (YouTube rejects a ``publishAt`` in the past, and a
    slot moments away risks the upload not finishing first), and any slots
    already claimed by scheduled videos.
    """
    if count <= 0:
        return []

    now = now or utcnow()
    tz = _tz(config.timezone)
    local_now = now.astimezone(tz)
    earliest = now + timedelta(hours=config.lead_time_hours)

    taken_list = sorted(taken or [])
    hours = sorted(set(config.publish_hours_local)) or [9]

    slots: list[datetime] = []
    day_offset = 0
    while len(slots) < count and day_offset <= config.max_queue_days + 2:
        day = (local_now + timedelta(days=day_offset)).date()
        for hour in hours:
            if len(slots) >= count:
                break
            local_slot = datetime(
                day.year, day.month, day.day, hour, 0, 0, tzinfo=tz
            )
            slot = local_slot.astimezone(timezone.utc)

            if slot < earliest:
                continue

            gap = timedelta(hours=config.min_gap_hours)
            if any(abs((slot - t).total_seconds()) < gap.total_seconds()
                   for t in taken_list + slots):
                continue

            slots.append(slot)
        day_offset += 1

    return slots


class SlotPlanner:
    """Stateful planner that avoids double-booking already-scheduled videos."""

    def __init__(self, config: ScheduleConfig, store=None):
        self.config = config
        self.store = store

    def plan(self, count: int, now: datetime | None = None) -> list[datetime]:
        now = now or utcnow()
        taken: list[datetime] = []
        if self.store is not None:
            taken = self.store.scheduled_after(now)
        return next_slots(self.config, count, now=now, taken=taken)

    def queue_depth(self, now: datetime | None = None) -> int:
        """How many videos are already scheduled to publish in the future."""
        if self.store is None:
            return 0
        return len(self.store.scheduled_after(now or utcnow()))

    def videos_needed(self, now: datetime | None = None) -> int:
        """How many videos this run should produce to keep the queue full.

        Keeping a buffer is what makes the channel resilient: if a run fails
        or an API key expires, already-scheduled videos keep publishing while
        the problem gets fixed.
        """
        now = now or utcnow()
        target = self.config.videos_per_day * self.config.max_queue_days
        if not self.config.backfill_when_short:
            return self.config.videos_per_day
        return max(0, min(self.config.videos_per_day, target - self.queue_depth(now)))
