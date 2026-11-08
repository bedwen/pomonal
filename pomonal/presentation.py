"""Command parsing and Linux terminal presentation."""

import argparse
import codecs
from contextlib import nullcontext
import math
import os
from pathlib import Path
import select
import shlex
import sys
import termios
import time
import tty

from .application import Service
from .domain import positive_integer
from .infrastructure import JsonRepository, SystemClock
from .preferences import Preferences
from .rendering import DisplaySnapshot, clean_text, clip_text, render_frame
from .workspace import Workspace


def positive(value):
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Enter a positive integer.") from error
    if not positive_integer(number):
        raise argparse.ArgumentTypeError("Enter a positive integer within the supported finite duration range.")
    return number


class EmbeddedParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)

    def exit(self, status=0, message=None):
        raise ValueError(message or "Use help for available workspace commands.")


def parser(*, embedded=False):
    parser_type = EmbeddedParser if embedded else argparse.ArgumentParser
    result = parser_type(prog="python3 -m pomonal", description="Local terminal Pomodoro timer.")
    commands = result.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="Start or recover a focus cycle.")
    start.add_argument("--task", required=True)
    start.add_argument("--minutes", type=positive)
    start.add_argument("--plain", action="store_true", help="Use the compact interface without screen controls.")
    ui = commands.add_parser("ui", help="Open the persistent terminal workspace.")
    ui.add_argument("--plain", action="store_true", help="Use a line-oriented workspace.")
    history = commands.add_parser("history", help="Show finalized sessions.")
    history.add_argument("--limit", type=positive, default=20)
    stats = commands.add_parser("stats", help="Show focus statistics.")
    period = stats.add_mutually_exclusive_group()
    period.add_argument("--today", action="store_true")
    period.add_argument("--week", action="store_true")
    config = commands.add_parser("config", help="Inspect or change durations.")
    actions = config.add_subparsers(dest="action", required=True)
    actions.add_parser("show")
    change = actions.add_parser("set")
    change.add_argument("key", choices=("work_minutes", "short_break_minutes", "long_break_minutes"))
    change.add_argument("value", type=positive)
    return result


def clean(value):
    """Prevent user text from injecting terminal controls."""
    return clean_text(str(value))


def duration(seconds):
    seconds = max(0, math.ceil(seconds))
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes:02d}:{seconds:02d}"


