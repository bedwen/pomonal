"""CLI and real Linux pseudo-terminal integration tests."""

import json
import fcntl
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import subprocess
import sys
import struct
import termios
import tempfile
import time
import unittest

from pomonal.application import Service
from pomonal.infrastructure import JsonRepository, SystemClock


ROOT = Path(__file__).resolve().parent.parent
TEMP_ROOT = ROOT / ".test-tmp"


class CliTests(unittest.TestCase):
    def setUp(self):
        TEMP_ROOT.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=TEMP_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / "pomonal", self.root / "pomonal", ignore=shutil.ignore_patterns("__pycache__"))
        self.environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "TERM": "xterm"}
        self.environment.pop("PYTHONPATH", None)
        self.environment.pop("NO_COLOR", None)
        self.environment.pop("COLORTERM", None)
        self.original_termios = {}

    def cli(self, *args):
        return subprocess.run([sys.executable, "-B", "-m", "pomonal", *args],
                              cwd=self.root, env=self.environment, capture_output=True, text=True, timeout=5)

    def terminal(self, *args, size=(80, 24), environment=None, code=None):
        master, slave = pty.openpty()
        self.resize(master, *size)
        self.original_termios[master] = termios.tcgetattr(slave)
        invocation = ([sys.executable, "-B", "-m", "pomonal", *args] if code is None else
                      [sys.executable, "-B", "-c", code, *args])
        process = subprocess.Popen(invocation,
                                   stdin=slave, stdout=slave, stderr=slave,
                                   cwd=self.root, env=environment if environment is not None else self.environment)
        os.close(slave)

        def cleanup():
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            os.close(master)

        self.addCleanup(cleanup)
        return process, master

    @staticmethod
    def resize(master, columns, rows):
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))

    def wait_text(self, master, expected, timeout=6):
        output = b""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    output += os.read(master, 65536)
                except OSError:
                    break
                if expected.encode() in output:
                    return output.decode(errors="replace")
        self.fail(f"Did not see {expected!r}; terminal output: {output!r}")

    def test_public_read_commands_and_config(self):
        result = self.cli("config", "show")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("work_minutes = 25", result.stdout)
        self.assertFalse((self.root / "data").exists())
        self.assertEqual(self.cli("config", "set", "work_minutes", "30").returncode, 0)
        self.assertIn("work_minutes = 30", self.cli("config", "show").stdout)
        self.assertIn("No finalized", self.cli("history").stdout)
        for period in ("--today", "--week"):
            self.assertIn("Total focus time: 00:00", self.cli("stats", period).stdout)

    def test_cli_errors_are_clear(self):
        for args in (("start", "--task", "Read"), ("start", "--task", "Read", "--minutes", "0"),
                     ("config", "set", "work_minutes", "-1"), ("unknown",),
                     ("config", "set", "work_minutes", str(10**400))):
            result = self.cli(*args)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Traceback", result.stderr)
        (self.root / "data").mkdir(exist_ok=True)
        path = self.root / "data" / "state.json"
        path.write_text("broken")
        result = self.cli("history")
        self.assertIn("original file has been preserved", result.stderr)
        self.assertEqual(path.read_text(), "broken")

    def test_actual_terminal_commands_countdown_interrupt_and_recovery(self):
        process, master = self.terminal("start", "--task", "Smoke", "--minutes", "1")
        self.wait_text(master, "01:00")
        os.write(master, b"hel")
        output = self.wait_text(master, "00:59")
        self.assertIn("hel", output)
        os.write(master, b"p\n")
        self.wait_text(master, "Commands: pause")
        os.write(master, b"invalid\n")
        self.wait_text(master, "Unknown command")
        os.write(master, b"pause\n")
        self.wait_text(master, "PAUSED")
        os.write(master, b"resume\n")
        self.wait_text(master, "> resume")
        process.send_signal(signal.SIGINT)
        self.wait_text(master, "Cycle saved")
        self.assertEqual(process.wait(timeout=5), 0)
        saved = json.loads((self.root / "data" / "state.json").read_text())
        self.assertIsNotNone(saved["active"])
        process, master = self.terminal("start", "--task", "Ignored")
        self.wait_text(master, "resume or discard")
        os.write(master, b"resume\n")
        self.wait_text(master, "Smoke")
        os.write(master, b"stop\n")
        self.assertEqual(process.wait(timeout=5), 0)
        saved = json.loads((self.root / "data" / "state.json").read_text())
        self.assertIsNone(saved["active"])
        self.assertEqual(saved["history"][0]["status"], "cancelled")

    def test_terminal_completion_requires_confirmation(self):
        repository = JsonRepository(self.root)
        service = Service(repository, SystemClock())
        service.start("Near completion", 1)
        service.state["active"]["session"]["elapsed_seconds"] = 59
        repository.save(service.state)
        process, master = self.terminal("start", "--task", "Ignored")
        self.wait_text(master, "resume or discard")
        os.write(master, b"resume\n")
        self.wait_text(master, "Ready for short_break")
        saved = repository.load()
        self.assertTrue(saved["active"]["waiting"])
        self.assertEqual(len(saved["history"]), 1)
        os.write(master, b"continue\n")
        self.wait_text(master, "05:00")
        os.write(master, b"stop\n")
        self.assertEqual(process.wait(timeout=5), 0)
        saved = repository.load()
        self.assertEqual([row["status"] for row in saved["history"]], ["completed", "cancelled"])
        self.assertEqual(saved["history"][1]["phase"], "short_break")

    def test_fullscreen_blue_and_resize_preserves_input(self):
        process, master = self.terminal("start", "--task", "Resize", "--minutes", "1", size=(100, 28))
        output = self.wait_text(master, "01:00")
        self.assertIn("\033[?1049h", output)
        self.assertIn("\033[94m", output)
        self.assertIn("##", output)
        os.write(master, b"pau")
        self.wait_text(master, "> pau")
        for columns, rows in ((64, 20), (40, 10), (100, 28)):
            self.resize(master, columns, rows)
            output = self.wait_text(master, "> pau")
            self.assertIn(f"\033[{rows};", output)
        os.write(master, b"se\n")
        self.wait_text(master, "PAUSED")
        os.write(master, b"stop\n")
        output = self.wait_text(master, "Cycle stopped.")
        self.assertIn("\033[0m\033[?1049l", output)
        self.assertEqual(process.wait(timeout=5), 0)
        self.assertEqual(termios.tcgetattr(master), self.original_termios[master])

    def test_truecolor_and_no_color(self):
        for extra, expected in (({"COLORTERM": "truecolor"}, "\033[38;2;96;165;250m"),
                                ({"COLORTERM": "truecolor", "NO_COLOR": ""}, None)):
            environment = {**self.environment, **extra}
            process, master = self.terminal("start", "--task", "Colors", environment=environment)
            output = self.wait_text(master, "25:00")
            self.assertIn("\033[?1049h", output)
            if expected:
                self.assertIn(expected, output)
            else:
                # Screen control remains active, but color attributes are absent.
                self.assertNotRegex(output.replace("\033[0m", ""), r"\x1b\[[0-9;]*m")
            os.write(master, b"stop\n")
            self.assertEqual(process.wait(timeout=5), 0)

    def test_plain_and_dumb_fallback_have_no_screen_controls(self):
        for term, explicit in (("xterm", True), ("dumb", False), (None, False)):
            environment = dict(self.environment)
            if term is None:
                environment.pop("TERM", None)
            else:
                environment["TERM"] = term
            extra = ("--plain",) if explicit else ()
            process, master = self.terminal("start", "--task", "Plain", *extra, environment=environment)
            output = self.wait_text(master, "25:00")
            self.assertNotIn("\033", output)
            os.write(master, b"stop\n")
            output = self.wait_text(master, "Cycle stopped.")
            self.assertNotIn("\033", output)
            self.assertEqual(process.wait(timeout=5), 0)
            self.assertEqual(termios.tcgetattr(master), self.original_termios[master])

    def test_interrupt_and_render_failure_restore_terminal(self):
        for failure in (False, True):
            code = None
            if failure:
                code = ("from pomonal import presentation; "
                        "presentation.render_frame = lambda *a, **k: (_ for _ in ()).throw(OSError('render failure')); "
                        "raise SystemExit(presentation.main())")
            process, master = self.terminal("start", "--task", "Cleanup", code=code)
            if failure:
                output = self.wait_text(master, "Error: render failure")
                self.assertEqual(process.wait(timeout=5), 1)
            else:
                output = self.wait_text(master, "25:00")
                process.send_signal(signal.SIGINT)
                output += self.wait_text(master, "Cycle saved")
                self.assertEqual(process.wait(timeout=5), 0)
            self.assertIn("\033[?1049h", output)
            self.assertIn("\033[0m\033[?1049l", output)
            # Visibility is never changed, preserving an initially hidden cursor.
            self.assertNotIn("\033[?25l", output)
            self.assertNotIn("\033[?25h", output)
            self.assertEqual(termios.tcgetattr(master), self.original_termios[master])
            recovery = Service(JsonRepository(self.root), SystemClock())
            if recovery.has_recovery:
                recovery.recover("discard")


    def test_view_selection_persists_across_recovery(self):
        process, master = self.terminal("start", "--task", "Views", size=(100, 28))
        self.wait_text(master, "TODAY")
        os.write(master, b"view focus\n")
        output = self.wait_text(master, "View: focus")
        self.assertNotIn("TODAY", output)
        process.send_signal(signal.SIGINT)
        self.wait_text(master, "Cycle saved")
        self.assertEqual(process.wait(timeout=5), 0)
        saved = json.loads((self.root / "data" / "state.json").read_text())
        self.assertEqual(saved["preferences"]["view"], "focus")
        process, master = self.terminal("start", "--task", "Ignored", size=(100, 28))
        self.wait_text(master, "resume or discard")
        os.write(master, b"resume\n")
        output = self.wait_text(master, "Views")
        self.assertNotIn("TODAY", output)
        os.write(master, b"view dashboard\n")
        self.wait_text(master, "TODAY")
        os.write(master, b"stop\n")
        self.assertEqual(process.wait(timeout=5), 0)


    def test_saved_task_cli_and_workspace_start(self):
        added = self.cli("task", "add", "Queued Reading")
        self.assertEqual(added.returncode, 0, added.stderr)
        path = self.root / "data" / "state.json"
        task_id = json.loads(path.read_text())["tasks"][0]["id"]
        self.assertIn(task_id, self.cli("task", "list").stdout)
        self.assertEqual(self.cli("task", "rename", task_id, "Focused Reading").returncode, 0)
        process, master = self.terminal("ui")
        self.wait_text(master, "IDLE")
        os.write(master, f"start --task-id {task_id} --minutes 1\n".encode())
        self.wait_text(master, "01:00")
        os.write(master, b"stop\n")
        self.wait_text(master, "Ready for another task")
        os.write(master, f"task done {task_id}\n".encode())
        self.wait_text(master, "Task updated")
        os.write(master, b"exit\n")
        self.assertEqual(process.wait(timeout=5), 0)
        self.assertIn("Focused Reading", self.cli("task", "list", "--completed").stdout)
        state = json.loads(path.read_text())
        self.assertEqual(state["history"][0]["task_id"], task_id)
        self.assertEqual(state["history"][0]["task"], "Focused Reading")
        self.assertEqual(self.cli("task", "reopen", task_id).returncode, 0)
        self.assertIn(task_id, self.cli("task", "list").stdout)
        self.assertNotEqual(self.cli("task", "move", task_id, "--position", "2").returncode, 0)


    def test_profile_selection_override_and_workspace_settings(self):
        saved = self.cli("profile", "save", "Reading", "--work", "40", "--short-break", "5", "--long-break", "15")
        self.assertEqual(saved.returncode, 0, saved.stderr)
        self.assertEqual(self.cli("profile", "use", "READING").returncode, 0)
        self.assertIn("* reading", self.cli("profile", "list").stdout)
        process, master = self.terminal("ui")
        self.wait_text(master, "IDLE")
        os.write(master, b'start --task "Profile test" --profile deep-work --minutes 1\n')
        self.wait_text(master, "01:00")
        os.write(master, b"profile save reading --work 60 --short-break 2 --long-break 8\n")
        self.wait_text(master, "Profile saved")
        os.write(master, b"exit\n")
        self.assertEqual(process.wait(timeout=5), 0)
        state = json.loads((self.root / "data" / "state.json").read_text())
        self.assertEqual(state["active"]["durations"],
                         {"work_minutes": 1, "short_break_minutes": 10, "long_break_minutes": 15})
        self.assertEqual(state["profiles"]["reading"]["work_minutes"], 60)
        self.assertNotEqual(self.cli("profile", "save", "classic", "--work", "1", "--short-break", "1", "--long-break", "1").returncode, 0)


    def test_daily_goals_cli_and_workspace_dashboard(self):
        self.assertIn("disabled", self.cli("goal", "show").stdout)
        self.assertFalse((self.root / "data").exists())
        self.assertEqual(self.cli("goal", "set", "--sessions", "2").returncode, 0)
        process, master = self.terminal("ui", size=(100, 28))
        self.wait_text(master, "Goal: 0/2 sessions")
        os.write(master, b"goal set --minutes 10\n")
        self.wait_text(master, "Goal: 0/10 minutes")
        os.write(master, b"goal clear\n")
        self.wait_text(master, "Daily goal: disabled")
        os.write(master, b"exit\n")
        self.assertEqual(process.wait(timeout=5), 0)
        self.assertIn("disabled", self.cli("goal", "show").stdout)
        self.assertNotEqual(self.cli("goal", "set", "--sessions", "2", "--minutes", "10").returncode, 0)


    def test_weekly_chart_redirect_and_workspace(self):
        output = self.cli("stats", "--week")
        self.assertEqual(output.returncode, 0, output.stderr)
        self.assertNotIn("\033", output.stdout)
        for day in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"):
            self.assertIn(day + " ", output.stdout)
        process, master = self.terminal("ui", size=(100, 28))
        self.wait_text(master, "IDLE")
        os.write(master, b"stats\n")
        output = self.wait_text(master, "Weekly focus by day")
        self.assertIn("\033[94m", output)
        self.assertIn("Mon ", output)
        os.write(master, b"exit\n")
        self.assertEqual(process.wait(timeout=5), 0)


if __name__ == "__main__":
    unittest.main()
