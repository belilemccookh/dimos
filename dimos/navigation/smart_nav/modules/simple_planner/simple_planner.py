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

"""SimplePlanner: grid-based A* alternative to FarPlanner.

Consumes a classified terrain pointcloud, voxelises it into an occupancy
grid (2D costmap in the XY plane), and runs A* from the robot's current
pose to the goal. Publishes the full path on ``goal_path`` and a
look-ahead waypoint on ``way_point`` for the local planner to track.

This is intentionally small and readable — no visibility graph, no
smoothing, no dynamic obstacle handling — to serve as a baseline against
FarPlanner.
"""

from __future__ import annotations

from collections.abc import Callable
import heapq
import math
import threading
import time
from typing import Any

from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.msgs.geometry_msgs.PointStamped import PointStamped
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.nav_msgs.Odometry import Odometry
from dimos.msgs.nav_msgs.Path import Path
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2

# ──────────────────────────────────────────────────────────────────────────
# Pure-Python costmap + A* (no dependencies beyond numpy/stdlib)
# ──────────────────────────────────────────────────────────────────────────


class Costmap:
    """2D occupancy grid keyed by (ix, iy) integer cell coords.

    Memory-efficient for sparse obstacle distributions — only populated
    cells are stored in the dict. Each cell records the highest obstacle
    height ever observed there, so re-observing the same grid cell with
    a taller point promotes it to an obstacle if it wasn't already.
    """

    def __init__(self, cell_size: float, obstacle_height: float, inflation_radius: float) -> None:
        if cell_size <= 0.0:
            raise ValueError(f"cell_size must be positive, got {cell_size}")
        if inflation_radius < 0.0:
            raise ValueError(f"inflation_radius must be non-negative, got {inflation_radius}")
        self.cell_size = float(cell_size)
        self.obstacle_height = float(obstacle_height)
        self.inflation_radius = float(inflation_radius)
        # Raw heights observed per cell (max-ever). Keyed by (ix, iy).
        self._heights: dict[tuple[int, int], float] = {}
        # Inflated blocked set (recomputed lazily).
        self._blocked: set[tuple[int, int]] = set()
        self._blocked_dirty = True

    def world_to_cell(self, x: float, y: float) -> tuple[int, int]:
        return (math.floor(x / self.cell_size), math.floor(y / self.cell_size))

    def cell_to_world(self, ix: int, iy: int) -> tuple[float, float]:
        # Return cell center.
        return ((ix + 0.5) * self.cell_size, (iy + 0.5) * self.cell_size)

    def update(self, x: float, y: float, height: float) -> None:
        """Record an obstacle-candidate point. Height is elevation above ground."""
        key = self.world_to_cell(x, y)
        prev = self._heights.get(key, float("-inf"))
        if height > prev:
            self._heights[key] = height
            self._blocked_dirty = True

    def clear(self) -> None:
        self._heights.clear()
        self._blocked.clear()
        self._blocked_dirty = False

    def is_blocked(self, ix: int, iy: int) -> bool:
        if self._blocked_dirty:
            self._rebuild_blocked()
        return (ix, iy) in self._blocked

    def _rebuild_blocked(self) -> None:
        """Build the inflated obstacle set from the raw height map."""
        blocked: set[tuple[int, int]] = set()
        # Inflation: the number of cells that lie within inflation_radius.
        r_cells = math.ceil(self.inflation_radius / self.cell_size)
        for (ix, iy), h in self._heights.items():
            if h < self.obstacle_height:
                continue
            if r_cells == 0:
                blocked.add((ix, iy))
                continue
            # Circle inflation (squared comparison to avoid sqrt per cell)
            max_sq = (self.inflation_radius / self.cell_size) ** 2
            for dx in range(-r_cells, r_cells + 1):
                for dy in range(-r_cells, r_cells + 1):
                    if dx * dx + dy * dy <= max_sq:
                        blocked.add((ix + dx, iy + dy))
        self._blocked = blocked
        self._blocked_dirty = False

    def blocked_cells(self) -> set[tuple[int, int]]:
        if self._blocked_dirty:
            self._rebuild_blocked()
        return self._blocked


