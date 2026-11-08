"""Shared report filters and complete local calendar-day buckets."""

from datetime import date, datetime, timedelta, timezone
import unittest
from zoneinfo import ZoneInfo

from test_core import FakeClock, MemoryRepository
from pomonal.application import Service
from pomonal.domain import default_state, new_session
from pomonal.reports import Reports


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.state = default_state()
        self.clock = FakeClock()
        self.reports = Reports(self.state, self.clock)

    def record(self, started, seconds=60, *, status="completed", phase="focus", task="Task", task_id=None, tags=()):
        item = new_session(task, phase, max(1, int(seconds // 60) + 1), started)
        item.update(elapsed_seconds=seconds, status=status, ended_at=(started + timedelta(seconds=seconds)).isoformat(),
                    task_id=task_id, tags=list(tags))
        self.state["history"].append(item)
        return item

    def test_empty_week_has_seven_zero_days_and_today_has_one(self):
        week = self.reports.statistics("week")
        self.assertEqual(week["start_date"], "2026-11-02")
        self.assertEqual(week["end_date"], "2026-11-08")
        self.assertEqual(len(week["days"]), 7)
        self.assertEqual([day["date"] for day in week["days"]], [f"2026-11-{day:02d}" for day in range(2, 9)])
        self.assertTrue(all(day["focus_seconds"] == 0 and day["completed_sessions"] == 0 for day in week["days"]))
        self.assertEqual(len(self.reports.statistics("today")["days"]), 1)

    def test_unequal_days_exclude_previous_week_break_and_active_time(self):
        self.record(datetime(2026, 11, 1, 23, 59, tzinfo=timezone.utc), 120)
        self.record(datetime(2026, 11, 2, 12, tzinfo=timezone.utc), 60)
        self.record(datetime(2026, 11, 3, 12, tzinfo=timezone.utc), 120, status="cancelled")
        self.record(datetime(2026, 11, 3, 13, tzinfo=timezone.utc), 30, status="interrupted")
        self.record(datetime(2026, 11, 3, 14, tzinfo=timezone.utc), 300, phase="short_break")
        self.state["active"] = {"session": {"elapsed_seconds": 999}}
        report = self.reports.statistics("week")
        self.assertEqual(report["focus_seconds"], 210)
        self.assertEqual(report["completed_sessions"], 1)
        self.assertEqual([day["focus_seconds"] for day in report["days"]], [60, 150, 0, 0, 0, 0, 0])

    def test_query_keeps_finalized_breaks_and_original_history_order(self):
        first = self.record(self.clock.now(), 10, status="cancelled")
        second = self.record(self.clock.now(), 20, phase="short_break")
        self.record(self.clock.now(), 30, status="running")
        self.assertEqual(self.reports.query(), [first, second])

    def test_and_filters_are_case_insensitive_for_tags_and_inclusive_for_dates(self):
        first = self.record(datetime(2026, 11, 2, 23, 59, tzinfo=timezone.utc), task_id="first", tags=("Reading",))
        second = self.record(datetime(2026, 11, 3, 0, 0, tzinfo=timezone.utc), task_id="first", tags=("READING",))
        self.record(datetime(2026, 11, 3, 0, 0, tzinfo=timezone.utc), task_id="second", tags=("reading",))
        self.record(datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc), task_id="first", tags=("other",))
        self.assertEqual(self.reports.query(task_id="first", tag=" reading ", start_date="2026-11-02", end_date=date(2026, 11, 3)), [first, second])
        self.assertEqual(self.reports.query(task_id="unknown"), [])
        report = self.reports.statistics("week", task_id="first", tag="reading", start_date="2026-11-03")
        self.assertEqual(report["focus_seconds"], 60)
        self.assertEqual(len(report["days"]), 7)

    def test_midnight_session_belongs_to_start_day(self):
        self.record(datetime(2026, 11, 2, 23, 59, 30, tzinfo=timezone.utc), 120)
        report = self.reports.statistics("week")
        self.assertEqual(report["days"][0]["focus_seconds"], 120)
        self.assertEqual(report["days"][1]["focus_seconds"], 0)

    def test_dst_session_uses_historical_local_date(self):
        berlin = ZoneInfo("Europe/Berlin")
        self.clock.wall = datetime(2026, 10, 25, 12, tzinfo=berlin)
        item = self.record(datetime(2026, 10, 25, 0, 30, tzinfo=berlin), 600)
        self.assertEqual(self.reports.query(start_date="2026-10-25", end_date="2026-10-25"), [item])
        report = self.reports.statistics("week")
        self.assertEqual(report["days"][-1]["date"], "2026-10-25")
        self.assertEqual(report["days"][-1]["focus_seconds"], 600)

    def test_invalid_filters_fail_before_reading_records(self):
        for filters in ({"task_id": ""}, {"tag": " "}, {"tag": []}, {"start_date": "20261108"},
                        {"start_date": "2026-11-99"}, {"start_date": datetime.now()},
                        {"start_date": "2026-11-08", "end_date": "2026-11-02"}):
            with self.subTest(filters=filters), self.assertRaises(ValueError):
                self.reports.query(**filters)
        with self.assertRaises(ValueError):
            self.reports.statistics("month")

    def test_service_wrapper_preserves_existing_contract_and_forwards_filters(self):
        repository = MemoryRepository()
        timer = Service(repository, self.clock)
        self.record(self.clock.now(), 30, tags=("reading",))
        timer.state["history"] = self.state["history"]
        report = timer.statistics("today", tag="READING")
        for field in ("period", "start_date", "end_date", "completed_sessions", "focus_seconds", "tasks"):
            self.assertIn(field, report)
        self.assertEqual(report["focus_seconds"], 30)
        self.assertEqual(report["days"][0]["focus_seconds"], 30)


if __name__ == "__main__":
    unittest.main()
