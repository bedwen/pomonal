"""Local persistence and system clock adapters."""

from contextlib import contextmanager
from datetime import datetime
import fcntl
import json
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


from .state import migrate_state, validate_state


class JsonRepository:
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        self.directory = self.root / "data"
        self.path = self.directory / "state.json"
        self._writer_depth = 0
        self._loaded = False
        self._last_raw = None

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

    def _read_bytes(self, directory_fd, name="state.json"):
        if directory_fd is None:
            return None
        self._regular(directory_fd, name)
        try:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        except FileNotFoundError:
            return None
        with os.fdopen(descriptor, "rb") as handle:
            return handle.read()

    @staticmethod
    def _decode(raw):
        try:
            state = json.loads(raw)
            validate_state(state)
        except (json.JSONDecodeError, UnicodeError, TypeError, KeyError, OverflowError) as error:
            raise ValueError("Invalid saved data. The original file has been preserved.") from error
        return state

    def load(self):
        from .domain import default_state
        self._safe(self.path)
        with self._directory() as directory_fd:
            raw = self._read_bytes(directory_fd)
            state = default_state() if raw is None else migrate_state(self._decode(raw))
            self._loaded = True
            self._last_raw = raw
            return state

    def _backup_legacy(self, directory_fd, raw):
        """Durably verify an exact source backup before upgrading its document."""
        name = f"state-v1-{uuid4().hex}.backup.json"
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory_fd)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            if self._read_bytes(directory_fd, name) != raw:
                raise OSError("Could not verify the legacy state backup; original data was preserved.")
            os.fsync(directory_fd)
        except BaseException:
            os.unlink(name, dir_fd=directory_fd)
            raise
        return name

    def save(self, state):
        state = migrate_state(state)
        self._safe(self.path)
        # The CLI holds the writer across its session; direct callers receive the
        # same exclusion automatically without reacquiring an already-held lock.
        with self.writer():
            with self._directory(create=True) as directory_fd:
                raw = self._read_bytes(directory_fd)
                if self._loaded and raw != self._last_raw:
                    raise ValueError("Saved data changed since it was loaded. Reload before saving.")
                if raw is not None:
                    source = self._decode(raw)
                    if source["version"] == 1:
                        self._backup_legacy(directory_fd, raw)
                serialized = (json.dumps(state, indent=2, allow_nan=False) + "\n").encode("utf-8")
                name = f".state-{uuid4().hex}.tmp"
                descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o600, dir_fd=directory_fd)
                try:
                    with os.fdopen(descriptor, "wb") as handle:
                        handle.write(serialized)
                        handle.flush()
                        os.fsync(handle.fileno())
                    self._regular(directory_fd, "state.json")
                    # A non-cooperating editor is not covered by flock. Recheck
                    # the source just before committing the replacement too.
                    if self._read_bytes(directory_fd) != raw:
                        raise ValueError("Saved data changed during the write. Reload before saving.")
                    os.replace(name, "state.json", src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
                    self._last_raw = serialized
                    self._loaded = True
                    os.fsync(directory_fd)
                finally:
                    try:
                        os.unlink(name, dir_fd=directory_fd)
                    except FileNotFoundError:
                        pass

    @contextmanager
    def writer(self):
        if self._writer_depth:
            self._writer_depth += 1
            try:
                yield
            finally:
                self._writer_depth -= 1
            return
        with self._directory(create=True) as directory_fd:
            self._regular(directory_fd, "writer.lock")
            descriptor = os.open("writer.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory_fd)
            try:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise ValueError("Another Pomonal writer is running. Close it before making changes.") from error
                self._writer_depth = 1
                try:
                    yield
                finally:
                    self._writer_depth = 0
            finally:
                os.close(descriptor)
