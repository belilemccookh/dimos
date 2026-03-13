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

import sqlite3
from typing import Any

from reactivex.disposable import Disposable

from dimos.memory2.backend import Backend
from dimos.memory2.blobstore.sqlite import SqliteBlobStore
from dimos.memory2.codecs.base import Codec, codec_for, codec_from_id, codec_id
from dimos.memory2.observationstore.sqlite import SqliteObservationStore
from dimos.memory2.store import Store, StoreConfig
from dimos.memory2.utils import open_sqlite_connection, validate_identifier

# ── SqliteStore ──────────────────────────────────────────────────


class SqliteStoreConfig(StoreConfig):
    """Config for SQLite-backed store."""

    path: str = "memory.db"
    page_size: int = 256


class SqliteStore(Store):
    """Store backed by a SQLite database file."""

    default_config: type[SqliteStoreConfig] = SqliteStoreConfig
    config: SqliteStoreConfig

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Dedicated connection for the stream registry table
        self._registry_conn = self._open_connection()
        self._registry_conn.execute(
            "CREATE TABLE IF NOT EXISTS _streams ("
            "    name           TEXT PRIMARY KEY,"
            "    payload_module TEXT NOT NULL,"
            "    codec_id       TEXT NOT NULL"
            ")"
        )
        self._registry_conn.commit()

    def _open_connection(self) -> sqlite3.Connection:
        """Open a new WAL-mode connection with sqlite-vec loaded."""
        return open_sqlite_connection(self.config.path)

    def _create_backend(
        self, name: str, payload_type: type[Any] | None = None, **config: Any
    ) -> Backend[Any]:
        validate_identifier(name)

        # Look up existing stream in registry
        row = self._registry_conn.execute(
            "SELECT payload_module, codec_id FROM _streams WHERE name = ?", (name,)
        ).fetchone()

        if row is not None:
            stored_module, stored_codec_id = row
            if payload_type is not None:
                actual_module = f"{payload_type.__module__}.{payload_type.__qualname__}"
                if actual_module != stored_module:
                    raise ValueError(
                        f"Stream {name!r} was created with type {stored_module}, "
                        f"but opened with {actual_module}"
                    )
            raw_codec = config.get("codec")
            if isinstance(raw_codec, str):
                codec = codec_from_id(raw_codec, stored_module)
            elif isinstance(raw_codec, Codec):
                codec = raw_codec
            elif raw_codec is not None:
                codec = raw_codec
            else:
                codec = codec_from_id(stored_codec_id, stored_module)
        else:
            if payload_type is None:
                raise TypeError(f"Stream {name!r} does not exist yet — payload_type is required")
            payload_module = f"{payload_type.__module__}.{payload_type.__qualname__}"
            raw_codec = config.get("codec")
            if isinstance(raw_codec, str):
                codec = codec_from_id(raw_codec, payload_module)
            elif isinstance(raw_codec, Codec):
                codec = raw_codec
            elif raw_codec is not None:
                codec = raw_codec
            else:
                codec = codec_for(payload_type)
            self._registry_conn.execute(
                "INSERT INTO _streams (name, payload_module, codec_id) VALUES (?, ?, ?)",
                (name, payload_module, codec_id(codec)),
            )
            self._registry_conn.commit()

        # Each backend gets its own connection for WAL-mode concurrency
        backend_conn = self._open_connection()

        # Create per-backend stores wrapping the backend's own connection
        bs = config.get("blob_store")
        if bs is None:
            bs = SqliteBlobStore(backend_conn)
        vs = config.get("vector_store")
        if vs is None:
            from dimos.memory2.vectorstore.sqlite import SqliteVectorStore

            vs = SqliteVectorStore(backend_conn)

        # Detect if blob_store shares the same SQLite connection (for eager JOIN)
        blob_store_conn_match = isinstance(bs, SqliteBlobStore) and bs._conn is backend_conn

        metadata_store: SqliteObservationStore[Any] = SqliteObservationStore(
            backend_conn,
            name,
            codec,
            blob_store_conn_match=blob_store_conn_match and config.get("eager_blobs", False),
            page_size=self.config.page_size,
        )

        backend: Backend[Any] = Backend(
            metadata_store=metadata_store,
            codec=codec,
            blob_store=bs,
            vector_store=vs,
            notifier=config.get("notifier"),
            eager_blobs=config.get("eager_blobs", False),
        )
        self.register_disposables(Disposable(action=lambda: backend_conn.close()))
        return backend

    def list_streams(self) -> list[str]:
        db_names = {
            row[0] for row in self._registry_conn.execute("SELECT name FROM _streams").fetchall()
        }
        return sorted(db_names | set(self._streams.keys()))

    def delete_stream(self, name: str) -> None:
        self._streams.pop(name, None)
        self._registry_conn.execute(f'DROP TABLE IF EXISTS "{name}"')
        self._registry_conn.execute(f'DROP TABLE IF EXISTS "{name}_blob"')
        self._registry_conn.execute(f'DROP TABLE IF EXISTS "{name}_vec"')
        self._registry_conn.execute(f'DROP TABLE IF EXISTS "{name}_rtree"')
        self._registry_conn.execute("DELETE FROM _streams WHERE name = ?", (name,))
        self._registry_conn.commit()

    def stop(self) -> None:
        super().stop()  # disposes owned metadata store connections
        self._registry_conn.close()
