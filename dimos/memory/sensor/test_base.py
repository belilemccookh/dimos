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
"""Tests for SensorStore implementations."""

from dataclasses import dataclass
from pathlib import Path
import tempfile
import uuid

import pytest

from dimos.memory.sensor.base import InMemoryStore, SensorStore
from dimos.memory.sensor.legacy import LegacyPickleStore
from dimos.memory.sensor.pickledir import PickleDirStore
from dimos.memory.sensor.sqlite import SqliteStore
from dimos.types.timestamped import Timestamped


class TestLegacyPickleStoreRealData:
    """Test LegacyPickleStore with real recorded data."""

    def test_read_lidar_recording(self) -> None:
        """Test reading from unitree_go2_bigoffice/lidar recording."""
        store = LegacyPickleStore("unitree_go2_bigoffice/lidar")

        # Check first timestamp exists
        first_ts = store.first_timestamp()
        assert first_ts is not None
        assert first_ts > 0

        # Check first data
        first = store.first()
        assert first is not None
        assert hasattr(first, "ts")

        # Check find_closest_seek works
        data_at_10s = store.find_closest_seek(10.0)
        assert data_at_10s is not None

        # Check iteration returns monotonically increasing timestamps
        prev_ts = None
        for i, item in enumerate(store.iterate()):
            assert item.ts is not None
            if prev_ts is not None:
                assert item.ts >= prev_ts, "Timestamps should be monotonically increasing"
            prev_ts = item.ts
            if i >= 10:  # Only check first 10 items
                break


@dataclass
class SampleData(Timestamped):
    """Simple timestamped data for testing."""

    value: str

    def __init__(self, value: str, ts: float) -> None:
        super().__init__(ts)
        self.value = value

    def __eq__(self, other: object) -> bool:
        if isinstance(other, SampleData):
            return self.value == other.value and self.ts == other.ts
        return False


