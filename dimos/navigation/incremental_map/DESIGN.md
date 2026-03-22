# Incremental Global Map: Design Document

## Problem

The existing global mapping pipeline uses PGO (Pose Graph Optimization) with GTSAM iSAM2,
which has a heavy dependency footprint (GTSAM Python bindings, scipy ICP, keyframe management).
We need a lighter-weight alternative that builds a global map incrementally without full PGO
while still detecting and correcting loop closures.

## Approach: Voxel-Based Incremental Map with Distance-Based Loop Closure

### Algorithm Summary

**Scan-to-Map Incremental Fusion** with **Position-History Loop Closure Detection**:

1. **Incremental Map Accumulation**:
   - Maintain a voxel hash map keyed by (x//voxel_size, y//voxel_size, z//voxel_size)
   - On each new scan: transform body-frame lidar to world-frame using current odometry
   - Insert/update voxels in the global map
   - Publish the accumulated map at a configurable rate

2. **Keyframe Selection**:
   - Accept a new keyframe when the robot has moved > `key_trans` meters OR rotated > `key_deg` degrees
   - Store keyframe pose (position + orientation) + downsampled body-frame scan

3. **Loop Closure Detection** (lightweight, no ICP required for detection):
   - Track all keyframe positions in a KD-tree
   - When a new keyframe is added, query the KD-tree for nearby past poses
   - A loop candidate is valid if:
     a. Euclidean distance < `loop_search_radius`
     b. Time gap > `loop_time_thresh` seconds (prevents matching recent poses)
   - Verify with lightweight ICP between the current scan and the candidate submap
   - Accept if ICP fitness score < `loop_score_thresh`

4. **Map Correction on Loop Closure**:
   - When a loop is detected, compute the rigid body correction (rotation + translation)
   - Apply correction by:
     a. Recomputing all keyframe world poses with the offset applied
     b. Rebuilding the voxel map from corrected keyframe scans
   - This is O(N·K) in keyframes × scan points, but only happens on loop closure events

### Why This Approach

| Property | PGO (existing) | Incremental Map (new) |
|----------|---------------|----------------------|
| Dependencies | GTSAM, scipy | numpy, scipy (KDTree only) |
| Loop closure | iSAM2 global optimization | Pose correction + map rebuild |
| Memory | Keyframe poses + scans | Voxel map + keyframe history |
| Complexity | High (pose graph, ISAM2) | Medium (KDTree + voxel hash) |
| Loop detection | ICP + GTSAM factors | Distance threshold + optional ICP verify |
| Map type | PointCloud2 | PointCloud2 or OccupancyGrid |
| Native port | Difficult (GTSAM needed) | Easy (pure array/KDTree ops → C++) |

### Alternatives Considered

1. **Voxblox / OpenVDB**: Full TSDF/ESDF pipeline — too heavy, requires C++ integration
2. **NDT (Normal Distributions Transform)**: Better scan matching but needs NDT voxel grids
3. **Surfel-based mapping**: High-fidelity but complex normal estimation required
4. **GMapping-style occupancy SLAM**: Good but requires particle filter, harder to correct globally
5. **Sliding window ICP only**: No global loop closure capability

### Interface

```
Inputs:
  odom: In[Odometry]          — robot pose from FastLIO2 or kinematic sim
  registered_scan: In[PointCloud2]  — world-frame lidar

Outputs:
  global_map: Out[PointCloud2]      — accumulated voxel map
  corrected_odom: Out[Odometry]     — pose after loop closure correction
```

### Loop Closure Detection in Integration Test

For the e2e test, we drive a square trajectory:
```
Start (0,0) → (5,0) → (5,5) → (0,5) → (0,0) [return to start]
```

With `loop_search_radius=3.0m` and `loop_time_thresh=5.0s`, when the robot returns
within 3m of its starting position after 5+ seconds, loop closure is triggered.
The map is then rebuilt from corrected keyframe poses.

**Geometric Consistency Check**:
- After correction, points from the initial scan should be within `correction_threshold`
  of corresponding points from the final scan at the same location.
- Corrected odometry drift should be less than raw odometry drift.
