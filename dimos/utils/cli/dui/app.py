"""DUI — DimOS Unified TUI."""

from __future__ import annotations

import os
import sys
import threading
import time

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.events import Click, Key, Resize
from textual.widget import Widget
from textual.widgets import RichLog, Static

from dimos.utils.cli.dui.sub_app import SubApp
from dimos.utils.cli.dui.sub_apps import get_sub_apps

_DUAL_WIDTH = 240   # >= this width: 2 panels
_TRIPLE_WIDTH = 320  # >= this width: 3 panels
_MAX_PANELS = 3
_QUIT_WINDOW = 1.5  # seconds to press again to confirm quit


class DUIApp(App[None]):
    CSS_PATH = "dui.tcss"

    BINDINGS = [
        Binding("alt+up", "tab_prev", "Tab prev", priority=True),
        Binding("alt+down", "tab_next", "Tab next", priority=True),
        Binding("ctrl+up", "tab_prev", "Tab prev", priority=True),
        Binding("ctrl+down", "tab_next", "Tab next", priority=True),
        Binding("alt+left", "focus_prev_panel", "Panel prev", priority=True),
        Binding("alt+right", "focus_next_panel", "Panel next", priority=True),
        Binding("ctrl+left", "focus_prev_panel", "Panel prev", priority=True),
        Binding("ctrl+right", "focus_next_panel", "Panel next", priority=True),
        Binding("escape", "quit_or_esc", "Quit", priority=True),
        Binding("ctrl+c", "quit_or_esc", "Quit", priority=True),
    ]

    def __init__(self, *, debug: bool = False) -> None:
        super().__init__()
        self._debug = debug
        self._sub_app_classes = get_sub_apps()
        n = len(self._sub_app_classes)
        # Which sub-app index each panel shows
        self._panel_idx: list[int] = [i % n for i in range(_MAX_PANELS)]
        self._focused_panel: int = 0
        self._num_panels: int = 1  # how many panels are currently visible
        self._initialized = False
        self._instances: list[SubApp] = []
        self._quit_pressed_at: float = 0.0
        self._quit_timer: object | None = None
        # Track which panel each instance is currently mounted in
        self._instance_pane: dict[int, int] = {}  # instance_idx -> panel (0..N-1)
        # Debug log
        self._debug_log_path: str | None = None
        self._debug_log_file: object | None = None
        if debug:
            from pathlib import Path
            log_path = Path.home() / ".dimos" / "dio-debug.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            f = open(log_path, "w")
            self._debug_log_path = str(log_path)
            self._debug_log_file = f

    # ------------------------------------------------------------------
    # Debug log
    # ------------------------------------------------------------------

    def _log(self, msg: str) -> None:
        if not self._debug:
            return
        import re
        plain = re.sub(r"\[/?[^\]]*\]", "", msg)
        if self._debug_log_file is not None:
            try:
                self._debug_log_file.write(plain + "\n")  # type: ignore[union-attr]
                self._debug_log_file.flush()  # type: ignore[union-attr]
            except Exception:
                pass
        try:
            panel = self.query_one("#debug-log", RichLog)
            panel.write(msg)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static("", id="hint-bar")
        with Container(id="sidebar"):
            for i, cls in enumerate(self._sub_app_classes):
                yield Static(cls.TITLE, classes="tab-item", id=f"tab-{i}")
        with Horizontal(id="displays"):
            for p in range(_MAX_PANELS):
                yield Container(id=f"display-{p + 1}", classes="display-pane")
        if self._debug:
            yield RichLog(id="debug-log", markup=True, wrap=True, highlight=False)
        yield Static("", id="hint-bar")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_mount(self) -> None:
        self._instances = [cls() for cls in self._sub_app_classes]
        d1 = self.query_one("#display-1", Container)
        for i, inst in enumerate(self._instances):
            inst.styles.display = "none"
            await d1.mount(inst)
            self._instance_pane[i] = 0

        if not self._initialized:
            self._refresh_panel_count()
            self._initialized = True
        await self._place_instances()
        self._sync_tabs()
        self._sync_hint()
        if self._instances:
            self._force_focus_subapp(self._instances[self._panel_idx[0]])
        self._log(f"[dim]mounted {len(self._instances)} sub-apps, debug={self._debug}[/dim]")
        if self._debug_log_path:
            self._log(f"[dim]log file: {self._debug_log_path}[/dim]")

    async def on_resize(self, _event: Resize) -> None:
        old = self._num_panels
        self._refresh_panel_count()
        if old != self._num_panels:
            # Clamp focused panel
            if self._focused_panel >= self._num_panels:
                self._focused_panel = self._num_panels - 1
            await self._place_instances()
        self._sync_tabs()
        self._sync_hint()

    async def on_unmount(self) -> None:
        for inst in self._instances:
            inst.on_unmount_subapp()

    # ------------------------------------------------------------------
    # Focus tracking — auto-update _focused_panel when focus moves
    # ------------------------------------------------------------------

    def _panel_for_widget(self, widget: Widget | None) -> int | None:
        """Return which panel (0..N-1) contains the given widget, or None."""
        node = widget
        while node is not None:
            node_id = getattr(node, "id", None) or ""
            if node_id.startswith("display-"):
                try:
                    p = int(node_id.split("-")[1]) - 1
                    return p if p < self._num_panels else None
                except (ValueError, IndexError):
                    return None
            node = node.parent
        return None

    def _sync_focused_panel(self) -> None:
        """Update _focused_panel to match where the actually-focused widget lives."""
        panel = self._panel_for_widget(self.focused)
        if panel is not None and panel != self._focused_panel:
            old = self._focused_panel
            self._focused_panel = panel
            self._sync_tabs()
            self._log(
                f"[dim]FOCUS-TRACK: panel {old}->{panel}[/dim]"
            )

    # ------------------------------------------------------------------
    # Click-to-focus panel
    # ------------------------------------------------------------------

    def on_click(self, event: Click) -> None:
        """When a display pane is clicked, focus that panel."""
        panel = self._panel_for_widget(event.widget)
        if panel is not None and panel != self._focused_panel:
            self._focus_panel(panel)

    # ------------------------------------------------------------------
    # Key logging
    # ------------------------------------------------------------------

    def on_key(self, event: Key) -> None:
        focused = self.focused
        focused_name = type(focused).__name__ if focused else "None"
        focused_id = getattr(focused, "id", None) or ""
        panel = self._panel_for_widget(focused)
        self._log(
            f"[#b5e4f4]KEY[/#b5e4f4] [bold #00eeee]{event.key!r}[/bold #00eeee]"
            f"  char={event.character!r}"
            f"  focused=[#5c9ff0]{focused_name}#{focused_id}[/#5c9ff0]"
            f"  _focused_panel={self._focused_panel}  actual_panel={panel}"
        )

    # ------------------------------------------------------------------
    # Sync helpers
    # ------------------------------------------------------------------

    def _refresh_panel_count(self) -> None:
        w = self.size.width
        if w >= _TRIPLE_WIDTH:
            new_count = 3
        elif w >= _DUAL_WIDTH:
            new_count = 2
        else:
            new_count = 1
        # Don't show more panels than sub-apps
        new_count = min(new_count, len(self._sub_app_classes))
        self._num_panels = new_count

        for p in range(_MAX_PANELS):
            pane = self.query_one(f"#display-{p + 1}")
            pane.styles.display = "block" if p < new_count else "none"

    async def _place_instances(self) -> None:
        """Show/hide sub-apps and reparent into the correct display pane."""
        panes = [self.query_one(f"#display-{p + 1}", Container) for p in range(_MAX_PANELS)]

        # Build set of visible sub-app indices
        visible: dict[int, int] = {}  # instance_idx -> panel
        for p in range(self._num_panels):
            visible[self._panel_idx[p]] = p

        for i, inst in enumerate(self._instances):
            target_panel = visible.get(i)
            current_panel = self._instance_pane.get(i)

            if target_panel is not None:
                dest = panes[target_panel]
                if current_panel != target_panel:
                    if inst.parent is not None:
                        await inst.remove()
                    await dest.mount(inst)
                    self._instance_pane[i] = target_panel
                    self._log(f"[dim]  moved {self._sub_app_classes[i].TITLE} -> panel{target_panel}[/dim]")
                inst.styles.display = "block"
            else:
                inst.styles.display = "none"

        names = " ".join(
            f"p{p}={self._sub_app_classes[self._panel_idx[p]].TITLE}"
            for p in range(self._num_panels)
        )
        self._log(f"[dim]placed: {names}[/dim]")

    _TAB_SELECTED_CLASSES = [f"--selected-{i}" for i in range(1, _MAX_PANELS + 1)]

    def _sync_tabs(self) -> None:
        for i in range(len(self._sub_app_classes)):
            tab = self.query_one(f"#tab-{i}", Static)
            tab.remove_class(*self._TAB_SELECTED_CLASSES)
            for p in range(self._num_panels):
                if i == self._panel_idx[p]:
                    tab.add_class(f"--selected-{p + 1}")

        # Panel focus borders
        for p in range(_MAX_PANELS):
            pane = self.query_one(f"#display-{p + 1}")
            pane.remove_class("--focused")
        target = self.query_one(f"#display-{self._focused_panel + 1}")
        target.add_class("--focused")

    def _sync_hint(self) -> None:
        bar = self.query_one("#hint-bar", Static)
        parts = ["Alt+Up/Down: switch tab"]
        if self._num_panels > 1:
            parts.append("Alt+Left/Right: switch panel")
        parts.append("Esc: quit")
        bar.update(" | ".join(parts))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def action_tab_prev(self) -> None:
        self._sync_focused_panel()
        self._log(f"[#ffcc00]ACTION[/#ffcc00] tab_prev  panel={self._focused_panel} idx={self._panel_idx[:self._num_panels]}")
        self._clear_quit_pending()
        await self._move_tab(-1)

    async def action_tab_next(self) -> None:
        self._sync_focused_panel()
        self._log(f"[#ffcc00]ACTION[/#ffcc00] tab_next  panel={self._focused_panel} idx={self._panel_idx[:self._num_panels]}")
        self._clear_quit_pending()
        await self._move_tab(1)

    def action_focus_prev_panel(self) -> None:
        self._sync_focused_panel()
        self._log(f"[#ffcc00]ACTION[/#ffcc00] focus_prev_panel  (was panel={self._focused_panel})")
        self._clear_quit_pending()
        new = max(0, self._focused_panel - 1)
        self._focus_panel(new)

    def action_focus_next_panel(self) -> None:
        self._sync_focused_panel()
        self._log(f"[#ffcc00]ACTION[/#ffcc00] focus_next_panel  (was panel={self._focused_panel})")
        self._clear_quit_pending()
        new = min(self._num_panels - 1, self._focused_panel + 1)
        self._focus_panel(new)

    def action_quit_or_esc(self) -> None:
        self._log(f"[#ffcc00]ACTION[/#ffcc00] quit_or_esc")
        self._handle_quit_press()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _focus_panel(self, panel: int) -> None:
        old = self._focused_panel
        self._focused_panel = panel
        idx = self._panel_idx[panel]
        self._force_focus_subapp(self._instances[idx])
        self._sync_tabs()
        self._sync_hint()
        # Log what actually got focus
        actual = self.focused
        actual_name = type(actual).__name__ if actual else "None"
        actual_id = getattr(actual, "id", None) or ""
        actual_panel = self._panel_for_widget(actual)
        self._log(
            f"  -> FOCUS panel {old}->{panel}  sub-app={self._sub_app_classes[idx].TITLE}"
            f"  actual_focus={actual_name}#{actual_id} in panel={actual_panel}"
        )

    def _force_focus_subapp(self, subapp: SubApp) -> None:
        """Force focus onto a widget inside the given sub-app.

        Widget.focus() can silently fail when an Input already has focus.
        We find the target focusable widget and use screen.set_focus() directly.
        """
        target = subapp.get_focus_target()
        if target is not None:
            self.screen.set_focus(target)
        else:
            self._log(f"[dim]WARNING: no focusable widget in {subapp.TITLE}[/dim]")

    async def _move_tab(self, delta: int) -> None:
        n = len(self._sub_app_classes)
        panel = self._focused_panel
        old_idx = self._panel_idx[panel]
        idx = (old_idx + delta) % n

        # Skip indices shown in other panels (a widget can't be in two panes)
        other_indices = {self._panel_idx[p] for p in range(self._num_panels) if p != panel}
        attempts = 0
        while idx in other_indices and attempts < n:
            idx = (idx + delta) % n
            attempts += 1

        self._panel_idx[panel] = idx
        self._log(
            f"  -> MOVE panel={panel} {self._sub_app_classes[old_idx].TITLE}->{self._sub_app_classes[idx].TITLE} "
            f"idx={self._panel_idx[:self._num_panels]}"
        )
        await self._place_instances()
        self._sync_tabs()
        self._force_focus_subapp(self._instances[idx])
        actual = self.focused
        actual_name = type(actual).__name__ if actual else "None"
        actual_id = getattr(actual, "id", None) or ""
        self._log(f"  -> after focus: {actual_name}#{actual_id} in panel={self._panel_for_widget(actual)}")

    def _handle_quit_press(self) -> None:
        now = time.monotonic()
        if now - self._quit_pressed_at < _QUIT_WINDOW:
            self.exit()
            return
        self._quit_pressed_at = now
        bar = self.query_one("#hint-bar", Static)
        bar.update("Press Esc or Ctrl+C again to exit")
        if self._quit_timer is not None:
            self._quit_timer.stop()  # type: ignore[union-attr]
        self._quit_timer = self.set_timer(_QUIT_WINDOW, self._clear_quit_pending)

    def _clear_quit_pending(self) -> None:
        self._quit_pressed_at = 0.0
        if self._quit_timer is not None:
            self._quit_timer.stop()  # type: ignore[union-attr]
            self._quit_timer = None
        if self._initialized:
            self._sync_hint()

    # ------------------------------------------------------------------
    # Prompt hooks (with deduplication)
    # ------------------------------------------------------------------

    # _pending_confirms maps message -> (event, result_list) so that
    # concurrent threads asking the same question share one modal.
    _pending_confirms: dict[str, tuple[threading.Event, list[bool]]] = {}
    _pending_confirms_lock = threading.Lock()

    def _handle_confirm(self, message: str, default: bool) -> bool | None:
        from dimos.utils.cli.dui.confirm_screen import ConfirmScreen

        with self._pending_confirms_lock:
            if message in self._pending_confirms:
                # Another thread is already showing this question — wait for it
                event, result = self._pending_confirms[message]
            else:
                event = threading.Event()
                result: list[bool] = []
                self._pending_confirms[message] = (event, result)

                def _push() -> None:
                    def _on_result(value: bool) -> None:
                        result.append(value)
                        event.set()
                    self.push_screen(ConfirmScreen(message, default), callback=_on_result)

                self.call_from_thread(_push)

        event.wait()

        # First thread to wake up after the modal cleans up the entry
        with self._pending_confirms_lock:
            self._pending_confirms.pop(message, None)

        return result[0] if result else default

    _pending_sudos: dict[str, tuple[threading.Event, list[bool]]] = {}
    _pending_sudos_lock = threading.Lock()

    def _handle_sudo(self, message: str) -> bool | None:
        from dimos.utils.cli.dui.confirm_screen import SudoScreen

        with self._pending_sudos_lock:
            if message in self._pending_sudos:
                event, result = self._pending_sudos[message]
            else:
                event = threading.Event()
                result: list[bool] = []
                self._pending_sudos[message] = (event, result)

                def _push() -> None:
                    def _on_result(value: bool) -> None:
                        result.append(value)
                        event.set()
                    self.push_screen(SudoScreen(message), callback=_on_result)

                self.call_from_thread(_push)

        event.wait()

        with self._pending_sudos_lock:
            self._pending_sudos.pop(message, None)

        return result[0] if result else False


def main() -> None:
    from dimos.utils.prompt import clear_dio_hook, set_dio_hook, set_dio_sudo_hook

    debug = "--debug" in sys.argv
    if debug:
        sys.argv.remove("--debug")

    app = DUIApp(debug=debug)
    set_dio_hook(app._handle_confirm)
    set_dio_sudo_hook(app._handle_sudo)

    _real_stdin = sys.stdin
    sys.stdin = open(os.devnull)  # noqa: SIM115
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        clear_dio_hook()
        sys.stdin.close()
        sys.stdin = _real_stdin
        if app._debug_log_path:
            print(f"Debug log: {app._debug_log_path}")
        if app._debug_log_file:
            try:
                app._debug_log_file.close()  # type: ignore[union-attr]
            except Exception:
                pass
        os._exit(0)


if __name__ == "__main__":
    main()
