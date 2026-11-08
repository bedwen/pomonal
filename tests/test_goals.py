"""Daily target accounting, local-calendar boundaries, and timer preservation."""

from datetime import datetime, timedelta, timezone
import unittest
from zoneinfo import ZoneInfo

from test_core import FakeClock, MemoryRepository
from pomonal.application import Service
from pomonal.domain import new_session
from pomonal.goals import Goals


class GoalTests(unittest.TestCase):
    def setUp(self):
        self.repository = MemoryRepository()
        self.clock = FakeClock()
        self.timer = Service(self.repository, self.clock)
        self.goals = Goals(self.timer)

    def test_disabled_switch_clear_and_persistence(self):
        self.assertIsNone(self.goals.progress())
        self.goals.set("sessions", 6)
        self.assertEqual(self.goals.progress(), {"type": "sessions", "target": 6, "achieved": 0, "fraction": 0})
        self.goals.set("minutes", 120)
        restored = Goals(Service(self.repository, self.clock))
        self.assertEqual(restored.progress()["type"], "minutes")
        self.assertEqual(restored.progress()["target"], 120)
        restored.clear()
        self.assertIsNone(Goals(Service(self.repository, self.clock)).progress())

    def test_completed_cancelled_interrupted_and_break_accounting(self):
        self.timer.start("Completed", 1)
        self.clock.advance(60)
        self.timer.tick()
        self.timer.continue_cycle()
        self.clock.advance(30)
        self.timer.stop()
        self.timer.start("Cancelled", 1)
        self.clock.advance(20)
        self.timer.stop()
        self.timer.start("Interrupted", 1)
        self.clock.advance(10)
        self.timer.checkpoint()
        recovered = Service(self.repository, self.clock)
        recovered.recover("discard")
        goals = Goals(recovered)
        goals.set("minutes", 2)
        self.assertEqual(goals.progress()["achieved"], 1.5)
        self.assertEqual(goals.progress()["fraction"], 0.75)
        goals.set("sessions", 6)
        self.assertEqual(goals.progress()["achieved"], 1)

    def test_active_paused_and_application_downtime_are_excluded(self):
        self.goals.set("minutes", 1)
        self.timer.start("Active", 1)
        self.clock.advance(10)
        self.timer.pause()
        self.clock.advance(1000)
        self.assertEqual(self.goals.progress()["achieved"], 0)
        recovered = Service(self.repository, self.clock)
        recovered.recover("resume")
        recovered.stop()
        self.assertAlmostEqual(Goals(recovered).progress()["achieved"], 10 / 60)

    def test_achievement_is_unrounded_and_progress_caps_without_hiding_total(self):
        self.goals.set("minutes", 1)
        self.timer.start("Fraction", 1)
        self.clock.advance(0.25)
        self.timer.stop()
        self.assertAlmostEqual(self.goals.progress()["achieved"], 0.25 / 60)
        self.timer.start("More", 2)
        self.clock.advance(120)
        self.timer.tick()
        report = self.goals.progress()
        self.assertAlmostEqual(report["achieved"], 2 + 0.25 / 60)
        self.assertEqual(report["fraction"], 1)

    def test_local_day_and_cross_midnight_attribution(self):
        self.clock.wall = datetime(2026, 11, 8, 23, 59, 50, tzinfo=timezone(timedelta(hours=3)))
        self.goals.set("minutes", 1)
        self.timer.start("Across midnight", 1)
        self.clock.advance(20)
        self.timer.stop()
        self.assertEqual(self.goals.progress()["achieved"], 0)
        self.timer.start("New local day", 1)
        self.clock.advance(15)
        self.timer.stop()
        self.assertEqual(self.goals.progress()["achieved"], 0.25)

    def test_dst_transition_keeps_early_local_session_in_same_day(self):
        berlin = ZoneInfo("Europe/Berlin")
        self.clock.wall = datetime(2026, 10, 25, 12, tzinfo=berlin)
        started = datetime(2026, 10, 25, 0, 30, tzinfo=berlin)
        record = new_session("Early local work", "focus", 10, started)
        record.update(status="completed", elapsed_seconds=600,
                      ended_at=(started + timedelta(minutes=10)).isoformat())
        self.timer.state["history"].append(record)
        self.goals.set("minutes", 20)
        self.assertEqual(self.goals.progress()["achieved"], 10)
        self.assertEqual(self.goals.progress()["fraction"], 0.5)

    def test_goal_edits_do_not_pause_reset_or_accrue_recovery_downtime(self):
        self.timer.start("Keep timing", 1)
        self.clock.advance(10)
        self.goals.set("minutes", 30)
        self.assertFalse(self.timer.state["active"]["paused"])
        self.assertEqual(self.timer.state["active"]["session"]["elapsed_seconds"], 10)
        self.clock.advance(1000)
        recovered = Service(self.repository, self.clock)
        Goals(recovered).set("sessions", 2)
        self.assertEqual(recovered.state["active"]["session"]["elapsed_seconds"], 10)
        recovered.recover("resume")
        self.clock.advance(5)
        Goals(recovered).clear()
        self.assertEqual(recovered.state["active"]["session"]["elapsed_seconds"], 15)

    def test_invalid_goals_preserve_previous_configuration(self):
        self.goals.set("sessions", 6)
        original = dict(self.timer.state["goal"])
        for kind, target in (("unknown", 1), (None, 1), ("minutes", 0), ("sessions", -1),
                             ("sessions", True), ("minutes", 1.5), ("minutes", 10**400)):
            with self.subTest(kind=kind, target=target), self.assertRaises(ValueError):
                self.goals.set(kind, target)
            self.assertEqual(self.timer.state["goal"], original)


if __name__ == "__main__":
    unittest.main()
