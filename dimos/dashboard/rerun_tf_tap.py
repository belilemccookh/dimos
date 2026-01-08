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

"""Rerun TF tap factory (dashboard-owned).

Installed on worker processes via `Module.install_tf_tap(...)`.
"""

from __future__ import annotations

import importlib
from typing import Callable

from dimos.dashboard.rerun_init import connect_rerun
from dimos.msgs.tf2_msgs import TFMessage


def make_rerun_tf_tap(
    *,
    server_addr: str | None = None,
    recording_id: str | None = None,
    entity_prefix: str = "tf",
    axes_length: float = 0.15,
    show_axes: bool = True,
) -> Callable[[TFMessage], None]:
    """Create a TF tap that logs transforms into Rerun using named transform frames."""
    seen_frames: set[str] = set()

    def _cb(msg: TFMessage) -> None:
        connect_rerun(server_addr=server_addr, recording_id=recording_id)
        rr = importlib.import_module("rerun")

        # Log each transform on a stable per-child entity path so transforms don't overwrite each other.
        for t in msg.transforms:
            # Ensure frame ids are strings (defensive)
            child = getattr(t, "child_frame_id", None)
            if not isinstance(child, str) or not child:
                continue
            parent = getattr(t, "frame_id", None)
            if not isinstance(parent, str) or not parent:
                parent = "world"

            # Log directly to the named child frame - no entity path indirection.
            # Transform3D with parent_frame and child_frame defines named-frame-to-named-frame relationships.
            # This is independent of entity paths and avoids implicit tf#/... frames.
            if child not in seen_frames:
                if show_axes:
                    try:
                        rr.log(child, rr.TransformAxes3D(axes_length), static=True)
                    except Exception:
                        pass
                seen_frames.add(child)
            rr.log(
                child,
                rr.Transform3D(
                    translation=[t.translation.x, t.translation.y, t.translation.z],
                    rotation=rr.Quaternion(xyzw=[t.rotation.x, t.rotation.y, t.rotation.z, t.rotation.w]),
                    parent_frame=parent,
                    child_frame=child,
                ),
                static=True,
            )

    return _cb


