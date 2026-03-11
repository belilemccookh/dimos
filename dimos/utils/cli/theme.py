# Copyright 2025-2026 Dimensional Inc.
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

"""DimOS theme system.

Provides named themes for the DUI TUI. Each theme defines:
  - Textual Theme fields (primary, background, etc.) for built-in widgets
  - Custom CSS variables (prefixed ``dui-``) for DimOS-specific styling
  - Python-level constants (ACCENT, DIM, AGENT, …) for Rich markup

Usage in CSS::

    background: $dui-bg;
    border: solid $dui-dim;
    color: $dui-text;

Usage in Python (Rich markup)::

    f"[{theme.AGENT}]agent response[/{theme.AGENT}]"
"""

from __future__ import annotations

from pathlib import Path
import re


def parse_tcss_colors(tcss_path: str | Path) -> dict[str, str]:
    """Parse color variables from a tcss file."""
    tcss_path = Path(tcss_path)
    content = tcss_path.read_text()
    pattern = r"\$([a-zA-Z0-9_-]+)\s*:\s*(#[0-9a-fA-F]{6}|#[0-9a-fA-F]{3});"
    matches = re.findall(pattern, content)
    return {name: value for name, value in matches}


# Load DimOS theme colors (used by standalone apps via CSS_PATH)
_THEME_PATH = Path(__file__).parent / "dimos.tcss"
COLORS = parse_tcss_colors(_THEME_PATH)

# Export CSS path for standalone Textual apps (not DUI)
CSS_PATH = str(_THEME_PATH)


# Convenience accessor
def get(name: str, default: str = "#ffffff") -> str:
    """Get a color by variable name."""
    return COLORS.get(name, default)


# ---------------------------------------------------------------------------
# Theme definitions
# ---------------------------------------------------------------------------

# Each entry maps custom CSS variable names (without ``$``) to hex values.
# These are injected into Textual's CSS variable system via Theme.variables.
# The keys here become ``$dui-bg``, ``$dui-dim``, etc. in CSS.

