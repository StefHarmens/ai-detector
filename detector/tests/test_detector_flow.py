import json
from collections import defaultdict
from datetime import datetime, timedelta
from threading import Lock
from time import sleep
from types import SimpleNamespace

import numpy as np

from aidetector.detection.detector import Detector
from aidetector.detection.yolo import TrackedSourceResult
from aidetector.exporters.disk import DiskExporter
from aidetector.exporters.telegram import TelegramExporter
from aidetector.exporters.webhook import WebhookExporter
from aidetector.review import ReviewSession
from aidetector.utils.config import (
    ChatConfig,
    Config,
    Crop,
    Detection,
    DetectionConfig,
    DetectorConfig,
    DiskConfig,
    ExportersConfig,
    ImageSet,
    OnnxConfig,
    WebhookConfig,
    YoloConfig,
)


def make_detection(date: datetime, confidence: dict[str, float] | None = None) -> Detection:
    image = np.zeros((80, 120, 3), dtype=np.uint8)
    return Detection(date, ImageSet(image), confidence or {"cow": 0.9})


class ImmediateExecutor:
    def submit(self, fn):
        fn()

    def shutdown(self, wait=True):
        pass


class FakeValidator:
    def __init__(self, value):
        self.value = value

    def validate(self, best_detection, detections):
        return self.value


class RecordingExporter:
    def __init__(self):
        self.calls = []

    def export(self, best_detection, detections, validated):
        self.calls.append((best_detection, detections, validated))


class ReviewExporter(RecordingExporter):
    config = SimpleNamespace(review=True)


def make_review_detection(date: datetime, *confidences: float) -> Detection:
    image = np.zeros((80, 120, 3), dtype=np.uint8)
    crops = [
        Crop(10 * i, 10, 10 * i + 20, 40, label="cow", confidence=confidence)
        for i, confidence in enumerate(confidences)
    ]
    return Detection(date, ImageSet(image, crops), {"cow": max(confidences)})


def make_review_detector(
    frames_min: int = 2,
) -> tuple[Detector, RecordingExporter, RecordingExporter]:
    alert, review = RecordingExporter(), ReviewExporter()
    detector = make_detector()
    detector.yolo_config = YoloConfig(
        model="model.onnx",
        confidence=0.85,
        review_confidence=0.7,
        frames_min=frames_min,
    )
    detector.exporters = [alert, review]
    return detector, alert, review


class RecordingYoloRunner:
    def __init__(self):
        self.calls = []

    def detect(self, frames):
        self.calls.append(("detect", len(frames)))
        return [f"batch-{index}" for index in range(len(frames))]

    def track_sources(self, batch):
        self.calls.append(("track_sources", list(batch)))
        return [
            TrackedSourceResult(source, f"{source}-tracked", frames)
            for source, frames in batch.items()
        ]


def make_detector() -> Detector:
    detector = Detector.__new__(Detector)
    detector.detections = defaultdict(list)
    detector.yolo_config = None
    detector.validator = FakeValidator(True)
    detector.exporters = []
    detector.export_executor = ImmediateExecutor()
    detector.last_detection_time = {}
    detector.last_frame_time = datetime.min
    detector.camera_names = {}
    detector.lock = Lock()
    detector.running = True
    return detector


def test_detector_from_config_builds_exporters():
    detector_config = DetectorConfig(
        detection=DetectionConfig(source=["video.mp4"]),
        exporters=ExportersConfig(
            disk=DiskConfig(directory="events"),
            webhook=WebhookConfig(url="https://example.test/hook"),
            telegram=ChatConfig(token="token", chat="chat-id"),
        ),
    )
    config = Config(detectors=[detector_config], onnx=OnnxConfig())

    detectors = Detector.from_config(config, detector_config)

    assert len(detectors) == 1
    assert [type(exporter) for exporter in detectors[0].exporters] == [
        TelegramExporter,
        WebhookExporter,
        DiskExporter,
    ]


