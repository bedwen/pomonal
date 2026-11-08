"""Saved task queue use cases, independent of terminal input and rendering."""

from uuid import uuid4


class Tasks:
    def __init__(self, timer):
        self.timer = timer

    @staticmethod
    def _title(title):
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Task title must not be empty.")
        return title.strip()

    def _task(self, task_id):
        task = next((item for item in self.timer.state["tasks"] if item["id"] == task_id), None)
        if task is None:
            raise ValueError("Unknown task ID. Use task list to find a task.")
        return task

    def add(self, title):
        task = {"id": str(uuid4()), "title": self._title(title),
                "created_at": self.timer.clock.now().isoformat(), "completed": False}
        self.timer.state["tasks"].append(task)
        self.timer.save_changes()
        return task

    def list(self, completed=False):
        if type(completed) is not bool:
            raise ValueError("Completed selection must be a boolean.")
        return [task for task in self.timer.state["tasks"] if task["completed"] == completed]

    def move(self, task_id, position):
        task = self._task(task_id)
        pending = self.list()
        if task["completed"]:
            raise ValueError("Only pending tasks can be moved.")
        if type(position) is not int or not 1 <= position <= len(pending):
            raise ValueError("Position must be between 1 and the pending task count.")
        pending.remove(task)
        pending.insert(position - 1, task)
        iterator = iter(pending)
        self.timer.state["tasks"][:] = [item if item["completed"] else next(iterator)
                                        for item in self.timer.state["tasks"]]
        self.timer.save_changes()

    def rename(self, task_id, title):
        task = self._task(task_id)
        task["title"] = self._title(title)
        self.timer.save_changes()
        return task

    def complete(self, task_id):
        task = self._task(task_id)
        if task["completed"]:
            raise ValueError("Task is already completed.")
        active = self.timer.state["active"]
        if active is not None and active["task_id"] == task_id:
            if self.timer.has_recovery:
                raise ValueError("Resume or discard the saved cycle before completing its task.")
            self.timer.tick()
            if not active["waiting"]:
                raise ValueError("Stop or finish the active phase before completing its task.")
            self.timer.state["active"] = None
        task["completed"] = True
        self.timer.state["tasks"].remove(task)
        self.timer.state["tasks"].append(task)
        self.timer.save_changes()
        return task

    def reopen(self, task_id):
        task = self._task(task_id)
        if not task["completed"]:
            raise ValueError("Task is already pending.")
        task["completed"] = False
        self.timer.state["tasks"].remove(task)
        self.timer.state["tasks"].append(task)
        self.timer.save_changes()
        return task

    def start(self, task_id, minutes=None, *, durations=None):
        task = self._task(task_id)
        if task["completed"]:
            raise ValueError("Reopen this task before starting it.")
        self.timer.start(task["title"], minutes, task_id=task["id"], durations=durations)
