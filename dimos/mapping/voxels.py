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

import time

import open3d as o3d  # type: ignore[import-untyped]
import open3d.core as o3c  # type: ignore[import-untyped]

from dimos.core.module import ModuleConfig
from dimos.core.stream import In, Out
from dimos.memory2.module import StreamModule
from dimos.memory2.stream import Stream
from dimos.memory2.voxel_map import VoxelMap
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.utils.decorators.decorators import simple_mcache
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class VoxelGrid:
    """Pure voxel grid accumulator using Open3D VoxelBlockGrid.

    No Module/framework dependency. Can be used standalone or wrapped
    by VoxelGridMapper (Module) or VoxelMap (memory2 Transformer).
    """

    def __init__(
        self,
        voxel_size: float = 0.05,
        block_count: int = 2_000_000,
        device: str = "CUDA:0",
        carve_columns: bool = True,
        frame_id: str = "world",
    ) -> None:
        self.voxel_size = voxel_size
        self.carve_columns = carve_columns
        self.frame_id = frame_id

        dev = (
            o3c.Device(device)
            if (device.startswith("CUDA") and o3c.cuda.is_available())
            else o3c.Device("CPU:0")
        )

        logger.info(f"VoxelGrid using device: {dev}")

        self.vbg = o3d.t.geometry.VoxelBlockGrid(
            attr_names=("dummy",),
            attr_dtypes=(o3c.uint8,),
            attr_channels=(o3c.SizeVector([1]),),
            voxel_size=voxel_size,
            block_resolution=1,
            block_count=block_count,
            device=dev,
        )

        self._dev = dev
        self._voxel_hashmap = self.vbg.hashmap()
        self._key_dtype = self._voxel_hashmap.key_tensor().dtype
        self._latest_frame_ts: float = 0.0

    def add_frame(self, frame: PointCloud2) -> None:
        if frame.ts is not None:
            self._latest_frame_ts = frame.ts

        pcd = ensure_tensor_pcd(frame.pointcloud, self._dev)

        if pcd.is_empty():
            return

        pts = pcd.point["positions"].to(self._dev, o3c.float32)
        vox = (pts / self.voxel_size).floor().to(self._key_dtype)
        keys_Nx3 = vox.contiguous()

        if self.carve_columns:
            self._carve_and_insert(keys_Nx3)
        else:
            self._voxel_hashmap.activate(keys_Nx3)

        self.get_global_pointcloud.invalidate_cache(self)  # type: ignore[attr-defined]
        self.get_global_pointcloud2.invalidate_cache(self)  # type: ignore[attr-defined]

    def _carve_and_insert(self, new_keys: o3c.Tensor) -> None:
        """Column carving: remove all existing voxels sharing (X,Y) with new_keys, then insert."""
        if new_keys.shape[0] == 0:
            self._voxel_hashmap.activate(new_keys)
            return

        xy_keys = new_keys[:, :2].contiguous()

        xy_hashmap = o3c.HashMap(
            init_capacity=xy_keys.shape[0],
            key_dtype=self._key_dtype,
            key_element_shape=o3c.SizeVector([2]),
            value_dtypes=[o3c.uint8],
            value_element_shapes=[o3c.SizeVector([1])],
            device=self._dev,
        )
        dummy_vals = o3c.Tensor.zeros((xy_keys.shape[0], 1), o3c.uint8, self._dev)
        xy_hashmap.insert(xy_keys, dummy_vals)

        active_indices = self._voxel_hashmap.active_buf_indices()
        if active_indices.shape[0] == 0:
            self._voxel_hashmap.activate(new_keys)
            return

        existing_keys = self._voxel_hashmap.key_tensor()[active_indices]
        existing_xy = existing_keys[:, :2].contiguous()

        _, found_mask = xy_hashmap.find(existing_xy)

        to_erase = existing_keys[found_mask]
        if to_erase.shape[0] > 0:
            self._voxel_hashmap.erase(to_erase)

        self._voxel_hashmap.activate(new_keys)

    @simple_mcache
    def get_global_pointcloud2(self) -> PointCloud2:
        return PointCloud2(
            ensure_legacy_pcd(self.get_global_pointcloud()),
            frame_id=self.frame_id,
            ts=self._latest_frame_ts if self._latest_frame_ts else time.time(),
        )

    @simple_mcache
    def get_global_pointcloud(self) -> o3d.t.geometry.PointCloud:
        voxel_coords, _ = self.vbg.voxel_coordinates_and_flattened_indices()
        pts = voxel_coords + (self.voxel_size * 0.5)
        out = o3d.t.geometry.PointCloud(device=self._dev)
        out.point["positions"] = pts
        return out

    def size(self) -> int:
        return self._voxel_hashmap.size()  # type: ignore[no-any-return]

    def __len__(self) -> int:
        return self.size()

    def dispose(self) -> None:
        """Free GPU resources. The object is unusable after this call."""
        self.get_global_pointcloud.invalidate_cache(self)  # type: ignore[attr-defined]
        self.get_global_pointcloud2.invalidate_cache(self)  # type: ignore[attr-defined]
        self.vbg = None  # type: ignore[assignment]
        self._voxel_hashmap = None  # type: ignore[assignment]


class VoxelGridMapperConfig(ModuleConfig):
    """Configuration for VoxelGridMapper."""

    voxel_size: float = 0.05
    block_count: int = 2_000_000
    device: str = "CUDA:0"
    carve_columns: bool = True
    frame_id: str = "world"


class VoxelGridMapper(StreamModule[VoxelGridMapperConfig]):
    """Accumulate lidar point clouds into a global voxel map."""

    default_config = VoxelGridMapperConfig

    def pipeline(self, stream: Stream[PointCloud2]) -> Stream[PointCloud2]:
        return stream.transform(
            VoxelMap(
                voxel_size=self.config.voxel_size,
                block_count=self.config.block_count,
                device=self.config.device,
                carve_columns=self.config.carve_columns,
                frame_id=self.config.frame_id,
            )
        )

    lidar: In[PointCloud2]
    global_map: Out[PointCloud2]


def ensure_tensor_pcd(
    pcd_any: o3d.t.geometry.PointCloud | o3d.geometry.PointCloud,
    device: o3c.Device,
) -> o3d.t.geometry.PointCloud:
    """Convert legacy / cuda.pybind point clouds into o3d.t.geometry.PointCloud on `device`."""

    if isinstance(pcd_any, o3d.t.geometry.PointCloud):
        return pcd_any.to(device)

    assert isinstance(pcd_any, o3d.geometry.PointCloud), (
        "Input must be a legacy PointCloud or a tensor PointCloud"
    )

    return o3d.t.geometry.PointCloud.from_legacy(pcd_any, o3c.float32, device)


def ensure_legacy_pcd(
    pcd_any: o3d.t.geometry.PointCloud | o3d.geometry.PointCloud,
) -> o3d.geometry.PointCloud:
    if isinstance(pcd_any, o3d.geometry.PointCloud):
        return pcd_any

    assert isinstance(pcd_any, o3d.t.geometry.PointCloud), (
        "Input must be a legacy PointCloud or a tensor PointCloud"
    )

    return pcd_any.to_legacy()
