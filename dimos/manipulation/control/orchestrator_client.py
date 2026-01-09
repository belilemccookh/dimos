#!/usr/bin/env python3
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

"""
Interactive client for the ControlOrchestrator.

Interfaces with a running ControlOrchestrator via RPC to:
- Query hardware and task status
- Plan and execute trajectories on single or multiple arms
- Monitor execution progress

Usage:
    # Terminal 1: Start the orchestrator
    dimos run orchestrator-mock          # Single arm
    dimos run orchestrator-dual-mock     # Dual arm

    # Terminal 2: Run this client
    python -m dimos.manipulation.control.orchestrator_client
    python -m dimos.manipulation.control.orchestrator_client --task traj_left
    python -m dimos.manipulation.control.orchestrator_client --task traj_right

How it works:
    1. Connects to ControlOrchestrator via LCM RPC
    2. Queries available hardware/tasks/joints
    3. You add waypoints (joint positions)
    4. Generates trajectory with trapezoidal velocity profile
    5. Sends trajectory to orchestrator via execute_trajectory() RPC
    6. Orchestrator's tick loop executes it at 100Hz
"""

from __future__ import annotations

import math
import sys
import time
from typing import TYPE_CHECKING, Any

from dimos.control.orchestrator import ControlOrchestrator
from dimos.core.rpc_client import RPCClient
from dimos.manipulation.planning import JointTrajectoryGenerator

if TYPE_CHECKING:
    from dimos.msgs.trajectory_msgs import JointTrajectory


