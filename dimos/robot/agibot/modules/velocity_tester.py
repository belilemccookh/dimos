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

"""Velocity command testing module for AGIbot."""

from dataclasses import dataclass
from enum import Enum

from dimos.core import Module, Streamer


class VelocityTestPhase(Enum):
    """Test sequence phases."""

    IDLE = "idle"
    FORWARD = "forward"
    BACKWARD = "backward"
    ROTATE_LEFT = "rotate_left"
    ROTATE_RIGHT = "rotate_right"
    STOP = "stop"
    COMPLETE = "complete"


@dataclass
class VelocityCommand:
    """Simple velocity command."""

    linear_x: float = 0.0
    linear_y: float = 0.0
    angular_z: float = 0.0


class VelocityTester(Module):
    """Test velocity commands and validate odometry feedback.

    Runs a simple test sequence:
    1. Forward motion (0.2 m/s for 2s)
    2. Backward motion (-0.2 m/s for 2s)
    3. Rotate left (0.5 rad/s for 2s)
    4. Rotate right (-0.5 rad/s for 2s)
    5. Stop

    Validates that odometry responds to commands.
    """

    test_enabled = False  # Set to True to run auto-test
    test_duration_ticks = 120  # 2 seconds at 60Hz

    def __init__(self):
        super().__init__()
        self.phase = VelocityTestPhase.IDLE
        self.phase_start_tick = 0
        self.last_odom = None
        self.odom_start = None

    def start(self):
        """Initialize tester."""
        if self.test_enabled:
            self.log.info("Velocity tester enabled - will run test sequence")
        else:
            self.log.info("Velocity tester disabled (set test_enabled=True to run)")

    @Streamer.handle("/odom")
    def on_odom(self, msg):
        """Store odometry for validation."""
        self.last_odom = msg
        if self.odom_start is None:
            self.odom_start = msg

    def _advance_phase(self):
        """Move to next test phase."""
        phases = list(VelocityTestPhase)
        current_idx = phases.index(self.phase)
        if current_idx < len(phases) - 1:
            self.phase = phases[current_idx + 1]
            self.phase_start_tick = self.ticks
            self.log.info(f"🧪 Test phase: {self.phase.value}")

            # Capture odometry at phase start
            if self.last_odom:
                self.odom_start = self.last_odom

    def _get_command_for_phase(self) -> VelocityCommand:
        """Get velocity command for current phase."""
        if self.phase == VelocityTestPhase.FORWARD:
            return VelocityCommand(linear_x=0.2)
        elif self.phase == VelocityTestPhase.BACKWARD:
            return VelocityCommand(linear_x=-0.2)
        elif self.phase == VelocityTestPhase.ROTATE_LEFT:
            return VelocityCommand(angular_z=0.5)
        elif self.phase == VelocityTestPhase.ROTATE_RIGHT:
            return VelocityCommand(angular_z=-0.5)
        else:
            return VelocityCommand()

    def _validate_odom_response(self):
        """Check if odometry changed as expected."""
        if not self.odom_start or not self.last_odom:
            return

        # Simple validation: check if position/orientation changed
        if hasattr(self.last_odom, "pose") and hasattr(self.last_odom.pose, "pose"):
            start_pos = self.odom_start.pose.pose.position
            current_pos = self.last_odom.pose.pose.position

            dx = current_pos.x - start_pos.x
            dy = current_pos.y - start_pos.y
            distance = (dx**2 + dy**2) ** 0.5

            if self.phase in [VelocityTestPhase.FORWARD, VelocityTestPhase.BACKWARD]:
                if distance > 0.1:  # Moved at least 10cm
                    self.log.info(f"✅ Odometry response OK: moved {distance:.3f}m")
                else:
                    self.log.warning(f"⚠️  Odometry response weak: only {distance:.3f}m")

    def tick(self):
        """Run test sequence."""
        if not self.test_enabled:
            return

        # Check if current phase is complete
        ticks_in_phase = self.ticks - self.phase_start_tick

        if ticks_in_phase >= self.test_duration_ticks:
            self._validate_odom_response()
            self._advance_phase()

        # Publish command for current phase
        cmd = self._get_command_for_phase()

        # Create ROS Twist message
        # Note: In real implementation, this would use proper ROS message types
        # For now, we publish a dict that will be converted
        self.stream(
            "/cmd_vel",
            {
                "linear": {"x": cmd.linear_x, "y": cmd.linear_y, "z": 0.0},
                "angular": {"x": 0.0, "y": 0.0, "z": cmd.angular_z},
            },
        )

        # Log phase transitions
        if ticks_in_phase == 0:
            self.log.info(f"Publishing {self.phase.value}: {cmd}")
