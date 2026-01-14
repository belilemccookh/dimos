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

import logging
import time
import json
import numpy as np
from typing import Any
from lerobot.motors import Motor, MotorCalibration, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus, OperatingMode
from ..base.sdk_interface import BaseManipulatorSDK, ManipulatorInfo
from .lerobot_kinematics import LerobotKinematics
from scipy.spatial.transform import Rotation as R

class SO101SDKWrapper(BaseManipulatorSDK):
    
    def __init__(
            self,
            port: str = "/dev/ttyUSB0",
            urdf_path: str = "dimos/hardware/manipulators/so101/urdf/so101_new_calib.urdf",
            ee_link_name: str = "gripper_frame_link",
            calibration_path: str = "dimos/hardware/manipulators/so101/calibration/so101_arm.json",
        ):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.dof = 5  # SO101 is always 5-DOF
        self._connected = False
        self._enabled = False

        self.port = port
        self.urdf_path = urdf_path
        self.ee_link_name = ee_link_name
        self.calibration_path = calibration_path

        self.bus: FeetechMotorsBus | None = None

        # Motor configuration: 5 DOF arm + 1 gripper
        self.motor_names = [
            "shoulder_pan",
            "shoulder_lift",
            "elbow_flex",
            "wrist_flex",
            "wrist_roll",
        ]
        self.gripper_name = "gripper"
        # Joint angle offsets in degrees (motor frame → robot frame)
        # Measured offsets when the arm is mechanically at zero.
        self.joint_offsets_deg = np.array(
            [0, 0.26373626, 2.59340659, 0.65934066, 0.21978022], dtype=float
        )

        self.motor_ids: dict[str, int] = {
            "shoulder_pan": 1,
            "shoulder_lift": 2,
            "elbow_flex": 3,
            "wrist_flex": 4,
            "wrist_roll": 5,
            "gripper": 6,
        }

        # Initialize kinematics
        try:
            self.kinematics = LerobotKinematics(
                self.urdf_path,
                self.ee_link_name,
                joint_names=self.motor_names,
            )
            self.logger.info(
                "Initialized LerobotKinematics with URDF %s, EE link %s",
                self.urdf_path,
                self.ee_link_name,
            )
        except Exception as e:
            self.logger.warning(f"Failed to initialize kinematics: {e}")
            self.kinematics = None
    
    # ============= Connection Management =============

    def _load_calibration(self, calibration_path: str = "") -> dict[str, MotorCalibration]:
        """Load motor calibration from JSON file."""
        try:
            with open(calibration_path) as f:
                calib_data = json.load(f)
            calibration: dict[str, MotorCalibration] = {}
            for name, data in calib_data.items():
                calibration[name] = MotorCalibration(**data)
            return calibration
        except Exception as e:
            self.logger.warning(f"Failed to load calibration from {calibration_path}: {e}")
            return {}

    def configure_motors(self) -> None:
        """
        Configure motors: operating mode + PID.

        - Puts all motors (joints + gripper) into POSITION mode.
        - Sets PID coefficients tuned to reduce shakiness.
        """
        if not self.bus:
            raise RuntimeError("Bus not connected.")

        self.logger.info("Configuring motors (OperatingMode + PID gains)")

        # Disable torque while tweaking settings
        with self.bus.torque_disabled():
            # Let LeRobot configure registers (limits, accel, etc.)
            self.bus.configure_motors()

            for motor in self.bus.motors:
                # Position control for all motors
                self.bus.write("Operating_Mode", motor, OperatingMode.POSITION.value)

                # PID gains – tuned for smoother motion
                self.bus.write("P_Coefficient", motor, 16)
                self.bus.write("I_Coefficient", motor, 0)
                self.bus.write("D_Coefficient", motor, 32)

        self.logger.info("Motor configuration complete")

    def connect(self, config: dict[str, Any]) -> bool:
        """Connect to SO101 via bus.

        Args:
            config: Configuration with 'port' (e.g., 'dev/ttyUSB0') and config path

        Returns:
            True if connection successful
        """
        try:
            port = config.get("port", "/dev/ttyUSB0")
            calibration_path = config.get("calibration_path", "dimos/hardware/so101_utils/calibration/so101_arm.json")
            self.logger.info("Connecting to SO-101 arm on port %s", port)

            norm_mode_body = MotorNormMode.DEGREES
            motors: dict[str, Motor] = {}

            # Arm joints: normalized in degrees
            for name in self.motor_names:
                motors[name] = Motor(self.motor_ids[name], "sts3215", norm_mode_body)

            # Gripper: normalized in [0, 100]
            motors[self.gripper_name] = Motor(
                self.motor_ids[self.gripper_name],
                "sts3215",
                MotorNormMode.RANGE_0_100,
            )

            calibration = self._load_calibration(calibration_path)

            self.bus = FeetechMotorsBus(
                port=port,
                motors=motors,
                calibration=calibration,
            )
            self.bus.connect()
            self.logger.info("FeetechMotorsBus connected")

            # Configure modes, PID, etc.
            self.configure_motors()
            self._connected = True
            return True
        except Exception:
            self.logger.error("Failed to connect to SO-101 arm")
            return False

    def disconnect(self) -> None:
        """Disconnect from the arm."""
        if self.bus:
            self.bus.disconnect()
            self.logger.info("SO-101 bus disconnected")
            self._connected = False

    def is_connected(self) -> bool:
        return self._connected
        
    # ============= Joint State Query =============

    def get_joint_positions(self, degree: bool = False) -> list[float]:
        """
        Get current joint angles with joint offsets applied.

        Returns:
            Joint positions in RADIANS or DEGREES (default: RADIANS)
        """
        if not self.bus:
            return [0.0] * len(self.motor_names)

        values = self.bus.sync_read("Present_Position")
        q_deg_raw = np.array([values[name] for name in self.motor_names], dtype=float)
        q_deg = q_deg_raw - self.joint_offsets_deg
        return q_deg.tolist() if degree else np.radians(q_deg).tolist()

    def get_joint_velocities(self) -> list[float]:
        """Get current joint velocities.

        Returns:
            Joint velocities in RAD/S (0-indexed)
        """
        try:
            if not self.bus:
                return [0.0] * len(self.motor_names)

            values = self.bus.sync_read("Present_Velocity")
            vel_deg = np.array([values[name] for name in self.motor_names], dtype=float)            
            return np.radians(vel_deg).tolist()
        except Exception as e:
            self.logger.debug(f"Error when reading Present_Velocity: {e}")
            return [0.0] * len(self.motor_names)

    def get_joint_efforts(self) -> list[float]:
        """Get current joint efforts/torques.

        Returns:
            Joint efforts in Nm (0-indexed)
        """
        return [0.0] * len(self.motor_names)

    # ============= Joint Motion Control =============
    
    def move_joint_ptp(self, q_target: list[float], velocity: float = 1.0, duration: float | None = None) -> None:
        """
        Joint-space PTP interpolation.

        Args:
            q_target: target joint angles [rad], shape (5,)
            velocity: Max velocity fraction (0-1)
            duration: total motion time (s). If None, derived from velocity.
        """
        if not self.bus:
            raise RuntimeError("Bus not connected.")

        q_target = np.asarray(q_target, dtype=float)
        if q_target.shape[0] != len(self.motor_names):
            raise ValueError(f"Expected {len(self.motor_names)} joints, got {q_target.shape[0]}")

        q_start = np.array(self.get_joint_positions(degree=False))
        dq = q_target - q_start
        max_delta = float(np.max(np.abs(dq)))
        dt = 0.02  # 50 Hz

        if max_delta < 1e-6:
            q_deg = np.degrees(q_target)
            cmd = {name: float(q_deg[i]) for i, name in enumerate(self.motor_names)}
            self.bus.sync_write("Goal_Position", cmd)
            return

        if duration is None:
            # Derive from velocity: 0..1 → 0.2..1.0 rad/s (tunable)
            min_speed = 0.2
            max_speed = 1.0
            joint_speed = min_speed + (max_speed - min_speed) * velocity
            if joint_speed < 1e-3:
                joint_speed = min_speed

            duration = max_delta / joint_speed
            if duration < dt:
                duration = dt

        steps = max(int(duration / dt), 1)

        for i in range(1, steps + 1):
            alpha = i / steps
            q_interp = q_start + alpha * dq
            q_deg = np.degrees(q_interp)
            cmd = {name: float(q_deg[j]) for j, name in enumerate(self.motor_names)}
            self.bus.sync_write("Goal_Position", cmd)
            time.sleep(dt)

    def set_joint_positions(
        self,
        positions: list[float],
        velocity: float = 1.0,
        _acceleration: float = 1.0,
        wait: bool = False,
        use_ptp: bool = True,
    ) -> bool:
        """Move joints to target positions.

        Args:
            positions: Target positions in RADIANS (0-indexed)
            velocity: Max velocity fraction (0-1)
            _acceleration: [UNUSED] Kept for interface compatibility
            wait: If True, block until motion completes
            use_ptp: If True, use PTP interpolation (smoother but blocking).
                     If False, send command directly (fast but requires external trajectory generation).

        Returns:
            True if command accepted
        """

        if not self.bus:
            return False

        if use_ptp:
            self.move_joint_ptp(positions, velocity=velocity)
            result = True
        else:
            q_target = np.asarray(positions, dtype=float)
            q_deg = np.degrees(q_target)
            cmd = {name: float(q_deg[i]) for i, name in enumerate(self.motor_names)}
            self.bus.sync_write("Goal_Position", cmd)
            result = True

        if wait and result:
            start_time = time.time()
            timeout = 30.0  # 30 second timeout

            while time.time() - start_time < timeout:
                try:
                    # Check if reached target (within tolerance)
                    current = self.get_joint_positions()
                    tolerance = 0.05  # radians
                    if all(abs(current[i] - positions[i]) < tolerance for i in range(len(self.motor_names))):
                        break
                except Exception:
                    pass  # Continue waiting
                time.sleep(0.01)

        return result


    def set_joint_velocities(self, velocities: list[float]) -> bool:
        """Set joint velocity targets.

        Note: Lerobot doesn't have native velocity control. The driver should
        implement velocity control via position integration if needed.

        Args:
            velocities: Target velocities in RAD/S (0-indexed)

        Returns:
            False - velocity control not supported at SDK level
        """
        # Lerobot doesn't have native velocity control
        # The driver layer should implement this via position integration
        self.logger.debug("Velocity control not supported at SDK level - use position integration")
        return False

    def set_joint_efforts(self, efforts: list[float]) -> bool:
        """Set joint effort/torque targets.

        Note: Lerobot doesn't have native torque control. The driver should
        implement torque control via position integration if needed.
        Args:
            efforts: Target efforts in Nm (0-indexed)

        Returns:
            False - torque control not supported at SDK level
        """
        self.logger.debug("Torque control not supported at SDK level - use position integration")
        return False

    def stop_motion(self) -> bool:
        """Stop all ongoing motion.

        Returns:
            True if stop successful
        """
        # SO101 emergency stop
        try:
            self.emergency_stop()
        except Exception as e:
            curr_pos = self.get_joint_positions()
            self.set_joint_positions(curr_pos)
        return True

    # ============= Servo Control =============
    def enable_servos(self) -> bool:
        """Enable motor control.

        Returns:
            True if servos enabled
        """
        if self.bus:
            self.bus.enable_torque()
            self._enabled = True
            return True
        else:
            return False

    def disable_servos(self) -> bool:
        """Disable motor control.

        Returns:
            True if servos disabled
        """
        if self.bus:
            self.bus.disable_torque()
            self._enabled = False
            return True
        
    def are_servos_enabled(self) -> bool:
        """Check if servos are enabled.

        Returns:
            True if enabled
        """
        return self._enabled

    # ============= System State =============
    def get_robot_state(self) -> dict[str, Any]:
        """Get current robot state.

        Returns:
            State dictionary
        """
        if not self.bus:
            return {
            "state": 2,  # Error if can't get status
            "mode": 0,
            "error_code": 999,
            "warn_code": 0,
            "is_moving": False,
            "cmd_num": 0,
        }

        # Default state mapping
        state = 0  # idle
        mode = 0  # position mode
        error_code = 0

        error_code = self.get_error_code()
        state = 2 if error_code != 0 else 0
        
        return {
            "state": state,
            "mode": mode,
            "error_code": error_code,
            "warn_code": 0,
            "is_moving": False,
            "cmd_num": 0,
        }

    # Sanjay see if this is possible
    def get_error_code(self) -> int:
        """Get current error code.

        Returns:
            Error code (0 = no error)
        """
        if not self.bus:
            return 0
        
        try:
            err_codes = {k: int(v) for k, v in self.bus.sync_read("Status").items()}
        except Exception:
            self.logger.exception("sync_read(Status) failed")
            return 0
        agg = 0
        for _, code in err_codes.items():
            agg |= int(code)
        return agg

    def _decode_hw_error_bits(self, err_code: int) -> list[str]:
        error_map = {
            0: "Voltage error",
            1: "Sensor/encoder error",
            2: "Overtemperature",
            3: "Overcurrent",
            4: "Angle/position error",
            5: "Overload",
        }

        msgs = []
        for bit, label in error_map.items():
            if err_code & (1 << bit):
                msgs.append(label)
        return msgs

    def get_error_message(self) -> str:
        """Get human-readable error message.

        Returns:
            Error message string
        """
        error_code = self.get_error_code()
        if error_code == 0:
            return ""

        msgs = self._decode_hw_error_bits(error_code)
        if msgs:
            return ", ".join(msgs)
        return f"Unknown error code: {error_code}"

    def clear_errors(self) -> bool:
        """Clear error states.

        Returns:
            True if errors cleared
        """
        self.disable_servos()
        time.sleep(0.1)
        return self.enable_servos()

    def emergency_stop(self) -> bool:
        """Execute emergency stop.

        Returns:
            True if e-stop executed
        """
        return self.disable_servos()

    # ============= Information =============
    def get_info(self) -> ManipulatorInfo:
        """Get manipulator information.

        Returns:
            ManipulatorInfo object
        """
        return ManipulatorInfo(
            vendor="LeRobot",
            model="SO101",
            dof=self.dof,
            firmware_version=None, # Lerobot does not expose firmware version
            serial_number=None,  # Lerobot does not expose serial number
        )

    def get_joint_limits(self) -> tuple[list[float], list[float]]:
        """Get joint position limits.

        Returns:
            Tuple of (lower_limits, upper_limits) in RADIANS
        """
        lower_limits = [-1.919, -1.74, -1.69, -1.65, -2.74]
        upper_limits = [1.919, 1.74, 1.69, 1.65, 2.84]

        return (lower_limits, upper_limits)

    def get_velocity_limits(self) -> list[float]:
        """Get joint velocity limits.

        Returns:
            Maximum velocities in RAD/S
        """
        # SO101 max velocities (approximate)
        max_vel = 2.0  # rad/s
        return [max_vel] * self.dof

    def get_acceleration_limits(self) -> list[float]:
        """Get joint acceleration limits.

        Returns:
            Maximum accelerations in RAD/S²
        """
        # SO101 max accelerations (approximate)
        max_acc = 8.0  # rad/s²
        return [max_acc] * self.dof

    # ============= Optional Methods =============
    def set_gripper_position(self, position: float) -> bool:
        """
        Move gripper to target position.

        Args:
            position: 0.0 (closed) to 0.1 (open) in meters.
        """
        if not self.bus:
            self.logger.warning("Robot not connected")
            return False

        # Map 0–0.1 m → 0–100 normalized range
        val = (position / 0.1) * 100.0
        val = max(0.0, min(100.0, val))
        self.bus.write("Goal_Position", self.gripper_name, val)
        return True

    def get_gripper_position(self) -> float | None:
        """
        Get gripper position.

        Returns:
            Position in meters( 0.0 [closed] to 0.1 [open]) or None
        """
        try:
            if not self.bus:
                return None
            
            raw_pos = float(self.bus.read("Present_Position", self.gripper_name))
            return max(0.0, min(0.1, raw_pos * 0.001))
    
    def get_cartesian_position(self) -> dict[str, float] | None:
        """Get current end-effector pose.

        Returns:
            Pose dict or None if not supported
        """
        if self.kinematics:
            q = self.get_joint_positions(degree=True)
            pos, quat_wxyz = self.kinematics.fk(q)

            # Convert quaternion (w, x, y, z) to (x, y, z, w) for scipy
            quat_xyzw = [quat_wxyz[1], quat_wxyz[2], quat_wxyz[3], quat_wxyz[0]]
            rot = R.from_quat(quat_xyzw)
            roll, pitch, yaw = rot.as_euler("xyz", degrees=False)

            return {
                "x": float(pos[0]),
                "y": float(pos[1]),
                "z": float(pos[2]),
                "roll": float(roll),
                "pitch": float(pitch),
                "yaw": float(yaw),
            }
        return None

    def set_cartesian_position(
        self,
        pose: dict[str, float],
        velocity: float = 1.0,
        acceleration: float = 1.0,
        wait: bool = False,
    ) -> bool:
        """Move end-effector to target pose.

        Args:
            pose: Target pose dict
            velocity: Max velocity fraction (0-1)
            acceleration: Max acceleration fraction (0-1)
            wait: Block until complete

        Returns:
            True if command accepted
        """
        if not self.kinematics:
            self.logger.warning("Cartesian control not available")
            return False

        curr_joint_angles = self.get_joint_positions(degree=True)
        target_pos = np.array(
            [pose["x"], pose["y"], pose["z"]],
            dtype=float,
        )
        
        # Convert RPY to Quaternion (wxyz)
        r = R.from_euler("xyz", [pose["roll"], pose["pitch"], pose["yaw"]], degrees=False)
        quat_xyzw = r.as_quat()
        target_quat_wxyz = np.array(
            [quat_xyzw[3], quat_xyzw[0], quat_xyzw[1], quat_xyzw[2]],
            dtype=float,
        )

        q_target_kin_deg = self.kinematics.ik(curr_joint_angles, target_pos, target_quat_wxyz)
        q_target = np.radians(q_target_kin_deg)
        self.set_joint_positions(q_target)

        # Wait if requested
        if wait:
            start_time = time.time()
            timeout = 30.0

            while time.time() - start_time < timeout:
                current = self.get_cartesian_position()
                if current:
                    # Check if reached target (within tolerance)
                    tol_pos = 0.01  # 5mm
                    tol_rot = 0.1  # ~3 degrees

                    if (
                        abs(current["x"] - pose["x"]) < tol_pos
                        and abs(current["y"] - pose["y"]) < tol_pos
                        and abs(current["z"] - pose["z"]) < tol_pos
                        and abs(current["roll"] - pose["roll"]) < tol_rot
                        and abs(current["pitch"] - pose["pitch"]) < tol_rot
                        and abs(current["yaw"] - pose["yaw"]) < tol_rot
                    ):
                        break

                time.sleep(0.01)

        return True
        