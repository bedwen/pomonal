"""Pure layout tests independent of real-time timer or terminal state."""

from dataclasses import replace
import re
import unittest

from pomonal.rendering import DisplaySnapshot, cell_width, clean_text, clip_text, daily_bars, render_frame


class RenderingTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = DisplaySnapshot("Study algorithms", "focus", 1455, 0.03,
                                        completed_focus=2, today_focus_seconds=4500,
                                        today_completed=3)

    def test_layout_thresholds_and_dimensions(self):
        for columns, rows, layout in ((120, 32, "large"), (100, 28, "large"),
                                      (99, 28, "standard"), (80, 24, "standard"),
                                      (64, 20, "standard"), (63, 20, "compact"),
                                      (80, 19, "compact"), (30, 8, "compact"),
                                      (1, 1, "compact")):
            with self.subTest(columns=columns, rows=rows):
                frame = render_frame(self.snapshot, columns, rows)
                self.assertEqual(frame.layout, layout)
                self.assertEqual(len(frame.lines), rows)
                self.assertTrue(all(cell_width(line) < columns for line in frame.lines))
                self.assertTrue(1 <= frame.cursor_column <= columns)
                self.assertEqual(frame.cursor_row, rows)

    def test_large_clock_has_doubled_height_and_normal_time(self):
        large = render_frame(self.snapshot, 100, 28)
        standard = render_frame(self.snapshot, 80, 24)
        large_pixels = [line for line in large.lines if "#" in line and "[" not in line]
        standard_pixels = [line for line in standard.lines if "#" in line and "[" not in line]
        self.assertEqual(len(large_pixels), 10)
        self.assertEqual(len(standard_pixels), 5)
        for frame in (large, standard):
            self.assertIn("24:15", "\n".join(frame.lines))
            self.assertIn("Focus: 75:00", "\n".join(frame.lines))
            self.assertIn("Completed: 3", "\n".join(frame.lines))
            self.assertIn("2 completed | short break next", "\n".join(frame.lines))

    def test_color_palette_and_monochrome(self):
        self.assertIn("\033[94m", render_frame(self.snapshot, 80, 24).text)
        self.assertIn("\033[38;2;96;165;250m", render_frame(self.snapshot, 80, 24, truecolor=True).text)
        self.assertIn("\033[92m", render_frame(replace(self.snapshot, phase="short_break"), 80, 24).text)
        paused = render_frame(replace(self.snapshot, paused=True), 80, 24)
        self.assertIn("\033[93m", paused.text)
        self.assertIn("PAUSED", paused.text)
        monochrome = render_frame(self.snapshot, 80, 24, color=False).text
        self.assertNotRegex(monochrome, r"\x1b\[[\d;]*m")
        self.assertNotIn("?25", monochrome)

    def test_waiting_is_zero_full_bar_and_explicit_confirmation(self):
        waiting = replace(self.snapshot, waiting=True, next_phase="short_break")
        text = "\n".join(render_frame(waiting, 80, 24).lines)
        self.assertIn("00:00", text)
        self.assertIn("100%", text)
        self.assertIn("Ready for short_break | continue / quit", text)
        self.assertIn("Commands: continue, quit, help", text)

    def test_progress_clamps_at_both_boundaries(self):
        for progress, expected in ((-0.5, "  0%"), (0, "  0%"), (0.5, " 50%"), (2, "100%")):
            with self.subTest(progress=progress):
                text = "\n".join(render_frame(replace(self.snapshot, progress=progress), 80, 24).lines)
                self.assertIn(expected, text)

    def test_large_duration_falls_back_to_full_normal_text(self):
        snapshot = replace(self.snapshot, remaining_seconds=10**40)
        frame = render_frame(snapshot, 80, 24)
        expected = str(10**40 // 60) + ":40"
        self.assertIn(expected, "\n".join(frame.lines))
        self.assertFalse(any("#" in line and "[" not in line for line in frame.lines))

    def test_extreme_duration_wraps_without_losing_digits(self):
        seconds = 10**100
        frame = render_frame(replace(self.snapshot, remaining_seconds=seconds), 64, 20)
        expected = str(seconds // 60) + ":40"
        self.assertIn(expected, "".join(line.strip().strip("|").strip() for line in frame.lines))

    def test_large_dashboard_has_main_and_separate_summary_panels(self):
        frame = render_frame(replace(self.snapshot, upcoming_tasks=("Read a book", "Practice 日本語")), 100, 28)
        text = "\n".join(frame.lines)
        self.assertIn("+ TIMER", text)
        self.assertIn("+ TODAY", text)
        self.assertIn("+ UPCOMING TASKS", text)
        self.assertIn("1. Read a book", text)
        self.assertIn("2. Practice 日本語", text)
        self.assertTrue(any(line.count("|") == 4 for line in frame.lines))
        self.assertTrue(all(cell_width(line) <= 99 for line in frame.lines))

    def test_standard_dashboard_stacks_summary_below_clock(self):
        frame = render_frame(self.snapshot, 64, 20)
        text = "\n".join(frame.lines)
        self.assertIn("TODAY / UPCOMING", text)
        self.assertIn("Upcoming: No queued tasks", text)
        clock_row = next(index for index, line in enumerate(frame.lines) if "24:15" in line)
        summary_row = next(index for index, line in enumerate(frame.lines) if "TODAY / UPCOMING" in line)
        self.assertGreater(summary_row, clock_row)

    def test_focus_view_omits_secondary_content_but_preserves_controls(self):
        snapshot = replace(self.snapshot, view="focus", upcoming_tasks=("Private queue title",),
                           feedback="Use view dashboard to return.", command="view dashboard")
        for columns, rows in ((100, 28), (64, 20), (40, 10)):
            with self.subTest(columns=columns, rows=rows):
                frame = render_frame(snapshot, columns, rows)
                text = "\n".join(frame.lines)
                for unwanted in ("TODAY", "UPCOMING", "Private queue title", "completed", "3%"):
                    self.assertNotIn(unwanted, text)
                self.assertIn("24:15", text)
                self.assertIn("Study algorithms", text)
                self.assertIn("FOCUS", text)
                self.assertIn("Use view dashboard to return.", text)
                self.assertEqual(frame.lines[-1], "> view dashboard")
                self.assertTrue(all(cell_width(line) < columns for line in frame.lines))

    def test_focus_waiting_keeps_confirmation_and_feedback(self):
        snapshot = replace(self.snapshot, view="focus", waiting=True, next_phase="short_break",
                           feedback="Phase complete.")
        for columns, rows in ((100, 28), (64, 20), (40, 10)):
            with self.subTest(columns=columns, rows=rows):
                text = "\n".join(render_frame(snapshot, columns, rows).lines)
                self.assertIn("00:00", text)
                self.assertIn("continue / quit", text)
                self.assertIn("Phase complete.", text)
                self.assertNotIn("TODAY", text)

    def test_panel_layout_clips_unicode_queue_and_feedback_once(self):
        snapshot = replace(self.snapshot, upcoming_tasks=("日本語 e\u0301 " * 50,) * 30,
                           feedback="Unknown command.", command="x" * 100 + "resume")
        for columns, rows in ((100, 28), (64, 20)):
            with self.subTest(columns=columns, rows=rows):
                frame = render_frame(snapshot, columns, rows)
                self.assertTrue(all(cell_width(line) < columns for line in frame.lines))
                self.assertEqual(sum("Unknown command." in line for line in frame.lines), 1)
                self.assertTrue(frame.lines[-1].endswith("resume"))

    def test_unicode_clipping_and_command_tail(self):
        self.assertEqual(cell_width("日e\u0301"), 3)
        self.assertEqual(clip_text("日e\u0301x", 3), "日e\u0301")
        self.assertEqual(clip_text("abc日", 2, tail=True), "日")
        self.assertEqual(clip_text("\u0301ab", 2), "ab")
        snapshot = replace(self.snapshot, task="日本語 e\u0301 " * 100,
                           command="a" * 100 + "resume", feedback="Invalid command.")
        frame = render_frame(snapshot, 40, 8)
        self.assertTrue(all(cell_width(line) <= 39 for line in frame.lines))
        self.assertTrue(frame.lines[-1].endswith("resume"))
        self.assertIn("Invalid command.", frame.lines[-2])

    def test_compact_feedback_has_one_reserved_row(self):
        snapshot = replace(self.snapshot, feedback="Invalid command.", command="pause")
        for rows in (4, 5, 8, 10):
            with self.subTest(rows=rows):
                frame = render_frame(snapshot, 40, rows)
                self.assertEqual(sum("Invalid command." in line for line in frame.lines), 1)
                self.assertIn("Invalid command.", frame.lines[-2])
                self.assertIn("24:15 | FOCUS", frame.lines[0])
                self.assertIn("Study algorithms", frame.lines[1])
                self.assertEqual(frame.lines[-1], "> pause")

    def test_tiny_layout_prioritizes_timer_task_and_input(self):
        snapshot = replace(self.snapshot, feedback="Invalid command.", command="pause")
        frame = render_frame(snapshot, 40, 3)
        self.assertEqual(frame.lines, ("24:15 | FOCUS", "Study algorithms", "> pause"))
        frame = render_frame(snapshot, 40, 2)
        self.assertEqual(frame.lines, ("24:15 | FOCUS", "> pause"))

    def test_malicious_controls_cannot_escape_the_layout(self):
        attack = "Task\x1b[2J\n\r\x9b31m\u202e"
        snapshot = replace(self.snapshot, task=attack, command=attack, feedback=attack)
        frame = render_frame(snapshot, 80, 24, color=False)
        for line in frame.lines:
            self.assertNotRegex(line, r"[\x00-\x1f\x7f-\x9f\u202e]")
        self.assertNotIn("\x1b[2J", frame.text)
        self.assertEqual(clean_text("a\nb"), "a b")


    def test_secondary_screens_keep_timer_and_paged_content(self):
        for columns, rows in ((100, 28), (64, 20), (40, 10)):
            frame = render_frame(replace(self.snapshot, screen="history", content_lines=("Saved record",),
                                         page=2, total_pages=3), columns, rows)
            text = "\n".join(frame.lines)
            self.assertIn("24:15", text)
            self.assertIn("HISTORY", text)
            self.assertIn("Saved record", text)
            self.assertIn("2/3", text)
            self.assertTrue(all(cell_width(line) < columns for line in frame.lines))
        waiting = replace(self.snapshot, screen="settings", waiting=True, next_phase="short_break")
        self.assertIn("Ready for short_break", render_frame(waiting, 100, 28).text)
        idle = replace(self.snapshot, idle=True, screen="tasks")
        self.assertIn("IDLE", render_frame(idle, 64, 20).text)


    def test_daily_goal_display_caps_indicator_and_focus_hides_it(self):
        snapshot = replace(self.snapshot, goal_label="13/6 sessions", goal_progress=2)
        for columns, rows in ((100, 28), (64, 20)):
            text = "\n".join(render_frame(snapshot, columns, rows).lines)
            self.assertIn("13/6 sessions", text)
            self.assertIn("100%", text)
            self.assertNotIn("200%", text)
        text = "\n".join(render_frame(replace(snapshot, view="focus"), 100, 28).lines)
        self.assertNotIn("13/6", text)


    def test_weekly_bars_scale_zero_days_and_keep_numeric_durations(self):
        days = [{"date": f"2026-11-{day:02d}", "focus_seconds": seconds}
                for day, seconds in zip(range(2, 9), (0, 60, 120, 60, 0, 0, 0))]
        for columns in (40, 64, 100):
            bars = daily_bars(days, columns)
            self.assertEqual(len(bars), 7)
            self.assertTrue(all(cell_width(line) <= columns for line in bars))
            self.assertTrue(bars[0].endswith("00:00"))
            self.assertTrue(bars[2].endswith("02:00"))
            self.assertGreater(bars[2].count("#"), bars[1].count("#"))
        empty = daily_bars([{**day, "focus_seconds": 0} for day in days], 64)
        self.assertTrue(all("#" not in line for line in empty))
        snapshot = replace(self.snapshot, screen="stats", phase="short_break", content_lines=empty)
        self.assertIn("\033[94m", render_frame(snapshot, 100, 28).text)
        self.assertNotRegex(render_frame(snapshot, 100, 28, color=False).text, r"\x1b\[[\d;]*m")


if __name__ == "__main__":
    unittest.main()
