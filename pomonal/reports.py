"""Shared finalized-session queries and calendar-aware focus reporting."""

from datetime import date, datetime, timedelta


class Reports:
    def __init__(self, state, clock):
        self.state = state
        self.clock = clock

    @staticmethod
    def _text(value, label):
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be nonempty text.")
        return value.strip()

    @staticmethod
    def _date(value):
        if value is None or type(value) is date:
            return value
        if isinstance(value, str):
            try:
                parsed = date.fromisoformat(value.strip())
                if parsed.isoformat() == value.strip():
                    return parsed
            except ValueError:
                pass
        raise ValueError("Date filters must use YYYY-MM-DD.")

    def query(self, *, task_id=None, tag=None, start_date=None, end_date=None):
        task_id = self._text(task_id, "Task ID")
        tag = self._text(tag, "Tag")
        tag = tag.casefold() if tag is not None else None
        first, last = self._date(start_date), self._date(end_date)
        if first is not None and last is not None and first > last:
            raise ValueError("Start date must not be after end date.")
        result = []
        for session in self.state["history"]:
            if session["status"] not in ("completed", "cancelled", "interrupted"):
                continue
            local_day = self.clock.local_date(datetime.fromisoformat(session["started_at"]))
            if task_id is not None and session.get("task_id") != task_id:
                continue
            if tag is not None and tag not in {value.strip().casefold() for value in session.get("tags", [])}:
                continue
            if first is not None and local_day < first or last is not None and local_day > last:
                continue
            result.append(session)
        return result

    def statistics(self, period="today", **filters):
        if period not in ("today", "week"):
            raise ValueError("Statistics period must be today or week.")
        today = self.clock.now().date()
        first = today if period == "today" else today - timedelta(days=today.weekday())
        last = today if period == "today" else first + timedelta(days=6)
        buckets = {
            first + timedelta(days=index): {"date": (first + timedelta(days=index)).isoformat(),
                                            "focus_seconds": 0.0, "completed_sessions": 0}
            for index in range(1 if period == "today" else 7)
        }
        result = {"period": period, "start_date": first.isoformat(), "end_date": last.isoformat(),
                  "completed_sessions": 0, "focus_seconds": 0.0, "tasks": {}, "days": list(buckets.values())}
        for session in self.query(**filters):
            day = self.clock.local_date(datetime.fromisoformat(session["started_at"]))
            if session["phase"] != "focus" or not first <= day <= last:
                continue
            completed = int(session["status"] == "completed")
            elapsed = session["elapsed_seconds"]
            result["completed_sessions"] += completed
            result["focus_seconds"] += elapsed
            task = session["task"]
            result["tasks"][task] = result["tasks"].get(task, 0.0) + elapsed
            buckets[day]["focus_seconds"] += elapsed
            buckets[day]["completed_sessions"] += completed
        return result
