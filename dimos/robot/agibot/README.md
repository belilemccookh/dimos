# AGIbot Integration for DimOS

**Linear Issue**: [DIM-530](https://linear.app/dimensional/issue/DIM-530/test-nav-stack-on-agibot)
**Branch**: `stash/dim-530-test-nav-stack-on-agibot`

## Overview

This integration enables AGIbot robots to run the DimOS navigation stack. The implementation follows the same modular architecture as the Unitree G1 integration.

## Architecture

### Phase 1: Navigation Validation (Current)
**Goal**: Verify ROS topic connectivity for lidar, camera, and velocity control.

**Blueprint**: `agibot_nav_test`
**Modules**:
- `ROSTopicMonitor` - Health monitoring for ROS topics
- `VelocityTester` - Velocity command testing and odom validation

### Phase 2: Full Navigation Stack (Future)
**Goal**: Add `ros_nav()` layer for autonomous navigation.

**Blueprint**: `agibot_basic`
**Extends**: Phase 1 + `ros_nav()` + AGIbot SDK connection

### Phase 3: Perception & Agentic (Future)
**Blueprint**: `agibot_agentic`
**Extends**: Phase 2 + VLM perception + autonomous skills

---

## Quick Start

### Prerequisites
1. AGIbot robot with ROS topics active:
   - `/scan` (lidar)
   - `/camera/image_raw` (camera)
   - `/odom` (odometry)
   - `/cmd_vel` (velocity commands)

2. DimOS navigation ARM docker image running:
   ```bash
   docker run -it --network host \
     ghcr.io/dimensionalos/navigation:latest
   ```

### Run Validation Test

```bash
# 1. Passive monitoring (no velocity commands)
dimos run agibot-nav-test

# 2. Active test with velocity sequence
dimos run agibot-nav-test --set VelocityTester.test_enabled=true
```

---

## Validation Checklist

### ✅ Pass Criteria
- [ ] **Lidar**: `/scan` publishing at >10 Hz with valid ranges
- [ ] **Camera**: `/camera/image_raw` publishing at >10 Hz with non-empty frames
- [ ] **Odometry**: `/odom` publishing at >10 Hz
- [ ] **Velocity Publish**: Can publish to `/cmd_vel` without errors
- [ ] **Velocity Response**: Odometry changes when velocity commands sent

### 📊 Expected Output
```
INFO  ROSTopicMonitor | === ROS Topic Health Report ===
INFO  ROSTopicMonitor | ✅ OK /scan                  | Count:    456 | Rate:  15.23 Hz | Latency:    12.3 ms
INFO  ROSTopicMonitor | ✅ OK /camera/image_raw      | Count:    789 | Rate:  25.67 Hz | Latency:     8.1 ms
INFO  ROSTopicMonitor | ✅ OK /odom                  | Count:   1234 | Rate:  41.22 Hz | Latency:     4.5 ms
INFO  ROSTopicMonitor | ✅ OK /cmd_vel               | Count:     12 | Rate:   0.40 Hz | Latency:   250.0 ms
```

### 🚨 Failure Modes
- **❌ NO DATA**: Topic not publishing → Check navigation stack running
- **⚠️ WARN**: Low rate or high latency → Check network/CPU load
- **Lidar quality low**: Check physical lidar connection
- **Odometry not responding**: Check `/cmd_vel` → robot controller connection

---

## ROS Topic Specification

| Topic | Type | Hz | Direction | Purpose |
|-------|------|----|-----------| --------|
| `/scan` | `sensor_msgs/LaserScan` | 10-20 | Subscribe | Lidar point cloud for navigation |
| `/camera/image_raw` | `sensor_msgs/Image` | 10-30 | Subscribe | Camera feed for perception |
| `/odom` | `nav_msgs/Odometry` | 30-50 | Subscribe | Robot position feedback |
| `/cmd_vel` | `geometry_msgs/Twist` | varies | Publish | Velocity commands to robot |

---

## Troubleshooting

### No lidar data
```bash
# Check if topic exists
ros2 topic list | grep scan

# Echo topic
ros2 topic echo /scan --once

# Check rate
ros2 topic hz /scan
```

### Camera issues
```bash
# Check image topic
ros2 topic list | grep camera

# View camera feed (requires GUI)
ros2 run rqt_image_view rqt_image_view
```

### Velocity not working
```bash
# Publish manual test command
ros2 topic pub /cmd_vel geometry_msgs/Twist \
  "{linear: {x: 0.1}, angular: {z: 0.0}}" --once

# Check if robot SDK is listening to /cmd_vel
ros2 topic info /cmd_vel
```

---

## Next Steps

After Phase 1 validation passes:

1. **Add AGIbot SDK connection** - Interface to robot hardware
2. **Integrate `ros_nav()`** - Full navigation stack (SLAM, planning, control)
3. **Test autonomous navigation** - Waypoint following, obstacle avoidance
4. **Add perception modules** - VLM, object detection, scene understanding
5. **Enable agentic skills** - Autonomous patrol, exploration, task execution

---

## Reference Implementation

See **Unitree G1** integration for reference architecture:
- `dimos/robot/unitree/g1/blueprints/basic/unitree_g1_basic.py`
- `dimos/robot/unitree/g1/connection.py`
- `dimos/navigation/rosnav/`

The AGIbot integration mirrors this structure:
```
G1 Architecture:              AGIbot Architecture:
├── primitive (sensors)       ├── test (validation) ← Current phase
├── connection (SDK)          ├── connection (TODO)
├── ros_nav() (navigation)    ├── basic (TODO: + ros_nav)
├── perception                └── agentic (TODO: + VLM/skills)
└── agentic
```

---

## Contributing

When adding AGIbot-specific features:
1. Follow G1 integration patterns
2. Use modular blueprints (composition via `autoconnect`)
3. Document ROS topic contracts
4. Add validation tests
5. Update this README

---

**Status**: ✅ Phase 1 complete - ready for hardware testing
**Next**: Phase 2 - Add `ros_nav()` integration after validation
