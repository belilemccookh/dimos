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

"""Dashboard helpers.

`rerun_viz()` enables Rerun visualization by installing `Out.tap()` callbacks for every
`Out[...]` declared in the blueprint (no producer-side rerun imports).
"""

from __future__ import annotations

from dimos.core.blueprints import ModuleBlueprintSet
from dimos.core.global_config import GlobalConfig
from dimos.dashboard.rerun_init import init_rerun_if_enabled

__all__ = ["init_rerun_if_enabled", "rerun_viz"]

_blueprint_sent = False


def rerun_viz(
    *,
    entity_prefix: str = "streams",
    rate_limit_hz: float | None = None,
    send_blueprint: bool = True,
    voxel_colormap: str = "turbo",
    voxel_box_size: float | None = None,
    tf_entity_prefix: str = "tf",
) -> ModuleBlueprintSet:
    """Enable Rerun visualization for all `Out[...]` streams in a blueprint.

    Entity path scheme (default):
        streams/<ModuleClass>/<out_name>

    Notes:
    - Only messages that implement `to_rerun()` will produce logs.
    - This installs taps pre-transport, inside the producer worker process.
    - Optionally sends a default Rerun blueprint so panels are not empty due to stale layouts.
    """

    def _install_all_taps(
        blueprint_set: ModuleBlueprintSet,
        coordinator,
        global_config: GlobalConfig,
    ) -> None:  # type: ignore[no-untyped-def]
        global _blueprint_sent

        if not global_config.rerun_enabled:
            return
        if not global_config.viewer_backend.startswith("rerun"):
            return

        factory = "dimos.dashboard.rerun_tap:make_rerun_tap"
        tf_factory = "dimos.dashboard.rerun_tf_tap:make_rerun_tf_tap"
        server_addr = global_config.rerun_server_addr
        recording_id = global_config.rerun_recording_id

        # Discover image-like outputs so we can provide a useful default 2D view.
        image_paths: list[str] = []
        metrics_by_module: dict[str, dict[str, str]] = {}

        # Iterate declared blueprint outputs and install taps on the owning modules.
        for bp in blueprint_set.blueprints:
            inst = coordinator.get_instance(bp.module)
            if inst is None:
                continue

            # If a module publishes both camera intrinsics (CameraInfo) and an Image stream,
            # log them onto a single entity path so Rerun can associate the image with the pinhole/frustum.
            out_conns = [c for c in bp.connections if c.direction == "out"]
            has_caminfo = any(
                c.name == "camera_info"
                or getattr(c.type, "msg_name", "") == "sensor_msgs.CameraInfo"
                for c in out_conns
            )
            has_image = any(
                c.name in ("color_image", "image")
                and getattr(c.type, "msg_name", "") == "sensor_msgs.Image"
                for c in out_conns
            )
            camera_entity_path = (
                f"{entity_prefix}/{bp.module.__name__}/camera" if (has_caminfo and has_image) else None
            )
            camera_image_entity_path = camera_entity_path  # log both pinhole + image on same entity
            forced_camera_frame_id = None
            camera_model_static = None
            if camera_entity_path is not None:
                cis = getattr(bp.module, "camera_info_static", None)
                if cis is not None:
                    fid = getattr(cis, "frame_id", None)
                    if isinstance(fid, str) and fid:
                        forced_camera_frame_id = fid
                    # Extract intrinsics for robust frustum logging on image tap
                    try:
                        camera_model_static = {
                            "width": cis.width,
                            "height": cis.height,
                            "K": cis.K,
                        }
                    except AttributeError:
                        pass

            # Install TF tap on every module (best-effort). Modules that never publish TF pay ~0 overhead.
            try:
                inst.install_tf_tap(
                    tf_factory,
                    {
                        "server_addr": server_addr,
                        "recording_id": recording_id,
                        "entity_prefix": tf_entity_prefix,
                    },
                )
            except Exception:
                pass

            for conn in bp.connections:
                if conn.direction != "out":
                    continue

                # Metrics: group by module and metric type in the entity tree.
                msg_name = getattr(conn.type, "msg_name", "")
                is_float32 = msg_name == "std_msgs.Float32" or conn.type.__name__ == "Float32"
                if is_float32:
                    if conn.name.endswith("_ms") or conn.name.endswith("_latency_ms"):
                        grp = "time_ms"
                    elif conn.name.endswith("_count"):
                        grp = "count"
                    else:
                        grp = "scalar"
                    entity_path = f"{entity_prefix}/{bp.module.__name__}/metrics/{grp}/{conn.name}"
                    metrics_by_module.setdefault(bp.module.__name__, {})[grp] = (
                        f"{entity_prefix}/{bp.module.__name__}/metrics/{grp}"
                    )
                else:
                    entity_path = f"{entity_prefix}/{bp.module.__name__}/{conn.name}"

                # Camera pairing:
                # Log both CameraInfo(Pinhole) and Image on the same entity path so the 3D view
                # can texture the frustum (this matches Rerun's own examples).
                if camera_entity_path is not None:
                    msg_name_local = getattr(conn.type, "msg_name", "")
                    if conn.name == "camera_info" and msg_name_local == "sensor_msgs.CameraInfo":
                        entity_path = camera_entity_path
                    elif conn.name == "color_image" and msg_name_local == "sensor_msgs.Image":
                        entity_path = camera_entity_path

                # Per-stream to_rerun kwargs (UI policy).
                to_rerun_kwargs = None
                also_log_to = None
                # Special-case voxelized map (PointCloud2 used as voxels): ensure turbo + correct box size.
                if bp.module.__name__ == "VoxelGridMapper" and conn.name == "global_map":
                    size = bp.kwargs.get("voxel_size", voxel_box_size)
                    if isinstance(size, (int, float)) and size > 0:
                        to_rerun_kwargs = {
                            "mode": "boxes",
                            "size": float(size),
                            "colormap": voxel_colormap,
                            "fill_mode": "solid",
                        }

                # Camera frustum/image robustness:
                # We log pinhole + image on the same entity, so no duplication needed.
                if camera_entity_path is not None and camera_image_entity_path is not None:
                    msg_name_local = getattr(conn.type, "msg_name", "")
                    if entity_path == camera_entity_path and msg_name_local == "sensor_msgs.CameraInfo":
                        also_log_to = None

                # Prefer "latest-only" logging for high-bandwidth streams instead of rate limiting:
                # log as static so rerun doesn't keep historical frames.
                stream_rate_limit = rate_limit_hz
                static_log = False
                if msg_name in ("sensor_msgs.Image", "sensor_msgs.PointCloud2"):
                    static_log = True
                    stream_rate_limit = rate_limit_hz  # user may still opt-in to rate limiting

                inst.install_out_tap(
                    conn.name,
                    factory,
                    {
                        "entity_path": entity_path,
                        "server_addr": server_addr,
                        "recording_id": recording_id,
                        "rate_limit_hz": stream_rate_limit,
                        "to_rerun_kwargs": to_rerun_kwargs,
                        "static": static_log,
                        # For camera streams, force the image + pinhole to share a named TF frame
                        # (CoordinateFrame does not inherit down the entity tree).
                        "force_frame_id": (
                            forced_camera_frame_id
                            if (
                                forced_camera_frame_id is not None
                                and camera_entity_path is not None
                                and (
                                    entity_path == camera_entity_path
                                    or entity_path.startswith(f"{camera_entity_path}/")
                                )
                            )
                            else None
                        ),
                        "also_log_to": also_log_to,
                        "camera_model": (
                            camera_model_static
                            if (
                                camera_model_static is not None
                                and camera_entity_path is not None
                                and (
                                    entity_path == camera_entity_path
                                    or entity_path.startswith(f"{camera_entity_path}/")
                                )
                            )
                            else None
                        ),
                    },
                )
                # Heuristic: treat sensor_msgs.Image-like types as candidates for a 2D panel.
                if msg_name == "sensor_msgs.Image" or conn.type.__name__ == "Image":
                    if entity_path not in image_paths:
                        image_paths.append(entity_path)

        if send_blueprint and not _blueprint_sent:
            # Send from the main process (this hook runs in the build() process).
            try:
                from dimos.dashboard.rerun_blueprint import send_default_blueprint
                from dimos.dashboard.rerun_init import connect_rerun
                import rerun as rr  # type: ignore[import-not-found]

                connect_rerun(server_addr=server_addr, recording_id=recording_id)
                # Define "map" as the root frame for the TF tree.
                # This is the origin frame for the 3D view.
                # Other frames (world, base_link, camera_optical, etc.) will be children of this.
                rr.log(
                    "map",
                    rr.Transform3D(
                        translation=[0, 0, 0],
                        rotation=rr.Quaternion(xyzw=[0, 0, 0, 1]),
                        child_frame="map",
                    ),
                    static=True,
                )
                send_default_blueprint(
                    entity_prefix=entity_prefix,
                    image_entity_paths=image_paths,
                    metrics_by_module=metrics_by_module,
                )
                _blueprint_sent = True
            except Exception:
                # Never fail the run due to optional UI policy.
                _blueprint_sent = True

    # Empty blueprint set: contributes only the hook to the final merged build().
    return ModuleBlueprintSet(blueprints=()).pre_start(_install_all_taps)