def test_export_validates_exports_and_clears_detections():
    source = "camera"
    exporter = RecordingExporter()
    detector = make_detector()
    detector.exporters = [exporter]
    detector.detections[source] = [
        make_detection(datetime(2026, 1, 1, 12, 0, 0), {"cow": 0.7}),
        make_detection(datetime(2026, 1, 1, 12, 0, 2), {"cow": 0.9}),
    ]

    detector._export(source)

    assert detector.detections[source] == []
    assert len(exporter.calls) == 1
    best_detection, detections, validated = exporter.calls[0]
    assert best_detection.confidence == {"cow": 0.9}
    assert len(detections) == 2
    assert validated is True


def test_validator_without_vlms_defaults_to_validated_true():
    from aidetector.detection.validator import Validator

    validator = Validator.from_config([])
    detection = make_detection(datetime(2026, 1, 1, 12, 0, 0), {"cow": 0.9})
    assert validator.validate(detection, [detection]) is True


def test_trailing_frames_are_included_after_latest_detection():
    source = "camera"
    detector = make_detector()
    detector.yolo_config = YoloConfig(
        model="model.pt",
        confidence=0.8,
        include_trailing_time=5,
    )
    detector.yolo_runner = type(
        "FakeYoloRunner",
        (),
        {"detections_from_result": lambda *_args, **_kwargs: None},
    )()
    detected_at = datetime(2026, 1, 1, 12, 0, 0)
    detector.detections[source] = [make_detection(detected_at)]
    processed = []
    detector._process = lambda src, detections=None: processed.append((src, detections))

    detector._handle_yolo_result(
        source,
        object(),
        [(detected_at + timedelta(seconds=2), np.zeros((80, 120, 3), dtype=np.uint8))],
    )

    assert processed[0][0] == source
    trailing = processed[0][1]
    assert len(trailing) == 1
    assert trailing[0].confidence == {}


def test_detector_batches_sources_when_tracking_is_disabled():
    detector = make_detector()
    detector.detection = DetectionConfig(source=["camera-1", "camera-2"])
    detector.yolo_config = YoloConfig(model="model.pt", tracking=False)
    detector.yolo_runner = RecordingYoloRunner()
    handled = []
    detector._handle_yolo_result = (
        lambda source, result, frames: handled.append((source, result, len(frames)))
    )
    frame = np.zeros((80, 120, 3), dtype=np.uint8)

    detector._handle_frame_batch(
        {
            "camera-1": [(datetime(2026, 1, 1, 12, 0, 0), frame)],
            "camera-2": [(datetime(2026, 1, 1, 12, 0, 1), frame)],
        }
    )

    assert detector.yolo_runner.calls == [("detect", 2)]
    assert handled == [
        ("camera-1", "batch-0", 1),
        ("camera-2", "batch-1", 1),
    ]


def test_detector_tracks_sources_as_stream_batch_when_tracking_is_enabled():
    detector = make_detector()
    detector.detection = DetectionConfig(source=["camera-1", "camera-2"])
    detector.yolo_config = YoloConfig(model="model.pt", tracking=True)
    detector.yolo_runner = RecordingYoloRunner()
    handled = []
    detector._handle_yolo_result = (
        lambda source, result, frames: handled.append((source, result, len(frames)))
    )
    frame = np.zeros((80, 120, 3), dtype=np.uint8)

    detector._handle_frame_batch(
        {
            "camera-1": [
                (datetime(2026, 1, 1, 12, 0, 0), frame),
                (datetime(2026, 1, 1, 12, 0, 1), frame),
            ],
            "camera-2": [(datetime(2026, 1, 1, 12, 0, 2), frame)],
        }
    )

    assert detector.yolo_runner.calls == [("track_sources", ["camera-1", "camera-2"])]
    assert handled == [
        ("camera-1", "camera-1-tracked", 2),
        ("camera-2", "camera-2-tracked", 1),
    ]


