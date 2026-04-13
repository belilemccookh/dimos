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
import logging
import os
from pathlib import Path
from typing import Any

from pydantic import field_validator
from reactivex.disposable import Disposable

from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.memory2.store.null import NullStore
from dimos.memory2.store.sqlite import SqliteStore
from dimos.memory2.stream import Stream

logger = logging.getLogger(__name__)


class StreamModule(Module):
    """Module base class that wires a memory2 stream pipeline.

    **Static pipeline**

        class VoxelGridMapper(StreamModule):
            pipeline = Stream().transform(VoxelMapTransformer())
            lidar: In[PointCloud2]
            global_map: Out[PointCloud2]

    **Config-driven pipeline**

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
        super().__init__(**kwargs)

    @rpc
    def start(self) -> None:
        super().start()

        if len(self.inputs) != 1 or len(self.outputs) != 1:
            raise TypeError(
                f"{self.__class__.__name__} must have exactly one In and one Out port, "
                f"found {len(self.inputs)} In and {len(self.outputs)} Out"
            )

        ((in_name, inp_port),) = self.inputs.items()
        ((_, out_port),) = self.outputs.items()

        store = self.register_disposable(NullStore())
        store.start()

        stream: Stream[Any] = store.stream(in_name, inp_port.type)

        # we push input into the stream
        inp_port.subscribe(lambda msg: stream.append(msg))

        live = stream.live()
        # and we push stream output to the output port
        self._apply_pipeline(live).subscribe(
            lambda obs: out_port.publish(obs.data),
        )

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
        if inspect.isfunction(pipeline):
            result = pipeline(self, stream)
            if not isinstance(result, Stream):
                raise TypeError(
                    f"{self.__class__.__name__}.pipeline() must return a Stream, got {type(result).__name__}"
                )
            return result

        # Static class attr: Stream (unbound chain) or Transformer
        if isinstance(pipeline, Stream):
            return stream.chain(pipeline)
        return stream.transform(pipeline)

    @rpc
    def stop(self) -> None:
        super().stop()


class RecorderConfig(ModuleConfig):
    """Config for the Recorder module."""

    db_path: str | Path = "recording.db"
    overwrite: bool = True

    @field_validator("db_path", mode="before")
    @classmethod
    def _resolve_path(cls, v: str | Path) -> Path:
        p = Path(os.fspath(v))
        if not p.is_absolute():
            from dimos.utils.data import get_project_root

            p = get_project_root() / p
        return p


class Recorder(Module):
    """Records all ``In`` ports to a memory2 SQLite database.

    Subclass with the topics you want to record::

        class MyRecorder(Recorder):
            color_image: In[Image]
            lidar: In[PointCloud2]

        blueprint.add(MyRecorder, db_path="session.db")
    """

    config: RecorderConfig

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._store: SqliteStore | None = None

    @rpc
    def start(self) -> None:
        super().start()

        db_path = Path(self.config.db_path)
        if db_path.exists():
            if self.config.overwrite:
                db_path.unlink()
                logger.info("Deleted existing recording %s", db_path)
            else:
                raise FileExistsError(f"Recording already exists: {db_path}")

        self._store = self.register_disposable(SqliteStore(path=str(db_path)))
        self._store.start()

        if not self.inputs:
            logger.warning("Recorder has no In ports — nothing to record, subclass the Recorder")
            return

        for name, port in self.inputs.items():
            stream: Stream[Any] = self._store.stream(name, port.type)
            self.register_disposable(
                Disposable(port.subscribe(lambda msg, s=stream: s.append(msg)))
            )
            logger.info("Recording %s (%s)", name, port.type.__name__)

    @rpc
    def stop(self) -> None:
        super().stop()