_THEME_VARIABLES: dict[str, dict[str, str]] = {
    "dark": {
        # Core
        "dui-bg": "#0b0f0f",
        "dui-fg": "#b5e4f4",
        "dui-text": "#b5e4f4",
        "dui-dim": "#404040",
        "dui-accent": "#00eeee",
        "dui-border": "#00eeee",
        # Base palette
        "dui-yellow": "#ffcc00",
        "dui-red": "#ff0000",
        "dui-green": "#00eeee",
        "dui-blue": "#5c9ff0",
        "dui-purple": "#c07ff0",
        # Chat message colors
        "dui-agent": "#88ff88",
        "dui-tool": "#00eeee",
        "dui-tool-result": "#ffff00",
        "dui-human": "#ffffff",
        "dui-timestamp": "#ffffff",
        # UI chrome
        "dui-header": "#ff8800",
        "dui-panel-bg": "#1a2a2a",
        "dui-hint-bg": "#1a2020",
        "dui-tab1": "#00eeee",
        "dui-tab1-bg": "#1a2a2a",
        "dui-tab2": "#5c9ff0",
        "dui-tab2-bg": "#1a1a2a",
        "dui-tab3": "#c07ff0",
        "dui-tab3-bg": "#2a1a2a",
    },
    "midnight": {
        "dui-bg": "#0a0e1a",
        "dui-fg": "#a0b8d0",
        "dui-text": "#a0b8d0",
        "dui-dim": "#303850",
        "dui-accent": "#4488cc",
        "dui-border": "#4488cc",
        "dui-yellow": "#ccaa44",
        "dui-red": "#cc4444",
        "dui-green": "#44aa88",
        "dui-blue": "#5588dd",
        "dui-purple": "#8866cc",
        "dui-agent": "#66cc88",
        "dui-tool": "#4488cc",
        "dui-tool-result": "#ccaa44",
        "dui-human": "#d0d8e0",
        "dui-timestamp": "#8899bb",
        "dui-header": "#dd8833",
        "dui-panel-bg": "#151c2e",
        "dui-hint-bg": "#101828",
        "dui-tab1": "#4488cc",
        "dui-tab1-bg": "#151c2e",
        "dui-tab2": "#5588dd",
        "dui-tab2-bg": "#14183a",
        "dui-tab3": "#8866cc",
        "dui-tab3-bg": "#1c1430",
    },
    "ember": {
        "dui-bg": "#120c0a",
        "dui-fg": "#e0c8b0",
        "dui-text": "#e0c8b0",
        "dui-dim": "#4a3028",
        "dui-accent": "#ee8844",
        "dui-border": "#ee8844",
        "dui-yellow": "#ddaa33",
        "dui-red": "#dd4433",
        "dui-green": "#88aa44",
        "dui-blue": "#cc8844",
        "dui-purple": "#cc6688",
        "dui-agent": "#aacc66",
        "dui-tool": "#ee8844",
        "dui-tool-result": "#ddaa33",
        "dui-human": "#e8d8c8",
        "dui-timestamp": "#aa9080",
        "dui-header": "#ff8844",
        "dui-panel-bg": "#2a1810",
        "dui-hint-bg": "#1a1210",
        "dui-tab1": "#ee8844",
        "dui-tab1-bg": "#2a1810",
        "dui-tab2": "#cc8844",
        "dui-tab2-bg": "#2a2010",
        "dui-tab3": "#cc6688",
        "dui-tab3-bg": "#2a1420",
    },
    "forest": {
        "dui-bg": "#0a100c",
        "dui-fg": "#b0d0b8",
        "dui-text": "#b0d0b8",
        "dui-dim": "#2a3a2e",
        "dui-accent": "#44cc88",
        "dui-border": "#44cc88",
        "dui-yellow": "#aacc44",
        "dui-red": "#cc4444",
        "dui-green": "#44cc88",
        "dui-blue": "#44aa99",
        "dui-purple": "#88aa66",
        "dui-agent": "#66dd88",
        "dui-tool": "#44cc88",
        "dui-tool-result": "#aacc44",
        "dui-human": "#d0e0d0",
        "dui-timestamp": "#80aa88",
        "dui-header": "#88cc44",
        "dui-panel-bg": "#142a1a",
        "dui-hint-bg": "#101a14",
        "dui-tab1": "#44cc88",
        "dui-tab1-bg": "#142a1a",
        "dui-tab2": "#44aa99",
        "dui-tab2-bg": "#142a26",
        "dui-tab3": "#88aa66",
        "dui-tab3-bg": "#1e2a14",
    },
}

# Textual Theme constructor args for each theme
_THEME_BASES: dict[str, dict[str, object]] = {
    "dark": {
        "primary": "#00eeee",
        "secondary": "#5c9ff0",
        "warning": "#ffcc00",
        "error": "#ff0000",
        "success": "#88ff88",
        "accent": "#00eeee",
        "foreground": "#b5e4f4",
        "background": "#0b0f0f",
        "surface": "#0b0f0f",
        "panel": "#1a2a2a",
        "dark": True,
    },
    "midnight": {
        "primary": "#4488cc",
        "secondary": "#5588dd",
        "warning": "#ccaa44",
        "error": "#cc4444",
        "success": "#44aa88",
        "accent": "#4488cc",
        "foreground": "#a0b8d0",
        "background": "#0a0e1a",
        "surface": "#0a0e1a",
        "panel": "#151c2e",
        "dark": True,
    },
    "ember": {
        "primary": "#ee8844",
        "secondary": "#cc8844",
        "warning": "#ddaa33",
        "error": "#dd4433",
        "success": "#88aa44",
        "accent": "#ee8844",
        "foreground": "#e0c8b0",
        "background": "#120c0a",
        "surface": "#120c0a",
        "panel": "#2a1810",
        "dark": True,
    },
    "forest": {
        "primary": "#44cc88",
        "secondary": "#44aa99",
        "warning": "#aacc44",
        "error": "#cc4444",
        "success": "#44cc88",
        "accent": "#44cc88",
        "foreground": "#b0d0b8",
        "background": "#0a100c",
        "surface": "#0a100c",
        "panel": "#142a1a",
        "dark": True,
    },
}

