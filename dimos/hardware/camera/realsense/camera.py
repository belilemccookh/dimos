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

"""Intel RealSense camera interface."""

from types import TracebackType
from typing import Any

import cv2
from dimos_lcm.sensor_msgs import CameraInfo  # type: ignore[import-untyped]
import numpy as np
import open3d as o3d  # type: ignore[import-untyped]
import pyrealsense2 as rs  # type: ignore[import-not-found]
from reactivex import interval

from dimos.core import Module, Out, rpc
from dimos.msgs.geometry_msgs import PoseStamped, Quaternion, Transform, Vector3
from dimos.msgs.sensor_msgs import Image, ImageFormat
from dimos.msgs.std_msgs import Header
from dimos.protocol.tf import TF
from dimos.utils.logging_config import setup_logger

logger = setup_logger(__name__)


class RealSenseCamera:
    """Intel RealSense Camera capture with depth processing."""

    def __init__(
        self,
        serial_number: str | None = None,
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        enable_color: bool = True,
        enable_depth: bool = True,
        enable_infrared: bool = False,
        align_depth_to_color: bool = True,
        **kwargs: Any,
    ) -> None:
        """
        Initialize Intel RealSense Camera.

        Args:
            serial_number: Camera serial number (None for first available)
            width: Frame width
            height: Frame height
            fps: Frame rate
            enable_color: Enable RGB stream
            enable_depth: Enable depth stream
            enable_infrared: Enable infrared stream
            align_depth_to_color: Align depth frames to color frames
        """
        self.serial_number = serial_number
        self.width = width
        self.height = height
        self.fps = fps
        self.enable_color = enable_color
        self.enable_depth = enable_depth
        self.enable_infrared = enable_infrared
        self.align_depth_to_color = align_depth_to_color

        # RealSense pipeline and config
        self.pipeline = rs.pipeline()
        self.config = rs.config()
        self.align = None

        # Filters for depth processing
        self.decimation_filter = rs.decimation_filter()
        self.spatial_filter = rs.spatial_filter()
        self.temporal_filter = rs.temporal_filter()
        self.hole_filling_filter = rs.hole_filling_filter()

        # Camera intrinsics (populated after start)
        self.color_intrinsics = None
        self.depth_intrinsics = None
        self.depth_scale = None

        self.is_opened = False

    def open(self) -> bool:
        """Open the RealSense camera and start streaming."""
        try:
            # Configure device by serial number if specified
            if self.serial_number:
                self.config.enable_device(self.serial_number)

            # Enable streams
            if self.enable_color:
                self.config.enable_stream(
                    rs.stream.color, self.width, self.height, rs.format.bgr8, self.fps
                )

            if self.enable_depth:
                self.config.enable_stream(
                    rs.stream.depth, self.width, self.height, rs.format.z16, self.fps
                )

            if self.enable_infrared:
                self.config.enable_stream(
                    rs.stream.infrared, 1, self.width, self.height, rs.format.y8, self.fps
                )

            # Start pipeline
            profile = self.pipeline.start(self.config)

            # Get device info
            device = profile.get_device()
            logger.info(f"RealSense Camera: {device.get_info(rs.camera_info.name)}")
            logger.info(f"Serial Number: {device.get_info(rs.camera_info.serial_number)}")
            logger.info(f"Firmware: {device.get_info(rs.camera_info.firmware_version)}")

            # Get depth scale
            depth_sensor = device.first_depth_sensor()
            self.depth_scale = depth_sensor.get_depth_scale()
            logger.info(f"Depth Scale: {self.depth_scale}")

            # Set up alignment if requested
            if self.align_depth_to_color and self.enable_color and self.enable_depth:
                self.align = rs.align(rs.stream.color)

            # Get intrinsics
            if self.enable_color:
                color_stream = profile.get_stream(rs.stream.color)
                self.color_intrinsics = color_stream.as_video_stream_profile().get_intrinsics()

            if self.enable_depth:
                depth_stream = profile.get_stream(rs.stream.depth)
                self.depth_intrinsics = depth_stream.as_video_stream_profile().get_intrinsics()

            self.is_opened = True
            logger.info("RealSense camera opened successfully")
            return True

        except Exception as e:
            logger.error(f"Error opening RealSense camera: {e}")
            return False

    def capture_frame(
        self,
        apply_filters: bool = True,
    ) -> tuple[np.ndarray | None, np.ndarray | None, np.ndarray | None]:
        """
        Capture a frame from RealSense camera.

        Args:
            apply_filters: Apply depth filters for noise reduction

        Returns:
            Tuple of (color_image, depth_image, infrared_image) as numpy arrays.
            Depth is in meters (float32). Returns None for disabled streams.
        """
        if not self.is_opened:
            logger.error("RealSense camera not opened")
            return None, None, None

        try:
            # Wait for frames
            frames = self.pipeline.wait_for_frames()

            # Align depth to color if enabled
            if self.align:
                frames = self.align.process(frames)

            color_image = None
            depth_image = None
            infrared_image = None

            # Get color frame
            if self.enable_color:
                color_frame = frames.get_color_frame()
                if color_frame:
                    color_image = np.asanyarray(color_frame.get_data())

            # Get depth frame
            if self.enable_depth:
                depth_frame = frames.get_depth_frame()
                if depth_frame:
                    # Apply filters if requested
                    if apply_filters:
                        depth_frame = self.decimation_filter.process(depth_frame)
                        depth_frame = self.spatial_filter.process(depth_frame)
                        depth_frame = self.temporal_filter.process(depth_frame)
                        depth_frame = self.hole_filling_filter.process(depth_frame)

                    # Convert to numpy and scale to meters
                    depth_data = np.asanyarray(depth_frame.get_data())
                    depth_image = depth_data.astype(np.float32) * self.depth_scale

            # Get infrared frame
            if self.enable_infrared:
                ir_frame = frames.get_infrared_frame()
                if ir_frame:
                    infrared_image = np.asanyarray(ir_frame.get_data())

            return color_image, depth_image, infrared_image

        except Exception as e:
            logger.error(f"Error capturing frame: {e}")
            return None, None, None

    def capture_pointcloud(
        self,
        apply_filters: bool = True,
    ) -> o3d.geometry.PointCloud | None:
        """
        Capture point cloud from RealSense camera.

        Args:
            apply_filters: Apply depth filters for noise reduction

        Returns:
            Open3D point cloud with XYZ coordinates and RGB colors
        """
        if not self.is_opened:
            logger.error("RealSense camera not opened")
            return None

        try:
            # Wait for frames
            frames = self.pipeline.wait_for_frames()

            # Align depth to color
            if self.align:
                frames = self.align.process(frames)

            depth_frame = frames.get_depth_frame()
            color_frame = frames.get_color_frame()

            if not depth_frame or not color_frame:
                return None

            # Apply filters
            if apply_filters:
                depth_frame = self.decimation_filter.process(depth_frame)
                depth_frame = self.spatial_filter.process(depth_frame)
                depth_frame = self.temporal_filter.process(depth_frame)
                depth_frame = self.hole_filling_filter.process(depth_frame)

            # Create point cloud
            pc = rs.pointcloud()
            pc.map_to(color_frame)
            points = pc.calculate(depth_frame)

            # Get vertices and texture coordinates
            vertices = np.asanyarray(points.get_vertices()).view(np.float32).reshape(-1, 3)
            tex_coords = np.asanyarray(points.get_texture_coordinates()).view(np.float32).reshape(-1, 2)

            # Get colors from color frame
            color_image = np.asanyarray(color_frame.get_data())
            h, w = color_image.shape[:2]

            # Map texture coordinates to colors
            tex_x = (tex_coords[:, 0] * w).astype(np.int32)
            tex_y = (tex_coords[:, 1] * h).astype(np.int32)

            # Clamp to valid range
            tex_x = np.clip(tex_x, 0, w - 1)
            tex_y = np.clip(tex_y, 0, h - 1)

            # Get colors (BGR to RGB)
            colors = color_image[tex_y, tex_x, ::-1].astype(np.float64) / 255.0

            # Filter out invalid points
            valid = np.isfinite(vertices).all(axis=1) & (vertices[:, 2] > 0)
            valid_vertices = vertices[valid]
            valid_colors = colors[valid]

            # Create Open3D point cloud
            pcd = o3d.geometry.PointCloud()
            if len(valid_vertices) > 0:
                pcd.points = o3d.utility.Vector3dVector(valid_vertices)
                pcd.colors = o3d.utility.Vector3dVector(valid_colors)

            return pcd

        except Exception as e:
            logger.error(f"Error capturing point cloud: {e}")
            return None

    def get_camera_info(self) -> dict[str, Any]:
        """Get RealSense camera information and calibration parameters."""
        if not self.is_opened:
            return {}

        try:
            profile = self.pipeline.get_active_profile()
            device = profile.get_device()

            info: dict[str, Any] = {
                "name": device.get_info(rs.camera_info.name),
                "serial_number": device.get_info(rs.camera_info.serial_number),
                "firmware": device.get_info(rs.camera_info.firmware_version),
                "depth_scale": self.depth_scale,
                "resolution": {
                    "width": self.width,
                    "height": self.height,
                },
                "fps": self.fps,
            }

            # Add color intrinsics
            if self.color_intrinsics:
                info["color_cam"] = {
                    "fx": self.color_intrinsics.fx,
                    "fy": self.color_intrinsics.fy,
                    "cx": self.color_intrinsics.ppx,
                    "cy": self.color_intrinsics.ppy,
                    "width": self.color_intrinsics.width,
                    "height": self.color_intrinsics.height,
                    "distortion_model": str(self.color_intrinsics.model),
                    "coeffs": list(self.color_intrinsics.coeffs),
                }

            # Add depth intrinsics
            if self.depth_intrinsics:
                info["depth_cam"] = {
                    "fx": self.depth_intrinsics.fx,
                    "fy": self.depth_intrinsics.fy,
                    "cx": self.depth_intrinsics.ppx,
                    "cy": self.depth_intrinsics.ppy,
                    "width": self.depth_intrinsics.width,
                    "height": self.depth_intrinsics.height,
                    "distortion_model": str(self.depth_intrinsics.model),
                    "coeffs": list(self.depth_intrinsics.coeffs),
                }

            return info

        except Exception as e:
            logger.error(f"Error getting camera info: {e}")
            return {}

    def calculate_intrinsics(self) -> dict[str, Any]:
        """Calculate camera intrinsics from RealSense calibration."""
        info = self.get_camera_info()
        if not info:
            return {}

        color_cam = info.get("color_cam", {})
        resolution = info.get("resolution", {})

        return {
            "focal_length_x": color_cam.get("fx", 0),
            "focal_length_y": color_cam.get("fy", 0),
            "principal_point_x": color_cam.get("cx", 0),
            "principal_point_y": color_cam.get("cy", 0),
            "resolution_width": resolution.get("width", 0),
            "resolution_height": resolution.get("height", 0),
        }

    def close(self) -> None:
        """Close the RealSense camera."""
        if self.is_opened:
            self.pipeline.stop()
            self.is_opened = False
            logger.info("RealSense camera closed")

    def __enter__(self) -> "RealSenseCamera":
        """Context manager entry."""
        if not self.open():
            raise RuntimeError("Failed to open RealSense camera")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Context manager exit."""
        self.close()


class RealSenseModule(Module):
    """
    Dask module for Intel RealSense camera that publishes sensor data via LCM.

    Publishes:
        - /realsense/color_image: RGB camera images
        - /realsense/depth_image: Depth images (in meters)
        - /realsense/camera_info: Camera calibration information
    """

    # Define LCM outputs
    color_image: Out[Image] = None  # type: ignore[assignment]
    depth_image: Out[Image] = None  # type: ignore[assignment]
    camera_info: Out[CameraInfo] = None  # type: ignore[assignment]

    def __init__(
        self,
        serial_number: str | None = None,
        width: int = 640,
        height: int = 480,
        fps: int = 30,
        enable_color: bool = True,
        enable_depth: bool = True,
        align_depth_to_color: bool = True,
        apply_filters: bool = True,
        publish_rate: float = 30.0,
        frame_id: str = "realsense_camera",
        recording_path: str | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Initialize RealSense Module.

        Args:
            serial_number: Camera serial number (None for first available)
            width: Frame width
            height: Frame height
            fps: Camera frame rate
            enable_color: Enable RGB stream
            enable_depth: Enable depth stream
            align_depth_to_color: Align depth frames to color frames
            apply_filters: Apply depth filters for noise reduction
            publish_rate: Rate to publish messages (Hz)
            frame_id: TF frame ID for messages
            recording_path: Path to save recorded data
        """
        super().__init__(**kwargs)

        self.serial_number = serial_number
        self.width = width
        self.height = height
        self.fps = fps
        self.enable_color = enable_color
        self.enable_depth = enable_depth
        self.align_depth_to_color = align_depth_to_color
        self.apply_filters = apply_filters
        self.publish_rate = publish_rate
        self.frame_id = frame_id
        self.recording_path = recording_path

        # Internal state
        self.realsense_camera: RealSenseCamera | None = None
        self._running = False
        self._subscription = None
        self._sequence = 0

        # Initialize TF publisher
        self.tf = TF()

        # Initialize storage for recording if path provided
        self.storages = None
        if self.recording_path:
            from dimos.utils.testing import TimedSensorStorage

            self.storages = {
                "color": TimedSensorStorage(f"{self.recording_path}/color"),
                "depth": TimedSensorStorage(f"{self.recording_path}/depth"),
                "camera_info": TimedSensorStorage(f"{self.recording_path}/camera_info"),
            }
            logger.info(f"Recording enabled - saving to {self.recording_path}")

        logger.info(f"RealSenseModule initialized (serial: {serial_number or 'auto'})")

    @rpc
    def start(self) -> None:
        """Start the RealSense module and begin publishing data."""
        if self._running:
            logger.warning("RealSense module already running")
            return

        super().start()

        try:
            # Initialize RealSense camera
            self.realsense_camera = RealSenseCamera(
                serial_number=self.serial_number,
                width=self.width,
                height=self.height,
                fps=self.fps,
                enable_color=self.enable_color,
                enable_depth=self.enable_depth,
                align_depth_to_color=self.align_depth_to_color,
            )

            # Open camera
            if not self.realsense_camera.open():
                logger.error("Failed to open RealSense camera")
                return

            # Publish camera info once at startup
            self._publish_camera_info()

            # Start periodic frame capture and publishing
            self._running = True
            publish_interval = 1.0 / self.publish_rate

            self._subscription = interval(publish_interval).subscribe(
                lambda _: self._capture_and_publish()
            )

            logger.info(f"RealSense module started, publishing at {self.publish_rate} Hz")

        except Exception as e:
            logger.error(f"Error starting RealSense module: {e}")
            self._running = False

    @rpc
    def stop(self) -> None:
        """Stop the RealSense module."""
        if not self._running:
            return

        self._running = False

        # Stop subscription
        if self._subscription:
            self._subscription.dispose()
            self._subscription = None

        # Close camera
        if self.realsense_camera:
            self.realsense_camera.close()
            self.realsense_camera = None

        super().stop()

    def _capture_and_publish(self) -> None:
        """Capture frame and publish all data."""
        if not self._running or not self.realsense_camera:
            return

        try:
            # Capture frame
            color_img, depth_img, _ = self.realsense_camera.capture_frame(
                apply_filters=self.apply_filters
            )

            # Save raw data if recording
            if self.storages:
                if color_img is not None:
                    self.storages["color"].save_one(color_img)
                if depth_img is not None:
                    self.storages["depth"].save_one(depth_img)

            # Create header
            header = Header(self.frame_id)
            self._sequence += 1

            # Publish color image
            if color_img is not None:
                self._publish_color_image(color_img, header)

            # Publish depth image
            if depth_img is not None:
                self._publish_depth_image(depth_img, header)

            # Publish camera info periodically
            self._publish_camera_info()

        except Exception as e:
            logger.error(f"Error in capture and publish: {e}")

    def _publish_color_image(self, image: np.ndarray, header: Header) -> None:
        """Publish color image as LCM message."""
        try:
            # Convert BGR to RGB
            if len(image.shape) == 3 and image.shape[2] == 3:
                image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            else:
                image_rgb = image

            # Create LCM Image message
            msg = Image(
                data=image_rgb,
                format=ImageFormat.RGB,
                frame_id=header.frame_id,
                ts=header.ts,
            )

            self.color_image.publish(msg)  # type: ignore[no-untyped-call]

        except Exception as e:
            logger.error(f"Error publishing color image: {e}")

    def _publish_depth_image(self, depth: np.ndarray, header: Header) -> None:
        """Publish depth image as LCM message."""
        try:
            # Depth is float32 in meters
            msg = Image(
                data=depth,
                format=ImageFormat.DEPTH,
                frame_id=header.frame_id,
                ts=header.ts,
            )
            self.depth_image.publish(msg)  # type: ignore[no-untyped-call]

        except Exception as e:
            logger.error(f"Error publishing depth image: {e}")

    def _publish_camera_info(self) -> None:
        """Publish camera calibration information."""
        try:
            if not self.realsense_camera:
                return

            info = self.realsense_camera.get_camera_info()
            if not info:
                return

            # Get calibration parameters
            color_cam = info.get("color_cam", {})
            resolution = info.get("resolution", {})

            # Only publish if we have valid intrinsics
            fx = color_cam.get("fx", 0)
            fy = color_cam.get("fy", 0)
            cx = color_cam.get("cx", 0)
            cy = color_cam.get("cy", 0)
            if fx == 0 or fy == 0:
                # Camera not fully initialized yet, skip publishing
                logger.debug("Skipping camera_info publish: intrinsics not yet available")
                return

            # Save raw camera info if recording
            if self.storages:
                self.storages["camera_info"].save_one(info)

            # Create CameraInfo message
            header = Header(self.frame_id)

            # Create camera matrix K (3x3)
            K = [
                fx,
                0,
                color_cam.get("cx", 0),
                0,
                fy,
                color_cam.get("cy", 0),
                0,
                0,
                1,
            ]

            # Distortion coefficients
            coeffs = color_cam.get("coeffs", [0, 0, 0, 0, 0])
            D = coeffs[:5] if len(coeffs) >= 5 else coeffs + [0] * (5 - len(coeffs))

            # Identity rotation matrix
            R = [1, 0, 0, 0, 1, 0, 0, 0, 1]

            # Projection matrix P (3x4)
            P = [
                fx,
                0,
                color_cam.get("cx", 0),
                0,
                0,
                fy,
                color_cam.get("cy", 0),
                0,
                0,
                0,
                1,
                0,
            ]

            msg = CameraInfo(
                D_length=len(D),
                header=header,
                height=resolution.get("height", 0),
                width=resolution.get("width", 0),
                distortion_model="plumb_bob",
                D=D,
                K=K,
                R=R,
                P=P,
                binning_x=0,
                binning_y=0,
            )

            self.camera_info.publish(msg)  # type: ignore[no-untyped-call]
            logger.debug(
                f"Published camera_info: fx={fx:.2f}, fy={fy:.2f}, cx={cx:.2f}, cy={cy:.2f}, "
                f"resolution={resolution.get('width', 0)}x{resolution.get('height', 0)}"
            )

        except Exception as e:
            logger.error(f"Error publishing camera info: {e}")

    @rpc
    def get_camera_info(self) -> dict[str, Any]:
        """Get camera information and calibration parameters."""
        if self.realsense_camera:
            return self.realsense_camera.get_camera_info()
        return {}