# 8-connected neighbourhood with per-step costs (straight=1, diagonal=√2).
_NEIGHBOURS: tuple[tuple[int, int, float], ...] = (
    (1, 0, 1.0),
    (-1, 0, 1.0),
    (0, 1, 1.0),
    (0, -1, 1.0),
    (1, 1, math.sqrt(2.0)),
    (1, -1, math.sqrt(2.0)),
    (-1, 1, math.sqrt(2.0)),
    (-1, -1, math.sqrt(2.0)),
)


def astar(
    start: tuple[int, int],
    goal: tuple[int, int],
    is_blocked: Callable[[int, int], bool],
    max_expansions: int = 200_000,
) -> list[tuple[int, int]] | None:
    """Grid A* with octile heuristic, 8-connected. Returns cell path or None."""
    if start == goal:
        return [start]

    def heuristic(c: tuple[int, int]) -> float:
        dx = abs(c[0] - goal[0])
        dy = abs(c[1] - goal[1])
        # Octile distance
        return (dx + dy) + (math.sqrt(2.0) - 2.0) * min(dx, dy)

    # If start or goal is blocked, try to step off — policy: we let the
    # caller handle that by pre-unblocking those cells.
    open_heap: list[tuple[float, int, tuple[int, int]]] = []
    counter = 0
    heapq.heappush(open_heap, (heuristic(start), counter, start))
    g_score: dict[tuple[int, int], float] = {start: 0.0}
    came_from: dict[tuple[int, int], tuple[int, int]] = {}

    expansions = 0
    while open_heap:
        expansions += 1
        if expansions > max_expansions:
            return None
        _, _, current = heapq.heappop(open_heap)
        if current == goal:
            # Reconstruct
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            path.reverse()
            return path

        cur_g = g_score[current]
        cx, cy = current
        for dx, dy, step in _NEIGHBOURS:
            nb = (cx + dx, cy + dy)
            if is_blocked(nb[0], nb[1]):
                continue
            tentative = cur_g + step
            if tentative < g_score.get(nb, float("inf")):
                came_from[nb] = current
                g_score[nb] = tentative
                counter += 1
                f = tentative + heuristic(nb)
                heapq.heappush(open_heap, (f, counter, nb))

    return None


# ──────────────────────────────────────────────────────────────────────────
# Config + Module
# ──────────────────────────────────────────────────────────────────────────


class SimplePlannerConfig(ModuleConfig):
    """Config for the simple grid-A* planner."""

    # Costmap resolution in metres per cell.
    cell_size: float = 0.4
    # Points above this elevation (height above ground from terrain_map
    # intensity) mark a cell as an obstacle.
    obstacle_height_threshold: float = 0.15
    # Circular inflation radius around each obstacle (metres). Roughly
    # robot_radius + safety margin.
    inflation_radius: float = 0.5
    # Look-ahead distance along the planned path to emit as the next
    # waypoint for the local planner.
    lookahead_distance: float = 2.0
    # Replan + publish rate (Hz).
    replan_rate: float = 5.0
    # Hard cap on A* node expansions per call.
    max_expansions: int = 200_000


