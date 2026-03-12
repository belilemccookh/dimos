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
Surveillance Intelligence Skill — MCP-exposed Module.

Reads from the observation store (written by SurveillanceStore in-process)
and answers natural-language queries about people and their activities.

Exposed as `query_surveillance` and `list_people` skills via MCP.
"""

from __future__ import annotations

import json
import os
import time

import anthropic

from dimos.agents.annotation import skill
from dimos.core.module import Module
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

_DATA_DIR = os.path.join("assets", "surveillance")
_OBS_FILE = os.path.join(_DATA_DIR, "observations.jsonl")
_ROSTER_FILE = os.path.join(_DATA_DIR, "roster.json")
_MAX_QUERY_OBS = 200


class SurveillanceSkill(Module):
    """Answers questions about people observed by the surveillance system."""

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self._claude: anthropic.Anthropic | None = None

    def start(self) -> None:
        super().start()
        logger.info("SurveillanceSkill started")

    def _get_claude(self) -> anthropic.Anthropic:
        """Lazy-init Claude client on first use (same pattern as temporal_memory VLM)."""
        if self._claude is None:
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError(
                    "ANTHROPIC_API_KEY environment variable not set. "
                    "Set it in your shell or .env file before launching dimos."
                )
            self._claude = anthropic.Anthropic(api_key=api_key)
            logger.info("SurveillanceSkill: Claude client initialized")
        return self._claude

    def stop(self) -> None:
        super().stop()

    def _load_roster(self) -> dict[str, dict]:
        if not os.path.exists(_ROSTER_FILE):
            return {}
        try:
            with open(_ROSTER_FILE) as f:
                return json.load(f)
        except Exception:
            return {}

    def _load_observations(self, max_lines: int = _MAX_QUERY_OBS) -> list[dict]:
        if not os.path.exists(_OBS_FILE):
            return []
        try:
            with open(_OBS_FILE) as f:
                lines = f.readlines()
            return [json.loads(line.strip()) for line in lines[-max_lines:] if line.strip()]
        except Exception:
            return []

    @skill
    def query_surveillance(self, question: str) -> str:
        """Answer a question about people observed by the surveillance system.

        Use this to ask about who has been seen, what they were doing,
        when they were last active, activity timelines, or any question
        about people in the monitored space.

        Examples:
            query_surveillance("Who is in the office right now?")
            query_surveillance("What has person-1 been doing?")
            query_surveillance("How many people have been seen today?")
            query_surveillance("When was the last time someone was at the desk?")

        Args:
            question: Natural language question about people and their activities.

        Returns:
            Answer based on surveillance observation history.
        """
        roster = self._load_roster()
        observations = self._load_observations()

        if not observations and not roster:
            return "No surveillance data available yet. No people have been observed."

        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        now_ts = time.time()

        roster_text = "## Currently Known People\n"
        for pid, info in roster.items():
            ago = now_ts - info.get("last_seen", 0)
            if ago < 120:
                ago_str = f"{int(ago)}s ago"
            else:
                ago_str = f"{int(ago / 60)}m ago"
            roster_text += (
                f"- {pid} (ID {info.get('long_term_id')}): "
                f'activity="{info.get("activity")}", last seen {ago_str}\n'
            )

        obs_text = "## Activity Log (chronological)\n"
        for obs in observations:
            obs_text += (
                f"[{obs.get('time', '?')}] {obs.get('person_id', '?')}: "
                f"{obs.get('activity', '?')}\n"
            )

        try:
            client = self._get_claude()
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=300,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"You are a surveillance intelligence assistant. "
                            f"Current time: {now_str}\n\n"
                            f"{roster_text}\n{obs_text}\n\n"
                            f"Question: {question}\n\n"
                            f"Answer concisely based on the surveillance data above. "
                            f"If the data doesn't contain enough info, say so."
                        ),
                    }
                ],
            )
            return response.content[0].text.strip()
        except Exception as e:
            logger.warning(f"SurveillanceSkill: query failed: {e}")
            return f"Error querying surveillance data: {e}"

    @skill
    def list_people(self) -> str:
        """List all people currently tracked by the surveillance system.

        Returns a summary of all known people with their current activity
        and when they were last seen.

        Example:
            list_people()

        Returns:
            Summary of all tracked people.
        """
        roster = self._load_roster()
        if not roster:
            return "No people have been observed yet."
        now = time.time()
        lines = []
        for pid, info in roster.items():
            ago = now - info.get("last_seen", 0)
            if ago < 120:
                ago_str = f"{int(ago)}s ago"
            else:
                ago_str = f"{int(ago / 60)}m ago"
            lines.append(
                f"{pid} (ID {info.get('long_term_id')}): "
                f"{info.get('activity', 'unknown')} — last seen {ago_str}"
            )
        return "\n".join(lines)


surveillance_skill = SurveillanceSkill.blueprint

__all__ = ["SurveillanceSkill", "surveillance_skill"]