class OrchestratorClient:
    """
    RPC client for the ControlOrchestrator.

    Connects to a running orchestrator and provides methods to:
    - Query state (joints, tasks, hardware)
    - Execute trajectories on any task
    - Monitor progress

    Example:
        client = OrchestratorClient()

        # Query state
        print(client.list_hardware())  # ['left_arm', 'right_arm']
        print(client.list_tasks())     # ['traj_left', 'traj_right']

        # Setup for a task
        client.select_task("traj_left")

        # Get current position and create trajectory
        current = client.get_current_positions()
        target = [0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        trajectory = client.generate_trajectory([current, target])

        # Execute
        client.execute_trajectory("traj_left", trajectory)
    """

    def __init__(self) -> None:
        """Initialize connection to orchestrator via RPC."""
        self._rpc = RPCClient(None, ControlOrchestrator)

        # Per-task state
        self._current_task: str | None = None
        self._task_joints: dict[str, list[str]] = {}  # task_name -> joint_names
        self._generators: dict[str, JointTrajectoryGenerator] = {}  # task_name -> generator

    def stop(self) -> None:
        """Stop the RPC client."""
        self._rpc.stop_rpc_client()

    # =========================================================================
    # Query methods (RPC calls)
    # =========================================================================

    def list_hardware(self) -> list[str]:
        """List all hardware IDs."""
        return self._rpc.list_hardware() or []

    def list_joints(self) -> list[str]:
        """List all joint names across all hardware."""
        return self._rpc.list_joints() or []

    def list_tasks(self) -> list[str]:
        """List all task names."""
        return self._rpc.list_tasks() or []

    def get_active_tasks(self) -> list[str]:
        """Get currently active task names."""
        return self._rpc.get_active_tasks() or []

    def get_joint_positions(self) -> dict[str, float]:
        """Get current joint positions for all joints."""
        return self._rpc.get_joint_positions() or {}

    def get_trajectory_status(self, task_name: str) -> dict[str, Any]:
        """Get status of a trajectory task."""
        return self._rpc.get_trajectory_status(task_name) or {}

    # =========================================================================
    # Trajectory execution (RPC calls)
    # =========================================================================

    def execute_trajectory(self, task_name: str, trajectory: JointTrajectory) -> bool:
        """Execute a trajectory on a task."""
        return self._rpc.execute_trajectory(task_name, trajectory) or False

    def cancel_trajectory(self, task_name: str) -> bool:
        """Cancel an active trajectory."""
        return self._rpc.cancel_trajectory(task_name) or False

    # =========================================================================
    # Task selection and setup
    # =========================================================================

    def select_task(self, task_name: str) -> bool:
        """
        Select a task and setup its trajectory generator.

        This queries the orchestrator to find which joints the task controls,
        then creates a trajectory generator for those joints.
        """
        tasks = self.list_tasks()
        if task_name not in tasks:
            print(f"Task '{task_name}' not found. Available: {tasks}")
            return False

        self._current_task = task_name

        # Get joints for this task (infer from task name pattern)
        # e.g., "traj_left" -> joints starting with "left_"
        # e.g., "traj_arm" -> joints starting with "arm_"
        all_joints = self.list_joints()

        # Try to infer prefix from task name
        if "_" in task_name:
            prefix = task_name.split("_", 1)[1]  # "traj_left" -> "left"
            task_joints = [j for j in all_joints if j.startswith(prefix + "_")]
        else:
            task_joints = all_joints

        if not task_joints:
            # Fallback: use all joints
            task_joints = all_joints

        self._task_joints[task_name] = task_joints

        # Create generator if not exists
        if task_name not in self._generators:
            self._generators[task_name] = JointTrajectoryGenerator(
                num_joints=len(task_joints),
                max_velocity=1.0,
                max_acceleration=2.0,
                points_per_segment=50,
            )

        return True

    def get_task_joints(self, task_name: str | None = None) -> list[str]:
        """Get joint names for a task."""
        task = task_name or self._current_task
        if task is None:
            return []
        return self._task_joints.get(task, [])

    def get_current_positions(self, task_name: str | None = None) -> list[float] | None:
        """Get current joint positions for a task as a list."""
        task = task_name or self._current_task
        if task is None:
            return None

        joints = self._task_joints.get(task, [])
        if not joints:
            return None

        positions = self.get_joint_positions()
        if not positions:
            return None

        return [positions.get(j, 0.0) for j in joints]

    def generate_trajectory(
        self, waypoints: list[list[float]], task_name: str | None = None
    ) -> JointTrajectory | None:
        """Generate trajectory from waypoints using trapezoidal velocity profile."""
        task = task_name or self._current_task
        if task is None:
            print("Error: No task selected")
            return None

        generator = self._generators.get(task)
        if generator is None:
            print(f"Error: No generator for task '{task}'. Call select_task() first.")
            return None

        return generator.generate(waypoints)

    def set_velocity_limit(self, velocity: float, task_name: str | None = None) -> None:
        """Set max velocity for trajectory generation."""
        task = task_name or self._current_task
        if task and task in self._generators:
            gen = self._generators[task]
            gen.set_limits(velocity, gen.max_acceleration)

    def set_acceleration_limit(self, acceleration: float, task_name: str | None = None) -> None:
        """Set max acceleration for trajectory generation."""
        task = task_name or self._current_task
        if task and task in self._generators:
            gen = self._generators[task]
            gen.set_limits(gen.max_velocity, acceleration)


# =============================================================================
# Interactive CLI
# =============================================================================


def parse_joint_input(line: str, num_joints: int) -> list[float] | None:
    """Parse joint positions from user input (degrees by default, 'r' suffix for radians)."""
    parts = line.strip().split()
    if len(parts) != num_joints:
        return None

    positions = []
    for part in parts:
        try:
            if part.endswith("r"):
                positions.append(float(part[:-1]))
            else:
                positions.append(math.radians(float(part)))
        except ValueError:
            return None

    return positions


def format_positions(positions: list[float], as_degrees: bool = True) -> str:
    """Format positions for display."""
    if as_degrees:
        return "[" + ", ".join(f"{math.degrees(p):.1f}" for p in positions) + "] deg"
    return "[" + ", ".join(f"{p:.3f}" for p in positions) + "] rad"


def preview_waypoints(waypoints: list[list[float]], joint_names: list[str]) -> None:
    """Show waypoints list."""
    if not waypoints:
        print("No waypoints")
        return

    print(f"\nWaypoints ({len(waypoints)}):")
    print("-" * 70)

    # Header with joint names (truncated)
    headers = [j.split("_")[-1][:6] for j in joint_names]  # e.g., "joint1" -> "joint1"
    header_str = " ".join(f"{h:>7}" for h in headers)
    print(f"  # | {header_str} (degrees)")
    print("-" * 70)

    for i, joints in enumerate(waypoints):
        deg = [f"{math.degrees(j):7.1f}" for j in joints]
        print(f" {i + 1:2} | {' '.join(deg)}")
    print("-" * 70)


