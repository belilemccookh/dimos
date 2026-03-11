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
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.panel import Panel
from rich.text import Text
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, RichLog, Static

from dimos.utils.cli import theme
from dimos.utils.cli.dui.sub_app import SubApp

if TYPE_CHECKING:
    from textual.app import ComposeResult


def _launch_log_path() -> Path:
    """Well-known path for launch stdout/stderr (shared with launcher)."""
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) / "dimos" if xdg else Path.home() / ".local" / "state" / "dimos"
    return base / "launch.log"


class StatusSubApp(SubApp):
    TITLE = "status"

    DEFAULT_CSS = f"""
    StatusSubApp {{
        layout: vertical;
        height: 1fr;
        background: {theme.BACKGROUND};
    }}
    StatusSubApp .subapp-header {{
        width: 100%;
        height: auto;
        color: #ff8800;
        padding: 1 2;
        text-style: bold;
    }}
    StatusSubApp RichLog {{
        height: 1fr;
        background: {theme.BACKGROUND};
        border: solid {theme.DIM};
        scrollbar-size: 0 0;
    }}
    StatusSubApp #idle-container {{
        height: 1fr;
        align: center middle;
    }}
    StatusSubApp #idle-panel {{
        width: auto;
        background: transparent;
    }}
    StatusSubApp #run-controls {{
        height: auto;
        padding: 0 1;
        background: {theme.BACKGROUND};
    }}
    StatusSubApp #run-controls Button {{
        margin: 0 1 0 0;
        min-width: 12;
        background: transparent;
        border: solid {theme.DIM};
        color: {theme.ACCENT};
    }}
    StatusSubApp .status-bar {{
        height: 1;
        dock: bottom;
        background: #1a2020;
        color: {theme.DIM};
        padding: 0 1;
    }}
    StatusSubApp #btn-stop {{
        border: solid #882222;
        color: #cc4444;
    }}
    StatusSubApp #btn-stop:hover {{
        border: solid #cc4444;
    }}
    StatusSubApp #btn-stop:focus {{
        background: #882222;
        color: #ffffff;
        border: solid #cc4444;
    }}
    StatusSubApp #btn-sudo-kill {{
        border: solid #882222;
        color: #ff4444;
    }}
    StatusSubApp #btn-sudo-kill:hover {{
        border: solid #ff4444;
    }}
    StatusSubApp #btn-sudo-kill:focus {{
        background: #882222;
        color: #ffffff;
        border: solid #ff4444;
    }}
    StatusSubApp #btn-restart {{
        border: solid #886600;
        color: #ccaa00;
    }}
    StatusSubApp #btn-restart:hover {{
        border: solid #ccaa00;
    }}
    StatusSubApp #btn-restart:focus {{
        background: #886600;
        color: #ffffff;
        border: solid #ccaa00;
    }}
    StatusSubApp #btn-open-log {{
        border: solid #445566;
        color: #8899aa;
    }}
    StatusSubApp #btn-open-log:hover {{
        border: solid #8899aa;
    }}
    StatusSubApp #btn-open-log:focus {{
        background: #445566;
        color: #ffffff;
        border: solid #8899aa;
    }}
    """

    def __init__(self) -> None:
        super().__init__()
        self._running_entry: Any = None
        self._log_thread: threading.Thread | None = None
        self._stop_log = False
        self._failed_stop_pid: int | None = None
        self._following_launch_log = False
        self._launch_log_mtime: float = 0.0

    def compose(self) -> ComposeResult:
        yield Static("Blueprint Status", classes="subapp-header")
        with VerticalScroll(id="idle-container"):
            yield Static(self._idle_panel(), id="idle-panel")
        yield RichLog(id="runner-log", markup=True, wrap=True)
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
        self._check_running()
        if self._running_entry is not None:
            self._show_running()
        else:
            # Check if there's a fresh launch log to tail
            self._check_launch_log()
            if not self._following_launch_log:
                self._show_idle()
        self.set_interval(1.0, self._poll_running)

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
        except Exception:
            self._running_entry = None

    def _poll_running(self) -> None:
        old_entry = self._running_entry
        self._check_running()
        new_entry = self._running_entry

        old_id = getattr(old_entry, "run_id", None)
        new_id = getattr(new_entry, "run_id", None)

        if old_id != new_id:
            if new_entry is not None:
                # A daemon appeared — switch from launch log to JSONL follow
                self._stop_log = True
                self._following_launch_log = False
                self._show_running()
                return
            elif old_entry is not None:
                # Process ended
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
        # Only pick up if it was modified recently (within 30s) and is newer than last seen
        if mtime <= self._launch_log_mtime:
            return
        if time.time() - mtime > 30:
            return
        self._launch_log_mtime = mtime
        self._following_launch_log = True
        self._show_launching(log_path)

    def _show_launching(self, log_path: Path) -> None:
        """Show the launch log output while the daemon is starting."""
        self.query_one("#idle-container").styles.display = "none"
        self.query_one("#runner-log").styles.display = "block"
        self.query_one("#run-controls").styles.display = "none"
        status = self.query_one("#runner-status", Static)
        status.update("Launching blueprint...")

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
                            self.app.call_from_thread(log_widget.write, rendered)
                        else:
                            time.sleep(0.2)
                            # Check if the launch process finished (file stopped growing)
                            # and a daemon entry appeared — poll handles the transition
            except Exception as e:
                self.app.call_from_thread(log_widget.write, f"[red]Error reading launch log: {e}[/red]")

        self._log_thread = threading.Thread(target=_tail, daemon=True)
        self._log_thread.start()

    def _show_running(self) -> None:
        """Show controls for a running blueprint."""
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
            status.update(f"Running: {entry.blueprint} (PID {entry.pid})")
            self._start_log_follow(entry)

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
    # Log streaming
    # ------------------------------------------------------------------

    def _start_log_follow(self, entry: Any) -> None:
        self._stop_log = False
        log_widget = self.query_one("#runner-log", RichLog)
        log_widget.clear()

        def _follow() -> None:
            try:
                from dimos.core.log_viewer import (
                    follow_log,
                    format_line,
                    read_log,
                    resolve_log_path,
                )

                path = resolve_log_path(entry.run_id)
                if not path:
                    self.app.call_from_thread(log_widget.write, "[dim]No log file found[/dim]")
                    return

                for line in read_log(path, 50):
                    if self._stop_log:
                        return
                    formatted = format_line(line)
                    self.app.call_from_thread(log_widget.write, formatted)

                for line in follow_log(path, stop=lambda: self._stop_log):
                    formatted = format_line(line)
                    self.app.call_from_thread(log_widget.write, formatted)
            except Exception as e:
                self.app.call_from_thread(log_widget.write, f"[red]Error: {e}[/red]")

        self._log_thread = threading.Thread(target=_follow, daemon=True)
        self._log_thread.start()

    # ------------------------------------------------------------------
    # Button handling
    # ------------------------------------------------------------------

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
                    if "permission" in str(e).lower() or "operation not permitted" in str(e).lower():
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
                    s.update(f"Stop failed (permission denied) — try Force Kill for PID {entry.pid}")
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

            cmd = [sys.executable, "-m", "dimos.robot.cli.dimos", *config_args, "run", "--daemon", name]
            env = os.environ.copy()
            env["FORCE_COLOR"] = "1"
            env["PYTHONUNBUFFERED"] = "1"
            env["TERM"] = env.get("TERM", "xterm-256color")
            try:
                log_file = _launch_log_path()
                with open(log_file, "w") as f:
                    proc = subprocess.Popen(
                        cmd,
                        stdin=subprocess.DEVNULL,
                        stdout=f,
                        stderr=subprocess.STDOUT,
                        env=env,
                        start_new_session=True,
                    )
                    proc.wait()
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
        try:
            from dimos.core.log_viewer import resolve_log_path

            entry = self._running_entry
            if entry:
                path = resolve_log_path(entry.run_id)
            else:
                path = resolve_log_path()  # most recent

            if not path:
                log_widget = self.query_one("#runner-log", RichLog)
                log_widget.write("[dim]No log file found[/dim]")
                return

            editor = os.environ.get("EDITOR", "vi")
            self.app.suspend()
            os.system(f"{editor} {path}")
            self.app.resume()
        except Exception:
            pass

    def on_unmount_subapp(self) -> None:
        self._stop_log = True
