"""Duration profile selection, snapshots, live edits, and task combinations."""

import unittest

from test_core import FakeClock, MemoryRepository
from pomonal.application import Service
from pomonal.profiles import Profiles
from pomonal.tasks import Tasks


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.repository = MemoryRepository()
        self.clock = FakeClock()
        self.timer = Service(self.repository, self.clock)
        self.profiles = Profiles(self.timer)

    def test_builtins_default_and_resolved_values_are_independent(self):
        self.assertEqual(self.profiles.resolve(" CLASSIC "),
                         {"work_minutes": 25, "short_break_minutes": 5, "long_break_minutes": 15})
        self.assertEqual(self.profiles.resolve("deep-work")["work_minutes"], 50)
        self.timer.set_config("work_minutes", 30)
        self.assertEqual(self.profiles.resolve()["work_minutes"], 30)
        value = self.profiles.resolve("classic")
        value["work_minutes"] = 999
        self.assertEqual(self.profiles.resolve("classic")["work_minutes"], 25)

    def test_custom_names_normalize_and_selection_persists(self):
        self.profiles.save(" Reading ", 40, 5, 15)
        self.profiles.use("READING")
        self.assertEqual(self.timer.state["preferences"]["profile"], "reading")
        restored = Profiles(Service(self.repository, self.clock))
        self.assertEqual(restored.resolve()["work_minutes"], 40)
        restored.save("READING", 45, 10, 20)
        self.assertEqual(restored.resolve()["work_minutes"], 45)
        self.assertEqual(len(restored.list()), 4)

    def test_explicit_profile_and_minutes_override_selected_profile(self):
        self.profiles.save("reading", 40, 5, 15)
        self.profiles.use("reading")
        self.timer.start("Task", 1, durations=self.profiles.resolve("deep-work"))
        active = self.timer.state["active"]
        self.assertEqual(active["durations"], {"work_minutes": 1, "short_break_minutes": 10, "long_break_minutes": 15})
        self.assertEqual(self.timer.state["preferences"]["profile"], "reading")
        self.assertEqual(self.profiles.resolve("deep-work")["work_minutes"], 50)

    def test_named_profile_edits_do_not_change_active_or_recovered_cycle(self):
        self.profiles.save("reading", 1, 2, 3)
        self.profiles.use("reading")
        self.timer.start("Task", durations=self.profiles.resolve())
        self.clock.advance(10)
        self.profiles.save("reading", 99, 98, 97)
        self.profiles.use("deep-work")
        self.timer.set_config("short_break_minutes", 88)
        self.assertEqual(self.timer.state["active"]["session"]["elapsed_seconds"], 10)
        self.assertFalse(self.timer.state["active"]["paused"])
        self.clock.advance(1000)
        recovered = Service(self.repository, self.clock)
        profiles = Profiles(recovered)
        profiles.save("reading", 90, 91, 92)
        recovered.recover("resume")
        self.assertEqual(recovered.state["active"]["session"]["elapsed_seconds"], 10)
        self.clock.advance(50)
        recovered.tick()
        recovered.continue_cycle()
        self.assertEqual(recovered.state["active"]["session"]["planned_seconds"], 120)

    def test_selected_profile_and_task_id_work_together_and_fourth_focus_has_long_break(self):
        self.profiles.save("fast", 1, 1, 2)
        self.profiles.use("fast")
        task = Tasks(self.timer).add("Task")
        Tasks(self.timer).start(task["id"], durations=self.profiles.resolve())
        for completed in range(1, 5):
            self.clock.advance(60)
            self.timer.tick()
            self.assertEqual(self.timer.state["active"]["completed_focus"], completed)
            self.timer.continue_cycle()
            session = self.timer.state["active"]["session"]
            self.assertEqual(session["task_id"], task["id"])
            self.assertEqual(session["planned_seconds"], 120 if completed == 4 else 60)
            if completed != 4:
                self.clock.advance(60)
                self.timer.tick()
                self.timer.continue_cycle()

    def test_invalid_names_durations_and_snapshots_leave_idle_state(self):
        for name in ("", " ", None, "DEFAULT", " Classic ", "deep-work"):
            with self.assertRaises(ValueError):
                self.profiles.save(name, 25, 5, 15)
        for bad in (0, -1, True, 1.5, 10**400):
            with self.assertRaises(ValueError):
                self.profiles.save("reading", bad, 5, 15)
        with self.assertRaises(ValueError):
            self.profiles.use("missing")
        for durations in ({}, [], {"work_minutes": True, "short_break_minutes": 5, "long_break_minutes": 15}):
            with self.assertRaises(ValueError):
                self.timer.start("Task", durations=durations)
        self.assertIsNone(self.timer.state["active"])
        self.assertEqual(self.timer.state["profiles"], {})


if __name__ == "__main__":
    unittest.main()