@pytest.fixture
def temp_dir():
    """Create a temporary directory for file-based store tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


def make_in_memory_store() -> SensorStore[SampleData]:
    return InMemoryStore[SampleData]()


def make_pickle_dir_store(tmpdir: str) -> SensorStore[SampleData]:
    return PickleDirStore[SampleData](tmpdir)


def make_sqlite_store(tmpdir: str) -> SensorStore[SampleData]:
    return SqliteStore[SampleData](Path(tmpdir) / "test.db")


def make_legacy_pickle_store(tmpdir: str) -> SensorStore[SampleData]:
    return LegacyPickleStore[SampleData](Path(tmpdir) / "legacy")


# Base test data (always available)
testdata: list[tuple[object, str]] = [
    (lambda _: make_in_memory_store(), "InMemoryStore"),
    (lambda tmpdir: make_pickle_dir_store(tmpdir), "PickleDirStore"),
    (lambda tmpdir: make_sqlite_store(tmpdir), "SqliteStore"),
    (lambda tmpdir: make_legacy_pickle_store(tmpdir), "LegacyPickleStore"),
]

# Track postgres tables to clean up
_postgres_tables: list[str] = []

try:
    import psycopg2

    from dimos.memory.sensor.postgres import PostgresStore

    # Test connection
    _test_conn = psycopg2.connect(dbname="dimensional")
    _test_conn.close()

    def make_postgres_store(_tmpdir: str) -> SensorStore[SampleData]:
        """Create PostgresStore with unique table name."""
        table = f"test_{uuid.uuid4().hex[:8]}"
        _postgres_tables.append(table)
        store = PostgresStore[SampleData](table)
        store.start()
        return store

    testdata.append((lambda tmpdir: make_postgres_store(tmpdir), "PostgresStore"))

    @pytest.fixture(autouse=True)
    def cleanup_postgres_tables():
        """Clean up postgres test tables after each test."""
        yield
        if _postgres_tables:
            try:
                conn = psycopg2.connect(dbname="dimensional")
                conn.autocommit = True
                with conn.cursor() as cur:
                    for table in _postgres_tables:
                        cur.execute(f"DROP TABLE IF EXISTS {table}")
                conn.close()
            except Exception:
                pass  # Ignore cleanup errors
            _postgres_tables.clear()

except Exception:
    print("PostgreSQL not available")


@pytest.mark.parametrize("store_factory,store_name", testdata)
class TestSensorStore:
    """Parametrized tests for all SensorStore implementations."""

    def test_save_and_load(self, store_factory, store_name, temp_dir):
        store = store_factory(temp_dir)
        store.save(SampleData("data_at_1", 1.0))
        store.save(SampleData("data_at_2", 2.0))

        assert store.load(1.0) == SampleData("data_at_1", 1.0)
        assert store.load(2.0) == SampleData("data_at_2", 2.0)
        assert store.load(3.0) is None

    def test_find_closest_timestamp(self, store_factory, store_name, temp_dir):
        store = store_factory(temp_dir)
        store.save(SampleData("a", 1.0), SampleData("b", 2.0), SampleData("c", 3.0))

        # Exact match
        assert store._find_closest_timestamp(2.0) == 2.0

        # Closest to 1.4 is 1.0
        assert store._find_closest_timestamp(1.4) == 1.0

        # Closest to 1.6 is 2.0
        assert store._find_closest_timestamp(1.6) == 2.0

        # With tolerance
        assert store._find_closest_timestamp(1.4, tolerance=0.5) == 1.0
        assert store._find_closest_timestamp(1.4, tolerance=0.3) is None

    def test_iter_items(self, store_factory, store_name, temp_dir):
        store = store_factory(temp_dir)
        store.save(SampleData("a", 1.0), SampleData("b", 2.0), SampleData("c", 3.0))

        # Should iterate in timestamp order
        items = list(store._iter_items())
        assert items == [
            (1.0, SampleData("a", 1.0)),
            (2.0, SampleData("b", 2.0)),
            (3.0, SampleData("c", 3.0)),
        ]

    def test_iter_items_with_range(self, store_factory, store_name, temp_dir):
        store = store_factory(temp_dir)
        store.save(
            SampleData("a", 1.0),
            SampleData("b", 2.0),
            SampleData("c", 3.0),
            SampleData("d", 4.0),
        )

        # Start only
        items = list(store._iter_items(start=2.0))
        assert items == [
            (2.0, SampleData("b", 2.0)),
            (3.0, SampleData("c", 3.0)),
            (4.0, SampleData("d", 4.0)),
        ]

        # End only
        items = list(store._iter_items(end=3.0))
        assert items == [(1.0, SampleData("a", 1.0)), (2.0, SampleData("b", 2.0))]

        # Both
        items = list(store._iter_items(start=2.0, end=4.0))
        assert items == [(2.0, SampleData("b", 2.0)), (3.0, SampleData("c", 3.0))]

    def test_empty_store(self, store_factory, store_name, temp_dir):
        store = store_factory(temp_dir)

        assert store.load(1.0) is None
        assert store._find_closest_timestamp(1.0) is None
        assert list(store._iter_items()) == []

    def test_first_and_first_timestamp(self, store_factory, store_name, temp_dir):
        store = store_factory(temp_dir)

        # Empty store
        assert store.first() is None
        assert store.first_timestamp() is None

        # Add data (in chronological order)
        store.save(SampleData("a", 1.0), SampleData("b", 2.0), SampleData("c", 3.0))

        # Should return first by timestamp
        assert store.first_timestamp() == 1.0
        assert store.first() == SampleData("a", 1.0)

    def test_find_closest(self, store_factory, store_name, temp_dir):
        store = store_factory(temp_dir)
        store.save(SampleData("a", 1.0), SampleData("b", 2.0), SampleData("c", 3.0))

        # Exact match
        assert store.find_closest(2.0) == SampleData("b", 2.0)

        # Closest to 1.4 is 1.0
        assert store.find_closest(1.4) == SampleData("a", 1.0)

        # Closest to 1.6 is 2.0
        assert store.find_closest(1.6) == SampleData("b", 2.0)

        # With tolerance
        assert store.find_closest(1.4, tolerance=0.5) == SampleData("a", 1.0)
        assert store.find_closest(1.4, tolerance=0.3) is None

    def test_find_closest_seek(self, store_factory, store_name, temp_dir):
        store = store_factory(temp_dir)
        store.save(SampleData("a", 10.0), SampleData("b", 11.0), SampleData("c", 12.0))

        # Seek 0 = first item (10.0)
        assert store.find_closest_seek(0.0) == SampleData("a", 10.0)

        # Seek 1.0 = 11.0
        assert store.find_closest_seek(1.0) == SampleData("b", 11.0)

        # Seek 1.4 -> closest to 11.4 is 11.0
        assert store.find_closest_seek(1.4) == SampleData("b", 11.0)

        # Seek 1.6 -> closest to 11.6 is 12.0
        assert store.find_closest_seek(1.6) == SampleData("c", 12.0)

        # With tolerance
        assert store.find_closest_seek(1.4, tolerance=0.5) == SampleData("b", 11.0)
        assert store.find_closest_seek(1.4, tolerance=0.3) is None

    def test_iterate(self, store_factory, store_name, temp_dir):
        store = store_factory(temp_dir)
        store.save(SampleData("a", 1.0), SampleData("b", 2.0), SampleData("c", 3.0))

        # Should iterate in timestamp order, returning data only (not tuples)
        items = list(store.iterate())
        assert items == [
            SampleData("a", 1.0),
            SampleData("b", 2.0),
            SampleData("c", 3.0),
        ]

    def test_iterate_with_seek_and_duration(self, store_factory, store_name, temp_dir):
        store = store_factory(temp_dir)
        store.save(
            SampleData("a", 10.0),
            SampleData("b", 11.0),
            SampleData("c", 12.0),
            SampleData("d", 13.0),
        )

        # Seek from start
        items = list(store.iterate(seek=1.0))
        assert items == [
            SampleData("b", 11.0),
            SampleData("c", 12.0),
            SampleData("d", 13.0),
        ]

        # Duration
        items = list(store.iterate(duration=2.0))
        assert items == [SampleData("a", 10.0), SampleData("b", 11.0)]

        # Seek + duration
        items = list(store.iterate(seek=1.0, duration=2.0))
        assert items == [SampleData("b", 11.0), SampleData("c", 12.0)]

        # from_timestamp
        items = list(store.iterate(from_timestamp=12.0))
        assert items == [SampleData("c", 12.0), SampleData("d", 13.0)]

    def test_variadic_save(self, store_factory, store_name, temp_dir):
        store = store_factory(temp_dir)

        # Save multiple items at once
        store.save(
            SampleData("a", 1.0),
            SampleData("b", 2.0),
            SampleData("c", 3.0),
        )

        assert store.load(1.0) == SampleData("a", 1.0)
        assert store.load(2.0) == SampleData("b", 2.0)
        assert store.load(3.0) == SampleData("c", 3.0)

    def test_pipe_save(self, store_factory, store_name, temp_dir):
        import reactivex as rx

        store = store_factory(temp_dir)

        # Create observable with test data
        source = rx.of(
            SampleData("a", 1.0),
            SampleData("b", 2.0),
            SampleData("c", 3.0),
        )

        # Pipe through store.pipe_save — should save and pass through
        results: list[SampleData] = []
        source.pipe(store.pipe_save).subscribe(results.append)

        # Data should be saved
        assert store.load(1.0) == SampleData("a", 1.0)
        assert store.load(2.0) == SampleData("b", 2.0)
        assert store.load(3.0) == SampleData("c", 3.0)

        # Data should also pass through
        assert results == [
            SampleData("a", 1.0),
            SampleData("b", 2.0),
            SampleData("c", 3.0),
        ]

    def test_pipe_save_raw(self, store_factory, store_name, temp_dir):
        import reactivex as rx

        store = store_factory(temp_dir)

        source = rx.of(
            SampleData("a", 1.0),
            SampleData("b", 2.0),
        )

        results: list[SampleData] = []
        source.pipe(store.pipe_save_raw(lambda d: d.ts)).subscribe(results.append)

        assert store.load(1.0) == SampleData("a", 1.0)
        assert store.load(2.0) == SampleData("b", 2.0)
        assert len(results) == 2

    def test_consume_stream(self, store_factory, store_name, temp_dir):
        import reactivex as rx

        store = store_factory(temp_dir)

        # Create observable with test data
        source = rx.of(
            SampleData("a", 1.0),
            SampleData("b", 2.0),
            SampleData("c", 3.0),
        )

        # Consume stream — should save all items
        disposable = store.consume_stream(source)

        # Data should be saved
        assert store.load(1.0) == SampleData("a", 1.0)
        assert store.load(2.0) == SampleData("b", 2.0)
        assert store.load(3.0) == SampleData("c", 3.0)

        disposable.dispose()

    def test_consume_stream_raw(self, store_factory, store_name, temp_dir):
        import reactivex as rx

        store = store_factory(temp_dir)

        source = rx.of(
            SampleData("a", 1.0),
            SampleData("b", 2.0),
        )

        disposable = store.consume_stream_raw(source, key=lambda d: d.ts)

        assert store.load(1.0) == SampleData("a", 1.0)
        assert store.load(2.0) == SampleData("b", 2.0)

        disposable.dispose()

    def test_iterate_items(self, store_factory, store_name, temp_dir):
        store = store_factory(temp_dir)
        store.save(SampleData("a", 1.0), SampleData("b", 2.0), SampleData("c", 3.0))

        items = list(store.iterate_items())
        assert items == [
            (1.0, SampleData("a", 1.0)),
            (2.0, SampleData("b", 2.0)),
            (3.0, SampleData("c", 3.0)),
        ]

        # With seek
        items = list(store.iterate_items(seek=1.0))
        assert len(items) == 2
        assert items[0] == (2.0, SampleData("b", 2.0))

    def test_stream_basic(self, store_factory, store_name, temp_dir):
        store = store_factory(temp_dir)
        store.save(SampleData("a", 1.0), SampleData("b", 2.0), SampleData("c", 3.0))

        # Stream at high speed (essentially instant)
        results: list[SampleData] = []
        store.stream(speed=1000.0).subscribe(
            on_next=results.append,
            on_completed=lambda: None,
        )

        # Give it a moment to complete
        import time

        time.sleep(0.1)

        assert results == [
            SampleData("a", 1.0),
            SampleData("b", 2.0),
            SampleData("c", 3.0),
        ]


class TestNonTimestampedData:
    """Test SensorStore with plain (non-Timestamped) data types."""

    def test_store_plain_strings(self):
        store: InMemoryStore[str] = InMemoryStore()
        store.save_raw(1.0, "hello")
        store.save_raw(2.0, "world")

        assert store.load(1.0) == "hello"
        assert store.load(2.0) == "world"
        assert store.first() == "hello"
        assert store.first_timestamp() == 1.0

    def test_iterate_plain_data(self):
        store: InMemoryStore[int] = InMemoryStore()
        store.save_raw(10.0, 100)
        store.save_raw(20.0, 200)
        store.save_raw(30.0, 300)

        assert list(store.iterate()) == [100, 200, 300]
        assert list(store.iterate_items()) == [(10.0, 100), (20.0, 200), (30.0, 300)]

    def test_find_closest_plain_data(self):
        store: InMemoryStore[str] = InMemoryStore()
        store.save_raw(1.0, "a")
        store.save_raw(3.0, "b")
        store.save_raw(5.0, "c")

        assert store.find_closest(2.0) == "a"
        assert store.find_closest(4.0) == "b"
        assert store.find_closest_seek(2.0) == "b"

    def test_pipe_save_raw_plain_data(self):
        import reactivex as rx

        store: InMemoryStore[dict] = InMemoryStore()
        source = rx.of(
            {"ts": 1.0, "val": "x"},
            {"ts": 2.0, "val": "y"},
        )

        results: list[dict] = []
        source.pipe(store.pipe_save_raw(lambda d: d["ts"])).subscribe(results.append)

        assert store.load(1.0) == {"ts": 1.0, "val": "x"}
        assert store.load(2.0) == {"ts": 2.0, "val": "y"}
        assert len(results) == 2

    def test_consume_stream_raw_plain_data(self):
        import reactivex as rx

        store: InMemoryStore[list] = InMemoryStore()
        source = rx.of(
            [1.0, "data_a"],
            [2.0, "data_b"],
        )

        disposable = store.consume_stream_raw(source, key=lambda d: d[0])

        assert store.load(1.0) == [1.0, "data_a"]
        assert store.load(2.0) == [2.0, "data_b"]

        disposable.dispose()