class Terminal:
    """Coordinate terminal lifecycle, editable commands, and pure rendering."""

    def __init__(self, service, *, plain=False, keep_open=False):
        self.service = service
        self.keep_open = keep_open
        self.workspace = Workspace(service)
        self.preferences = Preferences(service.state, service.checkpoint)
        self.buffer = ""
        self.feedback = "Type commands and press Enter. Ctrl+C saves the cycle."
        if keep_open and service.state["active"] is None:
            self.feedback = 'Idle: start --task "Title"; help for commands.'
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.fullscreen = not plain and os.environ.get("TERM", "").lower() not in ("", "dumb")
        self.color = "NO_COLOR" not in os.environ
        self.truecolor = os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")
        self.exit_message = ""
        self._escape = ""

    @staticmethod
    def dimensions():
        try:
            size = os.get_terminal_size(sys.stdout.fileno())
            return (max(1, size.columns), max(1, size.lines)) if size.columns and size.lines else (80, 24)
        except OSError:
            return (80, 24)

    def message(self, text):
        self.feedback = clean(text)
        if not self.fullscreen:
            print(self.feedback, flush=True)

    def saved_exit_message(self):
        return ("Cycle saved. Use start or ui to recover it." if self.service.state["active"]
                else "Workspace closed.")

    def snapshot(self):
        active = self.service.state["active"]
        session = active["session"] if active else None
        if active is None:
            phase, remaining, progress = "idle", self.service.state["config"]["work_minutes"] * 60, 0.0
        elif active["waiting"]:
            history = self.service.state["history"]
            phase = history[-1]["phase"] if history else (
                "short_break" if active["next_phase"] == "focus" else "focus")
            remaining, progress = 0, 1.0
        else:
            phase = session["phase"]
            remaining = session["planned_seconds"] - session["elapsed_seconds"]
            progress = session["elapsed_seconds"] / session["planned_seconds"]
        report = self.service.statistics("today")
        if phase == "focus":
            upcoming = "long_break" if (active["completed_focus"] + 1) % 4 == 0 else "short_break"
        else:
            upcoming = "focus"
        content, page, total_pages = self.workspace.content(self.dimensions()[1])
        return DisplaySnapshot(
            task=active["task"] if active else "No active task", phase=phase, remaining_seconds=remaining,
            progress=progress, completed_focus=active["completed_focus"] if active else 0,
            paused=active["paused"] if active else False, waiting=active["waiting"] if active else False,
            next_phase=active["next_phase"] if active and active["waiting"] else upcoming,
            today_focus_seconds=report["focus_seconds"], today_completed=report["completed_sessions"],
            feedback=self.feedback, command=self.buffer,
            view=self.preferences.view,
            upcoming_tasks=tuple(task["title"] for task in self.service.state["tasks"] if not task["completed"]),
            screen=self.workspace.screen, content_lines=content, page=page, total_pages=total_pages,
            idle=active is None,
        )

    def render(self):
        snapshot = self.snapshot()
        if snapshot is None:
            return
        columns, rows = self.dimensions()
        if self.fullscreen:
            frame = render_frame(snapshot, columns, rows, color=self.color, truecolor=self.truecolor)
            sys.stdout.write(frame.text)
            sys.stdout.flush()
        else:
            status = ("IDLE | start --task \"Title\"" if snapshot.idle else
                      f"Ready for {snapshot.next_phase} | continue / quit" if snapshot.waiting else
                      f"{snapshot.phase} {'PAUSED ' if snapshot.paused else ''}{duration(snapshot.remaining_seconds)}")
            # Dumb terminals cannot erase earlier text: emit readable snapshots.
            line = f"{status} | {snapshot.completed_focus} completed | {snapshot.task} | > {snapshot.command}"
            print(clip_text(line, max(1, columns - 1)), flush=True)
            if snapshot.screen != "timer":
                print(f"{snapshot.screen.upper()} | Page {snapshot.page}/{snapshot.total_pages}", flush=True)
                for content in snapshot.content_lines:
                    print(clip_text(content, max(1, columns - 1)), flush=True)

    def dispatch(self, command):
        try:
            parts = shlex.split(command)
        except ValueError as error:
            raise ValueError("Invalid quoting in command.") from error
        if not parts:
            return
        name = parts[0].lower()
        self.message("> " + command.strip())
        if name == "view" and len(parts) == 2:
            self.preferences.set_view(parts[1].lower())
            self.message(f"View: {self.preferences.view}")
        elif name in Workspace.SCREENS and len(parts) == 1:
            self.workspace.navigate(name)
        elif name in ("next", "prev") and len(parts) == 1:
            self.workspace.page(1 if name == "next" else -1, self.dimensions()[1])
        elif name == "start":
            if any(part in ("-h", "--help") for part in parts):
                raise ValueError('Usage: start --task "Title" [--minutes N]')
            args = parser(embedded=True).parse_args([name, *parts[1:]])
            self.service.start(args.task, args.minutes)
            self.workspace.navigate("timer")
        elif name == "config":
            if any(part in ("-h", "--help") for part in parts):
                raise ValueError("Usage: config show | config set KEY MINUTES")
            args = parser(embedded=True).parse_args([name, *parts[1:]])
            if args.action == "set":
                self.service.set_config(args.key, args.value)
                self.message(f"{args.key} = {args.value}")
            self.workspace.navigate("settings")
        elif name == "help" and len(parts) == 1:
            self.message("Commands: pause, resume, stop, help; timer/tasks/history/stats/settings; "
                         "next/prev; start --task TITLE; config show/set; view dashboard/focus; exit; continue/quit.")
        elif name == "pause" and len(parts) == 1:
            self.service.pause()
        elif name == "resume" and len(parts) == 1:
            self.service.resume()
        elif name == "continue" and len(parts) == 1:
            self.service.continue_cycle()
        elif name == "stop" and len(parts) == 1:
            self.service.stop()
            if self.keep_open:
                self.workspace.navigate("timer")
                self.message("Cycle stopped. Ready for another task.")
            else:
                self.exit_message = "Cycle stopped."
        elif name == "quit" and len(parts) == 1:
            active = self.service.state["active"]
            if active is None:
                self.exit_message = "Workspace closed."
            elif active["waiting"]:
                self.service.stop()
                self.exit_message = "Cycle stopped."
            else:
                raise ValueError("Use exit to save an active cycle, or stop to cancel it.")
        elif name == "exit" and len(parts) == 1:
            self.service.checkpoint()
            self.exit_message = self.saved_exit_message()
        else:
            raise ValueError("Unknown command. Type help for available commands.")

    def accept(self, character):
        # Ignore entire cursor/function-key escape sequences rather than adding
        # their printable suffixes to a command. Commands still require Enter.
        if self._escape:
            self._escape += character
            if len(self._escape) == 2 and character not in ("[", "O"):
                self._escape = ""
            elif len(self._escape) > 2 and ("@" <= character <= "~" or len(self._escape) > 32):
                self._escape = ""
            return
        if character == "\x1b":
            self._escape = character
        elif character in ("\n", "\r"):
            command, self.buffer = self.buffer, ""
            try:
                self.dispatch(command)
            except ValueError as error:
                self.message(str(error))
        elif character in ("\x7f", "\b"):
            self.buffer = self.buffer[:-1]
        elif character == "\x04":
            self.service.checkpoint()
            self.exit_message = self.saved_exit_message()
        elif character.isprintable() and len(self.buffer) < 256:
            self.buffer += character

    def run(self):
        descriptor = sys.stdin.fileno()
        original = termios.tcgetattr(descriptor)
        alternate = False
        try:
            tty.setcbreak(descriptor)
            if self.fullscreen:
                # Leave cursor visibility untouched; 1049 saves its position.
                alternate = True
                sys.stdout.write("\033[?1049h\033[0m\033[2J")
                sys.stdout.flush()
            else:
                self.message(self.feedback)
            last_render = None
            while (self.keep_open or self.service.state["active"] is not None) and not self.exit_message:
                if self.service.state["active"] is not None:
                    self.service.tick()
                active = self.service.state["active"]
                marker = (int(time.monotonic()), self.dimensions(),
                          (active["waiting"], active["paused"], active["next_phase"]) if active else None)
                if marker != last_render:
                    self.render()
                    last_render = marker
                ready, _, _ = select.select([descriptor], [], [], 0.2)
                if not ready:
                    continue
                chunk = os.read(descriptor, 1024)
                if not chunk:
                    self.service.checkpoint()
                    self.exit_message = self.saved_exit_message()
                    break
                for character in self.decoder.decode(chunk):
                    self.accept(character)
                    if (not self.keep_open and self.service.state["active"] is None) or self.exit_message:
                        break
                if not self.exit_message:
                    self.render()
        except KeyboardInterrupt:
            self.service.checkpoint()
            self.exit_message = self.saved_exit_message()
        finally:
            try:
                termios.tcsetattr(descriptor, termios.TCSADRAIN, original)
            finally:
                if alternate:
                    sys.stdout.write("\033[0m\033[?1049l")
                    sys.stdout.flush()
        if self.exit_message:
            print(self.exit_message, flush=True)


