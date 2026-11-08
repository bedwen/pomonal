"""Use cases, isolated from storage and terminal implementation details."""

from datetime import date, datetime
from typing import Protocol

from .domain import CONFIG_KEYS, new_session, next_phase, positive_integer
from .reports import Reports
from .annotations import normalize_tags


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def now(self) -> datetime: ...

    def local_date(self, timestamp: datetime) -> date: ...


class Repository(Protocol):
    def load(self) -> dict: ...

    def save(self, state: dict) -> None: ...


class Service:
    """Manage one persisted run with explicit phase transitions."""

    def __init__(self, repository: Repository, clock: Clock):
        self.repository = repository
        self.clock = clock
        self.state = repository.load()
        self.has_recovery = self.state["active"] is not None
        self._anchor = clock.monotonic()
        self._last_saved = self._anchor

    def _save(self) -> None:
        self.repository.save(self.state)
        self._last_saved = self.clock.monotonic()

    def _active(self) -> dict:
        if self.has_recovery:
            raise ValueError("Choose resume or discard for the saved run first.")
        active = self.state["active"]
        if active is None:
            raise ValueError("There is no active run.")
        return active

    def start(self, task: str, minutes: int | None = None, *, task_id: str | None = None,
              durations: dict | None = None, tags: list | tuple | None = None) -> None:
        if not isinstance(task, str) or not task.strip():
            raise ValueError("A nonempty task name is required.")
        if minutes is not None and not positive_integer(minutes):
            raise ValueError("Duration must be a positive integer number of minutes.")
        if self.state["active"] is not None:
            raise ValueError("An active or recoverable run already exists.")
        if task_id is not None:
            saved_task = next((item for item in self.state["tasks"] if item["id"] == task_id), None)
            if saved_task is None or saved_task["completed"]:
                raise ValueError("Choose an existing pending task.")
            task = saved_task["title"]
        source = self.state["config"] if durations is None else durations
        if not isinstance(source, dict) or not all(positive_integer(source.get(key)) for key in CONFIG_KEYS):
            raise ValueError("Cycle durations must contain positive finite integer minutes.")
        durations = {key: source[key] for key in CONFIG_KEYS}
        tags = normalize_tags([] if tags is None else tags)
        duration = minutes if minutes is not None else durations["work_minutes"]
        durations["work_minutes"] = duration
        self.state["active"] = {
            "task": task.strip(),
            "work_minutes": duration,
            "durations": durations,
            "task_id": task_id,
            "tags": list(tags),
            "completed_focus": 0,
            "waiting": False,
            "next_phase": None,
            "paused": False,
            "session": new_session(task.strip(), "focus", duration, self.clock.now()),
        }
        self.state["active"]["session"]["task_id"] = task_id
        self.state["active"]["session"]["tags"] = list(tags)
        self.has_recovery = False
        self._anchor = self.clock.monotonic()
        self._save()

    def recover(self, choice: str) -> None:
        if not self.has_recovery:
            raise ValueError("There is no saved run to recover.")
        if choice not in ("resume", "discard"):
            raise ValueError("Choose resume or discard.")
        active = self.state["active"]
        if choice == "discard":
            session = active["session"]
            if session is not None:
                session["status"] = "interrupted"
                session["ended_at"] = self.clock.now().isoformat()
                self.state["history"].append(session)
            self.state["active"] = None
        self.has_recovery = False
        self._anchor = self.clock.monotonic()
        self._save()

    def tick(self) -> None:
        active = self._active()
        now = self.clock.monotonic()
        delta = max(0.0, now - self._anchor)
        self._anchor = now
        session = active["session"]
        if not active["waiting"] and not active["paused"]:
            session["elapsed_seconds"] = min(
                session["planned_seconds"], session["elapsed_seconds"] + delta
            )
            if session["elapsed_seconds"] >= session["planned_seconds"]:
                session["status"] = "completed"
                session["ended_at"] = self.clock.now().isoformat()
                self.state["history"].append(session)
                if session["phase"] == "focus":
                    active["completed_focus"] += 1
                active["next_phase"] = next_phase(
                    session["phase"], active["completed_focus"]
                )
                active["session"] = None
                active["waiting"] = True
                self._save()
                return
        if now - self._last_saved >= 5:
            self._save()

    def pause(self) -> None:
        self.tick()
        active = self._active()
        if active["waiting"] or active["paused"]:
            raise ValueError("Only a running phase can be paused.")
        active["paused"] = True
        self._save()

    def resume(self) -> None:
        active = self._active()
        if active["waiting"] or not active["paused"]:
            raise ValueError("Only a paused phase can be resumed.")
        self._anchor = self.clock.monotonic()
        active["paused"] = False
        self._save()

    def stop(self) -> None:
        self.tick()
        active = self._active()
        session = active["session"]
        if session is not None:
            session["status"] = "cancelled"
            session["ended_at"] = self.clock.now().isoformat()
            self.state["history"].append(session)
        self.state["active"] = None
        self._save()

    def continue_cycle(self) -> None:
        active = self._active()
        if not active["waiting"]:
            raise ValueError("Continue is available only between phases.")
        phase = active["next_phase"]
        minutes = (
            active["work_minutes"] if phase == "focus"
            else active["durations"][f"{phase}_minutes"]
        )
        active["session"] = new_session(active["task"], phase, minutes, self.clock.now())
        active["session"]["task_id"] = active["task_id"]
        active["session"]["tags"] = list(active["tags"]) if phase == "focus" else []
        active["waiting"] = False
        active["paused"] = False
        active["next_phase"] = None
        self._anchor = self.clock.monotonic()
        self._save()

    def checkpoint(self) -> None:
        if self.has_recovery:
            return
        if self.state["active"] is not None:
            self.tick()
        self._save()

    def save_changes(self) -> None:
        """Persist productivity edits without counting unresolved recovery downtime."""
        if self.has_recovery:
            self._save()
        else:
            self.checkpoint()

    def set_config(self, key: str, value: int) -> None:
        if key not in CONFIG_KEYS:
            raise ValueError("Unknown configuration key.")
        if not positive_integer(value):
            raise ValueError("Duration must be a positive integer number of minutes.")
        self.state["config"][key] = value
        self._save()

    def history(self, limit: int = 20, **filters) -> list[dict]:
        if not positive_integer(limit):
            raise ValueError("History limit must be a positive integer.")
        return list(reversed(Reports(self.state, self.clock).query(**filters)[-limit:]))

    def statistics(self, period: str = "today", **filters) -> dict:
        return Reports(self.state, self.clock).statistics(period, **filters)
