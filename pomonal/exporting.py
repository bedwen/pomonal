"""Project-contained atomic CSV snapshots using only descriptor-relative writes."""

from contextlib import contextmanager
import csv
import json
import os
from pathlib import Path
import stat
from uuid import uuid4


COLUMNS = ("session_id", "task_id", "task_title", "phase", "status", "started_at", "ended_at",
           "planned_seconds", "active_seconds", "note", "tags")


class CsvExporter:
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)

    def _relative(self, output):
        if not isinstance(output, (str, os.PathLike)) or not str(output).strip():
            raise ValueError("An export destination is required.")
        path = Path(output)
        if path.is_absolute():
            try:
                path = path.relative_to(self.root)
            except ValueError as error:
                raise ValueError("Export destinations must stay inside the project.") from error
        if not path.parts or any(part.startswith(".") for part in path.parts):
            raise ValueError("Export paths must not contain hidden, current, or parent directory components.")
        # Explicit './' components disappear in Path normalization; reject them
        # before opening anything, consistently with other hidden components.
        if "." in str(output).split("/"):
            raise ValueError("Export paths must not contain current-directory components.")
        if path.parts[0].casefold() in ("data", "pomonal", "tests"):
            raise ValueError("Exports must not replace files in protected product directories.")
        if path.suffix.casefold() != ".csv":
            raise ValueError("Export destinations must have a .csv extension.")
        return path

    @contextmanager
    def _parent(self, parts):
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            yield descriptor
        finally:
            os.close(descriptor)

    @staticmethod
    def _target(descriptor, name):
        try:
            metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("Export targets must be regular files without symbolic or hard links.")
        return True

    @staticmethod
    def _rows(records):
        result = []
        try:
            for record in records:
                if record["status"] not in ("completed", "cancelled", "interrupted"):
                    raise ValueError("Only finalized sessions can be exported.")
                result.append({
                    "session_id": record["id"], "task_id": record.get("task_id") or "",
                    "task_title": record["task"], "phase": record["phase"], "status": record["status"],
                    "started_at": record["started_at"], "ended_at": record["ended_at"],
                    "planned_seconds": record["planned_seconds"], "active_seconds": record["elapsed_seconds"],
                    "note": record.get("note", ""), "tags": json.dumps(record.get("tags", []), ensure_ascii=False),
                })
        except (TypeError, KeyError) as error:
            raise ValueError("Export records must be finalized session records.") from error
        return result

    def write(self, records, output, overwrite=False):
        if type(overwrite) is not bool:
            raise ValueError("Overwrite must be explicitly enabled with a boolean.")
        relative = self._relative(output)
        rows = self._rows(records)
        with self._parent(relative.parts[:-1]) as descriptor:
            name = relative.name
            if self._target(descriptor, name) and not overwrite:
                raise FileExistsError("Export already exists. Use --overwrite to replace it.")
            temporary = f".export-{uuid4().hex}.tmp"
            handle_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                0o600, dir_fd=descriptor)
            try:
                with os.fdopen(handle_fd, "w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=COLUMNS)
                    writer.writeheader()
                    writer.writerows(rows)
                    handle.flush()
                    os.fsync(handle.fileno())
                self._target(descriptor, name)
                if overwrite:
                    os.replace(temporary, name, src_dir_fd=descriptor, dst_dir_fd=descriptor)
                else:
                    # link is an atomic no-clobber commit; it cannot replace a
                    # destination created after the earlier existence check.
                    os.link(temporary, name, src_dir_fd=descriptor, dst_dir_fd=descriptor,
                            follow_symlinks=False)
                    os.unlink(temporary, dir_fd=descriptor)
                os.fsync(descriptor)
            finally:
                try:
                    os.unlink(temporary, dir_fd=descriptor)
                except FileNotFoundError:
                    pass
        return self.root / relative
