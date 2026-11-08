"""Daily goals built on the shared finalized-session reporting contract."""

from .domain import positive_integer


class Goals:
    def __init__(self, timer):
        self.timer = timer

    def set(self, kind, target):
        if kind not in ("sessions", "minutes"):
            raise ValueError("Goal type must be sessions or minutes.")
        if not positive_integer(target):
            raise ValueError("Goal target must be a positive finite integer.")
        self.timer.state["goal"] = {"type": kind, "target": target}
        self.timer.save_changes()

    def clear(self):
        self.timer.state["goal"] = None
        self.timer.save_changes()

    def progress(self):
        goal = self.timer.state["goal"]
        if goal is None:
            return None
        report = self.timer.statistics("today")
        achieved = (report["completed_sessions"] if goal["type"] == "sessions"
                    else report["focus_seconds"] / 60)
        return {"type": goal["type"], "target": goal["target"], "achieved": achieved,
                "fraction": min(1.0, max(0.0, achieved / goal["target"]))}