THEME_NAMES: list[str] = list(_THEME_VARIABLES)
DEFAULT_THEME = "dark"


def get_textual_themes() -> list[object]:
    """Return a list of Textual ``Theme`` objects for all DimOS themes."""
    from textual.theme import Theme as TextualTheme

    themes = []
    for name in THEME_NAMES:
        base = _THEME_BASES[name]
        variables = _THEME_VARIABLES[name]
        themes.append(
            TextualTheme(
                name=f"dimos-{name}",
                variables=variables,
                **base,  # type: ignore[arg-type]
            )
        )
    return themes


def _vars_for(name: str) -> dict[str, str]:
    """Get the CSS variable dict for a theme by short name."""
    return _THEME_VARIABLES.get(name, _THEME_VARIABLES[DEFAULT_THEME])


# ---------------------------------------------------------------------------
# Active theme tracking + Python-level constants
# ---------------------------------------------------------------------------

active_theme: str = DEFAULT_THEME


def set_theme(name: str) -> None:
    """Switch the active theme and update all module-level color constants.

    This updates the Python constants used in Rich markup (e.g. ``theme.AGENT``).
    For Textual CSS variables, also call ``app.theme = f"dimos-{name}"``.
    """
    global active_theme
    if name not in _THEME_VARIABLES:
        return
    active_theme = name
    v = _THEME_VARIABLES[name]
    _apply_vars(v)


def _apply_vars(v: dict[str, str]) -> None:
    """Update module-level constants from a CSS-variable dict."""
    import dimos.utils.cli.theme as _self

    _self.BACKGROUND = v["dui-bg"]
    _self.BG = v["dui-bg"]
    _self.FOREGROUND = v["dui-fg"]
    _self.ACCENT = v["dui-text"]
    _self.DIM = v["dui-dim"]
    _self.CYAN = v["dui-accent"]
    _self.BORDER = v["dui-border"]
    _self.YELLOW = v["dui-yellow"]
    _self.RED = v["dui-red"]
    _self.GREEN = v["dui-green"]
    _self.BLUE = v["dui-blue"]
    _self.PURPLE = v.get("dui-purple", v["dui-accent"])
    _self.AGENT = v["dui-agent"]
    _self.TOOL = v["dui-tool"]
    _self.TOOL_RESULT = v["dui-tool-result"]
    _self.HUMAN = v["dui-human"]
    _self.TIMESTAMP = v["dui-timestamp"]
    _self.SYSTEM = v["dui-red"]
    _self.SUCCESS = v["dui-green"]
    _self.ERROR = v["dui-red"]
    _self.WARNING = v["dui-yellow"]
    _self.INFO = v["dui-accent"]
    _self.BLACK = v["dui-bg"]
    _self.WHITE = v["dui-fg"]
    _self.BRIGHT_BLACK = v["dui-dim"]
    _self.BRIGHT_WHITE = v["dui-timestamp"]
    _self.CURSOR = v["dui-accent"]
    _self.BRIGHT_RED = v["dui-red"]
    _self.BRIGHT_GREEN = v["dui-green"]
    _self.BRIGHT_YELLOW = v.get("dui-yellow", "#f2ea8c")
    _self.BRIGHT_BLUE = v.get("dui-blue", "#8cbdf2")
    _self.BRIGHT_PURPLE = v.get("dui-purple", v["dui-accent"])
    _self.BRIGHT_CYAN = v["dui-accent"]


# ---------------------------------------------------------------------------
# Initial module-level constants (from dimos.tcss defaults)
# ---------------------------------------------------------------------------

# Base color palette
BLACK = COLORS.get("black", "#0b0f0f")
RED = COLORS.get("red", "#ff0000")
GREEN = COLORS.get("green", "#00eeee")
YELLOW = COLORS.get("yellow", "#ffcc00")
BLUE = COLORS.get("blue", "#5c9ff0")
PURPLE = COLORS.get("purple", "#00eeee")
CYAN = COLORS.get("cyan", "#00eeee")
WHITE = COLORS.get("white", "#b5e4f4")

