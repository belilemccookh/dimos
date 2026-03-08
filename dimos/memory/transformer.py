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

from abc import ABC, abstractmethod
import logging
from typing import TYPE_CHECKING, Any, Generic, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

    from dimos.memory.stream import Stream
    from dimos.memory.type import Observation
    from dimos.models.embedding.base import Embedding, EmbeddingModel
    from dimos.models.vl.base import Captioner, VlModel
    from dimos.perception.detection.type import ImageDetections2D

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")


class Transformer(ABC, Generic[T, R]):
    """Transforms a source stream into results on a target stream."""

    supports_backfill: bool = True
    supports_live: bool = True
    output_type: type | None = None

    def __repr__(self) -> str:
        return type(self).__name__

    @abstractmethod
    def process(self, source: Stream[T], target: Stream[R]) -> None:
        """Batch/historical processing.

        Has full access to the source stream — can query, filter, batch, skip, etc.
        """

    def on_append(self, obs: Observation[Any], target: Stream[R]) -> None:
        """Reactive per-item processing. Called for each new item."""


class PerItemTransformer(Transformer[T, R]):
    """Wraps a simple callable as a per-item Transformer."""

    def __init__(self, fn: Callable[[T], R | list[R] | None]) -> None:
        self._fn = fn

    def process(self, source: Stream[T], target: Stream[R]) -> None:
        for page in source.fetch_pages():
            for obs in page:
                self._apply(obs, target)

    def on_append(self, obs: Observation[Any], target: Stream[R]) -> None:
        self._apply(obs, target)

    def _apply(self, obs: Observation[Any], target: Stream[R]) -> None:
        result = self._fn(obs.data)
        if result is None:
            return
        if isinstance(result, list):
            for item in result:
                target.append(item, ts=obs.ts, pose=obs.pose, tags=obs.tags, parent_id=obs.id)
        else:
            target.append(result, ts=obs.ts, pose=obs.pose, tags=obs.tags, parent_id=obs.id)


class QualityWindowTransformer(Transformer[T, T]):
    """Keeps the highest-quality item per time window.

    Like ``sharpness_barrier`` but operates on stored data (no wall-clock dependency).
    In live mode, buffers the current window and emits the best item when a new
    observation falls outside the window.
    """

    supports_backfill: bool = True
    supports_live: bool = True

    def __init__(self, quality_fn: Callable[[T], float], window: float = 0.5) -> None:
        self._quality_fn = quality_fn
        self._window = window

    def __repr__(self) -> str:
        fn_name = getattr(self._quality_fn, "__name__", None) or repr(self._quality_fn)
        return f"QualityWindowTransformer({fn_name}, window={self._window})"
        # Live state
        self._window_start: float | None = None
        self._best_obs: Observation[T] | None = None
        self._best_score: float = -1.0

    def process(self, source: Stream[T], target: Stream[T]) -> None:
        window_start: float | None = None
        best_obs: Observation[T] | None = None
        best_score: float = -1.0

        for obs in source:
            ts = obs.ts or 0.0
            if window_start is None:
                window_start = ts

            if (ts - window_start) >= self._window:
                if best_obs is not None:
                    target.append(
                        best_obs.data,
                        ts=best_obs.ts,
                        pose=best_obs.pose,
                        tags=best_obs.tags,
                        parent_id=best_obs.id,
                    )
                window_start = ts
                best_score = -1.0
                best_obs = None

            score = self._quality_fn(obs.data)
            if score > best_score:
                best_score = score
                best_obs = obs

        if best_obs is not None:
            target.append(
                best_obs.data,
                ts=best_obs.ts,
                pose=best_obs.pose,
                tags=best_obs.tags,
                parent_id=best_obs.id,
            )

    def on_append(self, obs: Observation[T], target: Stream[T]) -> None:  # type: ignore[override]
        ts = obs.ts or 0.0

        if self._window_start is None:
            self._window_start = ts

        if (ts - self._window_start) >= self._window:
            if self._best_obs is not None:
                target.append(
                    self._best_obs.data,
                    ts=self._best_obs.ts,
                    pose=self._best_obs.pose,
                    tags=self._best_obs.tags,
                    parent_id=self._best_obs.id,
                )
            self._window_start = ts
            self._best_score = -1.0
            self._best_obs = None

        score = self._quality_fn(obs.data)
        if score > self._best_score:
            self._best_score = score
            self._best_obs = obs


class CaptionTransformer(Transformer[Any, str]):
    """Wraps a Captioner (or VlModel) to produce text captions from images.

    When stored, the output stream becomes a TextStream with FTS index.
    Uses caption_batch() during backfill for efficiency.
    """

    supports_backfill: bool = True
    supports_live: bool = True

    def __init__(self, model: Captioner, *, batch_size: int = 16) -> None:
        self.model = model
        self.batch_size = batch_size
        self.output_type: type | None = str

    def __repr__(self) -> str:
        model_name = type(self.model).__name__
        parts = [model_name]
        if self.batch_size != 16:
            parts.append(f"batch_size={self.batch_size}")
        return f"CaptionTransformer({', '.join(parts)})"

    def process(self, source: Stream[Any], target: Stream[str]) -> None:
        for page in source.fetch_pages(batch_size=self.batch_size):
            images = [obs.data for obs in page]
            if not images:
                continue
            captions = self.model.caption_batch(*images)
            for obs, cap in zip(page, captions, strict=True):
                target.append(cap, ts=obs.ts, pose=obs.pose, tags=obs.tags, parent_id=obs.id)

    def on_append(self, obs: Observation[Any], target: Stream[str]) -> None:
        caption = self.model.caption(obs.data)
        target.append(caption, ts=obs.ts, pose=obs.pose, tags=obs.tags, parent_id=obs.id)


