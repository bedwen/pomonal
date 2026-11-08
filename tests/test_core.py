"""Domain and application tests without real-time waiting."""

import copy
from datetime import datetime, timedelta, timezone
import os
import time
import unittest

from pomonal.application import Service
from pomonal.domain import default_state


class MemoryRepository:
    def __init__(self):
        self.saved = default_state()
        self.saves = 0

    def load(self):
        return copy.deepcopy(self.saved)

    def save(self, state):
        self.saved = copy.deepcopy(state)
        self.saves += 1


class FakeClock:
    def __init__(self):
        self.seconds = 0.0
        self.wall = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)

    def monotonic(self):
        return self.seconds

    def now(self):
        return self.wall

    def local_date(self, timestamp):
        return timestamp.astimezone(self.wall.tzinfo).date()

    def advance(self, seconds):
        self.seconds += seconds
        self.wall += timedelta(seconds=seconds)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.repository = MemoryRepository()
        self.clock = FakeClock()
        self.service = Service(self.repository, self.clock)

    def complete_phase(self):
        phase = self.service.state["active"]["session"]
        self.clock.advance(phase["planned_seconds"])
        self.service.tick()

    def test_cycle_waits_and_fourth_focus_has_long_break(self):
        self.service.start("Algorithms", 1)
        for completed in range(1, 5):
            self.complete_phase()
            active = self.service.state["active"]
            self.assertTrue(active["waiting"])
            self.assertIsNone(active["session"])
            self.assertEqual(active["completed_focus"], completed)
            expected = "long_break" if completed == 4 else "short_break"
            self.assertEqual(active["next_phase"], expected)
            self.clock.advance(10000)
            self.service.tick()
            self.assertEqual(len(self.service.state["history"]), completed * 2 - 1)
            self.service.continue_cycle()
            self.assertEqual(active["session"]["phase"], expected)
            self.complete_phase()
            self.assertEqual(active["next_phase"], "focus")
            self.service.continue_cycle()
            self.assertEqual(active["session"]["planned_seconds"], 60)

    def test_pause_and_wall_clock_changes_do_not_affect_countdown(self):
        self.service.start("Read", 1)
        self.clock.advance(10)
        self.service.pause()
        self.clock.advance(500)
        self.service.tick()
        self.assertEqual(self.service.state["active"]["session"]["elapsed_seconds"], 10)
        self.service.resume()
        self.clock.wall -= timedelta(days=30)
        self.clock.seconds += 20
        self.service.tick()
        self.assertEqual(self.service.state["active"]["session"]["elapsed_seconds"], 30)

    def test_auto_checkpoint_and_recovery_exclude_downtime(self):
        self.service.start("Read", 1)
        self.clock.advance(5)
        self.service.tick()
        self.assertEqual(self.repository.saved["active"]["session"]["elapsed_seconds"], 5)
        self.clock.advance(5000)
        recovered = Service(self.repository, self.clock)
        self.assertTrue(recovered.has_recovery)
        with self.assertRaises(ValueError):
            recovered.tick()
        recovered.recover("resume")
        recovered.tick()
        self.assertEqual(recovered.state["active"]["session"]["elapsed_seconds"], 5)
        self.clock.advance(55)
        recovered.tick()
        recovered.checkpoint()
        self.assertEqual(len(recovered.state["history"]), 1)
        again = Service(self.repository, self.clock)
        again.recover("resume")
        again.tick()
        self.assertEqual(len(again.state["history"]), 1)
        self.assertTrue(again.state["active"]["waiting"])

    def test_recover_paused_and_discard(self):
        self.service.start("Read", 1)
        self.clock.advance(10)
        self.service.pause()
        self.clock.advance(500)
        recovered = Service(self.repository, self.clock)
        recovered.recover("resume")
        self.assertTrue(recovered.state["active"]["paused"])
        self.clock.advance(1000)
        recovered.tick()
        self.assertEqual(recovered.state["active"]["session"]["elapsed_seconds"], 10)
        recovered.checkpoint()
        discarded = Service(self.repository, self.clock)
        discarded.recover("discard")
        self.assertIsNone(discarded.state["active"])
        record = discarded.history()[0]
        self.assertEqual(record["status"], "interrupted")
        self.assertEqual(record["elapsed_seconds"], 10)
        with self.assertRaises(ValueError):
            discarded.recover("discard")
        self.assertEqual(len(discarded.history()), 1)

    def test_stop_records_elapsed_and_history_is_newest_first(self):
        for task in ("First", "Second"):
            self.service.start(task)
            self.clock.advance(12)
            self.service.stop()
        self.assertEqual([row["task"] for row in self.service.history()], ["Second", "First"])
        self.assertEqual(self.service.history(1)[0]["status"], "cancelled")
        self.assertEqual(self.service.history(1)[0]["elapsed_seconds"], 12)

    def test_invalid_inputs_and_transitions(self):
        for value in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                self.service.start("Task", value)
            with self.assertRaises(ValueError):
                self.service.set_config("work_minutes", value)
            with self.assertRaises(ValueError):
                self.service.history(value)
        with self.assertRaises(ValueError):
            self.service.start("   ")
        with self.assertRaises(ValueError):
            self.service.set_config("unknown", 1)
        with self.assertRaises(ValueError):
            self.service.statistics("month")
        self.service.start("Task", 1)
        for operation in (self.service.resume, self.service.continue_cycle):
            with self.assertRaises(ValueError):
                operation()
        with self.assertRaises(ValueError):
            self.service.start("Another")
        self.service.pause()
        with self.assertRaises(ValueError):
            self.service.pause()

    def test_configuration_override_is_not_persisted_as_setting(self):
        self.service.set_config("work_minutes", 30)
        self.service.start("Task", 1)
        self.assertEqual(self.repository.saved["config"]["work_minutes"], 30)
        self.assertEqual(self.service.state["active"]["session"]["planned_seconds"], 60)

    def test_override_survives_recovery_and_following_cycle(self):
        self.service.set_config("work_minutes", 30)
        self.service.start("Read", 1)
        self.clock.advance(10)
        self.service.checkpoint()
        self.clock.advance(1000)
        recovered = Service(self.repository, self.clock)
        recovered.recover("resume")
        self.assertEqual(recovered.state["active"]["work_minutes"], 1)
        self.clock.advance(50)
        recovered.tick()
        self.assertTrue(recovered.state["active"]["waiting"])
        recovered.continue_cycle()
        self.clock.advance(300)
        recovered.tick()
        recovered.continue_cycle()
        self.assertEqual(recovered.state["active"]["session"]["planned_seconds"], 60)
        self.assertEqual(recovered.state["config"]["work_minutes"], 30)

    def test_recovered_waiting_run_does_not_duplicate_completed_phase(self):
        self.service.start("Read", 1)
        self.complete_phase()
        self.clock.advance(10000)
        recovered = Service(self.repository, self.clock)
        recovered.recover("resume")
        for _ in range(3):
            self.clock.advance(10)
            recovered.tick()
            recovered.checkpoint()
        self.assertEqual(len(recovered.history()), 1)
        self.assertTrue(recovered.state["active"]["waiting"])
        self.assertEqual(recovered.state["active"]["next_phase"], "short_break")
        recovered.stop()
        self.assertEqual(len(recovered.history()), 1)

    def test_statistics_boundaries_and_unfinished_focus(self):
        self.clock.wall = datetime(2026, 11, 2, 23, 59, 50, tzinfo=timezone.utc)
        self.service.start("Monday", 1)
        self.complete_phase()
        self.service.stop()
        self.clock.wall = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
        self.service.start("Sunday", 1)
        self.clock.advance(20)
        self.service.stop()
        self.service.start("Active", 1)
        self.clock.advance(10)
        self.service.tick()
        today = self.service.statistics("today")
        self.assertEqual(today["completed_sessions"], 0)
        self.assertEqual(today["focus_seconds"], 20)
        self.assertEqual(today["tasks"], {"Sunday": 20})
        week = self.service.statistics("week")
        self.assertEqual(week["start_date"], "2026-11-02")
        self.assertEqual(week["end_date"], "2026-11-08")
        self.assertEqual(week["completed_sessions"], 1)
        self.assertEqual(week["focus_seconds"], 80)
        self.clock.wall = datetime(2026, 11, 9, 0, tzinfo=timezone.utc)
        self.assertEqual(self.service.statistics("week")["focus_seconds"], 0)

    def test_breaks_do_not_contribute_to_focus_statistics(self):
        self.service.start("Read", 1)
        self.complete_phase()
        self.service.continue_cycle()
        self.clock.advance(20)
        self.service.stop()
        stats = self.service.statistics()
        self.assertEqual(stats["focus_seconds"], 60)
        self.assertEqual(stats["completed_sessions"], 1)

    def test_week_excludes_previous_sunday_and_includes_interrupted_time(self):
        self.clock.wall = datetime(2026, 11, 1, 23, 59, 50, tzinfo=timezone.utc)
        self.service.start("Previous Sunday", 1)
        self.clock.advance(30)
        self.service.stop()
        self.clock.wall = datetime(2026, 11, 2, 0, 0, 1, tzinfo=timezone.utc)
        self.service.start("Monday", 1)
        self.clock.advance(15)
        self.service.checkpoint()
        recovered = Service(self.repository, self.clock)
        recovered.recover("discard")
        report = recovered.statistics("week")
        self.assertEqual(report["tasks"], {"Monday": 15})
        self.assertEqual(report["focus_seconds"], 15)
        self.assertEqual(report["completed_sessions"], 0)
        self.assertEqual(recovered.history()[0]["status"], "interrupted")

    def test_statistics_uses_local_day_of_offset_timestamp(self):
        local = timezone(timedelta(hours=3))
        self.clock.wall = datetime(2026, 11, 9, 0, 0, 10, tzinfo=local)
        self.service.start("Local Monday", 1)
        self.clock.advance(10)
        self.service.stop()
        report = self.service.statistics("today")
        self.assertEqual(report["start_date"], "2026-11-09")
        self.assertEqual(report["tasks"], {"Local Monday": 10})

    @unittest.skipUnless(hasattr(time, "tzset"), "Requires Linux timezone support")
    def test_statistics_preserves_local_day_across_dst_transition(self):
        from pomonal.infrastructure import SystemClock

        class FixedSystemClock(SystemClock):
            def now(self):
                return datetime(2026, 10, 25, 12, tzinfo=timezone(timedelta(hours=1)))

        previous = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "Europe/Berlin"
            time.tzset()
            self.clock.wall = datetime(
                2026, 10, 25, 0, 30, tzinfo=timezone(timedelta(hours=2))
            )
            self.service.start("Before DST change", 1)
            self.clock.advance(10)
            self.service.stop()
            service = Service(self.repository, FixedSystemClock())
            self.assertEqual(service.statistics("today")["tasks"], {"Before DST change": 10})
        finally:
            if previous is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = previous
            time.tzset()


if __name__ == "__main__":
    unittest.main()
