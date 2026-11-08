"""Versioned-state migration, source preservation, and recovery regressions."""

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pomonal.application import Service
from pomonal.domain import default_state, new_session
from pomonal.infrastructure import JsonRepository
from pomonal.state import migrate_state, validate_state
from test_core import FakeClock


TEMP_ROOT = Path(__file__).resolve().parent.parent / ".test-tmp"


def legacy_state(mode=None):
    state = default_state()
    state["version"] = 1
    for key in ("tasks", "profiles", "preferences", "goal"):
        del state[key]
    now = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
    historical = new_session("Previous 日本語", "focus", 1, now)
    historical.update(status="cancelled", elapsed_seconds=12.5, ended_at=now.isoformat())
    state["history"].append(historical)
    if mode is not None:
        session = new_session("Current", "focus", 1, now)
        session["elapsed_seconds"] = 10.25
        state["active"] = {
            "task": "Current", "work_minutes": 1, "completed_focus": 1,
            "waiting": mode == "waiting", "paused": mode == "paused",
            "next_phase": "short_break" if mode == "waiting" else None,
            "session": None if mode == "waiting" else session,
        }
    records = list(state["history"])
    if state["active"] and state["active"]["session"]:
        records.append(state["active"]["session"])
    for record in records:
        for key in ("task_id", "note", "tags"):
            del record[key]
    return state


