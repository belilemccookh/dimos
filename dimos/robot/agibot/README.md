# AGIbot Navigation Integration

## Phase 1: ROS Topic Validation (Current)

Validates connectivity to the navigation Docker container running on the AGIbot.
Uses `ROSTransport` directly on module streams — no bridge modules needed.

### How It Works

`AGIbotNavValidator` is a single module with `In`/`Out` streams for each ROS topic.
The blueprint overrides their transports to `ROSTransport`, pointing at the nav
container's topic names. The module just subscribes and counts messages.

```python
agibot_nav_test = autoconnect(
    AGIbotNavValidator.blueprint(),
).transports({
    ("registered_scan", PointCloud2): ROSTransport("/registered_scan", PointCloud2),
    ("cmd_vel", TwistStamped): ROSTransport("/cmd_vel", TwistStamped),
    # ...
})
```

### Usage

```bash
dimos run agibot-nav-test
```

### ROS Topics

| Direction | Topic | Type | Description |
|-----------|-------|------|-------------|
| IN | `/registered_scan` | PointCloud2 | Lidar point cloud |
| IN | `/cmd_vel` | TwistStamped | Velocity commands |
| IN | `/terrain_map_ext` | PointCloud2 | Global terrain map |
| IN | `/path` | Path | Planned path |
| IN | `/tf` | TFMessage | Transforms |
| OUT | `/goal_pose` | PoseStamped | Navigation goal |

### Validation Checklist

- [ ] `/registered_scan` receiving data (lidar)
- [ ] `/cmd_vel` receiving data (velocity commands)
- [ ] `/terrain_map_ext` receiving data (global map)
- [ ] `/path` receiving data (planned path)
- [ ] `/tf` receiving data (transforms)
- [ ] `/goal_pose` publishing accepted by nav container

### Health Output

Every 5 seconds the validator logs:
```
AGIbot Nav Stack Health:
  ✅ /registered_scan: 47 msgs
  ✅ /cmd_vel: 120 msgs
  ✅ /terrain_map_ext: 5 msgs
  ❌ /path: 0 msgs        ← no path yet (no goal set)
  ✅ /tf: 235 msgs
```

## Phase 2: Navigation Integration

Add `ros_nav()` layer using `.transports()` to wire DimOS navigation modules
to the AGIbot's ROS topics. Same pattern — just transport overrides.

## Phase 3: Perception + Agentic

Add VLM perception and autonomous skills on top of the navigation stack.
