#!/usr/bin/env python3
# Copyright 2025 Dimensional Inc.
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

"""Tests for Intel RealSense camera module."""

import pytest

from dimos.msgs.sensor_msgs.CameraInfo import CameraInfo


def test_realsense_import_and_calibration_access() -> None:
    """Test that realsense module can be imported and calibrations accessed."""
    # Import realsense module from camera
    from dimos.hardware.camera import realsense

    # Test that CameraInfo is accessible
    assert hasattr(realsense, "CameraInfo")

    # Test snake_case access
    camera_info_snake = realsense.CameraInfo.default_d435
    assert isinstance(camera_info_snake, CameraInfo)
    assert camera_info_snake.width == 640
    assert camera_info_snake.height == 480
    assert camera_info_snake.distortion_model == "plumb_bob"

    # Test PascalCase access
    camera_info_pascal = realsense.CameraInfo.DefaultD435
    assert isinstance(camera_info_pascal, CameraInfo)
    assert camera_info_pascal.width == 640
    assert camera_info_pascal.height == 480

    # Verify both access methods return the same cached object
    assert camera_info_snake is camera_info_pascal

    print("✓ RealSense import and calibration access test passed!")


@pytest.mark.skipif(
    not pytest.importorskip("pyrealsense2", reason="RealSense SDK not installed"),
    reason="RealSense SDK not installed",
)
def test_realsense_camera_initialization() -> None:
    """Test RealSense camera can be initialized (requires hardware)."""
    from dimos.hardware.camera import realsense

    if not realsense.HAS_REALSENSE_SDK:
        pytest.skip("RealSense SDK not installed")

    # Try to create camera instance (won't open without hardware)
    camera = realsense.RealSenseCamera(
        width=640,
        height=480,
        fps=30,
    )
    assert camera is not None
    assert camera.width == 640
    assert camera.height == 480
    assert camera.fps == 30

    print("✓ RealSense camera initialization test passed!")





