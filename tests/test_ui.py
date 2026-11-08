"""Persistent workspace behavior and real Linux terminal integration."""

from datetime import datetime, timezone
import os
import termios
import unittest
from unittest.mock import patch

import test_cli
from test_core import FakeClock, MemoryRepository
from pomonal.application import Service
from pomonal.domain import new_session
from pomonal.infrastructure import JsonRepository, SystemClock
from pomonal.presentation import Terminal
from pomonal.workspace import Workspace


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.repository = MemoryRepository()
        self.clock = FakeClock()
        self.service = Service(self.repository, self.clock)
        self.workspace = Workspace(self.service)

    def seed_history(self, count):
        for index in range(count):
            session = new_session(f"Task {index}", "focus", 1, self.clock.now())
            session.update(status="completed", elapsed_seconds=60, ended_at=self.clock.now().isoformat())
            self.service.state["history"].append(session)

    def test_navigation_and_paging_do_not_pause_or_change_timer(self):
        self.seed_history(40)
        self.service.start("Active", 1)
        self.clock.advance(10)
        self.workspace.navigate("history")
        self.workspace.page(1, 20)
        self.assertEqual(self.workspace.offsets["history"], 12)
        self.service.tick()
        self.assertFalse(self.service.state["active"]["paused"])
        self.assertEqual(self.service.state["active"]["session"]["elapsed_seconds"], 10)
        first, page, total = self.workspace.content(20)
        self.assertIn("Task 33", first[0])
        self.assertEqual((page, total), (2, 7))
        self.workspace.navigate("stats")
        self.workspace.navigate("history")
        self.assertIn("Task 33", self.workspace.content(28)[0][0])
        self.assertEqual(self.workspace.offsets["history"], 12)

    def test_paging_clamps_and_empty_screens_are_readable(self):
        self.workspace.navigate("tasks")
        self.assertEqual(self.workspace.content(20), (("No queued tasks.",), 1, 1))
        self.workspace.navigate("history")
        self.assertIn("No finalized", self.workspace.content(20)[0][0])
        self.seed_history(20)
        for _ in range(10):
            self.workspace.page(1, 20)
        self.assertEqual(self.workspace.offsets["history"], 36)
        for _ in range(10):
            self.workspace.page(-1, 20)
        self.assertEqual(self.workspace.offsets["history"], 0)
        with self.assertRaises(ValueError):
            self.workspace.navigate("unknown")

    def test_embedded_parser_preserves_titles_and_invalid_commands_do_not_exit(self):
        terminal = Terminal(self.service, plain=True, keep_open=True)
        with patch("builtins.print"):
            for command in ('start --task', 'start --task "x" --minutes 0', 'start --help', 'start --task "unterminated'):
                with self.subTest(command=command), self.assertRaises(ValueError):
                    terminal.dispatch(command)
            self.assertIsNone(self.service.state["active"])
            terminal.dispatch('start --task "Mixed Case Title" --minutes 1')
            self.assertEqual(self.service.state["active"]["task"], "Mixed Case Title")
            terminal.dispatch("stop")
            self.assertFalse(terminal.exit_message)
            self.assertIsNone(self.service.state["active"])
            terminal.dispatch("config set work_minutes 30")
            self.assertEqual(self.service.state["config"]["work_minutes"], 30)
            self.assertEqual(terminal.workspace.screen, "settings")
            terminal.dispatch("quit")
            self.assertEqual(terminal.exit_message, "Workspace closed.")

    def test_embedded_history_limit_accepts_equals_syntax(self):
        self.seed_history(3)
        terminal = Terminal(self.service, plain=True, keep_open=True)
        with patch("builtins.print"):
            terminal.dispatch("history --limit=1")
        self.assertEqual(terminal.workspace.history_limit, 1)
        entries = terminal.workspace.content(28)[0]
        self.assertEqual(len(entries), 2)
        self.assertIn("Task 2", entries[0])


