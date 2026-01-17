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

from threading import Event, RLock
import time
from typing import TYPE_CHECKING

import numpy as np
from reactivex.disposable import Disposable

from dimos.core.core import rpc
from dimos.core.global_config import GlobalConfig
from dimos.core.skill_module import SkillModule
from dimos.core.stream import In, Out
from dimos.models.qwen.video_query import BBox
from dimos.models.segmentation.edge_tam import EdgeTAMProcessor
from dimos.models.vl.qwen import QwenVlModel
from dimos.msgs.geometry_msgs import Twist, Vector3
from dimos.msgs.sensor_msgs import CameraInfo, Image, PointCloud2
from dimos.navigation.visual.query import get_object_bbox_from_image
from dimos.navigation.visual_servoing.visual_servoing_2d import VisualServoing2D
from dimos.perception.detection.type.detection2d.bbox import Detection2DBBox
from dimos.perception.detection.type.detection3d import Detection3DPC
from dimos.protocol.skill.skill import skill
from dimos.utils.logging_config import setup_logger

if TYPE_CHECKING:
    from dimos.models.vl.base import VlModel

logger = setup_logger()


class PersonFollowSkillContainer(SkillModule):
    """Skill container for following a person using visual servoing with EdgeTAM.

    This skill uses:
    - A VL model (QwenVlModel) to initially detect a person from a text description.
    - EdgeTAM for continuous tracking across frames.
    - Visual servoing OR 3D navigation to control robot movement towards the person.
    - Does not do obstacle avoidance; assumes a clear path.

    Navigation modes:
    - Visual servoing (default): Uses 2D bounding box to center person in camera view
      and estimate distance based on bbox size.
    - 3D navigation (use_3d_navigation=True): Projects the detection to 3D using the
      global pointcloud from lidar, then navigates to the 3D position of the person.
    """

    color_image: In[Image]
    global_pointcloud: In[PointCloud2]
    cmd_vel: Out[Twist]

    _frequency: float = 20.0  # Hz - control loop frequency
    _max_lost_frames: int = 15  # number of frames to wait before declaring person lost

    # 3D navigation parameters
    _target_distance_3d: float = 1.5  # meters to maintain from person
    _min_distance_3d: float = 0.8  # meters before backing up
    _max_linear_speed_3d: float = 0.5  # m/s
    _max_angular_speed_3d: float = 0.8  # rad/s
    _linear_gain_3d: float = 0.8
    _angular_gain_3d: float = 1.5

    def __init__(
        self,
        camera_info: CameraInfo,
        global_config: GlobalConfig,
        use_3d_navigation: bool = False,
    ) -> None:
        super().__init__()
        self._global_config: GlobalConfig = global_config
        self._use_3d_navigation: bool = use_3d_navigation
        self._latest_image: Image | None = None
        self._latest_pointcloud: PointCloud2 | None = None
        self._vl_model: VlModel = QwenVlModel()
        self._tracker: EdgeTAMProcessor | None = None
        self._should_stop: Event = Event()
        self._lock = RLock()

        # Use MuJoCo camera intrinsics in simulation mode
        if self._global_config.simulation:
            from dimos.robot.unitree_webrtc.mujoco_connection import MujocoConnection

            camera_info = MujocoConnection.camera_info_static

        self._camera_info = camera_info
        self._visual_servo = VisualServoing2D(camera_info, self._global_config.simulation)

    @rpc
    def start(self) -> None:
        super().start()
        self._disposables.add(Disposable(self.color_image.subscribe(self._on_color_image)))
        if self._use_3d_navigation:
            self._disposables.add(Disposable(self.global_pointcloud.subscribe(self._on_pointcloud)))

    @rpc
    def stop(self) -> None:
        self._stop_following()

        with self._lock:
            if self._tracker is not None:
                self._tracker.stop()
                self._tracker = None

        self._vl_model.stop()
        super().stop()

    @skill()
    def follow_person(self, query: str) -> str:
        """Follow a person matching the given description using visual servoing.

        The robot will continuously track and follow the person, while keeping
        them centered in the camera view.

        Args:
            query: Description of the person to follow (e.g., "man with blue shirt")

        Returns:
            Status message indicating the result of the following action.

        Example:
            follow_person("man with blue shirt")
            follow_person("person in the doorway")
        """

        self._stop_following()

        self._should_stop.clear()

        with self._lock:
            latest_image = self._latest_image

        if latest_image is None:
            return "No image available to detect person."

        initial_bbox = get_object_bbox_from_image(
            self._vl_model,
            latest_image,
            query,
        )

        if initial_bbox is None:
            return f"Could not find '{query}' in the current view."

        return self._follow_loop(query, initial_bbox)

    @skill()
    def stop_following(self) -> str:
        """Stop following the current person.

        Returns:
            Confirmation message.
        """
        self._stop_following()

        self.cmd_vel.publish(Twist.zero())

        return "Stopped following."

    def _on_color_image(self, image: Image) -> None:
        with self._lock:
            self._latest_image = image

    def _on_pointcloud(self, pointcloud: PointCloud2) -> None:
        with self._lock:
            self._latest_pointcloud = pointcloud

    def _compute_twist_from_3d(self, target_position: Vector3) -> Twist:
        """Compute twist command to navigate towards a 3D target position.

        The target position is in world frame. We use the robot's base_link transform
        to determine relative position and compute appropriate velocities.

        Args:
            target_position: 3D position of the target in world frame.

        Returns:
            Twist command for the robot.
        """
        # Get robot's current position in world frame
        robot_transform = self.tf.get("world", "base_link", time_tolerance=1.0)
        if robot_transform is None:
            logger.warning("Could not get robot transform for 3D navigation")
            return Twist.zero()

        robot_pos = robot_transform.translation

        # Compute vector from robot to target in world frame
        dx = target_position.x - robot_pos.x
        dy = target_position.y - robot_pos.y
        distance = np.sqrt(dx * dx + dy * dy)

        # Compute angle to target in world frame
        angle_to_target = np.arctan2(dy, dx)

        # Get robot's current heading from transform
        robot_yaw = robot_transform.rotation.to_euler().z

        # Angle error (how much to turn)
        angle_error = angle_to_target - robot_yaw
        # Normalize to [-pi, pi]
        while angle_error > np.pi:
            angle_error -= 2 * np.pi
        while angle_error < -np.pi:
            angle_error += 2 * np.pi

        # Compute angular velocity (turn towards target)
        angular_z = angle_error * self._angular_gain_3d
        angular_z = float(
            np.clip(angular_z, -self._max_angular_speed_3d, self._max_angular_speed_3d)
        )

        # Compute linear velocity based on distance
        distance_error = distance - self._target_distance_3d

        if distance < self._min_distance_3d:
            # Too close, back up
            linear_x = -self._max_linear_speed_3d * 0.6
        else:
            # Move forward based on distance error, reduce speed when turning
            turn_factor = 1.0 - min(abs(angle_error) / np.pi, 0.7)
            linear_x = distance_error * self._linear_gain_3d * turn_factor
            linear_x = float(
                np.clip(linear_x, -self._max_linear_speed_3d, self._max_linear_speed_3d)
            )

        return Twist(
            linear=Vector3(linear_x, 0.0, 0.0),
            angular=Vector3(0.0, 0.0, angular_z),
        )

    def _compute_twist_for_detection_3d(self, detection: Detection2DBBox, image: Image) -> Twist:
        """Project a 2D detection to 3D using pointcloud and compute navigation twist.

        Args:
            detection: 2D detection with bounding box
            image: Current image frame

        Returns:
            Twist command to navigate towards the detection's 3D position.
        """
        with self._lock:
            pointcloud = self._latest_pointcloud

        if pointcloud is None:
            logger.warning(
                "No pointcloud available for 3D navigation, falling back to visual servo"
            )
            return self._visual_servo.compute_twist(detection.bbox, image.width)

        # Get transform from world frame to camera optical frame
        world_to_optical = self.tf.get(
            "camera_optical", pointcloud.frame_id, image.ts, time_tolerance=1.0
        )
        if world_to_optical is None:
            logger.warning("Could not get camera transform, falling back to visual servo")
            return self._visual_servo.compute_twist(detection.bbox, image.width)

        # Convert CameraInfo to LCM format for Detection3DPC.from_2d
        from dimos_lcm.sensor_msgs import CameraInfo as LCMCameraInfo

        lcm_camera_info = LCMCameraInfo()
        lcm_camera_info.K = self._camera_info.K
        lcm_camera_info.width = self._camera_info.width
        lcm_camera_info.height = self._camera_info.height

        # Project to 3D using the pointcloud
        detection_3d = Detection3DPC.from_2d(
            det=detection,
            world_pointcloud=pointcloud,
            camera_info=lcm_camera_info,
            world_to_optical_transform=world_to_optical,
            filters=[],  # Skip filtering for faster processing in follow loop
        )

        if detection_3d is None:
            logger.warning("3D projection failed, falling back to visual servo")
            return self._visual_servo.compute_twist(detection.bbox, image.width)

        # Navigate towards the 3D center of the detection
        target_position = detection_3d.center
        logger.debug(
            f"3D target position: ({target_position.x:.2f}, {target_position.y:.2f}, {target_position.z:.2f})"
        )

        return self._compute_twist_from_3d(target_position)

    def _follow_loop(self, query: str, initial_bbox: BBox) -> str:
        x1, y1, x2, y2 = initial_bbox
        box = np.array([x1, y1, x2, y2], dtype=np.float32)

        with self._lock:
            if self._tracker is None:
                self._tracker = EdgeTAMProcessor()
            tracker = self._tracker
            latest_image = self._latest_image
            if latest_image is None:
                return "No image available to start tracking."

        initial_detections = tracker.init_track(
            image=latest_image,
            box=box,
            obj_id=1,
        )

        if len(initial_detections) == 0:
            return f"EdgeTAM failed to segment '{query}'."

        logger.info(f"EdgeTAM initialized with {len(initial_detections)} detections")

        lost_count = 0
        period = 1.0 / self._frequency
        next_time = time.monotonic()

        while not self._should_stop.is_set():
            next_time += period

            with self._lock:
                latest_image = self._latest_image
                assert latest_image is not None

            detections = tracker.process_image(latest_image)

            if len(detections) == 0:
                self.cmd_vel.publish(Twist.zero())

                lost_count += 1
                if lost_count > self._max_lost_frames:
                    return f"Lost track of '{query}'. Stopping."
            else:
                lost_count = 0
                best_detection = max(detections.detections, key=lambda d: d.bbox_2d_volume())

                if self._use_3d_navigation:
                    twist = self._compute_twist_for_detection_3d(best_detection, latest_image)
                else:
                    twist = self._visual_servo.compute_twist(
                        best_detection.bbox,
                        latest_image.width,
                    )
                self.cmd_vel.publish(twist)

            now = time.monotonic()
            sleep_duration = next_time - now
            if sleep_duration > 0:
                time.sleep(sleep_duration)

        self.cmd_vel.publish(Twist.zero())

        return "Stopped following as requested."

    def _stop_following(self) -> None:
        self._should_stop.set()


person_follow_skill = PersonFollowSkillContainer.blueprint

__all__ = ["PersonFollowSkillContainer", "person_follow_skill"]
