"""Local persistence and system clock adapters."""

from contextlib import contextmanager
from datetime import datetime
import fcntl
import json
import math
import os
from pathlib import Path
import time
import stat
from uuid import uuid4


class SystemClock:
    def monotonic(self):
        return time.monotonic()

    def now(self):
        return datetime.now().astimezone()

    def local_date(self, timestamp):
        return timestamp.astimezone().date()


def validate_state(state):
    """Reject malformed data rather than silently replacing user records."""
    def require(condition):
        if not condition:
            raise ValueError("Invalid saved data. The original file has been preserved.")

    from .domain import positive_integer

    def finite(value):
        try:
            return type(value) in (int, float) and math.isfinite(value)
        except OverflowError:
            return False

    require(isinstance(state, dict))
    require(all(key in state for key in ("version", "config", "history", "active")))
    require(type(state.get("version")) is int)
    if state["version"] != 1:
        raise ValueError("Unsupported saved data version. The original file has been preserved.")
    config = state.get("config")
    require(isinstance(config, dict))
    for key in ("work_minutes", "short_break_minutes", "long_break_minutes"):
        require(positive_integer(config.get(key)))
    require(isinstance(state.get("history"), list))

    def record(item, historical):
        require(isinstance(item, dict))
        for key in ("id", "task", "started_at", "status", "phase"):
            require(isinstance(item.get(key), str) and bool(item[key].strip()))
        require(item["phase"] in ("focus", "short_break", "long_break"))
        require(item["status"] in (("completed", "cancelled", "interrupted") if historical else ("running",)))
        for key in ("planned_seconds", "elapsed_seconds"):
            require(finite(item.get(key)))
        require(item["planned_seconds"] > 0 and 0 <= item["elapsed_seconds"] <= item["planned_seconds"])
        for key in ("started_at", "ended_at"):
            value = item.get(key)
            if key == "ended_at" and not historical:
                require(value is None)
            else:
                require(isinstance(value, str))
                require(datetime.fromisoformat(value).utcoffset() is not None)

    ids = set()
    for item in state["history"]:
        record(item, True)
        require(item["id"] not in ids)
        ids.add(item["id"])
    active = state.get("active")
    if active is not None:
        require(isinstance(active, dict))
        require(all(key in active for key in ("task", "work_minutes", "completed_focus",
                                              "waiting", "paused", "session", "next_phase")))
        require(isinstance(active.get("task"), str) and bool(active["task"].strip()))
        require(positive_integer(active.get("work_minutes")))
        require(type(active.get("completed_focus")) is int and active["completed_focus"] >= 0)
        require(type(active.get("waiting")) is bool and type(active.get("paused")) is bool)
        if active["waiting"]:
            require(active.get("session") is None)
            require(active.get("next_phase") in ("focus", "short_break", "long_break"))
        else:
            require(active.get("next_phase") is None)
            record(active.get("session"), False)
            require(active["session"]["id"] not in ids)
            require(active["session"]["task"] == active["task"])


class JsonRepository:
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        self.directory = self.root / "data"
        self.path = self.directory / "state.json"

    def _safe(self, path):
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise ValueError("Refusing a data path outside the project or a symbolic link.")
        if self.directory.is_symlink():
            raise ValueError("The data directory must not be a symbolic link.")

    @contextmanager
    def _directory(self, create=False):
        # Pin the directory inode: later path swaps cannot redirect writes.
        self._safe(self.directory)
        root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if create:
                try:
                    os.mkdir("data", mode=0o700, dir_fd=root_fd)
                except FileExistsError:
                    pass
            try:
                descriptor = os.open("data", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            except FileNotFoundError:
                if create:
                    raise
                yield None
                return
            try:
                yield descriptor
            finally:
                os.close(descriptor)
        finally:
            os.close(root_fd)

    @staticmethod
    def _regular(descriptor, name):
        try:
            metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("Data files must be regular files without symbolic or hard links.")

    def load(self):
        from .domain import default_state
        self._safe(self.path)
        with self._directory() as directory_fd:
            if directory_fd is None:
                return default_state()
            self._regular(directory_fd, "state.json")
            try:
                descriptor = os.open("state.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
            except FileNotFoundError:
                return default_state()
            try:
                with os.fdopen(descriptor, encoding="utf-8") as handle:
                    state = json.load(handle)
                validate_state(state)
            except (json.JSONDecodeError, UnicodeError, TypeError, KeyError, OverflowError) as error:
                raise ValueError("Invalid saved data. The original file has been preserved.") from error
            return state

    def save(self, state):
        validate_state(state)
        with self._directory(create=True) as directory_fd:
            self._regular(directory_fd, "state.json")
            name = f".state-{uuid4().hex}.tmp"
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory_fd)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(state, handle, indent=2, allow_nan=False)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                self._regular(directory_fd, "state.json")
                os.replace(name, "state.json", src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
                os.fsync(directory_fd)
            finally:
                try:
                    os.unlink(name, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass

    @contextmanager
    def writer(self):
        with self._directory(create=True) as directory_fd:
            self._regular(directory_fd, "writer.lock")
            descriptor = os.open("writer.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory_fd)
            try:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise ValueError("Another Pomonal writer is running. Close it before making changes.") from error
                yield
            finally:
                os.close(descriptor)
