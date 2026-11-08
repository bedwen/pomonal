"""Pure, terminal-cell-aware layouts for the Pomonal focus screen."""

from dataclasses import dataclass
import math
import unicodedata


@dataclass(frozen=True)
class DisplaySnapshot:
    task: str
    phase: str
    remaining_seconds: float
    progress: float
    completed_focus: int = 0
    paused: bool = False
    waiting: bool = False
    next_phase: str = "short_break"
    today_focus_seconds: float = 0
    today_completed: int = 0
    feedback: str = ""
    command: str = ""
    view: str = "dashboard"
    upcoming_tasks: tuple[str, ...] = ()
    screen: str = "timer"
    content_lines: tuple[str, ...] = ()
    page: int = 1
    total_pages: int = 1
    idle: bool = False


@dataclass(frozen=True)
class Frame:
    text: str
    lines: tuple[str, ...]
    cursor_row: int
    cursor_column: int
    layout: str


def clean_text(value):
    """Remove control characters, including invisible terminal controls."""
    return "".join(character if character.isprintable() or unicodedata.combining(character)
                   else " " for character in str(value))


def cell_width(value):
    return sum(0 if unicodedata.category(character) in ("Mn", "Me") else
               2 if unicodedata.east_asian_width(character) in ("W", "F") else 1
               for character in clean_text(value))


def clip_text(value, width, *, tail=False):
    """Clip without splitting a wide glyph or its combining marks."""
    clusters = []
    for character in clean_text(value):
        if unicodedata.category(character) in ("Mn", "Me"):
            if clusters:
                clusters[-1] += character
        else:
            clusters.append(character)
    result = []
    used = 0
    source = reversed(clusters) if tail else clusters
    for cluster in source:
        size = cell_width(cluster)
        if used + size > max(0, width):
            break
        result.append(cluster)
        used += size
    if tail:
        result.reverse()
    return "".join(result)


def format_duration(seconds):
    minutes, seconds = divmod(max(0, math.ceil(seconds)), 60)
    return f"{minutes:02d}:{seconds:02d}"


GLYPHS = {
    "0": ("###", "# #", "# #", "# #", "###"),
    "1": (" # ", "## ", " # ", " # ", "###"),
    "2": ("###", "  #", "###", "#  ", "###"),
    "3": ("###", "  #", "###", "  #", "###"),
    "4": ("# #", "# #", "###", "  #", "  #"),
    "5": ("###", "#  ", "###", "  #", "###"),
    "6": ("###", "#  ", "###", "# #", "###"),
    "7": ("###", "  #", "  #", "  #", "  #"),
    "8": ("###", "# #", "###", "# #", "###"),
    "9": ("###", "# #", "###", "  #", "###"),
    ":": (" ", "#", " ", "#", " "),
}


def _digits(value, scale):
    rows = [" ".join(GLYPHS[character][row] for character in value) for row in range(5)]
    return ["".join(character * scale for character in row) for row in rows
            for _ in range(scale)]


