"""Saved view changes use application persistence without changing timer rules."""

import unittest

from pomonal.application import Service
from pomonal.preferences import Preferences
from test_core import FakeClock, MemoryRepository


class PreferenceTests(unittest.TestCase):
    def test_view_persists_without_pausing_or_resetting_time(self):
        repository, clock = MemoryRepository(), FakeClock()
        service = Service(repository, clock)
        service.start("Read", 1)
        clock.advance(12)
        preferences = Preferences(service.state, service.checkpoint)
        preferences.set_view("focus")
        self.assertEqual(repository.saved["preferences"]["view"], "focus")
        self.assertFalse(service.state["active"]["paused"])
        self.assertEqual(service.state["active"]["session"]["elapsed_seconds"], 12)
        recovered = Service(repository, clock)
        self.assertEqual(Preferences(recovered.state, recovered.checkpoint).view, "focus")
        with self.assertRaises(ValueError):
            preferences.set_view("unknown")
        self.assertEqual(preferences.view, "focus")


if __name__ == "__main__":
    unittest.main()