def test_event_below_the_alert_confidence_goes_to_review():
    detector, alert, review = make_review_detector()
    detector.detections["camera"] = [
        make_review_detection(START_REVIEW, 0.75),
        make_review_detection(START_REVIEW + timedelta(seconds=1), 0.8),
    ]

    detector._export("camera")

    assert alert.calls == []
    [(best, detections, validated)] = review.calls
    assert best.confidence == {"cow": 0.8}
    assert len(detections) == 2
    assert validated is None


def test_event_with_too_few_frames_goes_to_review():
    detector, alert, review = make_review_detector(frames_min=3)
    detector.detections["camera"] = [
        make_review_detection(START_REVIEW, 0.9),
        make_review_detection(START_REVIEW + timedelta(seconds=1), 0.95),
    ]

    detector._export("camera")

    assert alert.calls == []
    assert len(review.calls) == 1


def test_alert_only_counts_and_shows_boxes_above_the_alert_confidence():
    detector, alert, review = make_review_detector(frames_min=2)
    detector.detections["camera"] = [
        make_review_detection(START_REVIEW, 0.9, 0.72),
        # A doubtful frame does not count towards frames_min.
        make_review_detection(START_REVIEW + timedelta(seconds=1), 0.75),
        make_review_detection(START_REVIEW + timedelta(seconds=2), 0.88),
    ]

    detector._export("camera")

    assert review.calls == []
    [(best, detections, _)] = alert.calls
    assert best.confidence == {"cow": 0.9}
    assert [crop.confidence for crop in best.images.crops] == [0.9]
    assert [detection.confidence for detection in detections] == [
        {"cow": 0.9},
        {},
        {"cow": 0.88},
    ]


def test_review_folder_can_be_sorted_with_review_feedback(tmp_path):
    exporter = DiskExporter(DiskConfig(directory=tmp_path / "twijfel", review=True))
    detection = make_review_detection(START_REVIEW, 0.75)
    detection.camera = "Stal Rechts"

    exporter.export(detection, [detection], None)

    [folder] = (tmp_path / "twijfel").iterdir()
    assert folder.name == "2026-01-01T12-00-00 Stal Rechts"
    metadata = json.loads((folder / "metadata.json").read_text())
    assert metadata["camera"] == "Stal Rechts"
    assert metadata["boxes"] == [
        {"x1": 0, "y1": 10, "x2": 20, "y2": 40, "label": "cow"}
    ]

    session = ReviewSession(tmp_path / "twijfel", tmp_path)
    session.decide(folder.name, "good")

    assert (tmp_path / "good" / f"{folder.name}.jpg").is_file()


START_REVIEW = datetime(2026, 1, 1, 12, 0, 0)


def test_timeout_monitor_and_frames_do_not_export_an_event_twice():
    from concurrent.futures import ThreadPoolExecutor as Pool

    exporter = RecordingExporter()
    detector = make_detector()
    detector.yolo_config = YoloConfig(model="model.onnx", frames_min=1, timeout=1)
    detector.exporters = [exporter]
    detector.detections["camera"] = [
        make_detection(datetime.now() - timedelta(seconds=5), {"cow": 0.9})
    ]
    # Widen the window between checking the timeout and clearing the event.
    detector._cooldown_exceeded = lambda source, confidences: sleep(0.05) or True

    with Pool(8) as pool:
        list(pool.map(lambda _: detector._process("camera"), range(8)))

    assert len(exporter.calls) == 1


def test_stop_exports_the_event_that_is_still_being_collected():
    exporter = RecordingExporter()
    detector = make_detector()
    detector.exporters = [exporter]
    detector.source_provider = SimpleNamespace(close=lambda: None)
    detector.running = True
    detector.detections["camera"] = [make_detection(datetime.now(), {"cow": 0.9})]

    detector.stop()

    assert len(exporter.calls) == 1
    assert detector.detections["camera"] == []


def test_frames_after_stop_are_dropped():
    exporter = RecordingExporter()
    detector = make_detector()
    detector.exporters = [exporter]
    detector.source_provider = SimpleNamespace(close=lambda: None)
    detector.stop()

    detector._process("camera", [make_detection(datetime.now(), {"cow": 0.9})])
    detector.stop()

    assert exporter.calls == []