def render_frame(snapshot, columns, rows, *, color=True, truecolor=False):
    """Return a complete positioned frame without reading or writing a terminal."""
    columns, rows = max(1, columns), max(1, rows)
    width = max(0, columns - 1)
    layout = "large" if columns >= 100 and rows >= 28 else (
        "standard" if columns >= 64 and rows >= 20 else "compact")
    time_text = format_duration(0 if snapshot.waiting else snapshot.remaining_seconds)
    phase = clean_text(snapshot.phase).upper().replace("_", " ")
    status = f"Ready for {snapshot.next_phase} | continue / quit" if snapshot.waiting else (
        f"{phase} | PAUSED" if snapshot.paused else phase)
    if snapshot.idle:
        status = "IDLE"
    progress = 1.0 if snapshot.waiting else min(1.0, max(0.0, snapshot.progress))
    bar_width = max(0, min(40, width - 9))
    filled = int(progress * bar_width)
    bar = "[" + "#" * filled + "-" * (bar_width - filled) + f"] {int(progress * 100):3d}%"
    completed = snapshot.completed_focus
    next_break = "long break" if (completed + (not snapshot.waiting)) % 4 == 0 else "short break"
    cycle = f"{completed} completed | {next_break} next"
    if snapshot.waiting:
        cycle = f"{completed} completed | Next: {snapshot.next_phase.replace('_', ' ')}"
    elif snapshot.phase != "focus":
        cycle = f"{completed} completed | Focus next"
    today = f"TODAY | Focus: {format_duration(snapshot.today_focus_seconds)} | Completed: {snapshot.today_completed}"
    help_text = "Commands: continue, quit, help" if snapshot.waiting else "Commands: pause, resume, stop, help"
    command = "> " + clip_text(snapshot.command, max(0, width - 2), tail=True)
    placements = [[] for _ in range(rows)]
    accent_rows = set()

    def put(row, text, centered=True, accent=False, *, x=0, area=None):
        if not 0 <= row < rows:
            return
        area = max(0, width - x) if area is None else max(0, min(area, width - x))
        text = clip_text(text, area)
        indent = max(0, (area - cell_width(text)) // 2) if centered else 0
        placements[row].append((x + indent, text))
        if accent:
            accent_rows.add(row)

    def panel(x, y, panel_width, height, title, content, *, centered=False):
        inner = max(0, panel_width - 2)
        heading = clip_text(" " + title + " ", inner)
        put(y, "+" + heading + "-" * (inner - cell_width(heading)) + "+",
            centered=False, accent=True, x=x, area=panel_width)
        put(y + height - 1, "+" + "-" * inner + "+",
            centered=False, x=x, area=panel_width)
        for row in range(y + 1, y + height - 1):
            put(row, "|", centered=False, x=x, area=1)
            put(row, "|", centered=False, x=x + panel_width - 1, area=1)
        usable = max(0, height - 2)
        content = content[:usable]
        offset = max(0, (usable - len(content)) // 2) if centered else 0
        for index, text in enumerate(content):
            put(y + 1 + offset + index, text, centered=centered,
                accent=("#" in text or text == time_text), x=x + 1, area=inner)

    def clock_lines(area, *, spacious=False):
        scale = 2 if layout == "large" else 1
        digits = _digits(time_text, scale)
        if cell_width(digits[0]) <= area:
            body = digits + [time_text]
        else:
            body = [time_text[index:index + area] for index in range(0, len(time_text), area)]
        if spacious:
            body.extend(["", snapshot.task])
        else:
            body.append(snapshot.task)
        return body

    focus_only = snapshot.view == "focus"
    upcoming = list(snapshot.upcoming_tasks) or ["No queued tasks"]
    if snapshot.screen != "timer":
        if layout != "compact":
            put(0, f"POMONAL | {time_text} | {status}", accent=True)
            put(1, f"{snapshot.screen.upper()} | Page {snapshot.page}/{snapshot.total_pages}")
            panel(0, 2, width, rows - 6, snapshot.screen.upper(), list(snapshot.content_lines))
            put(rows - 3, "timer / tasks / history / stats / settings | next / prev")
            put(rows - 2, snapshot.feedback, centered=False)
        else:
            put(0, f"{time_text} | {status}", centered=False, accent=True)
            if rows >= 3:
                put(1, f"{snapshot.screen.upper()} {snapshot.page}/{snapshot.total_pages}", centered=False)
            available = max(0, rows - 4)
            for index, text in enumerate(snapshot.content_lines[:available]):
                put(index + 2, text, centered=False)
            if rows >= 4:
                put(rows - 2, snapshot.feedback, centered=False)
        put(rows - 1, command, centered=False)
    elif layout != "compact":
        put(0, "POMONAL | " + status, accent=True)
        if focus_only:
            body = clock_lines(width, spacious=True)
            top = max(2, (rows - 2 - len(body)) // 2)
            for index, text in enumerate(body[:max(0, rows - 2 - top)]):
                put(top + index, text, accent=text != snapshot.task and bool(text))
            # Focus mode intentionally reserves no statistics, progress, or help panel.
        elif layout == "large":
            side_width = 30
            main_width = width - side_width - 2
            body = clock_lines(main_width - 2, spacious=True) + ["", bar, cycle]
            panel(0, 2, main_width, rows - 6, "TIMER", body, centered=True)
            panel(main_width + 2, 2, side_width, 7, "TODAY",
                  [f"Focus: {format_duration(snapshot.today_focus_seconds)}",
                   f"Completed: {snapshot.today_completed}"], centered=True)
            panel(main_width + 2, 10, side_width, rows - 14, "UPCOMING TASKS",
                  [f"{index}. {task}" if snapshot.upcoming_tasks else task
                   for index, task in enumerate(upcoming, 1)])
            put(rows - 3, help_text)
        else:
            body = clock_lines(width - 2) + [bar, cycle]
            panel(0, 2, width, 11, "TIMER", body, centered=True)
            summary = [today.removeprefix("TODAY | ")] + [
                "Upcoming: " + task if index == 0 else "          " + task
                for index, task in enumerate(upcoming)]
            panel(0, 13, width, rows - 16, "TODAY / UPCOMING", summary)
            put(rows - 3, help_text)
        put(rows - 2, snapshot.feedback, centered=False)
        put(rows - 1, command, centered=False)
    else:
        # Reserve input space even when the window is only one or two rows tall.
        content = [time_text + " | " + status, snapshot.task]
        if focus_only and snapshot.waiting:
            content.append("continue / quit")
        elif not focus_only:
            content.extend([bar, cycle, today, help_text])
        feedback_rows = int(rows >= 4 and bool(snapshot.feedback))
        for index, text in enumerate(content[:max(0, rows - 1 - feedback_rows)]):
            put(index, text, centered=False, accent=index in (0, 2))
        if feedback_rows:
            put(rows - 2, snapshot.feedback, centered=False)
        put(rows - 1, command, centered=False)

    lines = []
    for row in placements:
        line = ""
        for column, text in sorted(row):
            line += " " * max(0, column - cell_width(line)) + text
        lines.append(clip_text(line, width))

    code = ""
    if color:
        if snapshot.paused:
            code = "\033[93m"
        elif snapshot.phase != "focus" and not snapshot.idle:
            code = "\033[92m"
        else:
            code = "\033[38;2;96;165;250m" if truecolor else "\033[94m"
    output = ["\033[H"]
    for index, line in enumerate(lines):
        output.append(f"\033[{index + 1};1H\033[2K")
        if code and index in accent_rows:
            output.extend((code, line, "\033[0m"))
        else:
            output.append(line)
    cursor_column = min(columns, cell_width(lines[-1]) + 1)
    output.append(f"\033[{rows};{cursor_column}H")
    return Frame("".join(output), tuple(lines), rows, cursor_column, layout)
