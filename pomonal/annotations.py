"""Finalized-session notes and normalized tag use cases."""


def normalize_tags(values):
    if not isinstance(values, (list, tuple)):
        raise ValueError("Tags must be a list or tuple of nonempty text values.")
    result = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Tags must contain nonempty text values.")
        normalized = value.strip().casefold()
        if normalized not in result:
            result.append(normalized)
    return result


class Annotations:
    def __init__(self, timer):
        self.timer = timer

    def _record(self, session_id):
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("A finalized session ID is required.")
        for record in self.timer.state["history"]:
            if record["id"] == session_id and record["status"] in ("completed", "cancelled", "interrupted"):
                return record
        raise ValueError("Unknown finalized session ID. Active sessions cannot be annotated.")

    def note(self, session_id, text):
        if not isinstance(text, str):
            raise ValueError("A session note must be text.")
        record = self._record(session_id)
        record["note"] = text
        self.timer.save_changes()
        return record

    def tags(self, session_id, values):
        tags = normalize_tags(values)
        record = self._record(session_id)
        record["tags"] = tags
        self.timer.save_changes()
        return record
