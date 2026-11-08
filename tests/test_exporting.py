"""CSV fidelity and project-contained atomic export guarantees."""

import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pomonal.domain import new_session
from pomonal.exporting import COLUMNS, CsvExporter


TEMP_ROOT = Path(__file__).resolve().parent.parent / ".test-tmp"


class ExportTests(unittest.TestCase):
    def setUp(self):
        TEMP_ROOT.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=TEMP_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.exporter = CsvExporter(self.root)
        now = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
        self.record = new_session('Read "Algorithms", 日本語', "focus", 25, now)
        self.record.update(status="cancelled", elapsed_seconds=12.5, ended_at=now.isoformat(),
                           note='First line, "quoted".\nSecond line 日本語.', tags=["reading", "日本語"])

    def test_unicode_quotes_newlines_tags_and_numeric_values_round_trip(self):
        result = self.exporter.write([self.record], "reports/nested/sessions.csv")
        self.assertEqual(result, self.root / "reports/nested/sessions.csv")
        with result.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            self.assertEqual(tuple(reader.fieldnames), COLUMNS)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["task_id"], "")
        self.assertEqual(row["task_title"], self.record["task"])
        self.assertEqual(row["note"], self.record["note"])
        self.assertEqual(json.loads(row["tags"]), self.record["tags"])
        self.assertEqual(float(row["active_seconds"]), 12.5)
        self.assertEqual(int(row["planned_seconds"]), 1500)
        self.assertEqual(result.stat().st_mode & 0o777, 0o600)

    def test_empty_snapshot_still_has_header(self):
        target = self.exporter.write([], "empty.csv")
        with target.open(newline="") as handle:
            self.assertEqual(list(csv.reader(handle)), [list(COLUMNS)])

    def test_existing_destination_requires_explicit_overwrite(self):
        target = self.exporter.write([], "existing.csv")
        original = target.read_bytes()
        with self.assertRaises(FileExistsError):
            self.exporter.write([self.record], "existing.csv")
        self.assertEqual(target.read_bytes(), original)
        self.exporter.write([self.record], target, overwrite=True)
        self.assertNotEqual(target.read_bytes(), original)

    def test_invalid_external_hidden_and_protected_paths_write_nothing(self):
        cases = ("../outside.csv", self.root.parent / "outside.csv", "./relative.csv", "reports/../outside.csv",
                 ".hidden/file.csv", "reports/.hidden.csv", "data/export.csv", "pomonal/export.csv",
                 "tests/export.csv", "report.txt", "", ".", None)
        for output in cases:
            with self.subTest(output=output), self.assertRaises(ValueError):
                self.exporter.write([self.record], output)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_symlink_parent_target_and_hardlink_target_are_rejected(self):
        other = self.root / "other"
        other.mkdir()
        (self.root / "linked").symlink_to(other, target_is_directory=True)
        with self.assertRaises(OSError):
            self.exporter.write([], "linked/export.csv")
        original = other / "original.csv"
        original.write_text("unchanged")
        (self.root / "target.csv").symlink_to(original)
        with self.assertRaises(ValueError):
            self.exporter.write([], "target.csv", overwrite=True)
        os.link(original, self.root / "hard.csv")
        with self.assertRaises(ValueError):
            self.exporter.write([], "hard.csv", overwrite=True)
        self.assertEqual(original.read_text(), "unchanged")
        self.assertEqual(sorted(path.name for path in other.iterdir()), ["original.csv"])

    def test_directory_destination_is_rejected(self):
        (self.root / "directory.csv").mkdir()
        with self.assertRaises(ValueError):
            self.exporter.write([], "directory.csv", overwrite=True)

    def test_failed_replace_preserves_destination_and_removes_temporary(self):
        target = self.exporter.write([], "reports/export.csv")
        original = target.read_bytes()
        with patch("pomonal.exporting.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                self.exporter.write([self.record], "reports/export.csv", overwrite=True)
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(list(target.parent.glob("*.tmp")), [])

    def test_failed_write_preserves_destination_and_removes_temporary(self):
        target = self.exporter.write([], "export.csv")
        original = target.read_bytes()
        with patch("pomonal.exporting.os.fsync", side_effect=OSError("sync failed")):
            with self.assertRaises(OSError):
                self.exporter.write([self.record], "export.csv", overwrite=True)
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_no_overwrite_commit_cannot_clobber_racing_destination(self):
        original_link = os.link

        def race_before_link(*args, **kwargs):
            (self.root / "race.csv").write_text("created by another writer")
            return original_link(*args, **kwargs)

        with patch("pomonal.exporting.os.link", side_effect=race_before_link):
            with self.assertRaises(FileExistsError):
                self.exporter.write([], "race.csv")
        self.assertEqual((self.root / "race.csv").read_text(), "created by another writer")
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_pinned_parent_survives_directory_swap(self):
        parent = self.root / "reports"
        parent.mkdir()
        other = self.root / "other"
        other.mkdir()
        moved = self.root / "original-reports"
        original_replace = os.replace

        def swap_before_replace(*args, **kwargs):
            parent.rename(moved)
            parent.symlink_to(other, target_is_directory=True)
            return original_replace(*args, **kwargs)

        with patch("pomonal.exporting.os.replace", side_effect=swap_before_replace):
            self.exporter.write([self.record], "reports/export.csv", overwrite=True)
        self.assertEqual(list(other.iterdir()), [])
        self.assertTrue((moved / "export.csv").is_file())

    def test_invalid_and_active_records_are_rejected_before_creating_parents(self):
        running = dict(self.record, status="running")
        for records in ([running], [{}], None):
            with self.subTest(records=records), self.assertRaises(ValueError):
                self.exporter.write(records, "reports/export.csv")
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