class MigrationTests(unittest.TestCase):
    def setUp(self):
        TEMP_ROOT.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=TEMP_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repository = JsonRepository(self.root)

    def seed(self, mode=None):
        legacy = legacy_state(mode)
        self.repository.directory.mkdir(exist_ok=True)
        raw = (json.dumps(legacy, ensure_ascii=False, indent=3) + "\n\n").encode()
        self.repository.path.write_bytes(raw)
        return legacy, raw

    def backups(self):
        return list(self.repository.directory.glob("state-v1-*.backup.json"))

    def test_read_only_legacy_load_has_no_filesystem_mutation(self):
        legacy, raw = self.seed()
        before = self.repository.path.stat().st_mtime_ns
        migrated = self.repository.load()
        self.assertEqual(migrated["version"], 2)
        for key, value in legacy["history"][0].items():
            self.assertEqual(migrated["history"][0][key], value)
        self.assertEqual(migrated["history"][0]["tags"], [])
        self.assertEqual(self.repository.path.read_bytes(), raw)
        self.assertEqual(self.repository.path.stat().st_mtime_ns, before)
        self.assertEqual([p.name for p in self.repository.directory.iterdir()], ["state.json"])

    def test_first_write_verifies_exact_backup_and_never_overwrites_it(self):
        _, raw = self.seed("running")
        state = self.repository.load()
        with self.repository.writer():
            self.repository.save(state)
        backups = self.backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), raw)
        self.assertEqual(json.loads(self.repository.path.read_bytes())["version"], 2)
        state["config"]["work_minutes"] = 30
        self.repository.save(state)
        self.assertEqual(self.backups(), backups)
        self.assertEqual(backups[0].read_bytes(), raw)

    def test_running_paused_and_waiting_migrate_without_changing_legacy_fields(self):
        for mode in ("running", "paused", "waiting"):
            with self.subTest(mode=mode):
                legacy = legacy_state(mode)
                before = copy.deepcopy(legacy)
                state = migrate_state(legacy)
                self.assertEqual(legacy, before)
                active = state["active"]
                for key in ("task", "work_minutes", "completed_focus", "waiting", "paused", "next_phase"):
                    self.assertEqual(active[key], before["active"][key])
                self.assertEqual(active["durations"], {"work_minutes": 1, "short_break_minutes": 5, "long_break_minutes": 15})
                if mode != "waiting":
                    for key, value in before["active"]["session"].items():
                        self.assertEqual(active["session"][key], value)
                validate_state(state)

    def test_recovered_legacy_override_and_break_snapshot_exclude_downtime(self):
        self.seed("running")
        clock = FakeClock()
        clock.advance(10000)
        service = Service(self.repository, clock)
        service.recover("resume")
        service.set_config("short_break_minutes", 99)
        service.set_config("long_break_minutes", 99)
        service.tick()
        self.assertEqual(service.state["active"]["session"]["elapsed_seconds"], 10.25)
        clock.advance(49.75)
        service.tick()
        self.assertTrue(service.state["active"]["waiting"])
        service.continue_cycle()
        self.assertEqual(service.state["active"]["session"]["planned_seconds"], 300)
        clock.advance(300)
        service.tick()
        service.continue_cycle()
        self.assertEqual(service.state["active"]["session"]["planned_seconds"], 60)

    def test_waiting_recovery_does_not_duplicate_history(self):
        self.seed("waiting")
        service = Service(self.repository, FakeClock())
        service.recover("resume")
        service.checkpoint()
        self.assertEqual(len(service.history()), 1)
        service.continue_cycle()
        self.assertEqual(service.state["active"]["session"]["phase"], "short_break")

    def test_replace_failure_keeps_original_and_retry_preserves_backups(self):
        _, raw = self.seed()
        state = self.repository.load()
        with patch("pomonal.infrastructure.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                self.repository.save(state)
        self.assertEqual(self.repository.path.read_bytes(), raw)
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(self.backups()[0].read_bytes(), raw)
        self.repository.save(state)
        self.assertEqual(len(self.backups()), 2)
        self.assertTrue(all(path.read_bytes() == raw for path in self.backups()))

    def test_backup_verification_failure_aborts_replacement(self):
        _, raw = self.seed()
        state = self.repository.load()
        original = self.repository._read_bytes

        def corrupt_verification(directory_fd, name="state.json"):
            return b"wrong" if name.endswith(".backup.json") else original(directory_fd, name)

        with patch.object(self.repository, "_read_bytes", side_effect=corrupt_verification):
            with self.assertRaises(OSError):
                self.repository.save(state)
        self.assertEqual(self.repository.path.read_bytes(), raw)
        self.assertEqual(self.backups(), [])
        self.repository.save(state)

    def test_backup_sync_failure_can_retry_without_losing_source(self):
        _, raw = self.seed()
        state = self.repository.load()
        with patch("pomonal.infrastructure.os.fsync", side_effect=OSError("sync failed")):
            with self.assertRaises(OSError):
                self.repository.save(state)
        self.assertEqual(self.repository.path.read_bytes(), raw)
        self.assertEqual(self.backups(), [])
        self.repository.save(state)
        self.assertEqual(self.backups()[0].read_bytes(), raw)

    def test_stale_loaded_state_cannot_overwrite_another_writer(self):
        self.seed()
        stale = self.repository.load()
        other = JsonRepository(self.root)
        changed = other.load()
        changed["config"]["work_minutes"] = 42
        other.save(changed)
        with self.assertRaisesRegex(ValueError, "changed since"):
            self.repository.save(stale)
        self.assertEqual(JsonRepository(self.root).load()["config"]["work_minutes"], 42)
        self.assertEqual(len(self.backups()), 1)

    def test_source_change_after_backup_is_not_overwritten(self):
        legacy, raw = self.seed()
        state = self.repository.load()
        legacy["config"]["work_minutes"] = 42
        external = json.dumps(legacy).encode()
        original = self.repository._backup_legacy

        def change_source_after_backup(directory_fd, source):
            name = original(directory_fd, source)
            self.repository.path.write_bytes(external)
            return name

        with patch.object(self.repository, "_backup_legacy", side_effect=change_source_after_backup):
            with self.assertRaisesRegex(ValueError, "changed during"):
                self.repository.save(state)
        self.assertEqual(self.repository.path.read_bytes(), external)
        self.assertEqual(self.backups()[0].read_bytes(), raw)
        self.assertFalse(list(self.repository.directory.glob("*.tmp")))

    def test_invalid_source_is_never_reset_by_save(self):
        self.repository.directory.mkdir()
        self.repository.path.write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            self.repository.save(default_state())
        self.assertEqual(self.repository.path.read_bytes(), b"corrupt")

    def test_v2_metadata_is_independent_and_malformed_references_are_rejected(self):
        state = migrate_state(legacy_state())
        migrated = migrate_state(state)
        migrated["history"][0]["tags"].append("reading")
        self.assertEqual(state["history"][0]["tags"], [])
        for bad in ([], {}, "missing"):
            invalid = copy.deepcopy(state)
            invalid["history"][0]["task_id"] = bad
            with self.assertRaises(ValueError):
                validate_state(invalid)
        state["preferences"]["profile"] = []
        with self.assertRaises(ValueError):
            validate_state(state)


if __name__ == "__main__":
    unittest.main()
