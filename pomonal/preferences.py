"""Application preferences kept independent of terminal drawing."""


class Preferences:
    def __init__(self, state, save):
        self.state = state
        self.save = save

    @property
    def view(self):
        return self.state["preferences"]["view"]

    def set_view(self, value):
        if value not in ("dashboard", "focus"):
            raise ValueError("View must be dashboard or focus.")
        self.state["preferences"]["view"] = value
        self.save()