class SimplePlanner(Module[SimplePlannerConfig]):
    """Grid-A* global route planner (alternative to FarPlanner).

    Ports:
        terrain_map_ext (In[PointCloud2]): Accumulated long-range terrain
            cloud (world-frame, has decay built into the producer).
        odometry (In[Odometry]): Robot pose (world frame).
        goal (In[PointStamped]): User-specified goal (world frame).
        way_point (Out[PointStamped]): Next look-ahead waypoint for local
            planner.
        goal_path (Out[Path]): Full A* path for visualisation.
    """

    default_config: type[SimplePlannerConfig] = SimplePlannerConfig

    terrain_map_ext: In[PointCloud2]
    odometry: In[Odometry]
    goal: In[PointStamped]
    way_point: Out[PointStamped]
    goal_path: Out[Path]

    def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(**kwargs)
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._costmap = Costmap(
            cell_size=self.config.cell_size,
            obstacle_height=self.config.obstacle_height_threshold,
            inflation_radius=self.config.inflation_radius,
        )
        self._robot_x = 0.0
        self._robot_y = 0.0
        self._robot_z = 0.0
        self._has_odom = False
        self._goal_x: float | None = None
        self._goal_y: float | None = None
        self._goal_z = 0.0
        self._last_diag_print = 0.0

    def __getstate__(self) -> dict[str, Any]:
        state = super().__getstate__()
        for k in ("_lock", "_thread", "_costmap"):
            state.pop(k, None)
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        super().__setstate__(state)
        self._lock = threading.Lock()
        self._thread = None
        self._costmap = Costmap(
            cell_size=self.config.cell_size,
            obstacle_height=self.config.obstacle_height_threshold,
            inflation_radius=self.config.inflation_radius,
        )

    @rpc
    def start(self) -> None:
        self.odometry._transport.subscribe(self._on_odom)
        self.goal._transport.subscribe(self._on_goal)
        self.terrain_map_ext._transport.subscribe(self._on_terrain_map)
        self._running = True
        self._thread = threading.Thread(target=self._planning_loop, daemon=True)
        self._thread.start()
        print("[simple_planner] Started.")

    @rpc
    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        super().stop()

    # ── Subscription callbacks ─────────────────────────────────────────────

    def _on_odom(self, msg: Odometry) -> None:
        with self._lock:
            self._robot_x = float(msg.pose.position.x)
            self._robot_y = float(msg.pose.position.y)
            self._robot_z = float(msg.pose.position.z)
            self._has_odom = True

    def _on_goal(self, msg: PointStamped) -> None:
        if not all(math.isfinite(v) for v in (msg.x, msg.y, msg.z)):
            return
        with self._lock:
            self._goal_x = float(msg.x)
            self._goal_y = float(msg.y)
            self._goal_z = float(msg.z)
        print(f"[simple_planner] Goal received: ({msg.x:.2f}, {msg.y:.2f}, {msg.z:.2f})")

    def _on_terrain_map(self, msg: PointCloud2) -> None:
        """Replace the costmap with the latest terrain snapshot.

        ``terrain_map_ext`` already applies a decay window (8 s by
        default) on the producer side, so each message represents the
        current world view. We simply clear and re-populate instead of
        tracking per-cell freshness ourselves.

        The dimos PointCloud2 wrapper drops the intensity field, so we
        can't read elevation-above-ground directly. Instead we classify
        by the point's absolute z relative to the robot's standing
        ground (rz - 1.3 m). TerrainAnalysis only publishes ground /
        low-height obstacle voxels, so z-relative-to-ground is a good
        elevation proxy.
        """
        points, _ = msg.as_numpy()
        if points is None or len(points) == 0:
            return
        with self._lock:
            rz = self._robot_z if self._has_odom else 0.0
        ground_z = rz - 1.3
        new_cm = Costmap(
            cell_size=self.config.cell_size,
            obstacle_height=self.config.obstacle_height_threshold,
            inflation_radius=self.config.inflation_radius,
        )
        for p in points:
            h = float(p[2]) - ground_z
            if h <= 0.0:
                continue
            new_cm.update(float(p[0]), float(p[1]), h)
        # Hot-swap in one assignment so the planning loop sees either
        # the old or the new map but never a partial one.
        self._costmap = new_cm

    # ── Planning loop ──────────────────────────────────────────────────────

    def _planning_loop(self) -> None:
        rate = self.config.replan_rate
        period = 1.0 / rate if rate > 0 else 0.2
        while self._running:
            t0 = time.monotonic()
            try:
                self._replan_once()
            except Exception as exc:  # don't let the planning thread die
                print(f"[simple_planner] Replan error: {exc}")
            dt = time.monotonic() - t0
            sleep = period - dt
            if sleep > 0:
                time.sleep(sleep)

    def _replan_once(self) -> None:
        with self._lock:
            if not self._has_odom or self._goal_x is None or self._goal_y is None:
                return
            rx, ry, rz = self._robot_x, self._robot_y, self._robot_z
            gx, gy, gz = self._goal_x, self._goal_y, self._goal_z

        path_world = self.plan(rx, ry, gx, gy)
        now = time.time()
        if path_world is None:
            # A* failed (goal unreachable through the current costmap).
            # Don't drive the robot into a wall: publish the robot's
            # current position so the local planner stops, and wait
            # for the costmap to refresh before the next attempt.
            print(
                f"[simple_planner] A* failed from ({rx:.2f},{ry:.2f}) to "
                f"({gx:.2f},{gy:.2f}); holding position."
            )
            self.way_point.publish(PointStamped(ts=now, frame_id="map", x=rx, y=ry, z=rz))
            self.goal_path.publish(
                Path(
                    ts=now,
                    frame_id="map",
                    poses=[
                        PoseStamped(
                            ts=now,
                            frame_id="map",
                            position=[rx, ry, rz],
                            orientation=[0.0, 0.0, 0.0, 1.0],
                        ),
                        PoseStamped(
                            ts=now,
                            frame_id="map",
                            position=[gx, gy, gz],
                            orientation=[0.0, 0.0, 0.0, 1.0],
                        ),
                    ],
                )
            )
            return

        # Publish goal_path
        poses: list[PoseStamped] = []
        for wx, wy in path_world:
            poses.append(
                PoseStamped(
                    ts=now,
                    frame_id="map",
                    position=[wx, wy, rz],
                    orientation=[0.0, 0.0, 0.0, 1.0],
                )
            )
        self.goal_path.publish(Path(ts=now, frame_id="map", poses=poses))

        # Pick look-ahead waypoint
        wx, wy = self._lookahead(path_world, rx, ry, self.config.lookahead_distance)
        self.way_point.publish(PointStamped(ts=now, frame_id="map", x=wx, y=wy, z=gz))

        # 1 Hz diagnostic: cells in costmap, path length, chosen waypoint
        if now - self._last_diag_print >= 1.0:
            self._last_diag_print = now
            blocked = len(self._costmap.blocked_cells())
            print(
                f"[simple_planner] path={len(path_world)} cells  "
                f"blocked_cells={blocked}  robot=({rx:.2f},{ry:.2f})  "
                f"goal=({gx:.2f},{gy:.2f})  waypoint=({wx:.2f},{wy:.2f})"
            )

    def plan(self, rx: float, ry: float, gx: float, gy: float) -> list[tuple[float, float]] | None:
        """Run A* in world coordinates. Returns [(x, y), ...] or None."""
        cm = self._costmap
        blocked = cm.blocked_cells()

        start = cm.world_to_cell(rx, ry)
        goal = cm.world_to_cell(gx, gy)

        # Ignore start/goal cell obstructions so we can plan even if the
        # robot or the goal clip an inflated cell.
        def is_blocked(ix: int, iy: int) -> bool:
            if (ix, iy) == start or (ix, iy) == goal:
                return False
            return (ix, iy) in blocked

        path_cells = astar(start, goal, is_blocked, max_expansions=self.config.max_expansions)
        if path_cells is None:
            return None
        return [cm.cell_to_world(ix, iy) for (ix, iy) in path_cells]

    @staticmethod
    def _lookahead(
        path: list[tuple[float, float]], rx: float, ry: float, distance: float
    ) -> tuple[float, float]:
        """Pick a look-ahead point at least ``distance`` metres ahead of the
        robot along the path.

        First finds the path index closest to (rx, ry), then walks forward
        until the cumulative distance from that closest point exceeds
        ``distance``. Falls back to the final path node if nothing is far
        enough. Path is ordered start → goal.
        """
        if not path:
            return (rx, ry)
        # Closest path index to the robot
        best_idx = 0
        best_d2 = float("inf")
        for i, (wx, wy) in enumerate(path):
            d2 = (wx - rx) ** 2 + (wy - ry) ** 2
            if d2 < best_d2:
                best_d2 = d2
                best_idx = i
        # Walk forward from there until we've covered `distance`
        d2_target = distance * distance
        for i in range(best_idx, len(path)):
            wx, wy = path[i]
            if (wx - rx) ** 2 + (wy - ry) ** 2 >= d2_target:
                return (wx, wy)
        return path[-1]
