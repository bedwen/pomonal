"""Task queue ordering, session association, and recovery behavior."""

import unittest

from test_core import FakeClock, MemoryRepository
from pomonal.application import Service
from pomonal.tasks import Tasks


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.repository = MemoryRepository()
        self.clock = FakeClock()
        self.timer = Service(self.repository, self.clock)
        self.tasks = Tasks(self.timer)

    def test_add_move_complete_reopen_and_persist_queue_order(self):
        first = self.tasks.add(" First ")
        second = self.tasks.add("Second")
        third = self.tasks.add("Third")
        self.assertEqual(first["title"], "First")
        self.assertEqual(len({task["id"] for task in self.tasks.list()}), 3)
        self.tasks.move(third["id"], 1)
        self.assertEqual([task["title"] for task in self.tasks.list()], ["Third", "First", "Second"])
        self.tasks.complete(first["id"])
        self.assertEqual(self.tasks.list(True), [first])
        self.tasks.reopen(first["id"])
        self.assertEqual([task["title"] for task in self.tasks.list()], ["Third", "Second", "First"])
        self.assertEqual(Tasks(Service(self.repository, self.clock)).list(), [third, second, first])

    def test_rename_preserves_current_cycle_and_historical_title(self):
        task = self.tasks.add("Original")
        self.tasks.start(task["id"], 1)
        self.tasks.rename(task["id"], "Renamed")
        self.assertEqual(self.timer.state["active"]["task"], "Original")
        self.clock.advance(60)
        self.timer.tick()
        self.timer.continue_cycle()
        self.assertEqual(self.timer.state["active"]["session"]["task"], "Original")
        self.assertEqual(self.timer.state["active"]["session"]["task_id"], task["id"])
        self.timer.stop()
        self.assertTrue(all(item["task"] == "Original" for item in self.timer.history()))
        self.tasks.start(task["id"], 1)
        self.assertEqual(self.timer.state["active"]["task"], "Renamed")

    def test_associated_active_and_paused_completion_rejected(self):
        task = self.tasks.add("Read")
        self.tasks.start(task["id"], 1)
        with self.assertRaisesRegex(ValueError, "Stop or finish"):
            self.tasks.complete(task["id"])
        self.timer.pause()
        with self.assertRaises(ValueError):
            self.tasks.complete(task["id"])
        self.assertFalse(task["completed"])

    def test_complete_between_phases_closes_cycle_without_duplicate_history(self):
        task = self.tasks.add("Read")
        self.tasks.start(task["id"], 1)
        self.clock.advance(60)
        self.timer.tick()
        self.tasks.complete(task["id"])
        self.assertIsNone(self.timer.state["active"])
        self.assertEqual(len(self.timer.history()), 1)
        self.assertEqual(self.timer.history()[0]["task_id"], task["id"])
        with self.assertRaisesRegex(ValueError, "Reopen"):
            self.tasks.start(task["id"])

    def test_switching_requires_idle_and_new_cycle_resets_counter(self):
        first = self.tasks.add("First")
        second = self.tasks.add("Second")
        self.tasks.start(first["id"], 1)
        self.clock.advance(60)
        self.timer.tick()
        with self.assertRaisesRegex(ValueError, "active or recoverable"):
            self.tasks.start(second["id"])
        self.timer.stop()
        self.tasks.start(second["id"])
        self.assertEqual(self.timer.state["active"]["completed_focus"], 0)

    def test_unrelated_task_edits_do_not_pause_active_session(self):
        first = self.tasks.add("Active")
        second = self.tasks.add("Other")
        self.tasks.start(first["id"])
        self.clock.advance(10)
        self.tasks.complete(second["id"])
        self.assertFalse(self.timer.state["active"]["paused"])
        self.assertEqual(self.timer.state["active"]["session"]["elapsed_seconds"], 10)

    def test_recovery_retains_association_and_edits_exclude_downtime(self):
        task = self.tasks.add("Recover")
        self.tasks.start(task["id"], 1)
        self.clock.advance(10)
        self.timer.checkpoint()
        self.clock.advance(1000)
        recovered = Service(self.repository, self.clock)
        tasks = Tasks(recovered)
        tasks.rename(task["id"], "Next title")
        self.assertEqual(recovered.state["active"]["session"]["elapsed_seconds"], 10)
        with self.assertRaisesRegex(ValueError, "Resume or discard"):
            tasks.complete(task["id"])
        recovered.recover("resume")
        self.assertEqual(recovered.state["active"]["task_id"], task["id"])
        self.assertEqual(recovered.state["active"]["task"], "Recover")
        recovered.stop()
        self.assertEqual(recovered.history()[0]["task_id"], task["id"])

    def test_invalid_titles_positions_and_ids_leave_queue_unchanged(self):
        for title in ("", "   ", None):
            with self.assertRaises(ValueError):
                self.tasks.add(title)
        task = self.tasks.add("Task")
        for position in (0, -1, 2, True, 1.5):
            with self.assertRaises(ValueError):
                self.tasks.move(task["id"], position)
        for operation in (self.tasks.rename, self.tasks.move):
            with self.assertRaises(ValueError):
                operation("missing", "unused")
        for operation in (self.tasks.complete, self.tasks.reopen, self.tasks.start):
            with self.assertRaises(ValueError):
                operation("missing")
        self.assertEqual(self.tasks.list(), [task])
        self.tasks.complete(task["id"])
        with self.assertRaises(ValueError):
            self.tasks.move(task["id"], 1)


if __name__ == "__main__":
    unittest.main()
