# Pomonal

Pomonal is a Linux terminal Pomodoro timer for focused work and local progress tracking.

Its guiding principle is simple: **a focus tool that runs locally, using only Python's standard library.** Built with Python 3.14, Pomonal keeps your records in the project directory and requires no third-party packages, account, network connection, or system-wide installation.

**Python 3.14 · Linux · Standard library only**

| Focus | Organize | Review |
| --- | --- | --- |
| Large countdown, pause/resume, and confirmed breaks | Saved task queue, duration profiles, and daily goals | Session history, notes, tags, weekly charts, and CSV export |

## Quick start

To use the short command, add this project's `bin` directory to your shell's
`PATH`. For the current terminal, run from the project directory:

```bash
export PATH="$PWD/bin:$PATH"
pomonal
```

`pomonal` opens the workspace. Subcommands also work, for example
`pomonal stats --today` or `pomonal start --task "Read a book"`.
The launcher resolves its project location even through a symbolic link, so
records stay in the project when launched from another directory. To keep the
command available in future terminals, add the absolute `bin` path to your
shell configuration or link `bin/pomonal` from a user-owned directory already
on `PATH`. No Python package installation is needed.

From the project directory, open the workspace:

```bash
python3 -m pomonal ui
```

Then type a command and press Enter:

```text
start --task "Study algorithms"
```

For a single cycle, launch directly from your shell:

```bash
python3 -m pomonal start --task "Read a book" --minutes 45
```

**25 minutes of focus → 5-minute break → repeat.** Every fourth completed focus session is followed by a 15-minute break. Each new phase waits for `continue`.

The timer requires interactive Linux terminal input and output. History, statistics, and configuration inspection also work with redirected output. Run from this directory so Python can find the module; data remains attached to the project even when launched from another working directory.

