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

"""SO101 driver using the generalized component-based architecture."""

import logging
import time
from typing import Any

from dimos.hardware.manipulators.base import (
    BaseManipulatorDriver,
    StandardMotionComponent,
    StandardServoComponent,
    StandardStatusComponent,
)

from .so101_wrapper import SO101SDKWrapper

logger = logging.getLogger(__name__)

class SO101Driver(BaseManipulatorDriver):
    """SO101 driver using component-based architecture.

    This driver supports the SO101 LeRobot manipulator.
    All the complex logic is handled by the base class and standard components.
    This file just assembles the pieces.
    """

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the SO101 driver.

        Args:
            **kwargs: Arguments for Module initialization.
                Driver configuration can be passed via 'config' keyword arg:
                - port: USB port id (e.g., '/dev/ttyUSB0')
                - calibration_path: Path of SO101 calibration file
                - enable_on_start: Whether to enable servos on start
                - has_gripper: Whether gripper is attached (Should always be True)
        """
        config: dict[str, Any] = kwargs.pop("config", {})

        # Extract driver-specific params that might be passed directly
        driver_params = [
            "port",
            "calibration_path",
            "enable_on_start",
            "has_gripper",
        ]
        for param in driver_params:
            if param in kwargs:
                config[param] = kwargs.pop(param)

        logger.info(f"Initializing SO101Driver with config: {config}")

        # Create SDK wrapper
        sdk = SO101SDKWrapper()

        # Create standard components
        components = [
            StandardMotionComponent(sdk),
            StandardServoComponent(sdk),
            StandardStatusComponent(sdk),
        ]

        # Remove any kwargs that would conflict with explicit arguments
        kwargs.pop("sdk", None)
        kwargs.pop("components", None)
        kwargs.pop("name", None)

        # Initialize base driver with SDK and components
        super().__init__(
            sdk=sdk, components=components, config=config, name="SO101Driver", **kwargs
        )

        # Initialize position target for velocity integration
        self._position_target: list[float] | None = None
        self._last_velocity_time: float = 0.0

        # Enable on start if configured
        if config.get("enable_on_start", False):
            logger.info("Enabling SO101 servos on start...")
            servo_component = self.get_component(StandardServoComponent)
            if servo_component:
                result = servo_component.enable_servo()
                if result["success"]:
                    logger.info("SO101 servos enabled successfully")
                else:
                    logger.warning(f"Failed to enable servos: {result.get('error')}")

    def _process_command(self, command: Any) -> None:
        """Override to implement velocity control via position integration.

        Args:
            command: Command to process
        """
        # Handle velocity commands specially for SO101
        if command.type == "velocity":
            # SO101 doesn't have native velocity control - integrate to position
            current_time = time.time()

            # Initialize position target from current state on first velocity command
            if self._position_target is None:
                positions = self.shared_state.joint_positions
                if positions:
                    self._position_target = list(positions)
                    logger.info(
                        f"Velocity control: Initialized position target from current state: {self._position_target}"
                    )
                else:
                    logger.warning("Cannot start velocity control - no current position available")
                    return

            # Calculate dt since last velocity command
            if self._last_velocity_time > 0:
                dt = current_time - self._last_velocity_time
            else:
                dt = 1.0 / self.control_rate  # Use nominal period for first command

            self._last_velocity_time = current_time

            # Integrate velocity to position: pos += vel * dt
            velocities = command.data["velocities"]
            for i in range(min(len(velocities), len(self._position_target))):
                self._position_target[i] += velocities[i] * dt

            # Send integrated position command using fast path (use_ptp=False)
            success = self.sdk.set_joint_positions(
                self._position_target,
                velocity=1.0,  # Ignored for fast path but good practice
                acceleration=1.0,
                wait=False,
                use_ptp=False,  # Bypass blocking interpolation for high-freq control
            )

            if success:
                self.shared_state.target_positions = self._position_target
                self.shared_state.target_velocities = velocities
        else:
            # Reset velocity integration when switching to position mode
            if command.type == "position":
                self._position_target = None
                self._last_velocity_time = 0.0
            
            # For standard position commands, base class calls set_joint_positions,
            # which defaults to use_ptp=True (safe blocking interpolation).
            super()._process_command(command)