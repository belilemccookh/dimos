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

"""Unit tests for Costmap + A* used by SimplePlanner."""

from __future__ import annotations

from collections.abc import Callable
import math

import pytest

from dimos.navigation.smart_nav.modules.simple_planner.simple_planner import (
    Costmap,
    SimplePlanner,
    astar,
)

# ─── Costmap ─────────────────────────────────────────────────────────────


class TestCostmap:
    def test_world_cell_roundtrip(self) -> None:
        cm = Costmap(cell_size=0.5, obstacle_height=0.1, inflation_radius=0.0)
        for x, y in [(0.0, 0.0), (1.25, -2.75), (10.1, 4.4)]:
            ix, iy = cm.world_to_cell(x, y)
            cx, cy = cm.cell_to_world(ix, iy)
            # Cell center is within half-cell of original
            assert abs(cx - x) <= 0.5
            assert abs(cy - y) <= 0.5

    def test_height_max_tracks_tallest(self) -> None:
        cm = Costmap(cell_size=1.0, obstacle_height=0.5, inflation_radius=0.0)
        cm.update(0.1, 0.1, 0.2)
        cm.update(0.2, 0.3, 0.8)
        cm.update(0.4, 0.4, 0.4)  # same cell, smaller than 0.8
        assert cm.is_blocked(0, 0)  # 0.8 > 0.5

    def test_height_below_threshold_not_blocked(self) -> None:
        cm = Costmap(cell_size=1.0, obstacle_height=0.5, inflation_radius=0.0)
        cm.update(0.5, 0.5, 0.3)  # below threshold
        assert not cm.is_blocked(0, 0)

    def test_clear_wipes_obstacles(self) -> None:
        cm = Costmap(cell_size=1.0, obstacle_height=0.1, inflation_radius=0.0)
        cm.update(0.0, 0.0, 1.0)
        assert cm.is_blocked(0, 0)
        cm.clear()
        assert not cm.is_blocked(0, 0)

    def test_inflation_blocks_neighbours(self) -> None:
        cm = Costmap(cell_size=1.0, obstacle_height=0.1, inflation_radius=1.5)
        cm.update(0.0, 0.0, 1.0)
        # Center is blocked
        assert cm.is_blocked(0, 0)
        # Cells within radius 1.5 are blocked (Manhattan dist ≤ 1 is always in a circle of r=1.5)
        assert cm.is_blocked(1, 0)
        assert cm.is_blocked(0, 1)
        assert cm.is_blocked(-1, 0)
        assert cm.is_blocked(1, 1)  # sqrt(2) ≈ 1.41 < 1.5
        # Cells outside radius 1.5 are not blocked
        assert not cm.is_blocked(2, 0)
        assert not cm.is_blocked(0, 2)

    def test_zero_inflation(self) -> None:
        cm = Costmap(cell_size=1.0, obstacle_height=0.1, inflation_radius=0.0)
        cm.update(0.0, 0.0, 1.0)
        assert cm.is_blocked(0, 0)
        assert not cm.is_blocked(1, 0)

    def test_invalid_cell_size(self) -> None:
        with pytest.raises(ValueError):
            Costmap(cell_size=0.0, obstacle_height=0.1, inflation_radius=0.0)
        with pytest.raises(ValueError):
            Costmap(cell_size=-1.0, obstacle_height=0.1, inflation_radius=0.0)

    def test_invalid_inflation(self) -> None:
        with pytest.raises(ValueError):
            Costmap(cell_size=1.0, obstacle_height=0.1, inflation_radius=-0.1)


# ─── A* ──────────────────────────────────────────────────────────────────


def _never_blocked(ix: int, iy: int) -> bool:
    return False


def _blocked_set(cells: set[tuple[int, int]]) -> Callable[[int, int], bool]:
    def _inner(ix: int, iy: int) -> bool:
        return (ix, iy) in cells

    return _inner


class TestAstar:
    def test_trivial_same_cell(self) -> None:
        assert astar((3, 4), (3, 4), _never_blocked) == [(3, 4)]

    def test_straight_line_no_obstacles(self) -> None:
        path = astar((0, 0), (5, 0), _never_blocked)
        assert path is not None
        assert path[0] == (0, 0)
        assert path[-1] == (5, 0)
        # 5 straight steps → 6 cells
        assert len(path) == 6

    def test_diagonal_no_obstacles(self) -> None:
        path = astar((0, 0), (3, 3), _never_blocked)
        assert path is not None
        assert path[0] == (0, 0)
        assert path[-1] == (3, 3)
        # Prefer diagonal: 3 moves + 1 cell = 4 cells
        assert len(path) == 4

    def test_wall_detours(self) -> None:
        # vertical wall at x=2 for y in [-1..1], need to go around
        wall = {(2, -1), (2, 0), (2, 1)}
        path = astar((0, 0), (4, 0), _blocked_set(wall))
        assert path is not None
        assert path[0] == (0, 0)
        assert path[-1] == (4, 0)
        # Must not pass through wall cells
        for cell in path:
            assert cell not in wall

    def test_unreachable_goal(self) -> None:
        # Enclosed goal
        wall = {(2, -1), (2, 0), (2, 1), (1, -1), (3, -1), (1, 1), (3, 1), (2, 2)}
        # Add missing walls to fully enclose (2, 0)
        wall |= {(1, 0), (3, 0)}  # but goal is (2, 0) which is inside walls — wait
        # Actually goal (2, 0) IS in the wall. Use a different example.
        wall = {
            (0, 1),
            (1, 1),
            (2, 1),
            (2, 0),
            (0, -1),
            (1, -1),
            (2, -1),
            (-1, -1),
            (-1, 0),
            (-1, 1),
        }  # encloses (0, 0) and (1, 0)
        # Goal outside the box
        path = astar((0, 0), (5, 0), _blocked_set(wall))
        assert path is None

    def test_max_expansions_cap(self) -> None:
        # Should give up instead of hanging
        path = astar((0, 0), (10000, 10000), _never_blocked, max_expansions=100)
        assert path is None

    def test_octile_prefers_diagonal(self) -> None:
        # 4 straight moves vs 2 diagonal + 2 straight = same displacement
        # but A* should find the optimal octile path.
        path = astar((0, 0), (2, 2), _never_blocked)
        assert path is not None
        # Two diagonal steps = 3 cells
        assert len(path) == 3


