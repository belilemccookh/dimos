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

"""
Tests for the Skills Feed feature — skill_invocation SocketIO events,
duration tracking, and dashboard rendering.

Tests cover:
1. skill_invocation event emission from _on_agent_message (LCM path)
2. skill_invocation event emission from /api/chat (HTTP path)
3. Duration tracking (pending tool call matching)
4. Event payload shapes
5. Dashboard HTML has skills feed panel + JS handler

Run with:
    uv run pytest hackathon/tests/test_skills_feed.py -v
"""

from pathlib import Path
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def templates_dir() -> Path:
    return Path(__file__).parents[2] / "dimos" / "web" / "templates"


@pytest.fixture()
def mission_control_html(templates_dir: Path) -> str:
    return (templates_dir / "mission_control.html").read_text()


@pytest.fixture()
def mock_vis_module() -> MagicMock:
    """A mock WebsocketVisModule with just enough to test _on_agent_message."""
    mod = MagicMock()
    mod._emit = MagicMock()
    # Real dict for pending tool call tracking (MagicMock would intercept dict ops)
    mod._pending_tool_calls = {}
    # Bind the real method
    from dimos.web.websocket_vis.websocket_vis_module import WebsocketVisModule

    mod._on_agent_message = WebsocketVisModule._on_agent_message.__get__(mod)
    return mod


# ---------------------------------------------------------------------------
# 1. skill_invocation from _on_agent_message — AIMessage with tool_calls
# ---------------------------------------------------------------------------


class TestSkillInvocationFromAgentMonitor:
    """Test that _on_agent_message emits skill_invocation for tool calls."""

    def _make_entry(self, message: Any) -> Any:
        from dimos.utils.cli.agentspy.agentspy import MessageEntry

        return MessageEntry(timestamp=time.time(), message=message)

    def test_ai_message_with_tool_calls_emits_running(self, mock_vis_module: MagicMock) -> None:
        from langchain_core.messages import AIMessage

        msg = AIMessage(
            content="",
            tool_calls=[
                {"name": "navigate", "args": {"x": 1.0}, "id": "call_123", "type": "tool_call"}
            ],
        )
        entry = self._make_entry(msg)
        mock_vis_module._on_agent_message(entry)

        # Should emit both agent_message and skill_invocation
        calls = mock_vis_module._emit.call_args_list
        event_names = [c[0][0] for c in calls]
        assert "agent_message" in event_names
        assert "skill_invocation" in event_names

        # Check skill_invocation payload
        skill_call = next(c for c in calls if c[0][0] == "skill_invocation")
        payload = skill_call[0][1]
        assert payload["id"] == "call_123"
        assert payload["name"] == "navigate"
        assert payload["status"] == "running"
        assert payload["args"] == {"x": 1.0}

    def test_tool_message_emits_success(self, mock_vis_module: MagicMock) -> None:
        from langchain_core.messages import AIMessage, ToolMessage

        # First send AIMessage with tool call to populate pending
        ai_msg = AIMessage(
            content="",
            tool_calls=[
                {"name": "navigate", "args": {"x": 1.0}, "id": "call_456", "type": "tool_call"}
            ],
        )
        mock_vis_module._on_agent_message(self._make_entry(ai_msg))
        mock_vis_module._emit.reset_mock()

        # Now send ToolMessage response
        tool_msg = ToolMessage(content="arrived", tool_call_id="call_456", name="navigate")
        mock_vis_module._on_agent_message(self._make_entry(tool_msg))

        calls = mock_vis_module._emit.call_args_list
        skill_call = next(c for c in calls if c[0][0] == "skill_invocation")
        payload = skill_call[0][1]
        assert payload["id"] == "call_456"
        assert payload["name"] == "navigate"
        assert payload["status"] == "success"
        assert "arrived" in payload["result"]

    def test_tool_message_includes_duration(self, mock_vis_module: MagicMock) -> None:
        from langchain_core.messages import AIMessage, ToolMessage

        ai_msg = AIMessage(
            content="",
            tool_calls=[{"name": "slow_skill", "args": {}, "id": "call_789", "type": "tool_call"}],
        )
        mock_vis_module._on_agent_message(self._make_entry(ai_msg))

        # Simulate small delay
        time.sleep(0.05)

        tool_msg = ToolMessage(content="done", tool_call_id="call_789", name="slow_skill")
        mock_vis_module._on_agent_message(self._make_entry(tool_msg))

        calls = mock_vis_module._emit.call_args_list
        skill_call = next(
            c for c in calls if c[0][0] == "skill_invocation" and c[0][1].get("status") != "running"
        )
        payload = skill_call[0][1]
        assert payload["duration_ms"] is not None
        assert payload["duration_ms"] >= 40  # at least ~50ms

    def test_tool_message_error_status(self, mock_vis_module: MagicMock) -> None:
        from langchain_core.messages import AIMessage, ToolMessage

        ai_msg = AIMessage(
            content="",
            tool_calls=[
                {"name": "failing_skill", "args": {}, "id": "call_err", "type": "tool_call"}
            ],
        )
        mock_vis_module._on_agent_message(self._make_entry(ai_msg))
        mock_vis_module._emit.reset_mock()

        tool_msg = ToolMessage(
            content="error: timeout", tool_call_id="call_err", name="failing_skill", status="error"
        )
        mock_vis_module._on_agent_message(self._make_entry(tool_msg))

        calls = mock_vis_module._emit.call_args_list
        skill_call = next(c for c in calls if c[0][0] == "skill_invocation")
        payload = skill_call[0][1]
        assert payload["status"] == "error"

    def test_regular_ai_message_does_not_emit_skill(self, mock_vis_module: MagicMock) -> None:
        from langchain_core.messages import AIMessage

        msg = AIMessage(content="Just thinking out loud")
        mock_vis_module._on_agent_message(self._make_entry(msg))

        calls = mock_vis_module._emit.call_args_list
        event_names = [c[0][0] for c in calls]
        assert "skill_invocation" not in event_names

    def test_human_message_does_not_emit_skill(self, mock_vis_module: MagicMock) -> None:
        from langchain_core.messages import HumanMessage

        msg = HumanMessage(content="Go to kitchen")
        mock_vis_module._on_agent_message(self._make_entry(msg))

        calls = mock_vis_module._emit.call_args_list
        event_names = [c[0][0] for c in calls]
        assert "skill_invocation" not in event_names

    def test_multiple_tool_calls_in_one_message(self, mock_vis_module: MagicMock) -> None:
        from langchain_core.messages import AIMessage

        msg = AIMessage(
            content="",
            tool_calls=[
                {"name": "navigate", "args": {"x": 1}, "id": "c1", "type": "tool_call"},
                {"name": "detect", "args": {}, "id": "c2", "type": "tool_call"},
            ],
        )
        mock_vis_module._on_agent_message(self._make_entry(msg))

        calls = mock_vis_module._emit.call_args_list
        skill_calls = [c for c in calls if c[0][0] == "skill_invocation"]
        assert len(skill_calls) == 2
        names = {c[0][1]["name"] for c in skill_calls}
        assert names == {"navigate", "detect"}

    def test_orphan_tool_message_still_emits(self, mock_vis_module: MagicMock) -> None:
        """ToolMessage without a prior AIMessage should still emit (graceful)."""
        from langchain_core.messages import ToolMessage

        tool_msg = ToolMessage(content="result", tool_call_id="orphan_1", name="some_skill")
        mock_vis_module._on_agent_message(self._make_entry(tool_msg))

        calls = mock_vis_module._emit.call_args_list
        skill_call = next(c for c in calls if c[0][0] == "skill_invocation")
        payload = skill_call[0][1]
        assert payload["name"] == "some_skill"
        assert payload["status"] == "success"
        assert payload["duration_ms"] is None  # no start time


