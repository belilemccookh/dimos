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

"""Rerun blueprint/layout helpers (dashboard-owned).

Goal: ensure the viewer has a sane default layout for the *actual* entity paths produced by
`rerun_viz()` (i.e. `streams/<Module>/<out>`), so users don't end up with a persisted/old
layout that points at different paths and shows empty panels.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping


def _pick_primary_image_paths(entity_paths: Iterable[str]) -> list[str]:
    """Pick a small set of image-like entity paths for dedicated 2D panels."""
    paths = list(entity_paths)
    # Prefer common names first.
    preferred = []
    for key in ("camera/image", "camera", "color_image", "rgb", "image", "debug"):
        for p in paths:
            if key in p:
                preferred.append(p)
    # Deduplicate preserving order.
    out: list[str] = []
    for p in preferred:
        if p not in out:
            out.append(p)
    return out[:2]


def build_default_rerun_blueprint(
    *,
    entity_prefix: str,
    image_entity_paths: Iterable[str],
    metrics_by_module: Mapping[str, Mapping[str, str]] | None = None,
):
    """Build a simple default Rerun blueprint for DimOS streams.

    This uses only the high-level view types and `origin=...` so it stays resilient across
    Rerun versions.
    """
    import rerun.blueprint as rrb  # type: ignore[import-not-found]  # dashboard-owned dependency

    images = _pick_primary_image_paths(image_entity_paths)

    # Metrics views: split by module, and within each module split by group.
    metrics_tabs = []
    if metrics_by_module:
        for module_name, groups in sorted(metrics_by_module.items()):
            views = []
            if "time_ms" in groups:
                views.append(rrb.TimeSeriesView(origin=groups["time_ms"], name="time (ms)"))
            if "count" in groups:
                views.append(rrb.TimeSeriesView(origin=groups["count"], name="count"))
            if "scalar" in groups:
                views.append(rrb.TimeSeriesView(origin=groups["scalar"], name="scalar"))
            if views:
                metrics_tabs.append(rrb.Vertical(*views, name=module_name))

    metrics_panel: object
    if metrics_tabs:
        metrics_panel = rrb.Tabs(*metrics_tabs, name="metrics")
    else:
        metrics_panel = rrb.TimeSeriesView(origin=entity_prefix, name="metrics")

    right_col_children = [metrics_panel]
    for p in images:
        right_col_children.append(rrb.Spatial2DView(origin=p, name=p.split("/")[-1]))

    # Use a named-frame anchor entity path as origin so we don't end up with `tf#/` as the view origin frame.
    # The `map` entity will be logged as a CoordinateFrame("map") by the dashboard pre-start hook.
    layout = rrb.Horizontal(
        # Important: contents defaults to "$origin/**".
        # If we set origin="map" without overriding contents, the view would only include "map/**",
        # hiding all actual data logged under e.g. "/streams/**" and "/tf/**".
        rrb.Spatial3DView(
            origin="map",
            contents="/**",
            name="3D",
            background=[0, 0, 0],  # Black background
        ),
        rrb.Vertical(*right_col_children),
    )

    return rrb.Blueprint(layout, collapse_panels=True)


def send_default_blueprint(
    *,
    entity_prefix: str,
    image_entity_paths: Iterable[str],
    metrics_by_module: Mapping[str, Mapping[str, str]] | None = None,
) -> None:
    """Send the default blueprint to the connected Rerun viewer."""
    import rerun as rr  # type: ignore[import-not-found]  # dashboard-owned dependency

    bp = build_default_rerun_blueprint(
        entity_prefix=entity_prefix,
        image_entity_paths=image_entity_paths,
        metrics_by_module=metrics_by_module,
    )
    rr.send_blueprint(bp)


