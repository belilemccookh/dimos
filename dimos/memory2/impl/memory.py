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

from typing import Any

from dimos.memory2.backend import Backend
from dimos.memory2.codecs.base import Codec, codec_for
from dimos.memory2.observationstore.memory import ListObservationStore
from dimos.memory2.store import Store


class MemoryStore(Store):
    """In-memory store for experimentation."""

    def _create_backend(
        self, name: str, payload_type: type[Any] | None = None, **config: Any
    ) -> Backend[Any]:
        metadata_store: ListObservationStore[Any] = ListObservationStore(name)

        # Resolve codec
        raw_codec = config.pop("codec", None)
        codec: Codec[Any]
        if isinstance(raw_codec, Codec):
            codec = raw_codec
        elif isinstance(raw_codec, str):
            from dimos.memory2.codecs.base import codec_from_id

            module = (
                f"{payload_type.__module__}.{payload_type.__qualname__}"
                if payload_type
                else "builtins.object"
            )
            codec = codec_from_id(raw_codec, module)
        else:
            codec = codec_for(payload_type)

        backend: Backend[Any] = Backend(
            metadata_store=metadata_store,
            codec=codec,
            blob_store=config.get("blob_store"),
            vector_store=config.get("vector_store"),
            notifier=config.get("notifier"),
            eager_blobs=config.get("eager_blobs", False),
        )
        return backend

    def list_streams(self) -> list[str]:
        return list(self._streams.keys())

    def delete_stream(self, name: str) -> None:
        self._streams.pop(name, None)
