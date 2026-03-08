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

"""Tests for memory transformers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest

from dimos.memory.impl.sqlite import SqliteSession, SqliteStore
from dimos.memory.transformer import DetectionTransformer, TextEmbeddingTransformer
from dimos.models.embedding.base import Embedding, EmbeddingModel
from dimos.msgs.sensor_msgs import Image
from dimos.perception.detection.type import Detection2DBBox, ImageDetections2D

if TYPE_CHECKING:
    from collections.abc import Iterator


class FakeTextEmbedder(EmbeddingModel):
    device = "cpu"

    def embed(self, *imgs: Image) -> Embedding | list[Embedding]:  # type: ignore[override]
        raise NotImplementedError

    def embed_text(self, *texts: str) -> Embedding | list[Embedding]:
        results = []
        for text in texts:
            h = hash(text) % 1000 / 1000.0
            results.append(Embedding(np.array([h, 1.0 - h, 0.0, 0.0], dtype=np.float32)))
        return results if len(results) > 1 else results[0]


class SemanticFakeEmbedder(EmbeddingModel):
    """Embeds 'kitchen' texts to one region, everything else to another."""

    device = "cpu"

    def embed(self, *imgs: Image) -> Embedding | list[Embedding]:  # type: ignore[override]
        raise NotImplementedError

    def embed_text(self, *texts: str) -> Embedding | list[Embedding]:
        results = []
        for text in texts:
            if "kitchen" in text.lower():
                results.append(Embedding(np.array([1.0, 0.0, 0.0], dtype=np.float32)))
            else:
                results.append(Embedding(np.array([0.0, 1.0, 0.0], dtype=np.float32)))
        return results if len(results) > 1 else results[0]


@pytest.fixture
def session(tmp_path: object) -> Iterator[SqliteSession]:
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    store = SqliteStore(str(tmp_path / "test.db"))
    sess = store.session()
    yield sess
    sess.stop()
    store.stop()


class TestTextEmbeddingTransformer:
    """Test text -> embedding -> semantic search pipeline."""

    def test_text_to_embedding_backfill(self, session: SqliteSession) -> None:
        """Backfill: store text, transform to embeddings, search by text."""
        logs = session.stream("te_logs", str)
        logs.append("Robot navigated to kitchen", ts=1.0)
        logs.append("Battery low warning", ts=2.0)
        logs.append("Robot navigated to bedroom", ts=3.0)

        emb_stream = logs.transform(TextEmbeddingTransformer(FakeTextEmbedder())).store(
            "te_log_embeddings"
        )

        assert emb_stream.count() == 3

        results = emb_stream.search_embedding("Robot navigated to kitchen", k=1).fetch()
        assert len(results) == 1
        assert isinstance(results[0].data, Embedding)

        # project_to to get source text
        projected = (
            emb_stream.search_embedding("Robot navigated to kitchen", k=1).project_to(logs).fetch()
        )
        assert len(projected) == 1
        assert isinstance(projected[0].data, str)

    def test_text_embedding_live(self, session: SqliteSession) -> None:
        """Live mode: new text is embedded automatically."""
        logs = session.stream("te_live_logs", str)
        emb_stream = logs.transform(TextEmbeddingTransformer(FakeTextEmbedder()), live=True).store(
            "te_live_embs"
        )

        assert emb_stream.count() == 0  # no backfill

        logs.append("New log entry", ts=1.0)
        assert emb_stream.count() == 1

        logs.append("Another log entry", ts=2.0)
        assert emb_stream.count() == 2

    def test_text_embedding_search_and_project(self, session: SqliteSession) -> None:
        """search_embedding + project_to retrieves source text."""
        logs = session.stream("te_proj_logs", str)
        logs.append("Robot entered kitchen", ts=1.0)
        logs.append("Battery warning", ts=2.0)
        logs.append("Cleaning kitchen floor", ts=3.0)

        emb_stream = logs.transform(TextEmbeddingTransformer(SemanticFakeEmbedder())).store(
            "te_proj_embs"
        )

        results = emb_stream.search_embedding("kitchen", k=2).project_to(logs).fetch()
        assert len(results) == 2
        assert all("kitchen" in r.data.lower() for r in results)


def _make_image(ts: float) -> Image:
    return Image(data=np.zeros((64, 64, 3), dtype=np.uint8), ts=ts)


class FakeVlModel:
    """Minimal VlModel stub for detection tests."""

    def __init__(
        self,
        detections_per_image: int = 2,
        *,
        raise_on_call: bool = False,
    ) -> None:
        self.detections_per_image = detections_per_image
        self.raise_on_call = raise_on_call

    def query_detections(self, image: Image, query: str, **kwargs: object) -> ImageDetections2D:
        if self.raise_on_call:
            raise RuntimeError("model error")
        dets = [
            Detection2DBBox(
                bbox=(10.0 * i, 10.0 * i, 20.0 * i + 20, 20.0 * i + 20),
                track_id=i,
                class_id=-1,
                confidence=0.9,
                name=query,
                ts=image.ts,
                image=image,
            )
            for i in range(self.detections_per_image)
        ]
        return ImageDetections2D(image=image, detections=dets)


class TestDetectionTransformer:
    """Test VLM detection transformer."""

    def test_detection_backfill(self, session: SqliteSession) -> None:
        """Backfill: 3 images → transform → 3 detection observations."""
        imgs = session.stream("det_imgs", Image)
        for i in range(3):
            imgs.append(_make_image(float(i + 1)), ts=float(i + 1))

        det_stream = imgs.transform(DetectionTransformer(FakeVlModel(2), "cup")).store("det_cups")

        assert det_stream.count() == 3
        results = det_stream.fetch()
        for obs in results:
            assert obs.data.image is None, "image should be stripped"
            for det in obs.data.detections:
                assert det.image is None, "detection image should be stripped"
            assert obs.tags["query"] == "cup"
            assert obs.tags["count"] == 2

    def test_detection_skip_empty(self, session: SqliteSession) -> None:
        """skip_empty=True (default): 0 detections → observation skipped."""
        imgs = session.stream("det_skip_imgs", Image)
        imgs.append(_make_image(1.0), ts=1.0)

        det_stream = imgs.transform(DetectionTransformer(FakeVlModel(0), "nothing")).store(
            "det_skip"
        )

        assert det_stream.count() == 0

    def test_detection_keep_empty(self, session: SqliteSession) -> None:
        """skip_empty=False: 0 detections → observation stored with count=0."""
        imgs = session.stream("det_keep_imgs", Image)
        imgs.append(_make_image(1.0), ts=1.0)

        det_stream = imgs.transform(
            DetectionTransformer(FakeVlModel(0), "nothing", skip_empty=False)
        ).store("det_keep")

        assert det_stream.count() == 1
        obs = det_stream.fetch()[0]
        assert obs.tags["count"] == 0
        assert len(obs.data.detections) == 0

    def test_detection_model_error(self, session: SqliteSession) -> None:
        """Model raises → observation skipped, no crash."""
        imgs = session.stream("det_err_imgs", Image)
        imgs.append(_make_image(1.0), ts=1.0)

        det_stream = imgs.transform(
            DetectionTransformer(FakeVlModel(raise_on_call=True), "cup")
        ).store("det_err")

        assert det_stream.count() == 0

    def test_detection_lineage(self, session: SqliteSession) -> None:
        """project_to(image_stream) recovers source images."""
        imgs = session.stream("det_lin_imgs", Image)
        imgs.append(_make_image(1.0), ts=1.0)
        imgs.append(_make_image(2.0), ts=2.0)

        det_stream = imgs.transform(DetectionTransformer(FakeVlModel(1), "obj")).store("det_lin")

        projected = det_stream.project_to(imgs).fetch()
        assert len(projected) == 2
        for obs in projected:
            assert isinstance(obs.data, Image)

    def test_detection_live(self, session: SqliteSession) -> None:
        """Live mode: append images after transform, verify reactive detection."""
        imgs = session.stream("det_live_imgs", Image)
        det_stream = imgs.transform(DetectionTransformer(FakeVlModel(1), "cup"), live=True).store(
            "det_live"
        )

        assert det_stream.count() == 0

        imgs.append(_make_image(1.0), ts=1.0)
        assert det_stream.count() == 1

        imgs.append(_make_image(2.0), ts=2.0)
        assert det_stream.count() == 2
