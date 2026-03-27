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

"""TwitchChat: module that connects to a Twitch channel and publishes
chat-voted robot commands as Twist messages on ``cmd_vel``.

Viewers type keywords in chat (e.g. ``forward``, ``left``).  Votes are
collected over a configurable window, and the winning command is converted
to a Twist and published.

Supports multiple voting mechanisms via ``vote_mode``:

- **plurality**: most votes wins (classic "Twitch Plays" style)
- **majority**: winner must have >50% of votes, else no action
- **weighted_recent**: votes are time-weighted — later votes in the window
  count more (rewards reaction speed)
- **runoff**: if no majority, top-2 enter an instant runoff using voters'
  most recent vote as preference

See ``examples/twitch_plays/`` for usage.
"""

from __future__ import annotations

from collections import Counter, deque
from enum import Enum
import threading
import time
from typing import Any

from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import Out
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class VoteMode(str, Enum):
    """Voting mechanism for aggregating chat commands."""

    PLURALITY = "plurality"
    """Most votes wins. Ties broken by first occurrence."""

    MAJORITY = "majority"
    """Winner must have >50%% of total votes. No action if no majority."""

    WEIGHTED_RECENT = "weighted_recent"
    """Votes are time-weighted: later votes count more. Encourages fast reactions."""

    RUNOFF = "runoff"
    """If no majority, top-2 enter instant runoff using each voter's latest vote."""


class TwitchChatConfig(ModuleConfig):
    """Configuration for the TwitchChat module."""

    twitch_token: str = ""
    """OAuth token for the Twitch bot (oauth:xxx). Set via DIMOS_TWITCH_TOKEN env var."""

    channel_name: str = ""
    """Twitch channel to join. Set via DIMOS_CHANNEL_NAME env var."""

    bot_prefix: str = "!"
    """Chat command prefix (e.g. !forward)."""

    commands: list[str] = ["forward", "back", "left", "right", "stop"]
    """Valid vote keywords."""

    vote_window_seconds: float = 5.0
    """Duration of each voting window in seconds."""

    min_votes_threshold: int = 1
    """Minimum total votes required to trigger an action."""

    vote_mode: VoteMode = VoteMode.PLURALITY
    """Voting aggregation mechanism."""

    linear_speed: float = 0.3
    """Forward/backward speed in m/s for movement commands."""

    angular_speed: float = 0.5
    """Turning speed in rad/s for left/right commands."""

    command_duration: float = 1.0
    """How long each winning command is published (seconds)."""


# ── Command → Twist mapping ──

