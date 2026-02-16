#!/usr/bin/env python3
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

"""Minimal AGIbot navigation stack test blueprint.

This blueprint validates that we can correctly receive:
- Lidar data (/scan)
- Camera images (/camera/image_raw)
- Odometry (/odom)

And publish:
- Velocity commands (/cmd_vel)

Usage:
    dimos run agibot-nav-test

    # To run velocity test sequence:
    dimos run agibot-nav-test --set VelocityTester.test_enabled=true

Expected ROS topics (from AGIbot or nav stack):
    Subscribe:
        /scan                 - sensor_msgs/LaserScan
        /camera/image_raw     - sensor_msgs/Image
        /odom                 - nav_msgs/Odometry
        /cmd_vel              - geometry_msgs/Twist (echo mode)

    Publish:
        /cmd_vel              - geometry_msgs/Twist (test commands)

Validation checks:
    ✅ Lidar publishing at >10 Hz
    ✅ Camera publishing at >10 Hz
    ✅ Odometry publishing at >10 Hz
    ✅ Can publish velocity commands
    ✅ Odometry responds to velocity commands
"""

from dimos.core.blueprints import Blueprint
from dimos.robot.agibot.modules.ros_topic_monitor import ROSTopicMonitor
from dimos.robot.agibot.modules.velocity_tester import VelocityTester

agibot_nav_test = Blueprint(
    modules=[
        ROSTopicMonitor.blueprint(),
        VelocityTester.blueprint(),
    ],
    description="AGIbot navigation stack validation - ROS topic health check",
)

__all__ = ["agibot_nav_test"]