# ---------------------------------------------------------------------------
# 2. skill_invocation event payload shape
# ---------------------------------------------------------------------------


class TestSkillInvocationPayloadShape:
    """Validate the required fields in skill_invocation events."""

    REQUIRED_FIELDS_RUNNING = {"id", "name", "args", "status", "timestamp", "timestamp_str"}
    REQUIRED_FIELDS_COMPLETE = REQUIRED_FIELDS_RUNNING | {"result", "duration_ms"}

    def _make_running_payload(self) -> dict[str, Any]:
        return {
            "id": "call_1",
            "name": "navigate",
            "args": {"x": 1.0},
            "status": "running",
            "timestamp": time.time(),
            "timestamp_str": "12:34:56",
        }

    def _make_success_payload(self) -> dict[str, Any]:
        return {
            "id": "call_1",
            "name": "navigate",
            "args": {"x": 1.0},
            "status": "success",
            "result": "arrived at target",
            "duration_ms": 1234,
            "timestamp": time.time(),
            "timestamp_str": "12:34:57",
        }

    def test_running_payload_has_required_fields(self) -> None:
        payload = self._make_running_payload()
        for field in self.REQUIRED_FIELDS_RUNNING:
            assert field in payload, f"Missing field: {field}"

    def test_success_payload_has_required_fields(self) -> None:
        payload = self._make_success_payload()
        for field in self.REQUIRED_FIELDS_COMPLETE:
            assert field in payload, f"Missing field: {field}"

    def test_status_values(self) -> None:
        assert self._make_running_payload()["status"] == "running"
        assert self._make_success_payload()["status"] == "success"

    def test_duration_is_numeric_or_none(self) -> None:
        p = self._make_success_payload()
        assert isinstance(p["duration_ms"], (int, float, type(None)))

    def test_args_is_dict(self) -> None:
        p = self._make_running_payload()
        assert isinstance(p["args"], dict)

    def test_timestamp_is_positive(self) -> None:
        p = self._make_running_payload()
        assert p["timestamp"] > 0

    def test_timestamp_str_has_colons(self) -> None:
        p = self._make_running_payload()
        assert ":" in p["timestamp_str"]


