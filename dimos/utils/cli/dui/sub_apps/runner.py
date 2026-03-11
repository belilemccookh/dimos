# Copyright 2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Status sub-app — log viewer and blueprint lifecycle controls."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import TYPE_CHECKING, Any

from rich.panel import Panel
from rich.text import Text
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, RichLog, Static

from dimos.utils.cli import theme
from dimos.utils.cli.dui.sub_app import SubApp

if TYPE_CHECKING:
    from textual.app import ComposeResult


def _launch_log_dir() -> Path:
    """Base directory for launch logs."""
    xdg = os.environ.get("XDG_STATE_HOME")
    return Path(xdg) / "dimos" if xdg else Path.home() / ".local" / "state" / "dimos"


def _launch_log_path() -> Path:
    """Well-known path for launch stdout/stderr (with ANSI colors)."""
    return _launch_log_dir() / "launch.log"


def _launch_log_plain_path() -> Path:
    """Well-known path for launch stdout/stderr (plain text, no ANSI)."""
    return _launch_log_dir() / "launch.plain.log"


class StatusSubApp(SubApp):
    TITLE = "status"

    DEFAULT_CSS = """
    StatusSubApp {
        layout: vertical;
        height: 1fr;
        background: $dui-bg;
    }
    StatusSubApp .subapp-header {
        width: 100%;
        height: auto;
        color: #ff8800;
        padding: 1 2;
        text-style: bold;
    }
    StatusSubApp RichLog {
        height: 1fr;
        background: $dui-bg;
        border: solid $dui-dim;
        scrollbar-size-vertical: 0;
        scrollbar-size-horizontal: 1;
    }
    StatusSubApp #idle-container {
        height: 1fr;
        align: center middle;
    }
    StatusSubApp #idle-panel {
        width: auto;
        background: transparent;
    }
    StatusSubApp #run-controls {
        height: auto;
        padding: 0 1;
        background: $dui-bg;
    }
    StatusSubApp #run-controls Button {
        margin: 0 1 0 0;
        min-width: 12;
        background: transparent;
        border: solid $dui-dim;
        color: $dui-text;
    }
    StatusSubApp .status-bar {
        height: 1;
        dock: bottom;
        background: $dui-hint-bg;
        color: $dui-dim;
        padding: 0 1;
    }
    StatusSubApp #btn-stop {
        border: solid #882222;
        color: #cc4444;
    }
    StatusSubApp #btn-stop:hover {
        border: solid #cc4444;
    }
    StatusSubApp #btn-stop:focus {
        background: #882222;
        color: #ffffff;
        border: solid #cc4444;
    }
    StatusSubApp #btn-sudo-kill {
        border: solid #882222;
        color: #ff4444;
    }
    StatusSubApp #btn-sudo-kill:hover {
        border: solid #ff4444;
    }
    StatusSubApp #btn-sudo-kill:focus {
        background: #882222;
        color: #ffffff;
        border: solid #ff4444;
    }
    StatusSubApp #btn-restart {
        border: solid #886600;
        color: #ccaa00;
    }
    StatusSubApp #btn-restart:hover {
        border: solid #ccaa00;
    }
    StatusSubApp #btn-restart:focus {
        background: #886600;
        color: #ffffff;
        border: solid #ccaa00;
    }
    StatusSubApp #btn-open-log {
        border: solid #445566;
        color: #8899aa;
    }
    StatusSubApp #btn-open-log:hover {
        border: solid #8899aa;
    }
    StatusSubApp #btn-open-log:focus {
        background: #445566;
        color: #ffffff;
        border: solid #8899aa;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._running_entry: Any = None
        self._log_thread: threading.Thread | None = None
        self._stop_log = False
        self._failed_stop_pid: int | None = None
        self._following_launch_log = False
        self._launch_log_mtime: float = 0.0
        self._poll_count = 0
        self._last_click_time: float = 0.0
        self._last_click_y: int = -1
        self._saved_status: str = ""

    def _debug(self, msg: str) -> None:
        """Log to the DUI debug panel if available."""
        try:
            self.app._log(f"[#8899aa]STATUS:[/#8899aa] {msg}")  # type: ignore[attr-defined]
        except Exception:
            pass

    def compose(self) -> ComposeResult:
        yield Static("Blueprint Status", classes="subapp-header")
        with VerticalScroll(id="idle-container"):
            yield Static(self._idle_panel(), id="idle-panel")
        yield RichLog(id="runner-log", markup=True, wrap=False, auto_scroll=True, min_width=600)
        with Horizontal(id="run-controls"):
            yield Button("Stop", id="btn-stop", variant="error")
            yield Button("Force Kill (sudo)", id="btn-sudo-kill")
            yield Button("Restart", id="btn-restart", variant="warning")
            yield Button("Open Log File", id="btn-open-log")
        yield Static("", id="runner-status", classes="status-bar")

    def _idle_panel(self) -> Panel:
        msg = Text(justify="center")
        msg.append("No Blueprint Running\n\n", style="bold #cc4444")
        msg.append("Use the ", style=theme.DIM)
        msg.append("launch", style=f"bold {theme.CYAN}")
        msg.append(" tab to start a blueprint", style=theme.DIM)
        return Panel(msg, border_style=theme.DIM, expand=False)

    def on_mount_subapp(self) -> None:
        self._debug("on_mount_subapp called")
        self._check_running()
        entry = self._running_entry
        self._debug(f"initial check: entry={getattr(entry, 'run_id', None)}")
        if entry is not None:
            self._debug("-> _show_running")
            self._show_running()
        else:
            self._check_launch_log()
            if not self._following_launch_log:
                self._debug("-> _show_idle")
                self._show_idle()
            else:
                self._debug("-> following launch log")
        self._start_poll_timer()

    def on_resume_subapp(self) -> None:
        self._debug("on_resume_subapp: restarting timer after remount")
        self._start_poll_timer()
        # Re-sync UI state with current data
        self._check_running()
        if self._running_entry is not None:
            self._show_running()

    def _start_poll_timer(self) -> None:
        self.set_interval(1.0, self._poll_running)
        self._debug("timer started")

    def get_focus_target(self) -> object | None:
        if self._running_entry is not None:
            try:
                return self.query_one("#runner-log", RichLog)
            except Exception:
                pass
        return super().get_focus_target()

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def _check_running(self) -> None:
        try:
            from dimos.core.run_registry import get_most_recent

            self._running_entry = get_most_recent(alive_only=True)
        except Exception as e:
            self._debug(f"_check_running exception: {e}")
            self._running_entry = None

    def _poll_running(self) -> None:
        self._poll_count += 1
        old_entry = self._running_entry
        self._check_running()
        new_entry = self._running_entry

        old_id = getattr(old_entry, "run_id", None)
        new_id = getattr(new_entry, "run_id", None)

        # Log every 10th poll or on state change
        changed = old_id != new_id
        if changed or self._poll_count % 10 == 1:
            self._debug(
                f"poll #{self._poll_count}: old={old_id} new={new_id} "
                f"changed={changed} following_launch={self._following_launch_log}"
            )

        if changed:
            if new_entry is not None:
                self._debug(f"-> _show_running (new entry: {new_id})")
                self._stop_log = True
                self._following_launch_log = False
                self._show_running()
                return
            elif old_entry is not None:
                self._debug(f"-> _show_stopped (entry gone: {old_id})")
                self._stop_log = True
                self._following_launch_log = False
                self._show_stopped("Process ended")
                return

        # If nothing is running yet, check for a fresh launch log
        if new_entry is None and not self._following_launch_log:
            self._check_launch_log()

    def _check_launch_log(self) -> None:
        """Detect a new/updated launch.log and start tailing it."""
        log_path = _launch_log_path()
        try:
            mtime = log_path.stat().st_mtime
        except FileNotFoundError:
            return
        age = time.time() - mtime
        if mtime <= self._launch_log_mtime:
            return
        if age > 30:
            return
        self._debug(f"_check_launch_log: new launch.log detected (age={age:.1f}s)")
        self._launch_log_mtime = mtime
        self._following_launch_log = True
        self._show_launching(log_path)

    def _show_launching(self, log_path: Path) -> None:
        """Show the launch log output while the daemon is starting."""
        self._debug(f"_show_launching: {log_path}")
        self.query_one("#idle-container").styles.display = "none"
        self.query_one("#runner-log").styles.display = "block"
        self.query_one("#run-controls").styles.display = "none"
        status = self.query_one("#runner-status", Static)
        status.update("Launching blueprint... — double-click log to open")

        self._stop_log = False
        log_widget = self.query_one("#runner-log", RichLog)
        log_widget.clear()

        def _tail() -> None:
            try:
                with open(log_path) as f:
                    while not self._stop_log:
                        line = f.readline()
                        if line:
                            rendered = Text.from_ansi(line.rstrip("\n"))
                            self.app.call_from_thread(self._write_log_line, log_widget, rendered)
                        else:
                            time.sleep(0.2)
            except Exception as e:
                self.app.call_from_thread(
                    self._write_log_line, log_widget, f"[red]Error reading launch log: {e}[/red]"
                )

        self._log_thread = threading.Thread(target=_tail, daemon=True)
        self._log_thread.start()

    def _show_running(self) -> None:
        """Show controls for a running blueprint."""
        self._debug("_show_running: setting widget display states")
        try:
            self.query_one("#idle-container").styles.display = "none"
            self.query_one("#runner-log").styles.display = "block"
            self.query_one("#run-controls").styles.display = "block"
            self.query_one("#btn-stop").styles.display = "block"
            self.query_one("#btn-sudo-kill").styles.display = "none"
            self.query_one("#btn-restart").styles.display = "block"
            self.query_one("#btn-open-log").styles.display = "block"
            self._failed_stop_pid = None
            entry = self._running_entry
            if entry:
                status = self.query_one("#runner-status", Static)
                status.update(self._format_status_line(entry))
                self._debug(f"_show_running: starting log follow for {entry.run_id}")
                self._start_log_follow(entry)
            self._debug("_show_running: done")
        except Exception as e:
            self._debug(f"_show_running CRASHED: {e}")

    def _show_stopped(self, message: str = "Stopped") -> None:
        """Show controls for a stopped state with logs still visible."""
        self.query_one("#idle-container").styles.display = "none"
        self.query_one("#runner-log").styles.display = "block"
        self.query_one("#run-controls").styles.display = "block"
        self.query_one("#btn-stop").styles.display = "none"
        self.query_one("#btn-sudo-kill").styles.display = "none"
        self.query_one("#btn-restart").styles.display = "none"
        self.query_one("#btn-open-log").styles.display = "block"
        self._failed_stop_pid = None
        self._following_launch_log = False
        # Reset so the next launch.log write is always detected
        self._launch_log_mtime = 0.0
        status = self.query_one("#runner-status", Static)
        status.update(message)

    def _show_idle(self) -> None:
        """Show big idle message — no blueprint running."""
        self._debug("_show_idle called")
        self.query_one("#idle-container").styles.display = "block"
        self.query_one("#runner-log").styles.display = "none"
        self.query_one("#run-controls").styles.display = "none"
        self._failed_stop_pid = None
        self._following_launch_log = False
        self._launch_log_mtime = 0.0

        # Check if there are past logs to show in status bar
        has_past = False
        try:
            from dimos.core.run_registry import get_most_recent

            entry = get_most_recent()
            if entry:
                has_past = True
                status = self.query_one("#runner-status", Static)
                status.update(f"Last run: {entry.blueprint} (run {entry.run_id})")
        except Exception:
            pass

        if not has_past:
            status = self.query_one("#runner-status", Static)
            status.update("No blueprint running")

    # ------------------------------------------------------------------
    # Entry info formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _format_status_line(entry: Any) -> str:
        """One-line status bar summary including config overrides."""
        overrides = getattr(entry, "config_overrides", None) or {}
        parts = [f"Running: {entry.blueprint} (PID {entry.pid}) — double-click log to open"]
        if overrides:
            flags = " ".join(
                f"--{k.replace('_', '-')}"
                if isinstance(v, bool) and v
                else f"--no-{k.replace('_', '-')}"
                if isinstance(v, bool)
                else f"--{k.replace('_', '-')}={v}"
                for k, v in overrides.items()
            )
            parts.append(flags)
        return " | ".join(parts)

    @staticmethod
    def _format_launch_header(entry: Any) -> list[str]:
        """Rich-markup lines summarising how the blueprint was launched."""
        lines: list[str] = []
        argv = getattr(entry, "original_argv", None) or []
        overrides = getattr(entry, "config_overrides", None) or {}
        if argv:
            lines.append(f"[dim]$ {' '.join(argv)}[/dim]")
        if overrides:
            items = "  ".join(f"[{theme.CYAN}]{k}[/{theme.CYAN}]={v}" for k, v in overrides.items())
            lines.append(f"[dim]config overrides:[/dim] {items}")
        lines.append("")  # blank separator
        return lines

    # ------------------------------------------------------------------
    # Log streaming
    # ------------------------------------------------------------------

    # Rich styles matching the structlog compact console color scheme
    _LEVEL_STYLES: dict[str, str] = {
        "dbg": "bold cyan",
        "deb": "bold cyan",
        "inf": "bold green",
        "war": "bold yellow",
        "err": "bold red",
        "cri": "bold red",
    }

    @staticmethod
    def _format_jsonl_line(raw: str) -> Text:
        """Parse a JSONL log line and return a colorized Rich Text object."""
        import json
        from pathlib import Path as P

        _STANDARD_KEYS = {"timestamp", "level", "logger", "event", "func_name", "lineno"}

        try:
            rec: dict[str, object] = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return Text(raw.rstrip())

        ts = str(rec.get("timestamp", ""))
        hms = ts[11:19] if len(ts) >= 19 else ts
        level = str(rec.get("level", "?"))[:3].lower()
        logger_name = P(str(rec.get("logger", "?"))).name
        event = str(rec.get("event", ""))

        line = Text()
        line.append(hms, style="dim")
        lvl_style = StatusSubApp._LEVEL_STYLES.get(level, "")
        line.append(f"[{level}]", style=lvl_style)
        line.append(f"[{logger_name:17}] ", style="dim")
        line.append(event, style="blue")

        extras = {k: v for k, v in rec.items() if k not in _STANDARD_KEYS}
        if extras:
            line.append(" ")
            for k, v in sorted(extras.items()):
                line.append(f"{k}", style="cyan")
                line.append("=", style="white")
                line.append(f"{v}", style="magenta")
                line.append(" ")

        return line

    def _write_log_line(self, log_widget: RichLog, rendered: Text | str) -> None:
        """Write a line to the log widget."""
        log_widget.write(rendered)

    def _start_log_follow(self, entry: Any) -> None:
        self._stop_log = False
        log_widget = self.query_one("#runner-log", RichLog)
        log_widget.clear()
        # Print launch info header
        for line in self._format_launch_header(entry):
            self._write_log_line(log_widget, line)

        def _follow() -> None:
            try:
                from dimos.core.log_viewer import (
                    follow_log,
                    read_log,
                    resolve_log_path,
                )

                path = resolve_log_path(entry.run_id)
                if not path:
                    self.app.call_from_thread(
                        self._write_log_line, log_widget, "[dim]No log file found[/dim]"
                    )
                    return

                for line in read_log(path, 50):
                    if self._stop_log:
                        return
                    rendered = self._format_jsonl_line(line)
                    self.app.call_from_thread(self._write_log_line, log_widget, rendered)

                for line in follow_log(path, stop=lambda: self._stop_log):
                    rendered = self._format_jsonl_line(line)
                    self.app.call_from_thread(self._write_log_line, log_widget, rendered)
            except Exception as e:
                self.app.call_from_thread(
                    self._write_log_line, log_widget, f"[red]Error: {e}[/red]"
                )

        self._log_thread = threading.Thread(target=_follow, daemon=True)
        self._log_thread.start()

    # ------------------------------------------------------------------
    # Button handling
    # ------------------------------------------------------------------

    def _is_click_on_log(self, event: Any) -> bool:
        """Return True if the click event is inside the runner-log RichLog."""
        try:
            node = event.widget
            while node is not None:
                if getattr(node, "id", None) == "runner-log":
                    return True
                node = node.parent
        except Exception:
            pass
        return False

    def on_click(self, event: Any) -> None:
        """Single click: show hint. Double click: open source file."""
        if not self._is_click_on_log(event):
            return

        now = time.monotonic()
        click_y = getattr(event, "screen_y", -1)
        is_double = (now - self._last_click_time) < 0.4 and abs(click_y - self._last_click_y) <= 1
        self._last_click_time = now
        self._last_click_y = click_y

        if is_double:
            self._handle_double_click(event)
        else:
            # Save current status and show hint
            status = self.query_one("#runner-status", Static)
            current = status.renderable
            if not isinstance(current, str) or "double-click" not in current:
                self._saved_status = str(current)
            status.update("double-click to open log file")
            # Restore after 2 seconds
            self.set_timer(2.0, self._restore_status)

    def _restore_status(self) -> None:
        """Restore the status bar after the hint."""
        try:
            status = self.query_one("#runner-status", Static)
            current = str(status.renderable)
            if "double-click" in current and self._saved_status:
                status.update(self._saved_status)
        except Exception:
            pass

    def _get_clicked_line_number(self, event: Any) -> int:
        """Map a click event to a 1-based line number in the log file."""
        try:
            log_widget = self.query_one("#runner-log", RichLog)
            # Convert screen_y to position relative to the RichLog
            local_y = event.screen_y - log_widget.region.y
            # Add scroll offset to get the visual line index
            line_idx = int(log_widget.scroll_y) + local_y
            # 1-based for editors
            return max(1, line_idx + 1)
        except Exception:
            return 1

    def _handle_double_click(self, event: Any) -> None:
        """Open the plain (no ANSI) launch log in the user's editor."""
        lineno = self._get_clicked_line_number(event)
        log_path = _launch_log_plain_path()
        if log_path.exists():
            self._open_source_file(str(log_path), lineno)
        else:
            # Fall back to colored version
            log_path = _launch_log_path()
            if log_path.exists():
                self._open_source_file(str(log_path), lineno)
            else:
                self.app.notify("No launch log found", severity="warning")

    def _open_source_file(self, file_path: str, lineno: int) -> None:
        """Open a source file in the user's preferred GUI editor.

        Only launches background (GUI) editors — never suspends the TUI.
        Falls back to copying the path to clipboard + notification.
        """
        import shutil

        # Resolve relative paths against the project root
        full_path = Path(file_path)
        if not full_path.is_absolute():
            for base in [Path.cwd(), Path(__file__).resolve().parents[5]]:
                candidate = base / file_path
                if candidate.exists():
                    full_path = candidate
                    break

        loc = f"{full_path}:{lineno}" if lineno else str(full_path)
        loc_short = f"{full_path.name}:{lineno}" if lineno else full_path.name

        if not full_path.exists():
            self.app.copy_to_clipboard(loc)
            self.app.notify(f"File not found, copied path: {loc}", severity="warning")
            return

        # GUI editors that can be launched as background processes
        _GUI_EDITORS: list[tuple[str, list[str]]] = []

        # Check $VISUAL and $EDITOR for GUI editors
        for env_var in ("VISUAL", "EDITOR"):
            cmd = os.environ.get(env_var, "")
            if not cmd or not shutil.which(cmd):
                continue
            cmd_name = Path(cmd).name
            if cmd_name in ("code", "code-insiders"):
                _GUI_EDITORS.append((cmd, ["-g", loc]))
            elif cmd_name in ("subl", "sublime", "subl3"):
                _GUI_EDITORS.append((cmd, [loc]))
            elif cmd_name in ("atom", "zed", "fleet"):
                _GUI_EDITORS.append((cmd, [loc]))
            elif cmd_name in ("idea", "pycharm", "goland", "webstorm", "clion"):
                _GUI_EDITORS.append((cmd, ["--line", str(lineno), str(full_path)]))

        # Fallback: try well-known GUI editors
        for cmd, args in [
            ("code", ["-g", loc]),
            ("subl", [loc]),
            ("zed", [loc]),
        ]:
            if shutil.which(cmd):
                _GUI_EDITORS.append((cmd, args))

        for cmd, args in _GUI_EDITORS:
            try:
                subprocess.Popen(
                    [cmd, *args],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                self.app.notify(f"Opened {loc_short}")
                return
            except Exception:
                continue

        # No GUI editor found — copy path to clipboard as fallback
        self.app.copy_to_clipboard(loc)
        self.app.notify(f"Copied to clipboard: {loc}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-stop":
            self._stop_running()
        elif event.button.id == "btn-sudo-kill":
            self._sudo_kill()
        elif event.button.id == "btn-restart":
            self._restart_running()
        elif event.button.id == "btn-open-log":
            self._open_log_in_editor()

    def on_key(self, event: Any) -> None:
        key = getattr(event, "key", "")
        if key in ("left", "right"):
            self._cycle_button_focus(1 if key == "right" else -1)
            event.prevent_default()
            event.stop()
        elif key == "enter":
            focused = self.app.focused
            if isinstance(focused, Button):
                focused.press()
                event.prevent_default()
                event.stop()

    def _get_visible_buttons(self) -> list[Button]:
        buttons: list[Button] = []
        for bid in ("btn-stop", "btn-sudo-kill", "btn-restart", "btn-open-log"):
            try:
                btn = self.query_one(f"#{bid}", Button)
                if btn.styles.display != "none":
                    buttons.append(btn)
            except Exception:
                pass
        return buttons

    def _cycle_button_focus(self, delta: int) -> None:
        buttons = self._get_visible_buttons()
        if not buttons:
            return
        focused = self.app.focused
        try:
            idx = buttons.index(focused)  # type: ignore[arg-type]
            idx = (idx + delta) % len(buttons)
        except ValueError:
            idx = 0
        buttons[idx].focus()

    # ------------------------------------------------------------------
    # Stop / restart / kill
    # ------------------------------------------------------------------

    def _stop_running(self) -> None:
        self._stop_log = True
        log_widget = self.query_one("#runner-log", RichLog)
        status = self.query_one("#runner-status", Static)
        status.update("Stopping blueprint...")
        for bid in ("btn-stop", "btn-restart"):
            try:
                self.query_one(f"#{bid}", Button).disabled = True
            except Exception:
                pass

        entry = self._running_entry
        self._running_entry = None

        def _do_stop() -> None:
            permission_error = False
            if entry:
                try:
                    from dimos.core.run_registry import stop_entry

                    msg, _ = stop_entry(entry)
                    self.app.call_from_thread(
                        log_widget.write, f"[{theme.YELLOW}]{msg}[/{theme.YELLOW}]"
                    )
                except PermissionError:
                    permission_error = True
                    self.app.call_from_thread(
                        log_widget.write,
                        f"[red]Permission denied — cannot stop PID {entry.pid}[/red]",
                    )
                except Exception as e:
                    if (
                        "permission" in str(e).lower()
                        or "operation not permitted" in str(e).lower()
                    ):
                        permission_error = True
                    self.app.call_from_thread(log_widget.write, f"[red]Stop error: {e}[/red]")

            def _after_stop() -> None:
                for bid in ("btn-stop", "btn-restart"):
                    try:
                        self.query_one(f"#{bid}", Button).disabled = False
                    except Exception:
                        pass
                if permission_error and entry:
                    self._failed_stop_pid = entry.pid
                    self.query_one("#btn-sudo-kill").styles.display = "block"
                    self.query_one("#btn-sudo-kill", Button).focus()
                    s = self.query_one("#runner-status", Static)
                    s.update(
                        f"Stop failed (permission denied) — try Force Kill for PID {entry.pid}"
                    )
                else:
                    self._show_stopped("Stopped")

            self.app.call_from_thread(_after_stop)

        threading.Thread(target=_do_stop, daemon=True).start()

    def _restart_running(self) -> None:
        entry = self._running_entry
        name = getattr(entry, "blueprint", None)
        if not name:
            return
        self._stop_log = True
        log_widget = self.query_one("#runner-log", RichLog)
        status = self.query_one("#runner-status", Static)
        status.update(f"Restarting {name}...")
        for bid in ("btn-stop", "btn-restart"):
            try:
                self.query_one(f"#{bid}", Button).disabled = True
            except Exception:
                pass

        old_entry = self._running_entry
        self._running_entry = None

        def _do_restart() -> None:
            if old_entry:
                try:
                    from dimos.core.run_registry import stop_entry

                    stop_entry(old_entry)
                except Exception:
                    pass

            config_args: list[str] = []
            try:
                from dimos.utils.cli.dui.sub_apps.config import ConfigSubApp

                for inst in self.app._instances:  # type: ignore[attr-defined]
                    if isinstance(inst, ConfigSubApp):
                        for k, v in inst.get_overrides().items():
                            cli_key = k.replace("_", "-")
                            if isinstance(v, bool):
                                config_args.append(f"--{cli_key}" if v else f"--no-{cli_key}")
                            else:
                                config_args.extend([f"--{cli_key}", str(v)])
                        break
            except Exception:
                pass

            cmd = [
                sys.executable,
                "-m",
                "dimos.robot.cli.dimos",
                *config_args,
                "run",
                "--daemon",
                name,
            ]
            env = os.environ.copy()
            env["FORCE_COLOR"] = "1"
            env["PYTHONUNBUFFERED"] = "1"
            env["TERM"] = env.get("TERM", "xterm-256color")
            try:
                import re as _re

                _ANSI_RE = _re.compile(r"\x1b\[[0-9;]*m")
                log_file = _launch_log_path()
                plain_file = _launch_log_plain_path()
                with (
                    open(log_file, "w") as f_color,
                    open(plain_file, "w") as f_plain,
                ):
                    proc = subprocess.Popen(
                        cmd,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        env=env,
                        start_new_session=True,
                    )
                    for raw_line in proc.stdout:  # type: ignore[union-attr]
                        line = raw_line.decode("utf-8", errors="replace")
                        f_color.write(line)
                        f_color.flush()
                        f_plain.write(_ANSI_RE.sub("", line))
                        f_plain.flush()
                    proc.wait()

                # Copy plain log into the run's log directory for archival
                try:
                    from dimos.utils.cli.dui.sub_apps.launcher import _copy_plain_log_to_run_dir

                    _copy_plain_log_to_run_dir(plain_file)
                except Exception:
                    pass
            except Exception as e:
                self.app.call_from_thread(log_widget.write, f"[red]Restart error: {e}[/red]")

            def _after() -> None:
                for bid in ("btn-stop", "btn-restart"):
                    try:
                        self.query_one(f"#{bid}", Button).disabled = False
                    except Exception:
                        pass
                self._check_running()
                if self._running_entry:
                    self._show_running()
                else:
                    self._show_stopped("Restart failed")

            self.app.call_from_thread(_after)

        threading.Thread(target=_do_restart, daemon=True).start()

    def _sudo_kill(self) -> None:
        pid = self._failed_stop_pid
        if pid is None:
            return
        log_widget = self.query_one("#runner-log", RichLog)
        self.query_one("#btn-sudo-kill", Button).disabled = True

        def _do_kill() -> None:
            try:
                result = subprocess.run(
                    ["sudo", "-n", "kill", "-9", str(pid)],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    self.app.call_from_thread(
                        log_widget.write,
                        f"[{theme.YELLOW}]Killed PID {pid} with sudo[/{theme.YELLOW}]",
                    )
                    try:
                        from dimos.core.run_registry import get_most_recent

                        entry = get_most_recent()
                        if entry and entry.pid == pid:
                            entry.remove()
                    except Exception:
                        pass

                    def _after() -> None:
                        self._failed_stop_pid = None
                        self._running_entry = None
                        self._show_stopped("Killed with sudo")

                    self.app.call_from_thread(_after)
                else:
                    from dimos.utils.prompt import sudo_prompt

                    got_sudo = sudo_prompt("sudo is required to force-kill the process")
                    if got_sudo:
                        result2 = subprocess.run(
                            ["sudo", "-n", "kill", "-9", str(pid)],
                            capture_output=True,
                            text=True,
                        )
                        if result2.returncode == 0:
                            self.app.call_from_thread(
                                log_widget.write,
                                f"[{theme.YELLOW}]Killed PID {pid} with sudo[/{theme.YELLOW}]",
                            )
                            try:
                                from dimos.core.run_registry import get_most_recent

                                entry = get_most_recent()
                                if entry and entry.pid == pid:
                                    entry.remove()
                            except Exception:
                                pass

                            def _after2() -> None:
                                self._failed_stop_pid = None
                                self._running_entry = None
                                self._show_stopped("Killed with sudo")

                            self.app.call_from_thread(_after2)
                            return

                    self.app.call_from_thread(
                        log_widget.write,
                        "[red]sudo kill failed — could not obtain sudo credentials[/red]",
                    )

                    def _reenable() -> None:
                        self.query_one("#btn-sudo-kill", Button).disabled = False

                    self.app.call_from_thread(_reenable)
            except Exception as e:
                self.app.call_from_thread(log_widget.write, f"[red]sudo kill error: {e}[/red]")

                def _reenable2() -> None:
                    self.query_one("#btn-sudo-kill", Button).disabled = False

                self.app.call_from_thread(_reenable2)

        threading.Thread(target=_do_kill, daemon=True).start()

    def _open_log_in_editor(self) -> None:
        """Open the log file in the user's editor (non-blocking)."""
        try:
            from dimos.core.log_viewer import resolve_log_path

            entry = self._running_entry
            if entry:
                path = resolve_log_path(entry.run_id)
            else:
                path = resolve_log_path()  # most recent

            if not path:
                self.app.notify("No log file found", severity="warning")
                return

            self._open_source_file(str(path), 0)
        except Exception:
            pass

    def on_unmount_subapp(self) -> None:
        self._stop_log = True
