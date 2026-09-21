"""Tests for publish-time planning."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ootube.models import Topic
from ootube.scheduler.slots import SlotPlanner, next_slots

from .conftest import NOW


class TestNextSlots:
    def test_returns_requested_count(self, config):
        assert len(next_slots(config.schedule, 5, now=NOW)) == 5

    def test_slots_land_on_configured_local_hours(self, config):
        tz = ZoneInfo(config.schedule.timezone)
        for slot in next_slots(config.schedule, 6, now=NOW):
            assert slot.astimezone(tz).hour in config.schedule.publish_hours_local

    def test_respects_lead_time(self, config):
        earliest = NOW + timedelta(hours=config.schedule.lead_time_hours)
        assert all(s >= earliest for s in next_slots(config.schedule, 4, now=NOW))

    def test_slots_are_strictly_increasing(self, config):
        slots = next_slots(config.schedule, 6, now=NOW)
        assert slots == sorted(slots)
        assert len(set(slots)) == len(slots)

    def test_avoids_taken_slots(self, config):
        first = next_slots(config.schedule, 1, now=NOW)[0]
        following = next_slots(config.schedule, 2, now=NOW, taken=[first])
        assert first not in following

    def test_honours_minimum_gap(self, config):
        gap = timedelta(hours=config.schedule.min_gap_hours)
        slots = next_slots(config.schedule, 6, now=NOW)
        for a, b in zip(slots, slots[1:]):
            assert b - a >= gap

    def test_zero_count(self, config):
        assert next_slots(config.schedule, 0, now=NOW) == []

    def test_unknown_timezone_falls_back_to_utc(self, config):
        config.schedule.timezone = "Not/AZone"
        assert len(next_slots(config.schedule, 2, now=NOW)) == 2

    def test_works_across_a_dst_boundary(self, config):
        """US DST ends Nov 1 2026; local publish hours must not drift."""
        before = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
        tz = ZoneInfo(config.schedule.timezone)
        slots = next_slots(config.schedule, 8, now=before)
        assert all(
            s.astimezone(tz).hour in config.schedule.publish_hours_local for s in slots
        )


class TestPlanner:
    def test_queue_depth_counts_future_only(self, config, store):
        planner = SlotPlanner(config.schedule, store)
        store.record_published(video_id="v1", topic=Topic(term="a"), title="T", url="u",
                               scheduled_for=NOW + timedelta(days=1))
        store.record_published(video_id="v2", topic=Topic(term="b"), title="T", url="u",
                               scheduled_for=NOW - timedelta(days=1))
        assert planner.queue_depth(NOW) == 1

    def test_backfills_up_to_daily_cap(self, config, store):
        planner = SlotPlanner(config.schedule, store)
        assert planner.videos_needed(NOW) == config.schedule.videos_per_day

    def test_stops_when_queue_is_full(self, config, store):
        planner = SlotPlanner(config.schedule, store)
        target = config.schedule.videos_per_day * config.schedule.max_queue_days
        for i in range(target):
            store.record_published(video_id=f"v{i}", topic=Topic(term=f"t{i}"),
                                   title="T", url="u",
                                   scheduled_for=NOW + timedelta(hours=i + 1))
        assert planner.videos_needed(NOW) == 0

    def test_plan_avoids_double_booking(self, config, store):
        planner = SlotPlanner(config.schedule, store)
        first = planner.plan(1, now=NOW)[0]
        store.record_published(video_id="v1", topic=Topic(term="a"), title="T", url="u",
                               scheduled_for=first)
        assert first not in planner.plan(2, now=NOW)
