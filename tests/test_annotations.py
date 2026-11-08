"""Finalized notes/tags and independent focus-cycle tag snapshots."""

from datetime import date, datetime, timezone
import unittest

from test_core import FakeClock, MemoryRepository
from pomonal.annotations import Annotations, normalize_tags
from pomonal.application import Service
from pomonal.profiles import Profiles
from pomonal.tasks import Tasks


class AnnotationTests(unittest.TestCase):
    def setUp(self):
        self.repository = MemoryRepository()
        self.clock = FakeClock()
        self.timer = Service(self.repository, self.clock)
        self.annotations = Annotations(self.timer)

    def finalized(self, title="Read", tags=None):
        self.timer.start(title, 1, tags=tags)
        self.clock.advance(10)
        self.timer.stop()
        return self.timer.history()[0]

    def test_normalization_preserves_order_and_empty_list_clears(self):
        self.assertEqual(normalize_tags([" Reading ", "READING", "Algorithms", "日本語"]),
                         ["reading", "algorithms", "日本語"])
        self.assertEqual(normalize_tags(("Straße", "STRASSE")), ["strasse"])
        record = self.finalized()
        self.annotations.tags(record["id"], [" Reading ", "READING"])
        self.assertEqual(record["tags"], ["reading"])
        self.annotations.tags(record["id"], [])
        self.assertEqual(Service(self.repository, self.clock).history()[0]["tags"], [])

    def test_note_unicode_newlines_and_empty_clear_are_preserved(self):
        record = self.finalized()
        note = 'Finished chapter "one", 日本語.\nNext: review algorithms.'
        self.annotations.note(record["id"], note)
        self.assertEqual(Service(self.repository, self.clock).history()[0]["note"], note)
        self.annotations.note(record["id"], "")
        self.assertEqual(record["note"], "")

    def test_active_unknown_and_partial_ids_are_rejected(self):
        self.timer.start("Active", 1)
        session_id = self.timer.state["active"]["session"]["id"]
        for selected in (session_id, session_id[:8], "unknown", None):
            with self.subTest(selected=selected):
                with self.assertRaises(ValueError):
                    self.annotations.note(selected, "Text")
                with self.assertRaises(ValueError):
                    self.annotations.tags(selected, ["tag"])
        self.assertEqual(self.timer.state["active"]["session"]["note"], "")

    def test_invalid_tag_inputs_and_notes_preserve_existing_values(self):
        record = self.finalized(tags=["original"])
        for values in ("reading", None, {"reading"}, [""], [" "], [1], [True]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.annotations.tags(record["id"], values)
            self.assertEqual(record["tags"], ["original"])
        with self.assertRaises(ValueError):
            self.annotations.note(record["id"], None)
        for values in ("reading", [" "]):
            with self.assertRaises(ValueError):
                self.timer.start("Invalid", tags=values)
        self.assertIsNone(self.timer.state["active"])

    def test_focus_cycle_tags_are_independent_of_input_history_and_breaks(self):
        supplied = ["Reading"]
        self.timer.start("Cycle", 1, tags=supplied)
        supplied.append("external")
        active = self.timer.state["active"]
        self.assertEqual(active["tags"], ["reading"])
        self.assertIsNot(active["tags"], active["session"]["tags"])
        self.clock.advance(60)
        self.timer.tick()
        first = self.timer.history()[0]
        self.annotations.tags(first["id"], ["edited"])
        self.timer.continue_cycle()
        self.assertEqual(active["session"]["phase"], "short_break")
        self.assertEqual(active["session"]["tags"], [])
        self.clock.advance(300)
        self.timer.tick()
        self.timer.continue_cycle()
        self.assertEqual(active["session"]["phase"], "focus")
        self.assertEqual(active["session"]["tags"], ["reading"])
        self.assertEqual(first["tags"], ["edited"])

    def test_recovery_keeps_cycle_tags_without_counting_application_downtime(self):
        previous = self.finalized("Previous")
        self.timer.start("Recover", 1, tags=[" Reading "])
        self.clock.advance(10)
        self.timer.checkpoint()
        self.clock.advance(1000)
        recovered = Service(self.repository, self.clock)
        Annotations(recovered).note(previous["id"], "Edited while a cycle was saved")
        self.assertEqual(recovered.state["active"]["session"]["elapsed_seconds"], 10)
        recovered.recover("resume")
        self.assertEqual(recovered.state["active"]["tags"], ["reading"])
        self.assertEqual(recovered.state["active"]["session"]["tags"], ["reading"])

    def test_task_profile_tags_and_combined_history_filters(self):
        tasks, profiles = Tasks(self.timer), Profiles(self.timer)
        task = tasks.add("Algorithms")
        self.clock.wall = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
        tasks.start(task["id"], 1, durations=profiles.resolve("deep-work"), tags=[" Study ", "study"])
        self.clock.advance(10)
        self.timer.stop()
        first = self.timer.history()[0]
        self.assertEqual(first["task_id"], task["id"])
        self.assertEqual(first["tags"], ["study"])
        self.assertEqual(first["planned_seconds"], 60)
        self.clock.wall = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
        self.finalized("Other", tags=["study"])
        selected = self.timer.history(task_id=task["id"], tag="STUDY", start_date="2026-11-02", end_date=date(2026, 11, 2))
        self.assertEqual(selected, [first])
        self.assertEqual(self.timer.history(tag="study")[0]["task"], "Other")
        self.assertEqual(len(self.timer.history(tag="study")), 2)


if __name__ == "__main__":
    unittest.main()
