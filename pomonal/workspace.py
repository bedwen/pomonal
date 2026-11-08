"""Presentation navigation and paging without owning timer rules or terminal I/O."""

from math import ceil

from .rendering import daily_bars, format_duration
from .profiles import Profiles
from .goals import Goals


class Workspace:
    SCREENS = ("timer", "tasks", "history", "stats", "settings")

    def __init__(self, service):
        self.service = service
        self.screen = "timer"
        self.offsets = {screen: 0 for screen in self.SCREENS}
        self.completed_tasks = False
        self.history_filters = {}
        self.history_limit = None
        self.stats_filters = {}
        self.stats_period = "week"

    def navigate(self, screen):
        if screen not in self.SCREENS:
            raise ValueError("Unknown screen. Use timer, tasks, history, stats, or settings.")
        self.screen = screen

    @staticmethod
    def capacity(rows):
        return max(1, rows - 8)

    def _entries(self, columns=80):
        state = self.service.state
        if self.screen == "tasks":
            return [f"{task['id']} | {'done' if task['completed'] else 'pending'} | {task['title']}"
                    for task in state["tasks"] if task["completed"] == self.completed_tasks] or ["No queued tasks."]
        if self.screen == "history":
            result = []
            limit = self.history_limit or max(1, len(state["history"]))
            for item in self.service.history(limit, **self.history_filters):
                result.extend([f"{item['task']} | {item['status']} | {format_duration(item['elapsed_seconds'])} | {item['started_at'][:16]}", item["id"]])
                if item["tags"]:
                    result.append("Tags: " + ", ".join(item["tags"]))
                if item["note"]:
                    result.append("Note: " + item["note"])
            return result or ["No finalized sessions."]
        if self.screen == "settings":
            return [f"{key} = {value}" for key, value in state["config"].items()] + [
                f"view = {state['preferences']['view']}",
                f"profile = {state['preferences']['profile']}",
                f"Daily goal: {state['goal'] or 'disabled'}",
                'Change durations: config set work_minutes 30',
                'Change view: view dashboard / view focus',
                'Profiles: profile list / profile use NAME',
            ] + [f"{name}: {values['work_minutes']}/{values['short_break_minutes']}/{values['long_break_minutes']}"
                 for name, values in Profiles(self.service).list().items()]
        if self.screen == "stats":
            today = self.service.statistics("today", **self.stats_filters)
            week = self.service.statistics(self.stats_period, **self.stats_filters)
            goal = Goals(self.service).progress()
            return [
                f"Today: {today['start_date']}",
                f"Completed focus sessions: {today['completed_sessions']}",
                f"Total focus time: {format_duration(today['focus_seconds'])}",
                f"Week: {week['start_date']} to {week['end_date']}",
                f"Weekly completed sessions: {week['completed_sessions']}",
                f"Weekly focus time: {format_duration(week['focus_seconds'])}",
            ] + ["Weekly focus by day:", *daily_bars(week["days"], max(1, columns - 3))] + ([f"Daily goal: {goal['achieved']:g}/{goal['target']} {goal['type']}"] if goal else []) + [f"{task}: {format_duration(seconds)}" for task, seconds in sorted(week["tasks"].items())]
        return []

    def page(self, delta, rows):
        entries = self._entries()
        capacity = self.capacity(rows)
        offset = self.offsets[self.screen] + delta * capacity
        last_page = max(0, (len(entries) - 1) // capacity * capacity)
        self.offsets[self.screen] = min(max(0, offset), last_page)

    def content(self, rows, columns=80):
        entries = self._entries(columns)
        capacity = self.capacity(rows)
        offset = min(self.offsets[self.screen], max(0, len(entries) - 1))
        self.offsets[self.screen] = offset
        return tuple(entries[offset:offset + capacity]), offset // capacity + 1, max(1, ceil(len(entries) / capacity))