def run(args, repository):
    writes = args.command in ("start", "ui") or (args.command == "config" and args.action == "set")
    if args.command in ("start", "ui"):
        if args.command == "start" and not args.task.strip():
            raise ValueError("Task must not be empty.")
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise ValueError("The timer requires an interactive terminal for input and output.")
    with repository.writer() if writes else nullcontext():
        service = Service(repository, SystemClock())
        if args.command in ("start", "ui"):
            keep_open = args.command == "ui"
            if service.has_recovery:
                while True:
                    choice = input("A saved cycle exists. Type resume or discard: ").strip().lower()
                    if choice in ("resume", "discard"):
                        break
                    print("Choose resume or discard.")
                service.recover(choice)
                if choice == "discard" and not keep_open:
                    service.start(args.task, args.minutes)
            elif not keep_open:
                service.start(args.task, args.minutes)
            Terminal(service, plain=args.plain, keep_open=keep_open).run()
        elif args.command == "history":
            records = service.history(args.limit)
            if not records:
                print("No finalized sessions.")
            for item in records:
                print(f"{item['started_at']} | {item['phase']} | {item['status']} | "
                      f"{duration(item['elapsed_seconds'])} | {clean(item['task'])}")
        elif args.command == "stats":
            report = service.statistics("week" if args.week else "today")
            print(f"{report['start_date']} to {report['end_date']}")
            print(f"Completed focus sessions: {report['completed_sessions']}")
            print(f"Total focus time: {duration(report['focus_seconds'])}")
            for task, seconds in sorted(report["tasks"].items()):
                print(f"  {clean(task)}: {duration(seconds)}")
        elif args.action == "show":
            for key, value in service.state["config"].items():
                print(f"{key} = {value}")
        else:
            service.set_config(args.key, args.value)
            print(f"{args.key} = {args.value}")


def main():
    args = parser().parse_args()
    root = Path(__file__).resolve().parent.parent
    try:
        run(args, JsonRepository(root))
    except (ValueError, OSError, EOFError, OverflowError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nExited.", file=sys.stderr)
        return 130
    return 0
