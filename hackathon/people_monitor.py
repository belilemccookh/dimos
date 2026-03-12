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

"""
People Intelligence Monitor — Phase 4

Leverages existing DimOS perception stack:
- Subscribes to Detection2DModule's output (YOLO detections via LCM)
  instead of running a duplicate YOLO instance
- Uses EmbeddingIDSystem (ReID) for persistent person IDs across track resets
- Classifies activity per person via Claude Haiku (new — not in existing stack)
- Publishes person sightings via PickleLCM for the dashboard

The dashboard (WebsocketVisModule) subscribes and emits `person_sighting`
SocketIO events to render person cards in real-time.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import threading
import time

import anthropic

from dimos.protocol.pubsub.impl.lcmpubsub import PickleLCM, Topic
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

# LCM topics
PEOPLE_TOPIC = "/people_intelligence"
DETECTION_TOPIC = "/detector2d/detections"

# How often to classify activity per person (seconds)
CLASSIFY_INTERVAL = 10.0

# Max persons to track simultaneously
MAX_PERSONS = 10


@dataclass
class PersonState:
    """State for a tracked person."""

    person_id: str  # "person-1", "person-2", etc.
    long_term_id: int  # ReID persistent ID (-1 if not yet assigned)
    track_id: int  # YOLO's ByteTrack ID
    first_seen: float = 0.0
    last_seen: float = 0.0
    last_classified: float = 0.0
    current_activity: str = "detected"
    activity_log: list[dict] = field(default_factory=list)
    bbox: tuple[float, float, float, float] = (0, 0, 0, 0)
    crop_b64: str = ""  # latest thumbnail as base64 jpeg


class PeopleMonitor:
    """People intelligence monitor using existing DimOS perception stack.

    Subscribes to Detection2DModule's LCM output (reusing existing YOLO),
    uses EmbeddingIDSystem for persistent ReID, and adds activity
    classification via Claude Haiku.

    Started by WebsocketVisModule alongside AgentMessageMonitor.
    """

    def __init__(
        self,
        detection_topic: str = DETECTION_TOPIC,
        image_topic: str = "color_image",
    ) -> None:
        # Person tracking state
        self._persons: dict[int, PersonState] = {}  # long_term_id → PersonState
        self._track_to_person: dict[int, int] = {}  # track_id → long_term_id
        self._next_person_num = 1
        self._lock = threading.Lock()

        # External services
        self._reid = None  # EmbeddingIDSystem — lazy init
        self._claude = None  # Anthropic client — lazy init

        # LCM pub/sub
        try:
            self._lcm = PickleLCM()
            self._lcm.start()
        except Exception as e:
            logger.warning(f"PeopleMonitor: PickleLCM init failed: {e}")
            self._lcm = None
        self._detection_topic = detection_topic
        self._image_topic = image_topic

        self._stopped = False
        self._callbacks: list = []

    def start(self) -> None:
        """Initialize ReID, Claude API, and subscribe to detection + image streams."""

        # Initialize ReID (EmbeddingIDSystem) — reuses existing TorchReIDModel
        try:
            from dimos.models.embedding import TorchReIDModel
            from dimos.perception.detection.reid.embedding_id_system import (
                EmbeddingIDSystem,
            )

            self._reid = EmbeddingIDSystem(model=TorchReIDModel, padding=0)
            logger.info("PeopleMonitor: ReID (EmbeddingIDSystem) initialized")
        except Exception as e:
            logger.warning(f"PeopleMonitor: ReID unavailable, using track_id only: {e}")

        # Initialize Claude for activity classification
        try:
            self._claude = anthropic.Anthropic()
            logger.info("PeopleMonitor: Claude API ready")
        except Exception as e:
            logger.warning(f"PeopleMonitor: Claude API unavailable: {e}")

        # Subscribe to Detection2DModule's detections via LCM
        try:
            from dimos.core.transport import LCMTransport
            from dimos.msgs.vision_msgs import Detection2DArray

            self._det_transport = LCMTransport(self._detection_topic, Detection2DArray)
            self._det_transport.subscribe(self._on_detections)
            logger.info(f"PeopleMonitor: subscribed to detections on {self._detection_topic}")
        except Exception as e:
            logger.warning(f"PeopleMonitor: Failed to subscribe to detections: {e}")
            # Fallback: subscribe to raw camera frames and run YOLO ourselves
            self._start_fallback_detector()

        # Subscribe to camera frames for person crops (needed for ReID + thumbnails)
        # Blueprint uses LCMTransport for color_image, not pSHMTransport
        try:
            from dimos.core.transport import LCMTransport as ImgLCMTransport
            from dimos.msgs.sensor_msgs import Image as SensorImage

            self._image_transport = ImgLCMTransport(f"/{self._image_topic}", SensorImage)
            self._latest_image = None
            self._image_transport.subscribe(self._on_image)
            logger.info(f"PeopleMonitor: subscribed to images on /{self._image_topic} (LCM)")
        except Exception as e:
            logger.warning(f"PeopleMonitor: Failed to subscribe to images: {e}")

    def _start_fallback_detector(self) -> None:
        """Fallback: run YOLO directly if Detection2DModule isn't in the blueprint."""
        try:
            from dimos.core.transport import pSHMTransport
            from dimos.perception.detection.detectors.yolo import Yolo2DDetector

            self._fallback_detector = Yolo2DDetector(model_name="yolo11n.pt")
            transport = pSHMTransport(self._image_topic)
            self._last_detect_time = 0.0
            transport.subscribe(self._on_fallback_frame)
            logger.info("PeopleMonitor: fallback YOLO detector initialized")
        except Exception as e:
            logger.warning(f"PeopleMonitor: Fallback detector also failed: {e}")

    def _on_fallback_frame(self, image) -> None:  # type: ignore[no-untyped-def]
        """Fallback: run YOLO on raw frames if Detection2DModule not available."""
        if self._stopped:
            return
        now = time.time()
        if now - self._last_detect_time < 0.5:  # 2Hz
            return
        self._last_detect_time = now
        self._latest_image = image
        threading.Thread(target=self._run_fallback_detect, args=(image, now), daemon=True).start()

    def _run_fallback_detect(self, image, now: float) -> None:  # type: ignore[no-untyped-def]
        try:
            detections = self._fallback_detector.process_image(image)
            self._process_detections(detections, image, now)
        except Exception as e:
            logger.warning(f"PeopleMonitor: fallback detection failed: {e}")

    def stop(self) -> None:
        self._stopped = True

    def subscribe(self, callback) -> None:  # type: ignore[no-untyped-def]
        """Subscribe to person sighting updates (called by WebsocketVisModule)."""
        self._callbacks.append(callback)

    def _on_image(self, image) -> None:  # type: ignore[no-untyped-def]
        """Cache latest camera frame for crop generation."""
        self._latest_image = image

    def _on_detections(self, det_array) -> None:  # type: ignore[no-untyped-def]
        """Handle Detection2DArray from Detection2DModule (via LCM).

        Converts ROS-format Detection2DArray back to ImageDetections2D
        for processing with ReID.
        """
        if self._stopped:
            return

        now = time.time()
        image = getattr(self, "_latest_image", None)
        if image is None:
            return

        try:
            from dimos.perception.detection.type.detection2d.imageDetections2D import (
                ImageDetections2D,
            )

            detections = ImageDetections2D.from_ros_detection2d_array(image, det_array)
            self._process_detections(detections, image, now)
        except Exception as e:
            logger.warning(f"PeopleMonitor: detection processing failed: {e}")

    def _process_detections(self, detections, image, now: float) -> None:  # type: ignore[no-untyped-def]
        """Process detections: ReID, crop, classify, publish."""
        # ROS round-trip loses the name field — filter by class_id instead.
        # YOLO class 0 = "person". Also accept name == "person" for fallback detector.
        # Apply confidence threshold + minimum bbox area to reject false positives
        # (bags, stands, objects misclassified as people).
        MIN_BBOX_AREA = 2000  # pixels² — reject tiny false-positive boxes

        person_dets = []
        for d in detections.detections:
            if not (d.name == "person" or d.class_id == 0):
                continue
            x1, y1, x2, y2 = d.bbox
            w, h = x2 - x1, y2 - y1
            if w * h < MIN_BBOX_AREA:
                continue
            person_dets.append(d)

        if not person_dets:
            return

        for det in person_dets[:MAX_PERSONS]:
            track_id = det.track_id
            if track_id is None or track_id < 0:
                continue

            # Get persistent ID via ReID (or fall back to track_id)
            long_term_id = track_id
            if self._reid is not None:
                try:
                    reid_id = self._reid.register_detection(det)
                    if reid_id >= 0:
                        long_term_id = reid_id
                except Exception:
                    pass

            with self._lock:
                if long_term_id not in self._persons:
                    pid = f"person-{self._next_person_num}"
                    self._next_person_num += 1
                    self._persons[long_term_id] = PersonState(
                        person_id=pid,
                        long_term_id=long_term_id,
                        track_id=track_id,
                        first_seen=now,
                        last_seen=now,
                    )
                person = self._persons[long_term_id]

            person.last_seen = now
            person.track_id = track_id
            person.bbox = det.bbox

            # Get crop thumbnail
            try:
                crop = det.cropped_image(padding=10)
                crop_cv = crop.to_opencv()
                import cv2

                _, buf = cv2.imencode(".jpg", crop_cv, [cv2.IMWRITE_JPEG_QUALITY, 60])
                person.crop_b64 = base64.b64encode(buf.tobytes()).decode()
            except Exception:
                pass

            # Classify activity if enough time has passed
            needs_classify = (now - person.last_classified) >= CLASSIFY_INTERVAL
            if needs_classify and self._claude and person.crop_b64:
                # Run classification in background to not block detection loop
                threading.Thread(
                    target=self._classify_activity,
                    args=(person,),
                    daemon=True,
                ).start()

            # Publish sighting
            self._publish_person(person)

    def _classify_activity(self, person: PersonState) -> None:
        """Send person crop to Claude Haiku for activity classification."""
        try:
            response = self._claude.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=50,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": person.crop_b64,
                                },
                            },
                            {
                                "type": "text",
                                "text": (
                                    "What is this person doing? Reply with ONLY a short "
                                    "activity label (2-5 words), e.g. 'working on laptop', "
                                    "'talking on phone', 'walking', 'eating'. No other text."
                                ),
                            },
                        ],
                    }
                ],
            )
            activity = response.content[0].text.strip().lower()
            if activity and len(activity) < 60:
                person.current_activity = activity
                person.last_classified = time.time()
                person.activity_log.append(
                    {
                        "time": time.strftime("%H:%M:%S"),
                        "ts": time.time(),
                        "activity": activity,
                    }
                )
                # Keep log bounded
                if len(person.activity_log) > 50:
                    person.activity_log = person.activity_log[-50:]
                # Re-publish with updated activity
                self._publish_person(person)
        except Exception as e:
            logger.debug(f"PeopleMonitor: classify failed for {person.person_id}: {e}")

    def _publish_person(self, person: PersonState) -> None:
        """Publish person sighting via LCM and callbacks."""
        sighting = {
            "person_id": person.person_id,
            "long_term_id": person.long_term_id,
            "track_id": person.track_id,
            "activity": person.current_activity,
            "activity_log": person.activity_log[-10:],  # last 10 entries
            "bbox": person.bbox,
            "first_seen": person.first_seen,
            "last_seen": person.last_seen,
            "crop_b64": person.crop_b64,
        }
        # Publish via LCM
        try:
            if self._lcm is not None:
                self._lcm.publish(Topic(PEOPLE_TOPIC), sighting)
        except Exception:
            pass
        # Notify direct callbacks
        for cb in self._callbacks:
            try:
                cb(sighting)
            except Exception:
                pass

    def get_all_persons(self) -> list[dict]:
        """Get snapshot of all tracked persons."""
        with self._lock:
            return [
                {
                    "person_id": p.person_id,
                    "long_term_id": p.long_term_id,
                    "track_id": p.track_id,
                    "activity": p.current_activity,
                    "activity_log": p.activity_log[-10:],
                    "first_seen": p.first_seen,
                    "last_seen": p.last_seen,
                    "crop_b64": p.crop_b64,
                }
                for p in self._persons.values()
            ]