_COMMAND_MAP: dict[str, tuple[float, float, float, float, float, float]] = {
    # (linear.x, linear.y, linear.z, angular.x, angular.y, angular.z)
    "forward": (1.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    "back": (-1.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    "left": (0.0, 0.0, 0.0, 0.0, 0.0, 1.0),
    "right": (0.0, 0.0, 0.0, 0.0, 0.0, -1.0),
    "stop": (0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
}


def _command_to_twist(command: str, linear_speed: float, angular_speed: float) -> Twist:
    """Convert a command string to a Twist message."""
    scales = _COMMAND_MAP.get(command, (0.0, 0.0, 0.0, 0.0, 0.0, 0.0))
    t = Twist()
    t.linear.x = scales[0] * linear_speed
    t.linear.y = scales[1] * linear_speed
    t.linear.z = scales[2] * linear_speed
    t.angular.x = scales[3] * angular_speed
    t.angular.y = scales[4] * angular_speed
    t.angular.z = scales[5] * angular_speed
    return t


# ── Vote tallying ──


def _tally_plurality(votes: list[tuple[str, float, str]]) -> str | None:
    counts = Counter(cmd for cmd, _, _ in votes)
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def _tally_majority(votes: list[tuple[str, float, str]]) -> str | None:
    counts = Counter(cmd for cmd, _, _ in votes)
    total = sum(counts.values())
    if total == 0:
        return None
    winner, count = counts.most_common(1)[0]
    return winner if count > total / 2 else None


def _tally_weighted_recent(
    votes: list[tuple[str, float, str]], window_start: float, window_end: float
) -> str | None:
    """Time-weighted: votes later in the window count more."""
    if not votes:
        return None
    duration = max(window_end - window_start, 0.001)
    weighted: Counter[str] = Counter()
    for cmd, ts, _ in votes:
        weight = 0.5 + 0.5 * ((ts - window_start) / duration)  # 0.5 → 1.0
        weighted[cmd] += int(weight * 1000)
    return weighted.most_common(1)[0][0] if weighted else None


def _tally_runoff(votes: list[tuple[str, float, str]]) -> str | None:
    """Instant runoff: if no majority, keep top-2 and re-tally using each voter's latest vote."""
    counts = Counter(cmd for cmd, _, _ in votes)
    total = sum(counts.values())
    if total == 0:
        return None
    winner, count = counts.most_common(1)[0]
    if count > total / 2:
        return winner

    # Top 2
    top2 = {cmd for cmd, _ in counts.most_common(2)}
    if len(top2) < 2:
        return winner

    # Re-tally: for each voter, use their latest vote if it's in top2
    latest: dict[str, str] = {}  # voter → latest command
    for cmd, _, voter in votes:
        latest[voter] = cmd

    runoff_counts: Counter[str] = Counter()
    for _voter, cmd in latest.items():
        if cmd in top2:
            runoff_counts[cmd] += 1

    return runoff_counts.most_common(1)[0][0] if runoff_counts else winner


class TwitchChat(Module["TwitchChatConfig"]):
    """Twitch chat → robot cmd_vel via vote aggregation.

    Connects to a Twitch channel, collects keyword votes from chat,
    and publishes the winning command as a Twist on ``cmd_vel``.
    """

    default_config = TwitchChatConfig

    cmd_vel: Out[Twist]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._votes: deque[tuple[str, float, str]] = deque()  # (command, timestamp, voter)
        self._running = threading.Event()
        self._vote_thread: threading.Thread | None = None
        self._bot_thread: threading.Thread | None = None
        self._bot: Any = None

    @rpc
    def start(self) -> None:
        super().start()
        self._running.set()

        # Start vote processing loop
        self._vote_thread = threading.Thread(
            target=self._vote_loop, daemon=True, name="twitch-vote"
        )
        self._vote_thread.start()

        # Start Twitch bot in a separate thread (asyncio)
        if self.config.twitch_token and self.config.channel_name:
            self._bot_thread = threading.Thread(
                target=self._run_bot, daemon=True, name="twitch-bot"
            )
            self._bot_thread.start()
            logger.info(
                "[TwitchChat] Started",
                channel=self.config.channel_name,
                vote_mode=self.config.vote_mode.value,
                window=self.config.vote_window_seconds,
            )
        else:
            logger.warning("[TwitchChat] No token/channel configured — running in local-only mode")

    @rpc
    def stop(self) -> None:
        self._running.clear()
        if self._vote_thread is not None:
            self._vote_thread.join(timeout=2)
            self._vote_thread = None
        if self._bot_thread is not None:
            self._bot_thread.join(timeout=2)
            self._bot_thread = None
        super().stop()

    def record_vote(self, command: str, voter: str = "anonymous") -> None:
        """Record a vote (called from bot callback or test code)."""
        cmd = command.lower().strip()
        if cmd in set(self.config.commands):
            self._votes.append((cmd, time.time(), voter))

    def _run_bot(self) -> None:
        """Run the TwitchIO bot in its own asyncio loop."""
        try:
            from twitchio.ext import (  # type: ignore[import-not-found]
                commands as twitch_commands,  # type: ignore[import-untyped]
            )

            module_ref = self

            class _Bot(twitch_commands.Bot):  # type: ignore[misc]
                def __init__(inner_self) -> None:  # noqa: N805
                    super().__init__(
                        token=module_ref.config.twitch_token,
                        prefix=module_ref.config.bot_prefix,
                        initial_channels=[module_ref.config.channel_name],
                    )

                async def event_ready(inner_self) -> None:  # noqa: N805
                    logger.info("[TwitchChat] Bot connected as %s", inner_self.nick)

                async def event_message(inner_self, message: Any) -> None:  # noqa: N805
                    if message.echo:
                        return
                    content = message.content.strip()
                    if content.startswith(module_ref.config.bot_prefix):
                        cmd = content[len(module_ref.config.bot_prefix) :].strip().lower()
                        module_ref.record_vote(cmd, voter=message.author.name)

            self._bot = _Bot()
            self._bot.run()
        except Exception:
            logger.exception("[TwitchChat] Bot crashed")

    def _vote_loop(self) -> None:
        """Periodically tally votes and publish winning command."""
        while self._running.is_set():
            window_start = time.time()
            time.sleep(self.config.vote_window_seconds)
            window_end = time.time()

            if not self._running.is_set():
                break

            # Collect votes within window
            cutoff = window_end - self.config.vote_window_seconds
            current_votes = [(cmd, ts, voter) for cmd, ts, voter in self._votes if ts >= cutoff]
            self._votes.clear()

            if len(current_votes) < self.config.min_votes_threshold:
                continue

            # Tally based on vote mode
            winner = self._tally(current_votes, window_start, window_end)
            if winner is None:
                continue

            # Publish winning command as Twist
            twist = _command_to_twist(winner, self.config.linear_speed, self.config.angular_speed)

            logger.info(
                "[TwitchChat] Winner: %s (%d votes)",
                winner,
                len(current_votes),
            )

            # Publish for command_duration
            end_time = time.time() + self.config.command_duration
            while time.time() < end_time and self._running.is_set():
                self.cmd_vel.publish(twist)
                time.sleep(0.1)

            # Publish stop after command
            self.cmd_vel.publish(_command_to_twist("stop", 0.0, 0.0))

            pass  # Future: publish status on a separate stream

    def _tally(
        self,
        votes: list[tuple[str, float, str]],
        window_start: float,
        window_end: float,
    ) -> str | None:
        mode = self.config.vote_mode
        if mode == VoteMode.PLURALITY:
            return _tally_plurality(votes)
        elif mode == VoteMode.MAJORITY:
            return _tally_majority(votes)
        elif mode == VoteMode.WEIGHTED_RECENT:
            return _tally_weighted_recent(votes, window_start, window_end)
        elif mode == VoteMode.RUNOFF:
            return _tally_runoff(votes)
        return _tally_plurality(votes)


twitch_chat = TwitchChat.blueprint
