"""Pure Pomodoro rules and serializable state constructors."""

from datetime import datetime
import math
from uuid import uuid4


CONFIG_KEYS = ("work_minutes", "short_break_minutes", "long_break_minutes")
PHASES = ("focus", "short_break", "long_break")


def positive_integer(value: object) -> bool:
    if type(value) is not int or value <= 0:
        return False
    try:
        return math.isfinite(value * 60)
    except OverflowError:
        return False


def default_state() -> dict:
    return {
        "version": 1,
        "config": {
            "work_minutes": 25,
            "short_break_minutes": 5,
            "long_break_minutes": 15,
        },
        "history": [],
        "active": None,
    }


def next_phase(phase: str, completed_focus: int) -> str:
    if phase != "focus":
        return "focus"
    return "long_break" if completed_focus % 4 == 0 else "short_break"


def new_session(task: str, phase: str, minutes: int, now: datetime) -> dict:
    return {
        "id": str(uuid4()),
        "task": task,
        "phase": phase,
        "started_at": now.isoformat(),
        "ended_at": None,
        "planned_seconds": minutes * 60,
        "elapsed_seconds": 0.0,
        "status": "running",
    }