# ---------------------------------------------------------------------------
# 3. Dashboard HTML — skills feed panel
# ---------------------------------------------------------------------------


class TestDashboardSkillsFeed:
    """Verify the mission_control.html has the skills feed panel and JS."""

    def test_has_skills_feed_panel(self, mission_control_html: str) -> None:
        assert "skills-feed" in mission_control_html

    def test_has_skills_feed_label(self, mission_control_html: str) -> None:
        assert "Skills Feed" in mission_control_html

    def test_subscribes_to_skill_invocation_event(self, mission_control_html: str) -> None:
        assert "skill_invocation" in mission_control_html

    def test_has_skill_entry_css(self, mission_control_html: str) -> None:
        assert ".skill-entry" in mission_control_html

    def test_has_skill_status_classes(self, mission_control_html: str) -> None:
        assert ".skill-status.running" in mission_control_html
        assert ".skill-status.success" in mission_control_html
        assert ".skill-status.error" in mission_control_html

    def test_has_duration_formatter(self, mission_control_html: str) -> None:
        assert "fmtDuration" in mission_control_html

    def test_has_pending_skills_tracker(self, mission_control_html: str) -> None:
        assert "pendingSkills" in mission_control_html

    def test_has_skill_name_display(self, mission_control_html: str) -> None:
        assert "skill-name" in mission_control_html

    def test_has_skill_duration_display(self, mission_control_html: str) -> None:
        assert "skill-dur" in mission_control_html

    def test_has_skill_result_display(self, mission_control_html: str) -> None:
        assert "skill-result" in mission_control_html


# ---------------------------------------------------------------------------
# 4. Dashboard HTML — system monitor panel
# ---------------------------------------------------------------------------


class TestDashboardCommandCenter:
    """Verify the mission_control.html has the Command Center panel."""

    def test_has_command_center_label(self, mission_control_html: str) -> None:
        assert "Command Center" in mission_control_html

    def test_has_command_center_iframe(self, mission_control_html: str) -> None:
        assert "/command-center" in mission_control_html

    def test_lcm_and_cmdcenter_in_split(self, mission_control_html: str) -> None:
        """LCM and Command Center should be side-by-side in bottom-left."""
        assert "bl-split" in mission_control_html
        assert "bl-lcm" in mission_control_html
        assert "bl-cmdcenter" in mission_control_html


# ---------------------------------------------------------------------------
# 5. Dashboard HTML — general structure
# ---------------------------------------------------------------------------


class TestDashboardStructure:
    """General dashboard integrity checks."""

    def test_has_socketio_import(self, mission_control_html: str) -> None:
        assert "socket.io" in mission_control_html

    def test_has_skills_feed_in_grid(self, mission_control_html: str) -> None:
        assert "skills-feed" in mission_control_html

    def test_has_lcm_stats(self, mission_control_html: str) -> None:
        assert "lcm_stats" in mission_control_html

    def test_has_claude_chat(self, mission_control_html: str) -> None:
        assert "claude-history" in mission_control_html

    def test_has_command_center(self, mission_control_html: str) -> None:
        """Command Center in bottom-left split."""
        assert "bl-cmdcenter" in mission_control_html

    def test_has_rerun_panel(self, mission_control_html: str) -> None:
        assert "Rerun" in mission_control_html

    def test_has_people_intelligence_panel(self, mission_control_html: str) -> None:
        """People Intelligence panel should exist in col3."""
        assert "p-people" in mission_control_html
        assert "People Intelligence" in mission_control_html
        assert "people-wrap" in mission_control_html
        assert "person_sighting" in mission_control_html

    def test_has_person_card_css(self, mission_control_html: str) -> None:
        """Person card CSS should exist."""
        assert ".person-card" in mission_control_html
        assert ".person-id" in mission_control_html
        assert ".person-activity" in mission_control_html

    def test_has_connection_status(self, mission_control_html: str) -> None:
        assert "conn-dot" in mission_control_html

    def test_has_clock(self, mission_control_html: str) -> None:
        assert "clock" in mission_control_html

    def test_mcp_tools_list_panel(self, mission_control_html: str) -> None:
        """MCP tools list panel should be present."""
        assert "mcp-wrap" in mission_control_html
        assert "fetchMcp" in mission_control_html
        assert "skill-count" in mission_control_html
