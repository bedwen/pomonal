"""Versioned persisted-state validation and lossless in-memory migration."""

from copy import deepcopy
from datetime import datetime
import math

from .domain import CONFIG_KEYS, PHASES, positive_integer


def _require(condition):
    if not condition:
        raise ValueError("Invalid saved data. The original file has been preserved.")


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _timestamp(value):
    _require(isinstance(value, str))
    try:
        _require(datetime.fromisoformat(value).utcoffset() is not None)
    except ValueError as error:
        raise ValueError("Invalid saved timestamp. The original file has been preserved.") from error


def _durations(value):
    _require(isinstance(value, dict))
    _require(all(positive_integer(value.get(key)) for key in CONFIG_KEYS))


def _tags(value):
    _require(isinstance(value, list) and all(_text(tag) for tag in value))
    _require(len({tag.casefold() for tag in value}) == len(value))


def validate_state(state):
    """Validate both known schemas without mutating their data."""
    _require(isinstance(state, dict))
    _require(all(key in state for key in ("version", "config", "history", "active")))
    version = state.get("version")
    _require(type(version) is int)
    if version not in (1, 2):
        raise ValueError("Unsupported saved data version. The original file has been preserved.")
    _durations(state["config"])
    _require(isinstance(state["history"], list))
    task_ids = set()
    if version == 2:
        _require(all(key in state for key in ("tasks", "profiles", "preferences", "goal")))
        _require(isinstance(state["tasks"], list))
        for task in state["tasks"]:
            _require(isinstance(task, dict))
            _require(_text(task.get("id")) and task["id"] not in task_ids)
            _require(_text(task.get("title")) and type(task.get("completed")) is bool)
            _timestamp(task.get("created_at"))
            task_ids.add(task["id"])
        profiles = state["profiles"]
        _require(isinstance(profiles, dict))
        for name, durations in profiles.items():
            _require(_text(name) and name not in ("default", "classic", "deep-work"))
            _durations(durations)
        preferences = state["preferences"]
        _require(isinstance(preferences, dict))
        _require(preferences.get("view") in ("dashboard", "focus"))
        _require(isinstance(preferences.get("profile"), str) and preferences["profile"] in {"default", "classic", "deep-work", *profiles})
        goal = state["goal"]
        if goal is not None:
            _require(isinstance(goal, dict) and goal.get("type") in ("sessions", "minutes"))
            _require(positive_integer(goal.get("target")))

    def metadata(value):
        _require(all(key in value for key in ("task_id", "note", "tags")))
        _require(value["task_id"] is None or (isinstance(value["task_id"], str) and value["task_id"] in task_ids))
        _require(isinstance(value["note"], str))
        _tags(value["tags"])

    def record(item, historical):
        _require(isinstance(item, dict))
        _require(all(key in item for key in ("id", "task", "phase", "status", "started_at", "ended_at")))
        _require(_text(item["id"]) and _text(item["task"]))
        _require(item["phase"] in PHASES)
        _require(item["status"] in (("completed", "cancelled", "interrupted") if historical else ("running",)))
        _require(all(_finite(item.get(key)) for key in ("planned_seconds", "elapsed_seconds")))
        _require(item["planned_seconds"] > 0 and 0 <= item["elapsed_seconds"] <= item["planned_seconds"])
        _timestamp(item["started_at"])
        if historical:
            _timestamp(item["ended_at"])
        else:
            _require(item["ended_at"] is None)
        if version == 2:
            metadata(item)

    ids = set()
    for item in state["history"]:
        record(item, True)
        _require(item["id"] not in ids)
        ids.add(item["id"])
    active = state["active"]
    if active is not None:
        _require(isinstance(active, dict))
        _require(all(key in active for key in ("task", "work_minutes", "completed_focus", "waiting", "paused", "session", "next_phase")))
        _require(_text(active["task"]) and positive_integer(active["work_minutes"]))
        _require(type(active["completed_focus"]) is int and active["completed_focus"] >= 0)
        _require(type(active["waiting"]) is bool and type(active["paused"]) is bool)
        if active["waiting"]:
            _require(active["session"] is None and active["next_phase"] in PHASES)
        else:
            _require(active["next_phase"] is None)
            record(active["session"], False)
            _require(active["session"]["id"] not in ids and active["session"]["task"] == active["task"])
        if version == 2:
            _require(all(key in active for key in ("durations", "task_id", "tags")))
            _durations(active["durations"])
            _require(active["durations"]["work_minutes"] == active["work_minutes"])
            _require(active["task_id"] is None or (isinstance(active["task_id"], str) and active["task_id"] in task_ids))
            _tags(active["tags"])
            if active["session"] is not None:
                _require(active["session"]["task_id"] == active["task_id"])


def migrate_state(state):
    """Return an independent version-2 value while preserving legacy fields."""
    validate_state(state)
    result = deepcopy(state)
    if result["version"] == 2:
        return result
    result.update(version=2, tasks=[], profiles={}, preferences={"view": "dashboard", "profile": "default"}, goal=None)
    records = list(result["history"])
    active = result["active"]
    if active is not None:
        durations = dict(result["config"])
        durations["work_minutes"] = active["work_minutes"]
        active.update(durations=durations, task_id=None, tags=[])
        if active["session"] is not None:
            records.append(active["session"])
    for record in records:
        record.update(task_id=None, note="", tags=[])
    validate_state(result)
    return result
