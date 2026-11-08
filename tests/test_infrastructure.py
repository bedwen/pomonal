"""Persistence guarantees using project-local temporary directories."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pomonal.domain import default_state
from pomonal.infrastructure import JsonRepository, validate_state


TEMP_ROOT = Path(__file__).resolve().parent.parent / ".test-tmp"


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        TEMP_ROOT.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=TEMP_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repository = JsonRepository(self.root)

    def test_read_defaults_without_creating_data(self):
        self.assertEqual(self.repository.load(), default_state())
        self.assertFalse((self.root / "data").exists())

    def test_roundtrip_and_atomic_replace_failure_preserves_old_file(self):
        state = default_state()
        self.repository.save(state)
        original = self.repository.path.read_bytes()
        state["config"]["work_minutes"] = 40
        with patch("pomonal.infrastructure.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.repository.save(state)
        self.assertEqual(self.repository.path.read_bytes(), original)
        self.assertFalse(list(self.repository.directory.glob("*.tmp")))
        self.repository.save(state)
        self.assertEqual(self.repository.load(), state)

    def test_corrupt_and_unsupported_data_are_preserved(self):
        self.repository.directory.mkdir()
        for payload in ("not json", '{"version":2}', '{"version":1}', '[]'):
            self.repository.path.write_text(payload)
            with self.assertRaises(ValueError):
                self.repository.load()
            self.assertEqual(self.repository.path.read_text(), payload)

    def test_missing_active_fields_are_rejected(self):
        state = default_state()
        state["active"] = {"task": "Read", "work_minutes": 25, "completed_focus": 1,
                           "waiting": True, "paused": False, "next_phase": "short_break"}
        with self.assertRaises(ValueError):
            validate_state(state)

    def test_writer_lock_is_exclusive_and_released(self):
        with self.repository.writer():
            with self.assertRaisesRegex(ValueError, "Another Pomonal"):
                with JsonRepository(self.root).writer():
                    self.fail("Second writer acquired the lock")
            self.assertEqual(self.repository.load(), default_state())
        with self.repository.writer():
            self.repository.save(default_state())

    def test_symbolic_data_directory_is_rejected(self):
        target = self.root / "target"
        target.mkdir()
        self.repository.directory.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.repository.save(default_state())
        self.assertEqual(list(target.iterdir()), [])

    def test_symbolic_state_and_lock_files_are_rejected(self):
        self.repository.directory.mkdir()
        target = self.root / "target"
        target.write_text("unchanged")
        for name in ("state.json", "writer.lock"):
            link = self.repository.directory / name
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                if name == "state.json":
                    self.repository.save(default_state())
                else:
                    with self.repository.writer():
                        self.fail("Followed symbolic lock")
            self.assertEqual(target.read_text(), "unchanged")
            link.unlink()

    def test_pinned_directory_survives_symlink_swap(self):
        self.repository.save(default_state())
        other = self.root / "other"
        other.mkdir()
        moved = self.root / "original-data"
        import os
        original_replace = os.replace

        def swap_then_replace(*args, **kwargs):
            self.repository.directory.rename(moved)
            self.repository.directory.symlink_to(other, target_is_directory=True)
            return original_replace(*args, **kwargs)

        state = default_state()
        state["config"]["work_minutes"] = 42
        with patch("pomonal.infrastructure.os.replace", side_effect=swap_then_replace):
            self.repository.save(state)
        self.assertEqual(list(other.iterdir()), [])
        self.assertEqual(json.loads((moved / "state.json").read_text()), state)


if __name__ == "__main__":
    unittest.main()