def preview_trajectory(trajectory: JointTrajectory, joint_names: list[str]) -> None:
    """Show generated trajectory preview."""
    headers = [j.split("_")[-1][:6] for j in joint_names]
    header_str = " ".join(f"{h:>7}" for h in headers)

    print("\n" + "=" * 70)
    print("GENERATED TRAJECTORY")
    print("=" * 70)
    print(f"Duration: {trajectory.duration:.3f}s")
    print(f"Points: {len(trajectory.points)}")
    print("-" * 70)
    print(f"{'Time':>6} | {header_str} (degrees)")
    print("-" * 70)

    num_samples = min(10, max(len(trajectory.points) // 10, 5))
    for i in range(num_samples + 1):
        t = (i / num_samples) * trajectory.duration
        q_ref, _ = trajectory.sample(t)
        q_deg = [f"{math.degrees(q):7.1f}" for q in q_ref]
        print(f"{t:6.2f} | {' '.join(q_deg)}")

    print("=" * 70)


def wait_for_completion(client: OrchestratorClient, task_name: str, timeout: float = 60.0) -> bool:
    """Wait for trajectory to complete with progress display."""
    start = time.time()
    last_progress = -1.0

    while time.time() - start < timeout:
        status = client.get_trajectory_status(task_name)
        if not status.get("active", False):
            state: str = status.get("state", "UNKNOWN")
            print(f"\nTrajectory finished: {state}")
            return state == "COMPLETED"

        progress = status.get("progress", 0.0)
        if progress != last_progress:
            bar_len = 30
            filled = int(bar_len * progress)
            bar = "=" * filled + "-" * (bar_len - filled)
            print(f"\r[{bar}] {progress * 100:.1f}%", end="", flush=True)
            last_progress = progress

        time.sleep(0.05)

    print("\nTimeout waiting for trajectory")
    return False


def print_help(num_joints: int, task_name: str) -> None:
    """Print help message."""
    joint_args = " ".join([f"<j{i + 1}>" for i in range(num_joints)])

    print("\n" + "=" * 70)
    print(f"Orchestrator Client - Task: {task_name} ({num_joints} joints)")
    print("=" * 70)
    print("\nWaypoint Commands:")
    print(f"  add {joint_args}")
    print("                         - Add waypoint (values in degrees)")
    print("  here                   - Add current position as waypoint")
    print("  list                   - List all waypoints")
    print("  delete <n>             - Delete waypoint n")
    print("  clear                  - Clear all waypoints")
    print("\nTrajectory Commands:")
    print("  preview                - Preview generated trajectory")
    print("  run                    - Execute trajectory")
    print("  status                 - Show task status")
    print("  cancel                 - Cancel active trajectory")
    print("\nMulti-Arm Commands:")
    print("  tasks                  - List all tasks")
    print("  switch <task>          - Switch to different task")
    print("  hw                     - List hardware")
    print("  joints                 - List joints for current task")
    print("\nSettings:")
    print("  current                - Show current joint positions")
    print("  vel <value>            - Set max velocity (rad/s)")
    print("  accel <value>          - Set max acceleration (rad/s^2)")
    print("  help                   - Show this help")
    print("  quit                   - Exit")
    print("=" * 70)


def interactive_mode(client: OrchestratorClient, initial_task: str) -> None:
    """Interactive mode for trajectory planning and execution."""
    # Setup initial task
    if not client.select_task(initial_task):
        print(f"Failed to select task: {initial_task}")
        return

    current_task = initial_task
    waypoints: list[list[float]] = []
    generated_trajectory: JointTrajectory | None = None

    joints = client.get_task_joints(current_task)
    num_joints = len(joints)

    print_help(num_joints, current_task)

    try:
        while True:
            # Prompt shows task and waypoint count
            active = client.get_active_tasks()
            active_marker = "*" if current_task in active else ""
            prompt = f"[{current_task}{active_marker}:{len(waypoints)}wp] > "

            try:
                line = input(prompt).strip()
            except EOFError:
                break

            if not line:
                continue

            parts = line.split()
            cmd = parts[0].lower()

            # === Waypoint commands ===

            if cmd == "add":
                if len(parts) < num_joints + 1:
                    print(f"Need {num_joints} joint values")
                    continue
                values = parse_joint_input(" ".join(parts[1 : num_joints + 1]), num_joints)
                if values:
                    waypoints.append(values)
                    generated_trajectory = None
                    print(f"Added waypoint {len(waypoints)}: {format_positions(values)}")
                else:
                    print(f"Invalid values. Need {num_joints} numbers (degrees)")

            elif cmd == "here":
                positions = client.get_current_positions(current_task)
                if positions:
                    waypoints.append(positions)
                    generated_trajectory = None
                    print(f"Added waypoint {len(waypoints)}: {format_positions(positions)}")
                else:
                    print("Could not get current positions")

            elif cmd == "list":
                preview_waypoints(waypoints, joints)

            elif cmd == "delete":
                if len(parts) < 2:
                    print("Usage: delete <n>")
                    continue
                try:
                    idx = int(parts[1]) - 1
                    if 0 <= idx < len(waypoints):
                        waypoints.pop(idx)
                        generated_trajectory = None
                        print(f"Deleted waypoint {idx + 1}")
                    else:
                        print(f"Invalid index (1-{len(waypoints)})")
                except ValueError:
                    print("Invalid index")

            elif cmd == "clear":
                waypoints.clear()
                generated_trajectory = None
                print("Cleared waypoints")

            # === Trajectory commands ===

            elif cmd == "preview":
                if len(waypoints) < 2:
                    print("Need at least 2 waypoints")
                    continue
                try:
                    generated_trajectory = client.generate_trajectory(waypoints, current_task)
                    if generated_trajectory:
                        preview_trajectory(generated_trajectory, joints)
                except Exception as e:
                    print(f"Error: {e}")

            elif cmd == "run":
                if len(waypoints) < 2:
                    print("Need at least 2 waypoints")
                    continue

                if generated_trajectory is None:
                    generated_trajectory = client.generate_trajectory(waypoints, current_task)

                if generated_trajectory is None:
                    print("Failed to generate trajectory")
                    continue

                preview_trajectory(generated_trajectory, joints)
                confirm = input("\nExecute? [y/N]: ").strip().lower()
                if confirm == "y":
                    if client.execute_trajectory(current_task, generated_trajectory):
                        print("Trajectory started...")
                        wait_for_completion(client, current_task)
                    else:
                        print("Failed to start trajectory")

            elif cmd == "status":
                status = client.get_trajectory_status(current_task)
                print(f"\nTask: {current_task}")
                print(f"  Active: {status.get('active', False)}")
                print(f"  State: {status.get('state', 'UNKNOWN')}")
                if "progress" in status:
                    print(f"  Progress: {status['progress'] * 100:.1f}%")

            elif cmd == "cancel":
                if client.cancel_trajectory(current_task):
                    print("Cancelled")
                else:
                    print("Cancel failed")

            # === Multi-arm commands ===

            elif cmd == "tasks":
                tasks = client.list_tasks()
                active = client.get_active_tasks()
                print("\nTasks:")
                for t in tasks:
                    marker = "* " if t == current_task else "  "
                    active_marker = " [ACTIVE]" if t in active else ""
                    t_joints = client.get_task_joints(t) if t in client._task_joints else []
                    joint_count = len(t_joints) if t_joints else "?"
                    print(f"{marker}{t} ({joint_count} joints){active_marker}")

            elif cmd == "switch":
                if len(parts) < 2:
                    print("Usage: switch <task_name>")
                    continue
                new_task = parts[1]
                if client.select_task(new_task):
                    current_task = new_task
                    joints = client.get_task_joints(current_task)
                    num_joints = len(joints)
                    waypoints.clear()
                    generated_trajectory = None
                    print(f"Switched to {current_task} ({num_joints} joints)")
                    print(f"Joints: {', '.join(joints)}")

            elif cmd == "hw":
                hardware = client.list_hardware()
                print(f"\nHardware: {', '.join(hardware)}")

            elif cmd == "joints":
                print(f"\nJoints for {current_task}:")
                for i, j in enumerate(joints):
                    pos = client.get_joint_positions().get(j, 0.0)
                    print(f"  {i + 1}. {j}: {math.degrees(pos):.1f} deg")

            # === Settings ===

            elif cmd == "current":
                positions = client.get_current_positions(current_task)
                if positions:
                    print(f"Current: {format_positions(positions)}")
                else:
                    print("Could not get positions")

            elif cmd == "vel":
                if len(parts) < 2:
                    gen = client._generators.get(current_task)
                    if gen:
                        print(f"Max velocity: {gen.max_velocity[0]:.2f} rad/s")
                    continue
                try:
                    vel = float(parts[1])
                    if vel <= 0:
                        print("Velocity must be positive")
                    else:
                        client.set_velocity_limit(vel, current_task)
                        generated_trajectory = None
                        print(f"Max velocity: {vel:.2f} rad/s")
                except ValueError:
                    print("Invalid velocity")

            elif cmd == "accel":
                if len(parts) < 2:
                    gen = client._generators.get(current_task)
                    if gen:
                        print(f"Max acceleration: {gen.max_acceleration[0]:.2f} rad/s^2")
                    continue
                try:
                    accel = float(parts[1])
                    if accel <= 0:
                        print("Acceleration must be positive")
                    else:
                        client.set_acceleration_limit(accel, current_task)
                        generated_trajectory = None
                        print(f"Max acceleration: {accel:.2f} rad/s^2")
                except ValueError:
                    print("Invalid acceleration")

            elif cmd == "help":
                print_help(num_joints, current_task)

            elif cmd in ("quit", "exit", "q"):
                break

            else:
                print(f"Unknown command: {cmd} (type 'help' for commands)")

    except KeyboardInterrupt:
        print("\n\nExiting...")


def main() -> int:
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Interactive client for ControlOrchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single arm (with orchestrator-mock running)
  python -m dimos.manipulation.control.orchestrator_client

  # Dual arm - control left arm
  python -m dimos.manipulation.control.orchestrator_client --task traj_left

  # Dual arm - control right arm
  python -m dimos.manipulation.control.orchestrator_client --task traj_right
        """,
    )
    parser.add_argument(
        "--task",
        type=str,
        default="traj_arm",
        help="Initial task to control (default: traj_arm)",
    )
    parser.add_argument(
        "--vel",
        type=float,
        default=1.0,
        help="Max velocity in rad/s (default: 1.0)",
    )
    parser.add_argument(
        "--accel",
        type=float,
        default=2.0,
        help="Max acceleration in rad/s^2 (default: 2.0)",
    )
    args = parser.parse_args()

    print("\n" + "=" * 70)
    print("Orchestrator Client")
    print("=" * 70)
    print("\nConnecting to ControlOrchestrator via RPC...")

    client = OrchestratorClient()

    # Check connection
    try:
        hardware = client.list_hardware()
        tasks = client.list_tasks()

        if not hardware:
            print("\nWarning: No hardware found. Is the orchestrator running?")
            print("Start with: dimos run orchestrator-mock")
            response = input("Continue anyway? [y/N]: ").strip().lower()
            if response != "y":
                client.stop()
                return 0
        else:
            print(f"Hardware: {', '.join(hardware)}")
            print(f"Tasks: {', '.join(tasks)}")

    except Exception as e:
        print(f"\nConnection error: {e}")
        print("Make sure orchestrator is running: dimos run orchestrator-mock")
        client.stop()
        return 1

    # Find initial task
    if args.task not in tasks and tasks:
        print(f"\nTask '{args.task}' not found.")
        print(f"Available: {', '.join(tasks)}")
        args.task = tasks[0]
        print(f"Using '{args.task}'")

    # Set velocity/acceleration limits
    if client.select_task(args.task):
        client.set_velocity_limit(args.vel, args.task)
        client.set_acceleration_limit(args.accel, args.task)

    try:
        interactive_mode(client, args.task)
    finally:
        client.stop()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nInterrupted")
        sys.exit(0)
    except Exception as e:
        print(f"\nError: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
