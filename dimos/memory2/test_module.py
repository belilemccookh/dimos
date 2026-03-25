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

from collections.abc import Iterator

import pytest

from dimos.memory2.store.memory import MemoryStore
from dimos.memory2.stream import Stream
from dimos.memory2.transform import FnTransformer, Transformer
from dimos.memory2.type.observation import Observation

# -- Unbound stream tests --


def test_unbound_stream_creation() -> None:
    """Stream() with no args creates an unbound stream."""
    s = Stream()
    assert s._xf is None


def test_unbound_stream_transform_chain() -> None:
    """Unbound streams support .transform() and .map() chaining."""

    class Double(Transformer[int, int]):
        def __call__(self, upstream: Iterator[Observation[int]]) -> Iterator[Observation[int]]:
            for obs in upstream:
                yield obs.derive(data=obs.data * 2)

    pipeline = Stream().transform(Double()).map(lambda obs: obs.derive(data=obs.data + 1))

    # Should have a chain of transforms
    assert pipeline._xf is not None
    assert isinstance(pipeline._source, Stream)


def test_unbound_stream_iteration_raises() -> None:
    """Iterating an unbound stream raises TypeError."""
    s = Stream().transform(FnTransformer(lambda obs: obs))
    with pytest.raises(TypeError, match="unbound"):
        list(s)


def test_chain_applies_transforms() -> None:
    """chain() replays unbound transforms on a real stream."""
    store = MemoryStore()
    with store:
        stream = store.stream("test", int)
        stream.append(10)
        stream.append(20)
        stream.append(30)

        class Double(Transformer[int, int]):
            def __call__(self, upstream: Iterator[Observation[int]]) -> Iterator[Observation[int]]:
                for obs in upstream:
                    yield obs.derive(data=obs.data * 2)

        pipeline = Stream().transform(Double())
        result = stream.chain(pipeline).fetch()

        assert [obs.data for obs in result] == [20, 40, 60]


def test_chain_multiple_transforms() -> None:
    """chain() preserves order of multiple transforms."""
    store = MemoryStore()
    with store:
        stream = store.stream("test", int)
        stream.append(5)

        class Double(Transformer[int, int]):
            def __call__(self, upstream: Iterator[Observation[int]]) -> Iterator[Observation[int]]:
                for obs in upstream:
                    yield obs.derive(data=obs.data * 2)

        class AddTen(Transformer[int, int]):
            def __call__(self, upstream: Iterator[Observation[int]]) -> Iterator[Observation[int]]:
                for obs in upstream:
                    yield obs.derive(data=obs.data + 10)

        # Double first, then AddTen: (5 * 2) + 10 = 20
        pipeline = Stream().transform(Double()).transform(AddTen())
        result = stream.chain(pipeline).fetch()

        assert result[0].data == 20  # (5 * 2) + 10


def test_chain_preserves_filters() -> None:
    """chain() replays filters from the unbound stream."""
    store = MemoryStore()
    with store:
        stream = store.stream("test", int)
        stream.append(10, ts=1.0)
        stream.append(20, ts=2.0)
        stream.append(30, ts=3.0)

        # Pipeline with a time filter: only ts > 1.5
        pipeline = Stream().after(1.5)
        result = stream.chain(pipeline).fetch()

        assert [obs.data for obs in result] == [20, 30]


def test_chain_rejects_bound_stream() -> None:
    """chain() raises if passed a bound (non-unbound) stream."""
    store = MemoryStore()
    with store:
        s1 = store.stream("a", int)
        s2 = store.stream("b", int)
        with pytest.raises(TypeError, match="unbound"):
            s1.chain(s2)


def test_live_rejects_unbound_stream() -> None:
    """live() raises on an unbound stream."""
    with pytest.raises(TypeError, match="unbound"):
        Stream().live()


def test_unbound_str() -> None:
    """Unbound streams display as Stream(unbound)."""
    s = Stream()
    assert "unbound" in str(s)


# -- StreamModule tests --


def test_stream_module_subclass_blueprint() -> None:
    """StreamModule subclass creates a Blueprint with correct In/Out ports."""
    from dimos.core.stream import In, Out
    from dimos.memory2.module import StreamModule

    class Identity(Transformer[str, str]):
        def __call__(self, upstream: Iterator[Observation[str]]) -> Iterator[Observation[str]]:
            yield from upstream

    class MyModule(StreamModule):
        pipeline = Stream().transform(Identity())
        messages: In[str]
        processed: Out[str]

    bp = MyModule.blueprint()

    assert len(bp.blueprints) == 1
    atom = bp.blueprints[0]
    stream_names = {s.name for s in atom.streams}
    assert "messages" in stream_names
    assert "processed" in stream_names


def test_stream_module_with_transformer_pipeline() -> None:
    """StreamModule accepts a bare Transformer as pipeline."""
    from dimos.core.stream import In, Out
    from dimos.memory2.module import StreamModule

    class Double(Transformer[int, int]):
        def __call__(self, upstream: Iterator[Observation[int]]) -> Iterator[Observation[int]]:
            for obs in upstream:
                yield obs.derive(data=obs.data * 2)

    class Doubler(StreamModule):
        pipeline = Double()
        numbers: In[int]
        doubled: Out[int]

    bp = Doubler.blueprint()

    assert len(bp.blueprints) == 1
    atom = bp.blueprints[0]
    stream_names = {s.name for s in atom.streams}
    assert "numbers" in stream_names
    assert "doubled" in stream_names


def test_stream_module_with_method_pipeline() -> None:
    """StreamModule accepts a method pipeline with access to self.config."""
    from dimos.core.module import ModuleConfig
    from dimos.core.stream import In, Out
    from dimos.memory2.module import StreamModule

    class MyConfig(ModuleConfig):
        factor: int = 3

    class Double(Transformer[int, int]):
        def __init__(self, factor: int = 2) -> None:
            self.factor = factor

        def __call__(self, upstream: Iterator[Observation[int]]) -> Iterator[Observation[int]]:
            for obs in upstream:
                yield obs.derive(data=obs.data * self.factor)

    class Multiplier(StreamModule[MyConfig]):
        default_config = MyConfig

        def pipeline(self, stream: Stream) -> Stream:
            return stream.transform(Double(factor=self.config.factor))

        numbers: In[int]
        result: Out[int]

    bp = Multiplier.blueprint(factor=5)

    assert len(bp.blueprints) == 1
    atom = bp.blueprints[0]
    stream_names = {s.name for s in atom.streams}
    assert "numbers" in stream_names
    assert "result" in stream_names