# ─── SimplePlanner.plan() + lookahead (no threading, no LCM) ─────────────


class TestSimplePlannerPlan:
    def _make_planner(self, cell_size: float = 0.5) -> SimplePlanner:
        # Constructing Module directly needs the blueprint machinery; use
        # object.__new__ and fill in the fields we actually need so we
        # can unit-test the plan() + _lookahead() logic standalone.
        p = SimplePlanner.__new__(SimplePlanner)
        p._costmap = Costmap(cell_size=cell_size, obstacle_height=0.1, inflation_radius=0.0)

        # Fake config holder for the plan() max_expansions access
        class _C:
            max_expansions = 200_000

        p.config = _C()  # type: ignore[assignment]
        return p

    def test_plan_straight_open_path(self) -> None:
        p = self._make_planner(cell_size=0.5)
        path = p.plan(0.0, 0.0, 2.0, 0.0)
        assert path is not None
        # First cell is near start, last cell is near goal
        assert abs(path[0][0] - 0.25) < 1e-6
        assert abs(path[0][1] - 0.25) < 1e-6
        assert abs(path[-1][0] - 2.25) < 1e-6
        assert abs(path[-1][1] - 0.25) < 1e-6

    def test_plan_routes_around_obstacle(self) -> None:
        p = self._make_planner(cell_size=0.5)
        # Build a wall at x≈1.0 between y=-0.5 and y=1.0
        for y in (-0.5, 0.0, 0.5, 1.0):
            p._costmap.update(1.0, y, 1.0)
        path = p.plan(0.0, 0.0, 2.0, 0.0)
        assert path is not None
        blocked = p._costmap.blocked_cells()
        # Path cells (converted back to cell indices) must not contain blocked cells
        for wx, wy in path:
            ix, iy = p._costmap.world_to_cell(wx, wy)
            assert (
                (ix, iy) not in blocked
                or (ix, iy) == p._costmap.world_to_cell(0.0, 0.0)
                or (ix, iy) == p._costmap.world_to_cell(2.0, 0.0)
            )

    def test_plan_returns_none_when_blocked(self) -> None:
        p = self._make_planner(cell_size=0.5)
        # Box in the start
        for x in (-0.5, 0.0, 0.5):
            for y in (-0.5, 0.0, 0.5):
                if (x, y) == (0.0, 0.0):
                    continue
                p._costmap.update(x, y, 1.0)
        # Also block further out — but actually with finite box, still reachable. Skip.
        # Instead test a tiny costmap where goal is surrounded on all 8 sides.
        p2 = self._make_planner(cell_size=1.0)
        gx, gy = 5.0, 0.0
        # Ring around goal cell (5, 0)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)):
            p2._costmap.update(gx + dx * 1.0, gy + dy * 1.0, 1.0)
        path = p2.plan(0.0, 0.0, gx, gy)
        assert path is None

    def test_lookahead_picks_far_enough(self) -> None:
        path = [(0.0, 0.0), (0.5, 0.0), (1.0, 0.0), (1.5, 0.0), (2.0, 0.0)]
        wx, wy = SimplePlanner._lookahead(path, 0.0, 0.0, 1.0)
        assert math.isclose(wx, 1.0)
        assert math.isclose(wy, 0.0)

    def test_lookahead_falls_back_to_end(self) -> None:
        path = [(0.0, 0.0), (0.1, 0.0)]
        wx, wy = SimplePlanner._lookahead(path, 0.0, 0.0, 5.0)
        assert math.isclose(wx, 0.1)
        assert math.isclose(wy, 0.0)

    def test_lookahead_empty_path(self) -> None:
        wx, wy = SimplePlanner._lookahead([], 3.0, 4.0, 1.0)
        assert wx == 3.0 and wy == 4.0

    def test_lookahead_moving_robot(self) -> None:
        # Robot is already halfway down the path; look-ahead should pick a
        # point ahead of the robot, not at the start.
        path = [(x, 0.0) for x in (0.0, 1.0, 2.0, 3.0, 4.0, 5.0)]
        wx, wy = SimplePlanner._lookahead(path, 2.0, 0.0, 1.5)
        # From (2, 0), first point ≥ 1.5 m away is (4, 0) (dist 2.0),
        # not (3, 0) which is only 1.0 m away.
        assert math.isclose(wx, 4.0)
