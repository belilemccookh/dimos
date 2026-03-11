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

"""Runner sub-app — blueprint launcher with log viewer."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from typing import TYPE_CHECKING, Any

from textual.containers import Horizontal
from textual.widgets import Button, Input, Label, ListItem, ListView, RichLog, Static

from dimos.utils.cli import theme
from dimos.utils.cli.dui.sub_app import SubApp

if TYPE_CHECKING:
    from textual.app import ComposeResult


class RunnerSubApp(SubApp):
    TITLE = "runner"

    DEFAULT_CSS = f"""
    RunnerSubApp {{
        layout: vertical;
        background: {theme.BACKGROUND};
    }}
    RunnerSubApp .subapp-header {{
        width: 100%;
        height: auto;
        color: #ff8800;
        padding: 1 2;
        text-style: bold;
    }}
    RunnerSubApp #runner-filter {{
        width: 100%;
        margin: 0 0 0 0;
        background: {theme.BACKGROUND};
        border: solid {theme.DIM};
        color: {theme.ACCENT};
    }}
    RunnerSubApp #runner-filter:focus {{
        border: solid {theme.CYAN};
    }}
    RunnerSubApp ListView {{
        height: 1fr;
        background: {theme.BACKGROUND};
    }}
    RunnerSubApp ListView > ListItem {{
        background: {theme.BACKGROUND};
        color: {theme.ACCENT};
        padding: 1 2;
    }}
    RunnerSubApp ListView > ListItem.--highlight {{
        background: #1a2a2a;
    }}
    RunnerSubApp RichLog {{
        height: 1fr;
        background: {theme.BACKGROUND};
        border: solid {theme.DIM};
        scrollbar-size: 0 0;
    }}
    RunnerSubApp .run-controls {{
        dock: bottom;
        height: auto;
        padding: 0 1;
        background: {theme.BACKGROUND};
    }}
    RunnerSubApp .status-bar {{
        height: 1;
        dock: bottom;
        background: #1a2020;
        color: {theme.DIM};
        padding: 0 1;
    }}
    RunnerSubApp .run-controls Button {{
        margin: 0 1 0 0;
        min-width: 12;
        background: transparent;
        border: solid {theme.DIM};
        color: {theme.ACCENT};
    }}
    RunnerSubApp #btn-stop {{
        border: solid #882222;
        color: #cc4444;
    }}
    RunnerSubApp #btn-stop:hover {{
        border: solid #cc4444;
    }}
    RunnerSubApp #btn-stop:focus {{
        background: #882222;
        color: #ffffff;
        border: solid #cc4444;
    }}
    RunnerSubApp #btn-restart {{
        border: solid #886600;
        color: #ccaa00;
    }}
    RunnerSubApp #btn-restart:hover {{
        border: solid #ccaa00;
    }}
    RunnerSubApp #btn-restart:focus {{
        background: #886600;
        color: #ffffff;
        border: solid #ccaa00;
    }}
    RunnerSubApp #btn-pick {{
        border: solid #226688;
        color: #44aacc;
    }}
    RunnerSubApp #btn-pick:hover {{
        border: solid #44aacc;
    }}
    RunnerSubApp #btn-pick:focus {{
        background: #226688;
        color: #ffffff;
        border: solid #44aacc;
    }}
    RunnerSubApp #btn-open-log {{
        border: solid #445566;
        color: #8899aa;
    }}
    RunnerSubApp #btn-open-log:hover {{
        border: solid #8899aa;
    }}
    RunnerSubApp #btn-open-log:focus {{
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
        self._blueprints: list[str] = []
        self._filtered: list[str] = []
        self._child_proc: subprocess.Popen[str] | None = None
        self._launched_name: str | None = None

    def compose(self) -> ComposeResult:
        yield Static("Blueprint Runner", classes="subapp-header")
        yield Input(placeholder="Type to filter blueprints...", id="runner-filter")
        yield ListView(id="blueprint-list")
        yield RichLog(id="runner-log", markup=True, wrap=True)
        with Horizontal(classes="run-controls", id="run-controls"):
            yield Button("Stop", id="btn-stop", variant="error")
            yield Button("Restart", id="btn-restart", variant="warning")
            yield Button("Open Log File", id="btn-open-log")
            yield Button("Pick Blueprint", id="btn-pick")
        yield Static("", id="runner-status", classes="status-bar")

    def on_mount_subapp(self) -> None:
        self._check_running()
        if self._running_entry is None:
            self._populate_blueprints()
            self._show_list_mode()
        else:
            self._show_log_mode()
        # Poll the run registry so we can re-attach to blueprints
        # launched (or stopped) from another terminal.
        self.set_interval(2.0, self._poll_running)

    def get_focus_target(self) -> object | None:
        """Return the widget that should receive focus."""
        if self._running_entry is not None:
            try:
                return self.query_one("#runner-log", RichLog)
            except Exception:
                pass
        try:
            return self.query_one("#runner-filter", Input)
        except Exception:
            return super().get_focus_target()

    def _check_running(self) -> None:
        try:
            from dimos.core.run_registry import get_most_recent

            self._running_entry = get_most_recent(alive_only=True)
        except Exception:
            self._running_entry = None

    def _poll_running(self) -> None:
        """Periodically check the run registry for state changes."""
        old_entry = self._running_entry
        self._check_running()
        new_entry = self._running_entry

        old_id = getattr(old_entry, "run_id", None)
        new_id = getattr(new_entry, "run_id", None)

        if old_id == new_id:
            return

        if new_entry is not None and old_entry is None:
            self._stop_log = True
            self._show_log_mode()
        elif new_entry is None and old_entry is not None:
            self._stop_log = True
            self._populate_blueprints()
            self._show_list_mode()
            self.query_one("#runner-filter", Input).value = ""
        else:
            self._stop_log = True
            self._show_log_mode()

    def _populate_blueprints(self) -> None:
        try:
            from dimos.robot.all_blueprints import all_blueprints

            self._blueprints = sorted(
                name for name in all_blueprints if not name.startswith("demo-")
            )
        except Exception:
            self._blueprints = []

        self._filtered = list(self._blueprints)
        self._rebuild_list()

    def _rebuild_list(self) -> None:
        lv = self.query_one("#blueprint-list", ListView)
        lv.clear()
        for name in self._filtered:
            lv.append(ListItem(Label(name)))
        if self._filtered:
            lv.index = 0

    def _apply_filter(self, query: str) -> None:
        q = query.strip().lower()
        if not q:
            self._filtered = list(self._blueprints)
        else:
            self._filtered = [n for n in self._blueprints if q in n.lower()]
        self._rebuild_list()

    def _show_list_mode(self) -> None:
        self.query_one("#runner-filter").styles.display = "block"
        self.query_one("#blueprint-list").styles.display = "block"
        self.query_one("#runner-log").styles.display = "none"
        self.query_one("#run-controls").styles.display = "none"
        self.query_one("#btn-pick").styles.display = "none"
        status = self.query_one("#runner-status", Static)
        status.update("Up/Down: navigate | Enter: run | Type to filter")

    def _show_log_mode(self) -> None:
        self.query_one("#runner-filter").styles.display = "none"
        self.query_one("#blueprint-list").styles.display = "none"
        self.query_one("#runner-log").styles.display = "block"
        self.query_one("#run-controls").styles.display = "block"
        self.query_one("#btn-stop").styles.display = "block"
        self.query_one("#btn-restart").styles.display = "block"
        self.query_one("#btn-open-log").styles.display = "block"
        self.query_one("#btn-pick").styles.display = "none"
        self.query_one("#btn-stop", Button).focus()
        entry = self._running_entry
        if entry:
            status = self.query_one("#runner-status", Static)
            status.update(f"Running: {entry.blueprint} (PID {entry.pid})")
            self._start_log_follow(entry)

    def _show_failed_mode(self) -> None:
        """Show log output with only a 'Pick Blueprint' button."""
        self.query_one("#runner-filter").styles.display = "none"
        self.query_one("#blueprint-list").styles.display = "none"
        self.query_one("#runner-log").styles.display = "block"
        self.query_one("#run-controls").styles.display = "block"
        self.query_one("#btn-stop").styles.display = "none"
        self.query_one("#btn-restart").styles.display = "none"
        self.query_one("#btn-open-log").styles.display = "block"
        self.query_one("#btn-pick").styles.display = "block"
        status = self.query_one("#runner-status", Static)
        status.update("Launch failed — pick a blueprint to try again")
        self.query_one("#btn-pick", Button).focus()

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

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "runner-filter":
            self._apply_filter(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "runner-filter":
            lv = self.query_one("#blueprint-list", ListView)
            idx = lv.index
            if idx is not None and 0 <= idx < len(self._filtered):
                self._launch_blueprint(self._filtered[idx])

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        lv = self.query_one("#blueprint-list", ListView)
        idx = lv.index
        if idx is not None and 0 <= idx < len(self._filtered):
            self._launch_blueprint(self._filtered[idx])

    def _get_visible_buttons(self) -> list[Button]:
        """Return the currently visible control buttons in order."""
        buttons: list[Button] = []
        for bid in ("btn-stop", "btn-restart", "btn-open-log", "btn-pick"):
            try:
                btn = self.query_one(f"#{bid}", Button)
                if btn.styles.display != "none":
                    buttons.append(btn)
            except Exception:
                pass
        return buttons

    def _cycle_button_focus(self, delta: int) -> None:
        """Move focus among visible control buttons by delta (+1 or -1)."""
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

    def on_key(self, event: Any) -> None:
        key = getattr(event, "key", "")

        # In list mode: arrow keys on filter input move the list selection
        is_list_mode = (
            self._running_entry is None
            and self._child_proc is None
            and self.query_one("#runner-filter").styles.display != "none"
        )
        if is_list_mode:
            focused = self.app.focused
            filter_input = self.query_one("#runner-filter", Input)
            if focused is filter_input and key in ("up", "down"):
                lv = self.query_one("#blueprint-list", ListView)
                if self._filtered:
                    current = lv.index or 0
                    if key == "up":
                        lv.index = max(0, current - 1)
                    else:
                        lv.index = min(len(self._filtered) - 1, current + 1)
                event.prevent_default()
                event.stop()
                return

        # In log/failed mode: arrow keys navigate between buttons
        controls_visible = self.query_one("#run-controls").styles.display != "none"
        if controls_visible and key in ("left", "right"):
            self._cycle_button_focus(1 if key == "right" else -1)
            event.prevent_default()
            event.stop()
            return

        if controls_visible and key == "enter":
            focused = self.app.focused
            if isinstance(focused, Button):
                focused.press()
                event.prevent_default()
                event.stop()
                return

    def _launch_blueprint(self, name: str) -> None:
        """Launch a blueprint in a fully detached child process."""
        log_widget = self.query_one("#runner-log", RichLog)
        log_widget.clear()
        # Switch to log view immediately
        self.query_one("#runner-filter").styles.display = "none"
        self.query_one("#blueprint-list").styles.display = "none"
        self.query_one("#runner-log").styles.display = "block"
        status = self.query_one("#runner-status", Static)
        status.update(f"Launching {name}...")

        # Gather config overrides on the main thread
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
        self._launched_name = name
        self.query_one("#run-controls").styles.display = "block"
        log_widget.write(f"[dim]$ {' '.join(cmd)}[/dim]")

        try:
            # Launch in a fully detached process so it doesn't share
            # CPU scheduling priority with the TUI event loop.
            self._child_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,  # detach from our process group
            )
        except Exception as e:
            log_widget.write(f"[red]Launch error: {e}[/red]")
            return

        # Stream stdout in a background thread
        proc = self._child_proc

        def _stream_output() -> None:
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    self.app.call_from_thread(log_widget.write, line.rstrip("\n"))
                proc.wait()
                rc = proc.returncode
                if rc != 0:
                    self.app.call_from_thread(
                        log_widget.write,
                        f"[{theme.YELLOW}]Process exited with code {rc}[/{theme.YELLOW}]",
                    )
            except Exception as e:
                self.app.call_from_thread(log_widget.write, f"[red]Stream error: {e}[/red]")
            finally:
                self._child_proc = None

                # After the launch command finishes, poll will pick up
                # the running entry and switch to log-follow mode.
                def _after() -> None:
                    self._check_running()
                    if self._running_entry:
                        self._show_log_mode()
                    else:
                        self._show_failed_mode()

                self.app.call_from_thread(_after)

        threading.Thread(target=_stream_output, daemon=True).start()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-stop":
            self._stop_running()
        elif event.button.id == "btn-restart":
            self._restart_running()
        elif event.button.id == "btn-open-log":
            self._open_log_in_editor()
        elif event.button.id == "btn-pick":
            self._go_to_list()

    def _go_to_list(self) -> None:
        """Switch back to the blueprint picker."""
        self._stop_log = True
        self._running_entry = None
        self._populate_blueprints()
        self._show_list_mode()
        self.query_one("#runner-filter", Input).value = ""
        self.query_one("#runner-filter", Input).focus()

    def _stop_running(self, *, then_launch: str | None = None) -> None:
        self._stop_log = True
        log_widget = self.query_one("#runner-log", RichLog)
        status = self.query_one("#runner-status", Static)
        status.update("Stopping blueprint...")
        # Disable buttons so user can't double-tap
        for bid in ("btn-stop", "btn-restart"):
            try:
                self.query_one(f"#{bid}", Button).disabled = True
            except Exception:
                pass

        # Kill the launch subprocess immediately (non-blocking)
        if self._child_proc is not None:
            try:
                self._child_proc.send_signal(signal.SIGTERM)
                log_widget.write(f"[{theme.YELLOW}]Stopped launch process[/{theme.YELLOW}]")
            except Exception:
                pass
            self._child_proc = None

        entry = self._running_entry
        self._running_entry = None

        def _do_stop() -> None:
            if entry:
                try:
                    from dimos.core.run_registry import stop_entry

                    msg, _ = stop_entry(entry)
                    self.app.call_from_thread(
                        log_widget.write, f"[{theme.YELLOW}]{msg}[/{theme.YELLOW}]"
                    )
                except Exception as e:
                    self.app.call_from_thread(log_widget.write, f"[red]Stop error: {e}[/red]")

            def _after_stop() -> None:
                # Re-enable buttons
                for bid in ("btn-stop", "btn-restart"):
                    try:
                        self.query_one(f"#{bid}", Button).disabled = False
                    except Exception:
                        pass
                if then_launch:
                    self._launch_blueprint(then_launch)
                else:
                    self._populate_blueprints()
                    self._show_list_mode()
                    self.query_one("#runner-filter", Input).value = ""
                    self.query_one("#runner-filter", Input).focus()

            self.app.call_from_thread(_after_stop)

        threading.Thread(target=_do_stop, daemon=True).start()

    def _restart_running(self) -> None:
        entry = self._running_entry
        name = getattr(entry, "blueprint", None) or self._launched_name
        if name:
            self._stop_running(then_launch=name)

    def _open_log_in_editor(self) -> None:
        try:
            from dimos.core.log_viewer import resolve_log_path

            # Try the running entry first, then fall back to most recent
            entry = self._running_entry
            if entry:
                path = resolve_log_path(entry.run_id)
            else:
                path = resolve_log_path()  # resolves most recent

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
        # Kill the launch subprocess if still running (not the daemon — just the launcher)
        if self._child_proc is not None:
            try:
                self._child_proc.send_signal(signal.SIGTERM)
            except Exception:
                pass
