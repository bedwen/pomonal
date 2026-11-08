"""Use cases, isolated from storage and terminal implementation details."""

from datetime import date, datetime, timedelta
from typing import Protocol

from .domain import CONFIG_KEYS, new_session, next_phase, positive_integer


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

    def start(self, task: str, minutes: int | None = None) -> None:
        if not isinstance(task, str) or not task.strip():
            raise ValueError("A nonempty task name is required.")
        if minutes is not None and not positive_integer(minutes):
            raise ValueError("Duration must be a positive integer number of minutes.")
        if self.state["active"] is not None:
            raise ValueError("An active or recoverable run already exists.")
        duration = minutes if minutes is not None else self.state["config"]["work_minutes"]
        self.state["active"] = {
            "task": task.strip(),
            "work_minutes": duration,
            "completed_focus": 0,
            "waiting": False,
            "next_phase": None,
            "paused": False,
            "session": new_session(task.strip(), "focus", duration, self.clock.now()),
        }
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
            else self.state["config"][f"{phase}_minutes"]
        )
        active["session"] = new_session(active["task"], phase, minutes, self.clock.now())
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

    def set_config(self, key: str, value: int) -> None:
        if key not in CONFIG_KEYS:
            raise ValueError("Unknown configuration key.")
        if not positive_integer(value):
            raise ValueError("Duration must be a positive integer number of minutes.")
        self.state["config"][key] = value
        self._save()

    def history(self, limit: int = 20) -> list[dict]:
        if not positive_integer(limit):
            raise ValueError("History limit must be a positive integer.")
        return list(reversed(self.state["history"][-limit:]))

    def statistics(self, period: str = "today") -> dict:
        if period not in ("today", "week"):
            raise ValueError("Statistics period must be today or week.")
        now = self.clock.now()
        today = now.date()
        first = today if period == "today" else today - timedelta(days=today.weekday())
        last = today if period == "today" else first + timedelta(days=6)
        result = {
            "period": period,
            "start_date": first.isoformat(),
            "end_date": last.isoformat(),
            "completed_sessions": 0,
            "focus_seconds": 0.0,
            "tasks": {},
        }
        for session in self.state["history"]:
            day = self.clock.local_date(datetime.fromisoformat(session["started_at"]))
            if session["phase"] != "focus" or not first <= day <= last:
                continue
            result["completed_sessions"] += session["status"] == "completed"
            elapsed = session["elapsed_seconds"]
            result["focus_seconds"] += elapsed
            task = session["task"]
            result["tasks"][task] = result["tasks"].get(task, 0.0) + elapsed
        return result
