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

"""Config sub-app — interactive GlobalConfig editor."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import TYPE_CHECKING, Any

from rich.text import Text
from textual.containers import Horizontal
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Input, Label, Static, Switch

from dimos.utils.cli import theme
from dimos.utils.cli.dui.sub_app import SubApp

if TYPE_CHECKING:
    from textual.app import ComposeResult

_VIEWER_OPTIONS = ["rerun", "rerun-web", "rerun-connect", "foxglove", "none"]


class _FormNavigationMixin:
    """Mixin that intercepts Up/Down to move focus between config fields."""

    _FIELD_ORDER = ("cfg-viewer", "cfg-n-workers", "cfg-robot-ip", "cfg-dtop")

    def _navigate_field(self, delta: int) -> None:
        my_id = getattr(self, "id", None)
        if my_id not in self._FIELD_ORDER:
            return
        idx = list(self._FIELD_ORDER).index(my_id)
        new_idx = (idx + delta) % len(self._FIELD_ORDER)
        try:
            self.screen.query_one(f"#{self._FIELD_ORDER[new_idx]}").focus()  # type: ignore[attr-defined]
        except Exception:
            pass


class CycleSelect(_FormNavigationMixin, Widget, can_focus=True):
    """A focusable selector that cycles through options with Left/Right."""

    BINDINGS = [
        ("left", "cycle(-1)", "Previous"),
        ("right", "cycle(1)", "Next"),
        ("enter", "cycle(1)", "Next"),
        ("space", "cycle(1)", "Next"),
        ("down", "nav(1)", "Next field"),
        ("up", "nav(-1)", "Previous field"),
    ]

    current_value: reactive[str] = reactive("")

    class Changed(Message):
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    def __init__(self, options: list[str], value: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._options = list(options)
        self._index = options.index(value) if value in options else 0
        self.current_value = options[self._index]

    def render(self) -> Text:
        txt = Text(justify="left")
        txt.append("  ◀  ", style="bold")
        txt.append(self.current_value, style="bold")
        txt.append("  ▶  ", style="bold")
        return txt

    def action_cycle(self, delta: int) -> None:
        self._index = (self._index + delta) % len(self._options)
        self.current_value = self._options[self._index]
        self.post_message(self.Changed(self.current_value))

    def action_nav(self, delta: int) -> None:
        self._navigate_field(delta)


class ConfigInput(_FormNavigationMixin, Input):
    """Input that uses Up/Down for form navigation instead of history."""

    BINDINGS = [
        ("down", "nav(1)", "Next field"),
        ("up", "nav(-1)", "Previous field"),
    ]

    def action_nav(self, delta: int) -> None:
        self._navigate_field(delta)


_DEFAULTS: dict[str, object] = {
    "viewer": "rerun",
    "n_workers": 2,
    "robot_ip": "",
    "dtop": False,
}


def _config_path() -> Path:
    """Return the path to the persisted dio config file inside .venv."""
    # Walk up from the interpreter to find the venv root
    venv = Path(sys.prefix)
    return venv / "dio-config.json"


def _load_config() -> dict[str, object]:
    """Load saved config, falling back to defaults."""
    values = dict(_DEFAULTS)
    try:
        data = json.loads(_config_path().read_text())
        for k in _DEFAULTS:
            if k in data:
                values[k] = data[k]
    except Exception:
        pass
    return values


def _save_config(values: dict[str, object]) -> None:
    """Persist config values to disk."""
    try:
        _config_path().write_text(json.dumps(values, indent=2) + "\n")
    except Exception:
        pass


class ConfigSubApp(SubApp):
    TITLE = "config"

    DEFAULT_CSS = f"""
    ConfigSubApp {{
        layout: vertical;
        padding: 1 2;
        background: {theme.BACKGROUND};
        overflow-y: auto;
    }}
    ConfigSubApp .subapp-header {{
        color: #ff8800;
        padding: 0;
        text-style: bold;
    }}
    ConfigSubApp Label {{
        margin-top: 1;
        color: {theme.ACCENT};
    }}
    ConfigSubApp .field-label {{
        color: {theme.CYAN};
        margin-bottom: 0;
    }}
    ConfigSubApp Input, ConfigSubApp ConfigInput {{
        width: 40;
    }}
    ConfigSubApp CycleSelect {{
        width: 40;
        height: 3;
        background: {theme.BACKGROUND};
        color: {theme.ACCENT};
        border: solid {theme.DIM};
        content-align: left middle;
    }}
    ConfigSubApp CycleSelect:focus {{
        border: solid {theme.CYAN};
        color: {theme.CYAN};
    }}
    ConfigSubApp .switch-row {{
        height: 3;
        margin-top: 1;
    }}
    ConfigSubApp .switch-row Label {{
        margin-top: 0;
        padding: 1 0;
    }}
    ConfigSubApp .switch-state {{
        color: {theme.DIM};
        padding: 1 1;
        width: 6;
    }}
    ConfigSubApp .switch-state.--on {{
        color: {theme.CYAN};
    }}
    ConfigSubApp #cfg-dirty-notice {{
        margin-top: 1;
        color: {theme.YELLOW};
        display: none;
    }}
    """

    def __init__(self) -> None:
        super().__init__()
        self.config_values: dict[str, object] = _load_config()

    def compose(self) -> ComposeResult:
        v = self.config_values
        yield Static("GlobalConfig Editor", classes="subapp-header")

        yield Label("viewer", classes="field-label")
        yield CycleSelect(
            _VIEWER_OPTIONS,
            value=str(v.get("viewer", "rerun")),
            id="cfg-viewer",
        )

        yield Label("n_workers", classes="field-label")
        yield ConfigInput(value=str(v.get("n_workers", 2)), id="cfg-n-workers", type="integer")

        yield Label("robot_ip", classes="field-label")
        yield ConfigInput(
            value=str(v.get("robot_ip", "")), placeholder="e.g. 192.168.12.1", id="cfg-robot-ip"
        )

        dtop_val = bool(v.get("dtop", False))
        with Horizontal(classes="switch-row"):
            yield Label("dtop", classes="field-label")
            yield Switch(value=dtop_val, id="cfg-dtop")
            state = Static("ON" if dtop_val else "OFF", id="cfg-dtop-state", classes="switch-state")
            if dtop_val:
                state.add_class("--on")
            yield state

        yield Static("edits only take effect on new blueprint launch", id="cfg-dirty-notice")

    def _mark_dirty(self) -> None:
        self.query_one("#cfg-dirty-notice").styles.display = "block"

    def on_cycle_select_changed(self, event: CycleSelect.Changed) -> None:
        self.config_values["viewer"] = event.value
        _save_config(self.config_values)
        self._mark_dirty()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "cfg-n-workers":
            try:
                self.config_values["n_workers"] = int(event.value)
            except ValueError:
                pass
            _save_config(self.config_values)
            self._mark_dirty()
        elif event.input.id == "cfg-robot-ip":
            self.config_values["robot_ip"] = event.value
            _save_config(self.config_values)
            self._mark_dirty()

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if event.switch.id == "cfg-dtop":
            self.config_values["dtop"] = event.value
            state_label = self.query_one("#cfg-dtop-state", Static)
            if event.value:
                state_label.update("ON")
                state_label.add_class("--on")
            else:
                state_label.update("OFF")
                state_label.remove_class("--on")
            _save_config(self.config_values)
            self._mark_dirty()

    def get_overrides(self) -> dict[str, object]:
        """Return config overrides for use by the runner."""
        overrides: dict[str, object] = {}
        for k, v in self.config_values.items():
            if k == "robot_ip" and not v:
                continue
            overrides[k] = v
        return overrides