# Bright colors
BRIGHT_BLACK = COLORS.get("bright-black", "#404040")
BRIGHT_RED = COLORS.get("bright-red", "#ff0000")
BRIGHT_GREEN = COLORS.get("bright-green", "#00eeee")
BRIGHT_YELLOW = COLORS.get("bright-yellow", "#f2ea8c")
BRIGHT_BLUE = COLORS.get("bright-blue", "#8cbdf2")
BRIGHT_PURPLE = COLORS.get("bright-purple", "#00eeee")
BRIGHT_CYAN = COLORS.get("bright-cyan", "#00eeee")
BRIGHT_WHITE = COLORS.get("bright-white", "#ffffff")

# Core theme colors
BACKGROUND = COLORS.get("background", "#0b0f0f")
FOREGROUND = COLORS.get("foreground", "#b5e4f4")
CURSOR = COLORS.get("cursor", "#00eeee")

# Semantic aliases
BG = COLORS.get("bg", "#0b0f0f")
BORDER = COLORS.get("border", "#00eeee")
ACCENT = COLORS.get("accent", "#b5e4f4")
DIM = COLORS.get("dim", "#404040")
TIMESTAMP = COLORS.get("timestamp", "#ffffff")

# Message type colors
SYSTEM = COLORS.get("system", "#ff0000")
AGENT = COLORS.get("agent", "#88ff88")
TOOL = COLORS.get("tool", "#00eeee")
TOOL_RESULT = COLORS.get("tool-result", "#ffff00")
HUMAN = COLORS.get("human", "#ffffff")

# Status colors
SUCCESS = COLORS.get("success", "#00eeee")
ERROR = COLORS.get("error", "#ff0000")
WARNING = COLORS.get("warning", "#ffcc00")
INFO = COLORS.get("info", "#00eeee")

ascii_logo = """
   ▇▇▇▇▇▇╗ ▇▇╗▇▇▇╗   ▇▇▇╗▇▇▇▇▇▇▇╗▇▇▇╗   ▇▇╗▇▇▇▇▇▇▇╗▇▇╗ ▇▇▇▇▇▇╗ ▇▇▇╗   ▇▇╗ ▇▇▇▇▇╗ ▇▇╗
   ▇▇╔══▇▇╗▇▇║▇▇▇▇╗ ▇▇▇▇║▇▇╔════╝▇▇▇▇╗  ▇▇║▇▇╔════╝▇▇║▇▇╔═══▇▇╗▇▇▇▇╗  ▇▇║▇▇╔══▇▇╗▇▇║
   ▇▇║  ▇▇║▇▇║▇▇╔▇▇▇▇╔▇▇║▇▇▇▇▇╗  ▇▇╔▇▇╗ ▇▇║▇▇▇▇▇▇▇╗▇▇║▇▇║   ▇▇║▇▇╔▇▇╗ ▇▇║▇▇▇▇▇▇▇║▇▇║
   ▇▇║  ▇▇║▇▇║▇▇║╚▇▇╔╝▇▇║▇▇╔══╝  ▇▇║╚▇▇╗▇▇║╚════▇▇║▇▇║▇▇║   ▇▇║▇▇║╚▇▇╗▇▇║▇▇╔══▇▇║▇▇║
   ▇▇▇▇▇▇╔╝▇▇║▇▇║ ╚═╝ ▇▇║▇▇▇▇▇▇▇╗▇▇║ ╚▇▇▇▇║▇▇▇▇▇▇▇║▇▇║╚▇▇▇▇▇▇╔╝▇▇║ ╚▇▇▇▇║▇▇║  ▇▇║▇▇▇▇▇▇▇╗
   ╚═════╝ ╚═╝╚═╝     ╚═╝╚══════╝╚═╝  ╚═══╝╚══════╝╚═╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝  ╚═╝╚══════╝
"""