class UiTests(unittest.TestCase):
    setUp = test_cli.CliTests.setUp
    terminal = test_cli.CliTests.terminal
    resize = staticmethod(test_cli.CliTests.resize)
    wait_text = test_cli.CliTests.wait_text
    cli = test_cli.CliTests.cli

    def test_idle_start_stop_start_exit_recovery(self):
        process, master = self.terminal("ui")
        self.wait_text(master, "IDLE")
        os.write(master, b'start --task "First Task" --minutes 1\n')
        self.wait_text(master, "First Task")
        os.write(master, b"stop\n")
        self.wait_text(master, "Ready for another task")
        self.assertIsNone(process.poll())
        os.write(master, b'start --task "Second Task" --minutes 1\n')
        self.wait_text(master, "Second Task")
        os.write(master, b"exit\n")
        self.wait_text(master, "Cycle saved")
        self.assertEqual(process.wait(timeout=5), 0)
        self.assertEqual(termios.tcgetattr(master), self.original_termios[master])
        saved = JsonRepository(self.root).load()
        self.assertEqual(saved["active"]["task"], "Second Task")
        self.assertEqual(saved["history"][0]["status"], "cancelled")
        process, master = self.terminal("ui")
        self.wait_text(master, "resume or discard")
        os.write(master, b"discard\n")
        self.wait_text(master, "IDLE")
        os.write(master, b"quit\n")
        self.assertEqual(process.wait(timeout=5), 0)
        self.assertIsNone(JsonRepository(self.root).load()["active"])

    def test_navigation_keeps_counting_and_input_survives_resize(self):
        process, master = self.terminal("ui", size=(100, 28))
        self.wait_text(master, "IDLE")
        os.write(master, b'start --task "Navigation" --minutes 1\n')
        self.wait_text(master, "01:00")
        os.write(master, b"history\n")
        self.wait_text(master, "HISTORY")
        self.wait_text(master, "00:59")
        os.write(master, b"sett")
        self.wait_text(master, "> sett")
        self.resize(master, 64, 20)
        self.wait_text(master, "> sett")
        os.write(master, b"ings\n")
        self.wait_text(master, "SETTINGS")
        os.write(master, b"exit\n")
        self.assertEqual(process.wait(timeout=5), 0)
        active = JsonRepository(self.root).load()["active"]
        self.assertGreater(active["session"]["elapsed_seconds"], 0)
        self.assertFalse(active["paused"])

    def test_completion_on_another_screen_requires_explicit_continue(self):
        repository = JsonRepository(self.root)
        service = Service(repository, SystemClock())
        service.start("Completion", 1)
        service.state["active"]["session"]["elapsed_seconds"] = 58
        repository.save(service.state)
        process, master = self.terminal("ui")
        self.wait_text(master, "resume or discard")
        os.write(master, b"resume\n")
        self.wait_text(master, "Completion")
        os.write(master, b"stats\n")
        self.wait_text(master, "STATS")
        self.wait_text(master, "Ready for short_break")
        self.assertTrue(repository.load()["active"]["waiting"])
        os.write(master, b"settings\n")
        output = self.wait_text(master, "SETTINGS")
        self.assertIn("Ready for short_break", output)
        os.write(master, b"continue\n")
        self.wait_text(master, "05:00")
        os.write(master, b"exit\n")
        self.assertEqual(process.wait(timeout=5), 0)

    def test_plain_ui_invalid_commands_and_config(self):
        process, master = self.terminal("ui", "--plain")
        self.wait_text(master, "IDLE")
        os.write(master, b"start --task\n")
        output = self.wait_text(master, "argument --task")
        self.assertNotIn("Traceback", output)
        self.assertIsNone(process.poll())
        os.write(master, b"config set work_minutes 30\n")
        self.wait_text(master, "work_minutes = 30")
        os.write(master, b"tasks\n")
        self.wait_text(master, "No queued tasks")
        os.write(master, b"quit\n")
        output = self.wait_text(master, "Workspace closed")
        self.assertNotIn("\033", output)
        self.assertEqual(process.wait(timeout=5), 0)

    def test_history_paging_retains_position_across_screen_changes(self):
        repository = JsonRepository(self.root)
        state = repository.load()
        now = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
        for index in range(30):
            record = new_session(f"History {index:02d}", "focus", 1, now)
            record.update(status="completed", elapsed_seconds=60, ended_at=now.isoformat())
            state["history"].append(record)
        repository.save(state)
        process, master = self.terminal("ui", size=(80, 20))
        self.wait_text(master, "IDLE")
        os.write(master, b"history\n")
        self.wait_text(master, "HISTORY")
        os.write(master, b"next\n")
        self.wait_text(master, "Page 2/5")
        os.write(master, b"settings\n")
        self.wait_text(master, "SETTINGS")
        os.write(master, b"history\n")
        self.wait_text(master, "Page 2/5")
        os.write(master, b"quit\n")
        self.assertEqual(process.wait(timeout=5), 0)


if __name__ == "__main__":
    unittest.main()
