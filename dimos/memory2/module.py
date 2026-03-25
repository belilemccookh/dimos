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

from __future__ import annotations

import inspect
from typing import Any, get_args, get_origin, get_type_hints

from reactivex.disposable import Disposable

from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfigT
from dimos.core.stream import In, Out
from dimos.memory2.stream import Stream


class StreamModule(Module[ModuleConfigT]):
    """Module base class that wires a memory2 stream pipeline.

    **Static pipeline** (class attribute)::

        class VoxelGridMapper(StreamModule):
            pipeline = Stream().transform(VoxelMap())
            lidar: In[PointCloud2]
            global_map: Out[PointCloud2]

    **Config-driven pipeline** (method with access to ``self.config``)::

        class VoxelGridMapper(StreamModule[VoxelGridMapperConfig]):
            def pipeline(self, stream: Stream) -> Stream:
                return stream.transform(VoxelMap(**self.config.model_dump()))

            lidar: In[PointCloud2]
            global_map: Out[PointCloud2]

    On start, the single ``In`` port feeds a MemoryStore, and the pipeline
    is applied to the live stream, publishing results to the single ``Out`` port.

    The MemoryStore acts as a bridge between the push-based Module In port
    and the pull-based memory2 stream pipeline — it also enables replay and
    persistence if the store is swapped for a persistent backend later.
    """

    def __init__(self, **kwargs: Any) -> None:
        from dimos.memory2.store.memory import MemoryStore

        super().__init__(**kwargs)
        self._store = MemoryStore()

    @rpc
    def start(self) -> None:
        super().start()
        self._store.start()

        in_name, in_type, out_name = self._resolve_ports()

        stream: Stream[Any] = self._store.stream(in_name, in_type)
        inp_port = getattr(self, in_name)
        out_port = getattr(self, out_name)

        unsub = inp_port.subscribe(lambda msg: stream.append(msg))
        self._disposables.add(Disposable(unsub))

        live = stream.live()
        bound = self._apply_pipeline(live)
        self._disposables.add(bound.publish(out_port))

    def _apply_pipeline(self, stream: Stream[Any]) -> Stream[Any]:
        """Apply the pipeline to a live stream.

        Handles both static (class attr) and dynamic (method) pipelines.
        """
        pipeline = getattr(self.__class__, "pipeline", None)
        if pipeline is None:
            raise TypeError(
                f"{self.__class__.__name__} must define a 'pipeline' attribute or method"
            )

        # Method pipeline: self.pipeline(stream) -> stream
        if inspect.isfunction(pipeline) or inspect.ismethod(pipeline):
            result: Stream[Any] = pipeline(self, stream)
            return result

        # Static class attr: Stream (unbound chain) or Transformer
        if isinstance(pipeline, Stream):
            return stream.chain(pipeline)
        return stream.transform(pipeline)

    @rpc
    def stop(self) -> None:
        super().stop()
        self._store.stop()

    def _resolve_ports(self) -> tuple[str, type, str]:
        """Find the single In and single Out port from type annotations."""
        hints = get_type_hints(self.__class__, include_extras=True)
        in_ports: list[tuple[str, type]] = []
        out_ports: list[str] = []
        for name, ann in hints.items():
            origin = get_origin(ann)
            if origin is In:
                in_ports.append((name, get_args(ann)[0]))
            elif origin is Out:
                out_ports.append(name)
        if len(in_ports) != 1 or len(out_ports) != 1:
            raise TypeError(
                f"{self.__class__.__name__} must declare exactly one In[T] and one Out[T] port, "
                f"found {len(in_ports)} In and {len(out_ports)} Out"
            )
        in_name, in_type = in_ports[0]
        return in_name, in_type, out_ports[0]
