#!/usr/bin/env python3
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

"""ROS topic monitoring module for AGIbot validation."""

from dataclasses import dataclass, field
import time
from typing import Any

from dimos.core import Module, Streamer


@dataclass
class TopicStats:
    """Statistics for a single ROS topic."""

    topic_name: str
    msg_count: int = 0
    last_msg_time: float = 0.0
    first_msg_time: float = 0.0
    msg_times: list[float] = field(default_factory=list)

    @property
    def rate_hz(self) -> float:
        """Compute message rate in Hz."""
        if len(self.msg_times) < 2:
            return 0.0
        time_span = self.msg_times[-1] - self.msg_times[0]
        if time_span == 0:
            return 0.0
        return (len(self.msg_times) - 1) / time_span

    @property
    def latency_ms(self) -> float:
        """Time since last message in ms."""
        if self.last_msg_time == 0:
            return float("inf")
        return (time.time() - self.last_msg_time) * 1000


class ROSTopicMonitor(Module):
    """Monitor ROS topics and report health statistics.

    Tracks message rates, latencies, and data validity for:
    - Lidar (/scan)
    - Camera (/camera/image_raw)
    - Odometry (/odom)
    - Velocity commands (/cmd_vel)
    """

    topics_to_monitor = [
        "/scan",
        "/camera/image_raw",
        "/odom",
        "/cmd_vel",
    ]

    def __init__(self):
        super().__init__()
        self.stats: dict[str, TopicStats] = {}
        self.window_size = 100  # Keep last N timestamps for rate calc

    def start(self):
        """Initialize topic statistics."""
        for topic in self.topics_to_monitor:
            self.stats[topic] = TopicStats(topic_name=topic)
        self.log.info(f"Monitoring topics: {self.topics_to_monitor}")

    def _update_stats(self, topic: str, msg: Any):
        """Update statistics for a topic."""
        if topic not in self.stats:
            return

        stats = self.stats[topic]
        now = time.time()

        stats.msg_count += 1
        stats.last_msg_time = now
        if stats.first_msg_time == 0:
            stats.first_msg_time = now

        stats.msg_times.append(now)
        if len(stats.msg_times) > self.window_size:
            stats.msg_times.pop(0)

    @Streamer.handle("/scan")
    def on_lidar(self, msg):
        """Handle lidar scan messages."""
        self._update_stats("/scan", msg)
        # Validate lidar data
        if hasattr(msg, "ranges"):
            valid_ranges = [r for r in msg.ranges if r > 0 and r < float("inf")]
            if len(valid_ranges) < len(msg.ranges) * 0.5:
                self.log.warning(
                    f"Lidar data quality low: only {len(valid_ranges)}/{len(msg.ranges)} valid ranges"
                )

    @Streamer.handle("/camera/image_raw")
    def on_camera(self, msg):
        """Handle camera image messages."""
        self._update_stats("/camera/image_raw", msg)
        # Validate image data
        if hasattr(msg, "height") and hasattr(msg, "width"):
            if msg.height == 0 or msg.width == 0:
                self.log.warning("Received empty camera frame")

    @Streamer.handle("/odom")
    def on_odom(self, msg):
        """Handle odometry messages."""
        self._update_stats("/odom", msg)

    @Streamer.handle("/cmd_vel")
    def on_cmd_vel(self, msg):
        """Handle velocity command messages."""
        self._update_stats("/cmd_vel", msg)

    def tick(self):
        """Periodic health report."""
        # Report every 5 seconds
        if self.ticks % (5 * self.frequency) != 0:
            return

        self.log.info("=== ROS Topic Health Report ===")
        for topic, stats in self.stats.items():
            rate = stats.rate_hz
            latency = stats.latency_ms

            status = (
                "✅ OK"
                if rate > 1.0 and latency < 1000
                else "⚠️  WARN"
                if rate > 0
                else "❌ NO DATA"
            )

            self.log.info(
                f"{status} {topic:25s} | "
                f"Count: {stats.msg_count:6d} | "
                f"Rate: {rate:6.2f} Hz | "
                f"Latency: {latency:7.1f} ms"
            )
