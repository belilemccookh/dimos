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

"""Tests for geometry_msgs.Point and geometry_msgs.PointStamped."""

from __future__ import annotations

import unittest

from dimos_lcm.geometry_msgs import Point as LCMPoint

from dimos.msgs.geometry_msgs.PointStamped import Point, PointStamped
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped


class TestPoint(unittest.TestCase):
    """Test the Point wrapper."""

    def test_inherits_lcm_point(self):
        self.assertIsInstance(Point(1.0, 2.0, 3.0), LCMPoint)

    def test_msg_name(self):
        self.assertEqual(Point.msg_name, "geometry_msgs.Point")


class TestPointStamped(unittest.TestCase):
    """Test PointStamped construction and inheritance."""

    def test_construction(self):
        pt = PointStamped(x=1.5, y=2.5, z=3.5, ts=100.0, frame_id="/world")
        self.assertAlmostEqual(pt.x, 1.5)
        self.assertAlmostEqual(pt.y, 2.5)
        self.assertAlmostEqual(pt.z, 3.5)
        self.assertAlmostEqual(pt.ts, 100.0)
        self.assertEqual(pt.frame_id, "/world")

    def test_inherits_point_and_lcm(self):
        pt = PointStamped(x=1.0, y=2.0, z=3.0)
        self.assertIsInstance(pt, Point)
        self.assertIsInstance(pt, LCMPoint)

    def test_auto_timestamp(self):
        pt = PointStamped(x=1.0, y=2.0, z=3.0)
        self.assertGreater(pt.ts, 0)


class TestLCMRoundtrip(unittest.TestCase):
    """Core test: msg -> lcm bytes -> msg."""

    def test_roundtrip(self):
        original = PointStamped(x=1.5, y=-2.5, z=3.5, ts=1234.5678, frame_id="/world/grid")
        data = original.lcm_encode()
        decoded = PointStamped.lcm_decode(data)
        self.assertAlmostEqual(decoded.x, original.x)
        self.assertAlmostEqual(decoded.y, original.y)
        self.assertAlmostEqual(decoded.z, original.z)
        self.assertAlmostEqual(decoded.ts, original.ts, places=6)
        self.assertEqual(decoded.frame_id, original.frame_id)

    def test_fingerprint_matches_lcm_point(self):
        """Verify lcm_msg.point = self works (same fingerprint)."""
        pt = PointStamped(x=1.0, y=2.0, z=3.0)
        self.assertEqual(pt._get_packed_fingerprint(), LCMPoint._get_packed_fingerprint())


class TestConversions(unittest.TestCase):
    def test_to_pose_stamped(self):
        pt = PointStamped(x=1.0, y=2.0, z=3.0, ts=500.0, frame_id="/map")
        pose = pt.to_pose_stamped()
        self.assertIsInstance(pose, PoseStamped)
        self.assertAlmostEqual(pose.x, 1.0)
        self.assertAlmostEqual(pose.y, 2.0)
        self.assertAlmostEqual(pose.z, 3.0)
        self.assertAlmostEqual(pose.orientation.w, 1.0)
        self.assertAlmostEqual(pose.ts, 500.0)
        self.assertEqual(pose.frame_id, "/map")

    def test_to_rerun(self):
        import rerun as rr
        pt = PointStamped(x=1.0, y=2.0, z=3.0)
        self.assertIsInstance(pt.to_rerun(), rr.Points3D)


if __name__ == "__main__":
    unittest.main()
