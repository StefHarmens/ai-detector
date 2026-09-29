import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Lock, Thread
from time import sleep

from aidetector.detection.validator import Validator
from aidetector.detection.yolo import YoloRunner
from aidetector.exporters.disk import DiskExporter
from aidetector.exporters.exporter import Exporter
from aidetector.exporters.telegram import TelegramExporter
from aidetector.exporters.webhook import WebhookExporter
from aidetector.sources.hires import HiresBuffer, hires_buffers
from aidetector.sources.source import SourceProvider
from aidetector.utils.config import (
    ChatConfig,
    Config,
    Detection,
    DetectionConfig,
    DetectorConfig,
    DiskConfig,
    ImageSet,
    OnnxConfig,
    VLMConfig,
    WebhookConfig,
    YoloConfig,
    confidence_matches,
    matching_confidences,
    max_confidence,
)
from numpy import ndarray
from typing_extensions import Self


class Detector:
    logger = logging.getLogger(__name__)
    detections: defaultdict[str, list[Detection]]
    detection: DetectionConfig
    yolo_config: YoloConfig | None
    yolo_runner: YoloRunner | None
    source_provider: SourceProvider
    validator: Validator
    exporters: list[Exporter]
    running: bool
    export_executor: ThreadPoolExecutor
    last_frame_time: datetime
    last_detection_time: dict[str, dict[str, datetime]]
    camera_names: dict[str, str]
    hires: dict[str, HiresBuffer]

    def __init__(
        self,
        detection: DetectionConfig,
        yolo_config: YoloConfig | None,
        validator: Validator,
        exporters: list[Exporter],
        onnx_config: OnnxConfig,
    ):
        self.detections = defaultdict(list)
        self.detection = detection
        self.yolo_config = yolo_config
        self.hires = hires_buffers(detection, camera_names(detection))
        self.source_provider = SourceProvider(detection, self._feed_hires)
        self.yolo_runner = (
            YoloRunner(yolo_config, onnx_config, self.source_provider.sources)
            if yolo_config is not None
            else None
        )
        self.validator = validator
        self.exporters = exporters
        self.running = True
        self.export_executor = ThreadPoolExecutor()
        self.last_frame_time = datetime.min
        self.last_detection_time = {}
        self.camera_names = camera_names(detection)
        # The frame thread and the timeout monitor both finish events.
        self.lock = Lock()

    @classmethod
    def from_config(cls, config: Config, detector: DetectorConfig) -> list[Self]:
        exporters: list[Exporter] = []
        if detector.exporters is not None:
            config_exporter_map = {
                "telegram": (ChatConfig, TelegramExporter),
                "webhook": (WebhookConfig, WebhookExporter),
                "disk": (DiskConfig, DiskExporter),
            }

            for config_name, (config_cls, exporter_cls) in config_exporter_map.items():
                config_obj = getattr(detector.exporters, config_name, []) or []
                config_list = (
                    [config_obj] if isinstance(config_obj, config_cls) else config_obj
                )
                for item in config_list:
                    exporters.append(exporter_cls(item))

        validator = Validator.from_config(
            [detector.vlm]
            if isinstance(detector.vlm, VLMConfig)
            else detector.vlm or []
        )

        return [
            cls(detector.detection, detector.yolo, validator, exporters, config.onnx)
        ]

    def _generate_frames(self):
        for batch in self.source_provider.iter_batches():
            if not self.running:
                return
            self._handle_frame_batch(batch)

    def _handle_frame_batch(self, batch: dict[str, list[tuple[datetime, ndarray]]]):
        if (
            datetime.now() - self.last_frame_time
        ).total_seconds() < self.detection.interval:
            sleep_for = max(
                0,
                self.detection.interval
                - (datetime.now() - self.last_frame_time).total_seconds(),
            )
            self.logger.info("Waiting for %f seconds before next detection", sleep_for)
            sleep(sleep_for)
            return
        self.last_frame_time = datetime.now()

        if self.yolo_runner and self.yolo_config:
            if self.yolo_config.tracking:
                tracked_results = self.yolo_runner.track_sources(batch)
                for tracked in tracked_results:
                    self._handle_yolo_result(
                        tracked.source,
                        tracked.result,
                        tracked.frames,
                    )
                return

            frames = [frames[-1][1] for frames in batch.values()]
            results = self.yolo_runner.detect(frames)
            for source, result in zip(batch.keys(), results):
                self._handle_yolo_result(source, result, batch[source])
            return

        for source, frames in batch.items():
            self._process(
                source,
                [Detection(frames[-1][0], ImageSet(frames[-1][1]), {})],
            )

    def _handle_yolo_result(
        self, source: str, result, frames: list[tuple[datetime, ndarray]]
    ):
        if self.yolo_config is None or self.yolo_runner is None:
            return

        detections = self.yolo_runner.detections_from_result(result, frames)
        if detections:
            self._process(source, detections)
            return

        self.logger.debug("Confidence does not match")
        latest_detection = self._latest_detection(source)
        if not latest_detection:
            return
        time_since_latest_detection = (
            (frames[-1][0] - latest_detection.date).total_seconds()
            if latest_detection
            else 0
        )
        if self.yolo_config.include_trailing_time > time_since_latest_detection:
            self.logger.info(
                "Including trailing frames: %f seconds", time_since_latest_detection
            )
            detections = [
                Detection(frame[0], ImageSet(frame[1]), {})
                for frame in frames
            ]
            self._process(source, detections)

    def start(self):
        def monitor_timeouts():
            self.logger.info("Starting timeout monitor")
            while self.running:
                self.logger.debug("Checking for timeouts")
                try:
                    for source in list(self.detections.keys()):
                        self._process(source)
                except Exception:
                    self.logger.exception("Error in timeout monitor")
                sleep(1)

        def frame_producer():
            try:
                self._generate_frames()
            finally:
                self.stop()

        for buffer in self.hires.values():
            buffer.start()
        Thread(target=monitor_timeouts, daemon=True).start()
        thread = Thread(target=frame_producer)
        thread.start()
        return thread

    def stop(self) -> None:
        """Stops reading frames, finishes the events that are still being
        collected and waits until all exports are sent."""
        self.running = False
        self.source_provider.close()
        for buffer in self.hires.values():
            buffer.stop()
        with self.lock:
            for source in list(self.detections):
                if self.detections[source]:
                    self._export(source)
        self.export_executor.shutdown(wait=True)

    def _submit(self, task) -> None:
        try:
            self.export_executor.submit(task)
        except RuntimeError:
            # A batch that was already being processed when the detector stopped.
            self.logger.warning("Detector is stopping, event is not exported")

    def _process(self, source: str, detections: list[Detection] | None = None):
        with self.lock:
            if not self.running:
                # stop() already finished the events; drop late frames.
                return
            if self._timeout_exceeded(source):
                self._export(source)

            if detections:
                if not self.detections[source]:
                    self._hold_hires(source, detections[0].date)
                for detection in detections:
                    self.detections[source].append(detection)

            if self._time_exceeded(source):
                self._export(source)

    def _export(self, source: str):
        try:
            self._export_event(source)
        finally:
            # The frames of this mount are copied or not needed any more.
            buffer = self.hires.get(source)
            if buffer is not None:
                buffer.release()

    def _export_event(self, source: str):
        all_detections = self.detections[source]
        for detection in all_detections:
            detection.source = source
            detection.camera = self.camera_names.get(source, source)
        detections = self._alert_detections(all_detections)
        if self._has_min_detections(detections):
            best_detection = max(detections, key=lambda x: max_confidence(x.confidence))
            self._attach_hires(source, best_detection, detections)

            matching_confs = (
                matching_confidences(
                    best_detection.confidence, self.yolo_config.confidence
                )
                if self.yolo_config
                else []
            )
            if self.yolo_config and not self._cooldown_exceeded(source, matching_confs):
                self.logger.info(
                    "Not exporting, cooldown not exceeded for %s", matching_confs
                )
                self.detections[source] = []
                return

            self.logger.info(
                "Finished collecting with %s detections over %s seconds with max confidence %s",
                len(detections),
                (detections[-1].date - detections[0].date).total_seconds(),
                max_confidence(best_detection.confidence),
            )

            def export_task():
                validated = self.validator.validate(best_detection, detections)

                if validated is not False and self.yolo_config:
                    last_detection_time = self.last_detection_time.get(source, {})
                    for class_name in matching_confs:
                        last_detection_time[class_name] = best_detection.date
                    self.last_detection_time[source] = last_detection_time

                for exporter in self.exporters:
                    if _is_review(exporter):
                        continue
                    try:
                        exporter.export(best_detection, detections, validated)
                    except Exception:
                        self.logger.exception(
                            f"Exporter {exporter.__class__.__name__} failed"
                        )

            self._submit(export_task)
        elif detections:
            confidences = [
                max_confidence(detection.confidence)
                for detection in detections
                if detection.confidence
            ]
            self.logger.info(
                "Not exporting, only %s/%s confident frame(s) (need %s): confidences %s",
                len(confidences),
                len(detections),
                self.yolo_config.frames_min if self.yolo_config else 0,
                confidences,
            )
            if any(detection.confidence for detection in all_detections):
                self._export_review(all_detections)
        self.detections[source] = []

    def _hold_hires(self, source: str, start: datetime) -> None:
        """A mount starts: its 4K frames, from before_seconds before it, stay
        until it is handled, however long it lasts."""
        buffer = self.hires.get(source)
        if buffer is not None:
            buffer.hold(start - timedelta(seconds=buffer.config.before_seconds))

    def _feed_hires(self, source: str, frame: ndarray) -> None:
        buffer = self.hires.get(source)
        if buffer is not None and buffer.from_detection:
            buffer.feed(frame)

    def _attach_hires(
        self, source: str, best_detection: Detection, detections: list[Detection]
    ) -> None:
        """Copies the high-resolution frames of the event now, before the buffer
        drops them while the exporters are still busy."""
        buffer = self.hires.get(source)
        if buffer is None:
            return
        start = detections[0].date - timedelta(seconds=buffer.config.before_seconds)
        best_detection.hires = buffer.frames_between(start, datetime.now())
        if not best_detection.hires:
            self.logger.warning("No high-resolution frames for this event on %s", source)

    def _alert_detections(self, detections: list[Detection]) -> list[Detection]:
        """Drops the boxes below yolo.confidence, which only count for review."""
        if self.yolo_config is None or self.yolo_config.review_confidence is None:
            return detections
        threshold = self.yolo_config.confidence

        def matches(label: str | None, confidence: float | None) -> bool:
            return (
                label is not None
                and confidence is not None
                and confidence_matches({label: confidence}, threshold)
            )

        return [
            Detection(
                detection.date,
                detection.images.with_crops(
                    [
                        crop
                        for crop in detection.images.crops
                        if matches(crop.label, crop.confidence)
                    ]
                ),
                {
                    label: confidence
                    for label, confidence in detection.confidence.items()
                    if matches(label, confidence)
                },
                source=detection.source,
                camera=detection.camera,
            )
            for detection in detections
        ]

    def _export_review(self, detections: list[Detection]) -> None:
        """Sends an event that did not become an alert to the review exporters,
        so it can be sorted into good or bad by hand."""
        exporters = [exporter for exporter in self.exporters if _is_review(exporter)]
        if not exporters:
            return
        best_detection = max(detections, key=lambda x: max_confidence(x.confidence))
        self.logger.info(
            "Exporting for review: %s detections with max confidence %s",
            len(detections),
            max_confidence(best_detection.confidence),
        )

        def export_task():
            for exporter in exporters:
                try:
                    exporter.export(best_detection, detections, None)
                except Exception:
                    self.logger.exception(
                        f"Exporter {exporter.__class__.__name__} failed"
                    )

        self._submit(export_task)

    def _has_min_detections(self, detections: list[Detection]) -> bool:
        detections_with_confidence = [
            detection for detection in detections if detection.confidence
        ]
        return len(detections_with_confidence) >= (
            self.yolo_config.frames_min if self.yolo_config else 0
        )

    def _latest_detection(self, source: str) -> Detection | None:
        detections = self.detections[source]
        if not detections:
            return None
        detections_with_confidence = [
            detection for detection in detections if detection.confidence
        ]
        return detections_with_confidence[-1] if detections_with_confidence else None

    def _cooldown_exceeded(self, source: str, matching_confidences: list[str]) -> bool:
        yolo_config = self.yolo_config
        if yolo_config is None:
            return True

        def cooldown_for(name: str) -> float:
            return (
                yolo_config.cooldown[name]
                if isinstance(yolo_config.cooldown, dict)
                else yolo_config.cooldown
            )

        return any(
            datetime.now()
            - self.last_detection_time.get(source, {}).get(name, datetime.min)
            > timedelta(seconds=cooldown_for(name))
            for name in matching_confidences
        )

    def _time_exceeded(self, source: str) -> bool:
        detections = self.detections[source]
        if not detections:
            return False
        now = datetime.now()
        time_collecting = (now - detections[0].date).total_seconds()
        time_collecting_exceeded = time_collecting > (
            self.yolo_config.time_max if self.yolo_config else 0
        )
        return time_collecting_exceeded

    def _timeout_exceeded(self, source: str) -> bool:
        latest_detection = self._latest_detection(source)
        if not latest_detection:
            return False
        now = datetime.now()
        timeout = (now - latest_detection.date).total_seconds()
        return (
            timeout > self.yolo_config.timeout
            if self.yolo_config and self.yolo_config.timeout
            else False
        )


def _is_review(exporter: Exporter) -> bool:
    return getattr(getattr(exporter, "config", None), "review", False)


def camera_names(detection: DetectionConfig) -> dict[str, str]:
    sources = (
        [detection.source] if isinstance(detection.source, str) else detection.source
    )
    names = [detection.name] if isinstance(detection.name, str) else detection.name or []
    # Stream URLs often embed credentials, so they never double as a display name.
    return {
        source: names[index]
        if index < len(names)
        else source
        if "://" not in source
        else f"Camera {index + 1}"
        for index, source in enumerate(sources)
    }
