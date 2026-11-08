"""Presentation navigation and paging without owning timer rules or terminal I/O."""

from math import ceil

from .rendering import format_duration


class Workspace:
    SCREENS = ("timer", "tasks", "history", "stats", "settings")

    def __init__(self, service):
        self.service = service
        self.screen = "timer"
        self.offsets = {screen: 0 for screen in self.SCREENS}

    def navigate(self, screen):
        if screen not in self.SCREENS:
            raise ValueError("Unknown screen. Use timer, tasks, history, stats, or settings.")
        self.screen = screen

    @staticmethod
    def capacity(rows):
        return max(1, rows - 8)

    def _entries(self):
        state = self.service.state
        if self.screen == "tasks":
            return [f"{task['id']} | {'done' if task['completed'] else 'pending'} | {task['title']}"
                    for task in state["tasks"]] or ["No queued tasks."]
        if self.screen == "history":
            return [f"{item['task']} | {item['status']} | {format_duration(item['elapsed_seconds'])} | "
                    f"{item['phase']} | {item['started_at']} | {item['id']}"
                    for item in reversed(state["history"])] or ["No finalized sessions."]
        if self.screen == "settings":
            return [f"{key} = {value}" for key, value in state["config"].items()] + [
                f"view = {state['preferences']['view']}",
                f"profile = {state['preferences']['profile']}",
                'Change durations: config set work_minutes 30',
                'Change view: view dashboard / view focus',
            ]
        if self.screen == "stats":
            today = self.service.statistics("today")
            week = self.service.statistics("week")
            return [
                f"Today: {today['start_date']}",
                f"Completed focus sessions: {today['completed_sessions']}",
                f"Total focus time: {format_duration(today['focus_seconds'])}",
                f"Week: {week['start_date']} to {week['end_date']}",
                f"Weekly completed sessions: {week['completed_sessions']}",
                f"Weekly focus time: {format_duration(week['focus_seconds'])}",
            ] + [f"{task}: {format_duration(seconds)}" for task, seconds in sorted(week["tasks"].items())]
        return []

    def page(self, delta, rows):
        entries = self._entries()
        capacity = self.capacity(rows)
        offset = self.offsets[self.screen] + delta * capacity
        last_page = max(0, (len(entries) - 1) // capacity * capacity)
        self.offsets[self.screen] = min(max(0, offset), last_page)

    def content(self, rows):
        entries = self._entries()
        capacity = self.capacity(rows)
        offset = min(self.offsets[self.screen], max(0, len(entries) - 1))
        self.offsets[self.screen] = offset
        return tuple(entries[offset:offset + capacity]), offset // capacity + 1, max(1, ceil(len(entries) / capacity))
