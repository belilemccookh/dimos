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

"""Intel RealSense camera hardware interfaces."""

from pathlib import Path

from dimos.msgs.sensor_msgs.CameraInfo import CalibrationProvider

# Check if RealSense SDK is available
try:
    import pyrealsense2 as rs  # type: ignore[import-not-found] # noqa: F401

    HAS_REALSENSE_SDK = True
except ImportError:
    HAS_REALSENSE_SDK = False

# Only import RealSense classes if SDK is available
if HAS_REALSENSE_SDK:
    from dimos.hardware.camera.realsense.camera import RealSenseCamera, RealSenseModule
else:
    # Provide stub classes when SDK is not available
    class RealSenseCamera:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
            raise ImportError(
                "Intel RealSense SDK not installed. "
                "Please install pyrealsense2 package: pip install pyrealsense2"
            )

    class RealSenseModule:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
            raise ImportError(
                "Intel RealSense SDK not installed. "
                "Please install pyrealsense2 package: pip install pyrealsense2"
            )


# Set up camera calibration provider (always available)
CALIBRATION_DIR = Path(__file__).parent
CameraInfo = CalibrationProvider(CALIBRATION_DIR)

__all__ = [
    "HAS_REALSENSE_SDK",
    "CameraInfo",
    "RealSenseCamera",
    "RealSenseModule",
]