[Timer controls](#timer-controls) · [Display](#focus-screen) · [Workflow reference](#workflow-reference) · [Local data](#local-data-and-safety) · [Development](#development)

## Focus screen

A blue digital countdown anchors the dashboard, with daily progress and upcoming tasks alongside it. Type `view focus` for a quieter screen or `view dashboard` to restore the panels. The layout adapts to the terminal size.

<details>
<summary>Layout, colors, accessibility, and terminal compatibility</summary>

The dashboard uses bordered timer, daily summary, and upcoming-task panels. Large terminals place secondary panels beside the clock; standard terminals stack them. An empty task queue shows `No queued tasks`.

While the timer is open, type `view focus` and press Enter for only the large clock, task, phase, and command field. Type `view dashboard` to restore details. The preference is saved and restored on the next run, including recovery. Switching views does not pause or reset the timer. Feedback and phase confirmation remain available in focus view.

The default interface fills the terminal with a centered digital countdown, task name, elapsed progress bar, completed focus count, next phase, and today's finalized focus time and completed-session count. Active focus time appears in the countdown and progress bar; today's summary follows the finalized-session statistics described below. No daily goal is required or configured.

The focus clock and progress bar are **blue**. Breaks are green and paused phases are yellow, with explicit phase and pause labels in every color mode. True-color terminals advertising `COLORTERM=truecolor` or `24bit` use blue `#60A5FA`; other terminals use bright ANSI blue. The application inherits your terminal background.

The layout follows the terminal's actual dimensions, including resizing while you type:

| Terminal dimensions | Layout |
| --- | --- |
| At least 100 columns and 28 rows | Enlarged ten-row block digital clock. |
| At least 64 columns and 20 rows | Standard five-row block digital clock. |
| Smaller | Compact view prioritizing time, status, task, and input. |

The digital clock uses full block (`█`) pixels, with ASCII (`#`) pixels when the output encoding cannot represent blocks. A normal-sized time remains visible alongside the digital clock. Minute counts too long for the glyph clock fall back to ordinary text. Very small terminals omit secondary information; enlarge the window to see the full summary and help. Long task names are clipped by display-cell width; the command field shows the end of long input while retaining the full buffer.

Messages and command errors appear in a dedicated feedback area. Phase completion shows `00:00`, 100% progress, and an explicit `continue / quit` prompt. The next phase does not start automatically.

Use `start --plain` for compact, line-oriented snapshots without screen-control sequences. This mode is also selected automatically when `TERM` is unset, empty, or `dumb`. Set `NO_COLOR` (even to an empty value) to disable color while retaining the full-screen layout:

```bash
NO_COLOR=1 python3 -m pomonal start --task "Read a book"
```

Full-screen mode uses the alternate terminal screen, leaving your previous screen available when the application exits. Cursor visibility is left untouched. Input settings and colors are restored and the alternate screen is closed after normal exit, Ctrl+C, or a rendering error. Ctrl+C and Ctrl+D save a recoverable cycle; their confirmation is printed after returning to your original screen.

</details>

## Timer controls

For a workspace that stays open between cycles, use `python3 -m pomonal ui` (or `ui --plain`). Start a cycle inside it with `start --task "Study algorithms" --minutes 25`. Navigate with `timer`, `tasks`, `history`, `stats`, and `settings`; `next` and `prev` page through longer lists. The timer continues while you browse, and every screen retains a small countdown and a persistent completion prompt.

In the workspace, `stop` cancels the active phase and returns to idle. `exit` saves a recoverable cycle and closes the application. `quit` closes an idle workspace or finishes a cycle between phases. The existing `start` entry point still exits after `stop`. Use `config show` and `config set work_minutes 30` inside the workspace to inspect/change future cycle defaults; running cycles keep their captured durations.

The screen displays the phase, remaining time, completed focus count, task, and command buffer. Type a command and press Enter:

| Command | Behavior |
| --- | --- |
| `pause` | Freeze the active phase. |
| `resume` | Resume a paused phase. |
| `stop` | Cancel the active phase; return to idle in `ui`, or exit in `start` mode. |
| `help` | Display available commands. |
| `continue` | Start the next phase when the previous phase has completed. |
| `quit` | Close an idle workspace or exit between phases. |
| `exit` | Save the current cycle and close the application. |

Backspace edits the input buffer. Commands are case-insensitive. Terminal settings are restored on exit.

Defaults are 25 minutes of focus, 5 minutes of short break, and 15 minutes of long break after every fourth completed focus session. Each phase transition waits for `continue`; time spent waiting is never counted. Paused time is also excluded. The same task continues until exit.

<details>
<summary>Saving, recovery, and timing guarantees</summary>

Press **Ctrl+C** to save the cycle and exit. Ctrl+D also saves and exits. Run `start` again to choose:

- `resume`: continue the saved cycle and its original task. A saved paused phase remains paused; a saved transition still waits for `continue`. The newly supplied task and duration are ignored.
- `discard`: finalize the saved phase as interrupted, then start the newly requested task. Discarding a waiting transition adds no duplicate history record.

Time while the application is closed is never counted. Progress is checkpointed every five seconds and on state changes; unexpected termination can lose up to five seconds of progress. The countdown uses monotonic time, so changing the wall clock does not change its remaining duration.

</details>

## Workflow reference

Common shell commands:

| Action | Command |
| --- | --- |
| Recent sessions | `python3 -m pomonal history --limit 10` |
| Today's progress | `python3 -m pomonal stats --today` |
| Weekly summary | `python3 -m pomonal stats --week` |
| Current settings | `python3 -m pomonal config show` |
| Change focus duration | `python3 -m pomonal config set work_minutes 30` |
| Command help | `python3 -m pomonal --help` |

Configuration keys are `work_minutes`, `short_break_minutes`, and `long_break_minutes`. Values must be positive integer minutes within the timer's finite numeric range. `--minutes` overrides focus duration for the current cycle, including recovery, without changing saved settings.


### Daily goals

<details>
<summary>Set a daily session or focus-minute target</summary>

```bash
python3 -m pomonal goal set --sessions 6
python3 -m pomonal goal set --minutes 120
python3 -m pomonal goal show
python3 -m pomonal goal clear
```

Goals are initially disabled. Select one target type at a time: completed focus sessions or finalized active focus minutes. Cancelled/interrupted sessions contribute their recorded focus time to minute targets, but do not count as completed sessions. Active sessions, breaks, pauses, and application downtime are excluded. Progress uses the current local day; indicators stop at 100% while actual achieved totals remain visible. The dashboard and stats/settings screens show the configured goal; the same commands work in `ui`. Focus-only view hides goal details.

</details>

### Duration profiles

<details>
<summary>Choose built-in durations or save your own</summary>

```bash
python3 -m pomonal profile list
python3 -m pomonal profile save reading --work 40 --short-break 5 --long-break 15
python3 -m pomonal profile use reading
python3 -m pomonal start --task "Read" --profile reading --minutes 45
```

Profiles work with saved task IDs too, and all profile commands are available inside `ui`. Names are trimmed and case-normalized. Built-in `classic` (25/5/15), `deep-work` (50/10/15), and `default` cannot be overwritten. `default` follows existing configuration and is initially selected. Explicit `--profile` overrides the saved selection; `--minutes` overrides only focus duration within that cycle. Focus and both break durations are captured together, so profile/settings edits do not alter active or recovered cycles. A long break still follows every fourth completed focus session.

</details>

### Saved task queue

<details>
<summary>Add, reorder, and revisit tasks</summary>

```bash
python3 -m pomonal task add "Study algorithms"
python3 -m pomonal task list
python3 -m pomonal task move TASK_ID --position 1
python3 -m pomonal task rename TASK_ID "Practice algorithms"
python3 -m pomonal start --task-id TASK_ID
python3 -m pomonal task done TASK_ID
python3 -m pomonal task list --completed
python3 -m pomonal task reopen TASK_ID
```

Use the full ID printed by `task add` or `task list`. The same task commands work inside `ui`. Pending tasks follow queue order; reopened tasks go to its end. Task selection/completion is explicit, with no automatic advancement. You must stop or finish an associated active phase before completing its task. Completion between phases closes the cycle without another history entry. Task renames affect future cycles; current and historical records keep their original title. Ad hoc `start --task "Title"` remains available; it cannot be combined with `--task-id`.

</details>

### History and statistics

<details>
<summary>Review sessions, add notes and tags, and filter reports</summary>

History prints full session IDs for use with annotations. In the workspace, each ID appears on a separate line below its record summary, so it remains readable in narrower terminals.

```bash
python3 -m pomonal start --task "Read" --tags algorithms reading
python3 -m pomonal session note SESSION_ID "Finished chapter three"
python3 -m pomonal session tags SESSION_ID algorithms reading
python3 -m pomonal session tags SESSION_ID
python3 -m pomonal history --tag reading --from-date 2026-11-01 --to-date 2026-11-08
python3 -m pomonal stats --week --task-id TASK_ID --tag reading
```

Notes and tags apply to finalized sessions only. An empty note or empty tag list clears that field. Tags are trimmed, case-normalized, and deduplicated. Cycle tags are copied onto focus sessions; breaks remain untagged. Editing a past session does not alter future focus tags. Notes preserve Unicode and line breaks in saved data; terminal output cleans control characters.

History and statistics accept `--task-id`, `--tag`, `--from-date`, and `--to-date` filters. Supplied filters combine with AND; dates are inclusive local start dates in YYYY-MM-DD format. These filtered commands and annotation commands also work inside `ui`. Bare `history` or `stats` resets that screen's filters. Daily goal progress always uses the full local day rather than a filtered report.

History shows the newest finalized sessions first, including breaks and completed, cancelled, or interrupted work. Active sessions are excluded.

Daily statistics use the current local calendar day. Weekly statistics use Monday through Sunday of the current local calendar week. They show completed focus count, total focus time, and time by task. Cancelled and interrupted focus sessions contribute their recorded active time, but do not increase completed count. Breaks and paused time never contribute. A session crossing midnight belongs entirely to its local start date. Displayed durations round up to the next second; stored durations retain fractional seconds.

`stats --week` and the workspace stats screen include seven daily bars, including zero-activity days. Bars share the week's largest daily value as their scale and retain numeric durations. Interactive bars are blue; redirected output and `NO_COLOR` remain plain text. Use `next`/`prev` to page the workspace summary when space is limited.

</details>

### CSV export

<details>
<summary>Export finalized sessions to a local CSV file</summary>

```bash
python3 -m pomonal export --output reports/sessions.csv
python3 -m pomonal export --output reports/reading.csv --tag reading --from-date 2026-11-01 --to-date 2026-11-08
python3 -m pomonal export --output reports/sessions.csv --overwrite
```

The same command works inside `ui`. Export includes finalized focus and break records, excludes the active session, and supports the same combined filters as history. UTF-8 CSV preserves Unicode, commas, quotes, line breaks, and tags encoded as a JSON array.

Columns are `session_id`, `task_id`, `task_title`, `phase`, `status`, `started_at`, `ended_at`, `planned_seconds`, `active_seconds`, `note`, and `tags`. An absent task ID is an empty CSV field.

Use a `.csv` destination inside the project, typically `reports/`. Parent directories are created locally. Paths outside the project, parent traversal, hidden components, protected product/data directories, symbolic links, and hard-linked targets are rejected. Existing files require explicit `--overwrite`. Writes use synchronized project-local temporary files and atomic publication; failed writes before publication preserve existing files.

Exports are excluded from Git by the product allowlist. A standalone export acquires the repository writer lock; while a workspace is running, use its internal `export` command or close it first. Export never changes the saved session data.

</details>

## Local data and safety

All runtime data is stored in `data/state.json` beneath the project root. This versioned JSON document contains configuration, finalized history, and the active cycle. Data and test artifacts are ignored by Git.

Writes use a project-local temporary file, file synchronization, atomic replacement, and directory synchronization. A Linux file lock prevents simultaneous timer/configuration writers. History and configuration reads remain available while the timer runs. The lock file may remain after exit; the operating system releases its lock automatically.

Data paths and files must not be symbolic links; data files must not be hard-linked. Descriptor-relative filesystem operations keep writes attached to the opened project data directory. Invalid or unsupported saved documents produce an error and are preserved for inspection. Version-1 documents are migrated in memory for reads. Before the first version-2 write, a verified byte-preserving backup is created inside `data/` under the writer lock. Existing backups are never overwritten. History, settings, and recoverable cycles are preserved; active cycles keep their captured focus and break durations.

No files are written to the home directory, system temporary directory, or global configuration paths. Bytecode generation is disabled during application startup. Do not redirect Python's own startup caches or diagnostics outside this project. Development commands use `-B` explicitly.

## Development

### Architecture

- `domain`: pure configuration, phase rules, and session creation.
- `state`: validation of supported data versions and lossless in-memory migration.
- `application`: use cases and state transitions, depending on small `Clock` and `Repository` protocols.
- `tasks`, `profiles`, `goals`, and `annotations`: focused productivity use cases sharing the timer's persistence context.
- `reports`: shared finalized-session filtering and local calendar aggregation; `exporting` handles atomic CSV output.
- `infrastructure`: system clock, local calendar conversion, validated JSON persistence, and locking.
- `presentation`: argument parsing, command dispatch, display snapshots, and terminal lifecycle.
- `rendering`: pure display snapshots to positioned frames, block digits, palettes, and Unicode cell-aware layout. It performs no terminal or filesystem I/O.

The entry point assembles concrete adapters. Timer rules are independent of terminal and disk operations. Tests substitute a controllable clock and in-memory repository. Small contracts keep clocks and storage replaceable without a framework. Productivity use cases share mutable state, and terminal coordination remains centralized; these are areas for further architectural refinement.

### Verification

```bash
python3 -B -m unittest discover -s tests -v
```

The suite tests cycle transitions, long breaks, pauses, wall-clock changes, five-second checkpoints, recovery, overrides, statistics boundaries including daylight-saving changes, corrupt documents, atomic-write failures, exclusive writers, and symbolic-link handling. Renderer tests cover all layout sizes, colors, Unicode/control text, progress boundaries, and long durations. Real Linux pseudo-terminal tests exercise command editing, live countdown, errors, interruption, recovery, confirmed break transitions, resizing during partial input, plain/monochrome modes, and terminal restoration after an injected rendering failure. Tests do not wait through full Pomodoro durations.

Filesystem tests and copied application instances use temporary directories exclusively beneath `.test-tmp/` inside this project. No test modifies your real `data/state.json`.

Cloud sync, desktop notifications, multiple users, background services, and non-Linux terminal support are outside the first release.