class TextEmbeddingTransformer(Transformer[Any, "Embedding"]):
    """Wraps an EmbeddingModel to embed text payloads (strings) into vectors.

    Use this for semantic search over logs, captions, or any text data.
    When stored, the output stream becomes an EmbeddingStream with vector index.
    """

    supports_backfill: bool = True
    supports_live: bool = True

    def __init__(self, model: EmbeddingModel) -> None:
        from dimos.models.embedding.base import Embedding as EmbeddingCls

        self.model = model
        self.output_type: type | None = EmbeddingCls

    def __repr__(self) -> str:
        return f"TextEmbeddingTransformer({type(self.model).__name__})"

    def process(self, source: Stream[Any], target: Stream[Embedding]) -> None:
        for page in source.fetch_pages():
            texts = [str(obs.data) for obs in page]
            if not texts:
                continue
            embeddings = self.model.embed_text(*texts)
            if not isinstance(embeddings, list):
                embeddings = [embeddings]
            for obs, emb in zip(page, embeddings, strict=True):
                target.append(emb, ts=obs.ts, pose=obs.pose, tags=obs.tags, parent_id=obs.id)

    def on_append(self, obs: Observation[Any], target: Stream[Embedding]) -> None:
        emb = self.model.embed_text(str(obs.data))
        if isinstance(emb, list):
            emb = emb[0]
        target.append(emb, ts=obs.ts, pose=obs.pose, tags=obs.tags, parent_id=obs.id)


class EmbeddingTransformer(Transformer[Any, "Embedding"]):
    """Wraps an EmbeddingModel as a Transformer that produces Embedding output.

    When stored, the output stream becomes an EmbeddingStream with vector index.
    """

    supports_backfill: bool = True
    supports_live: bool = True

    def __init__(self, model: EmbeddingModel) -> None:
        from dimos.models.embedding.base import Embedding as EmbeddingCls

        self.model = model
        self.output_type: type | None = EmbeddingCls

    def __repr__(self) -> str:
        return f"EmbeddingTransformer({type(self.model).__name__})"

    def process(self, source: Stream[Any], target: Stream[Embedding]) -> None:
        for page in source.fetch_pages():
            images = [obs.data for obs in page]
            if not images:
                continue
            embeddings = self.model.embed(*images)
            if not isinstance(embeddings, list):
                embeddings = [embeddings]
            for obs, emb in zip(page, embeddings, strict=True):
                target.append(emb, ts=obs.ts, pose=obs.pose, tags=obs.tags, parent_id=obs.id)

    def on_append(self, obs: Observation[Any], target: Stream[Embedding]) -> None:
        emb = self.model.embed(obs.data)
        if isinstance(emb, list):
            emb = emb[0]
        target.append(emb, ts=obs.ts, pose=obs.pose, tags=obs.tags, parent_id=obs.id)


class DetectionTransformer(Transformer[Any, "ImageDetections2D"]):
    """Runs VLM object detection on images, producing ImageDetections2D.

    Strips image references from detections before storage to avoid
    duplicating image data. Use project_to(image_stream) to recover
    source images via lineage.
    """

    supports_backfill = True
    supports_live = True

    def __init__(self, model: VlModel, query: str, *, skip_empty: bool = True) -> None:
        from dimos.perception.detection.type import ImageDetections2D as IDet2D

        self.model = model
        self.query = query
        self.skip_empty = skip_empty
        self.output_type: type | None = IDet2D

    def __repr__(self) -> str:
        model_name = type(self.model).__name__
        parts = [f"{model_name}, {self.query!r}"]
        if not self.skip_empty:
            parts.append("skip_empty=False")
        return f"DetectionTransformer({', '.join(parts)})"

    def process(self, source: Stream[Any], target: Stream[ImageDetections2D]) -> None:
        for page in source.fetch_pages():
            for obs in page:
                self._detect_and_append(obs, target)

    def on_append(self, obs: Observation[Any], target: Stream[ImageDetections2D]) -> None:
        self._detect_and_append(obs, target)

    def _detect_and_append(self, obs: Observation[Any], target: Stream[ImageDetections2D]) -> None:
        try:
            detections = self.model.query_detections(obs.data, self.query)
        except Exception:
            logger.warning("Detection failed for obs %s, skipping", obs.id, exc_info=True)
            return

        count = len(detections)
        if count == 0 and self.skip_empty:
            return

        # Strip image refs to avoid duplicating image data in storage
        detections.image = None
        for det in detections.detections:
            det.image = None

        tags = {**(obs.tags or {}), "query": self.query, "count": count}
        target.append(detections, ts=obs.ts, pose=obs.pose, tags=tags, parent_id=obs.id)
