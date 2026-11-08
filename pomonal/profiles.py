"""Named duration-profile use cases without terminal or persistence adapters."""

from .domain import CONFIG_KEYS, positive_integer


class Profiles:
    BUILTINS = {"classic": (25, 5, 15), "deep-work": (50, 10, 15)}

    def __init__(self, timer):
        self.timer = timer

    @staticmethod
    def _name(name):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Profile name must not be empty.")
        return name.strip().casefold()

    def list(self):
        result = {"default": dict(self.timer.state["config"])}
        result.update({name: dict(zip(CONFIG_KEYS, values)) for name, values in self.BUILTINS.items()})
        result.update({name: dict(values) for name, values in self.timer.state["profiles"].items()})
        return result

    def save(self, name, work, short_break, long_break):
        name = self._name(name)
        if name in ("default", *self.BUILTINS):
            raise ValueError("Built-in profile names cannot be overwritten.")
        values = (work, short_break, long_break)
        if not all(positive_integer(value) for value in values):
            raise ValueError("Profile durations must be positive finite integer minutes.")
        self.timer.state["profiles"][name] = dict(zip(CONFIG_KEYS, values))
        self.timer.save_changes()
        return dict(self.timer.state["profiles"][name])

    def use(self, name):
        name = self._name(name)
        self.resolve(name)
        self.timer.state["preferences"]["profile"] = name
        self.timer.save_changes()

    def resolve(self, name=None):
        name = self._name(name if name is not None else self.timer.state["preferences"]["profile"])
        profiles = self.list()
        if name not in profiles:
            raise ValueError("Unknown profile. Use profile list to see available profiles.")
        return profiles[name]
