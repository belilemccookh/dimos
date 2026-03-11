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

"""Launcher sub-app — blueprint picker and launcher."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import threading
from typing import TYPE_CHECKING, Any

from textual.widgets import Input, Label, ListItem, ListView, Static

from dimos.utils.cli import theme
from dimos.utils.cli.dui.sub_app import SubApp

if TYPE_CHECKING:
    from textual.app import ComposeResult


def _launch_log_path() -> Path:
    """Well-known path for launch stdout/stderr."""
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) / "dimos" if xdg else Path.home() / ".local" / "state" / "dimos"
    base.mkdir(parents=True, exist_ok=True)
    return base / "launch.log"


def _is_blueprint_running() -> bool:
    """Return True if a blueprint is currently running."""
    try:
        from dimos.core.run_registry import get_most_recent

        return get_most_recent(alive_only=True) is not None
    except Exception:
        return False


class LauncherSubApp(SubApp):
    TITLE = "launch"

    DEFAULT_CSS = f"""
    LauncherSubApp {{
        layout: vertical;
        height: 1fr;
        background: {theme.BACKGROUND};
    }}
    LauncherSubApp .subapp-header {{
        width: 100%;
        height: auto;
        color: #ff8800;
        padding: 1 2;
        text-style: bold;
    }}
    LauncherSubApp #launch-filter {{
        width: 100%;
        background: {theme.BACKGROUND};
        border: solid {theme.DIM};
        color: {theme.ACCENT};
    }}
    LauncherSubApp #launch-filter:focus {{
        border: solid {theme.CYAN};
    }}
    LauncherSubApp ListView {{
        height: 1fr;
        background: {theme.BACKGROUND};
    }}
    LauncherSubApp ListView > ListItem {{
        background: {theme.BACKGROUND};
        color: {theme.ACCENT};
        padding: 1 2;
    }}
    LauncherSubApp ListView > ListItem.--highlight {{
        background: #1a2a2a;
    }}
    LauncherSubApp.--locked ListView {{
        opacity: 0.35;
    }}
    LauncherSubApp.--locked #launch-filter {{
        opacity: 0.35;
    }}
    LauncherSubApp .status-bar {{
        height: 1;
        dock: bottom;
        background: #1a2020;
        color: {theme.DIM};
        padding: 0 1;
    }}
    """

    def __init__(self) -> None:
        super().__init__()
        self._blueprints: list[str] = []
        self._filtered: list[str] = []
        self._launching = False

    def compose(self) -> ComposeResult:
        yield Static("Blueprint Launcher", classes="subapp-header")
        yield Input(placeholder="Type to filter blueprints...", id="launch-filter")
        yield ListView(id="launch-list")
        yield Static("", id="launch-status", classes="status-bar")

    def on_mount_subapp(self) -> None:
        self._populate_blueprints()
        self._sync_status()
        self._start_poll_timer()

    def on_resume_subapp(self) -> None:
        self._start_poll_timer()
        self._sync_status()

    def _start_poll_timer(self) -> None:
        self.set_interval(2.0, self._sync_status)

    def get_focus_target(self) -> object | None:
        try:
            return self.query_one("#launch-filter", Input)
        except Exception:
            return super().get_focus_target()

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
        lv = self.query_one("#launch-list", ListView)
        lv.clear()
        for name in self._filtered:
            lv.append(ListItem(Label(name)))
        if self._filtered:
            lv.index = 0

    @property
    def _is_locked(self) -> bool:
        """True if launching is blocked (already running or mid-launch)."""
        return self._launching or _is_blueprint_running()

    def _sync_status(self) -> None:
        status = self.query_one("#launch-status", Static)
        locked = self._is_locked
        filter_input = self.query_one("#launch-filter", Input)
        lv = self.query_one("#launch-list", ListView)

        if locked:
            self.add_class("--locked")
            filter_input.disabled = True
            lv.disabled = True
        else:
            self.remove_class("--locked")
            filter_input.disabled = False
            lv.disabled = False

        if self._launching:
            return  # don't overwrite "Launching..." message
        if _is_blueprint_running():
            try:
                from dimos.core.run_registry import get_most_recent

                entry = get_most_recent(alive_only=True)
                name = entry.blueprint if entry else "unknown"
                status.update(f"Already running: {name} — stop it first")
            except Exception:
                status.update("A blueprint is already running")
        else:
            status.update("Up/Down: navigate | Enter: launch | Type to filter")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "launch-filter":
            q = event.value.strip().lower()
            if not q:
                self._filtered = list(self._blueprints)
            else:
                self._filtered = [n for n in self._blueprints if q in n.lower()]
            self._rebuild_list()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "launch-filter":
            if self._is_locked:
                return
            lv = self.query_one("#launch-list", ListView)
            idx = lv.index
            if idx is not None and 0 <= idx < len(self._filtered):
                self._launch(self._filtered[idx])

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if self._is_locked:
            return
        lv = self.query_one("#launch-list", ListView)
        idx = lv.index
        if idx is not None and 0 <= idx < len(self._filtered):
            self._launch(self._filtered[idx])

    def on_key(self, event: Any) -> None:
        key = getattr(event, "key", "")
        focused = self.app.focused
        filter_input = self.query_one("#launch-filter", Input)
        if focused is filter_input and key in ("up", "down"):
            lv = self.query_one("#launch-list", ListView)
            if self._filtered:
                current = lv.index or 0
                if key == "up":
                    lv.index = max(0, current - 1)
                else:
                    lv.index = min(len(self._filtered) - 1, current + 1)
            event.prevent_default()
            event.stop()

    def _launch(self, name: str) -> None:
        if self._is_locked:
            self._sync_status()
            return

        self._launching = True
        self._sync_status()  # lock the UI immediately
        status = self.query_one("#launch-status", Static)
        status.update(f"Launching {name}...")

        # Gather config overrides
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

        def _do_launch() -> None:
            log_file = _launch_log_path()
            # Preserve ANSI colors in piped output
            env = os.environ.copy()
            env["FORCE_COLOR"] = "1"
            env["PYTHONUNBUFFERED"] = "1"
            env["TERM"] = env.get("TERM", "xterm-256color")
            try:
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
                rc = proc.returncode

                def _after() -> None:
                    self._launching = False
                    if rc != 0:
                        s = self.query_one("#launch-status", Static)
                        s.update(f"Launch failed (exit code {rc})")
                    self._sync_status()

                self.app.call_from_thread(_after)
            except Exception:

                def _err() -> None:
                    self._launching = False
                    s = self.query_one("#launch-status", Static)
                    s.update(f"Launch error: {e}")
                    self._sync_status()

                self.app.call_from_thread(_err)

        threading.Thread(target=_do_launch, daemon=True).start()
