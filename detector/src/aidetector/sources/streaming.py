import logging
from collections.abc import Callable
from threading import Condition, Thread
from time import sleep

from numpy import ndarray

from ultralytics.data.loaders import LoadStreams

from .collector import FrameCollector
from .hires import hide_keys

logger = logging.getLogger(__name__)


class StreamBatcher:
    running: bool
    threads: list[Thread]
    collector: FrameCollector
    loaders: list[LoadStreams | None]
    missing_sources: set[str]
    condition: Condition

    def __init__(
        self,
        sources: list[str],
        width: int = 1280,
        retention: int = 1,
        on_frame: Callable[[str, ndarray], None] | None = None,
    ):
        logger.info("Initializing StreamBatcher with %d sources", len(sources))
        self.running = True
        self.sources = sources
        self.loaders = [None] * len(sources)
        self.collector = FrameCollector(width, retention)
        self.threads = []
        self.missing_sources = set()
        self.condition = Condition()

        def run_loader(index: int, source: str):
            # Stream links hold a secret key; logs get pasted into chats.
            label = hide_keys(source)
            logger.info("Stream loader started for %s", label)
            while self.running:
                loader: LoadStreams | None = None
                try:
                    loader = LoadStreams(source)
                    self.loaders[index] = loader
                    for _, imgs, _ in loader:
                        if not self.running:
                            break
                        if imgs is None:
                            continue
                        if on_frame is not None:
                            # The full-size frame, before it is made smaller.
                            try:
                                on_frame(source, imgs[0])
                            except Exception:
                                logger.exception("Failed to keep a full-size frame")
                        with self.condition:
                            self.collector.add(source, imgs[0])
                            logger.debug(
                                "Received frame %d from %s",
                                self.collector.frames[source],
                                source,
                            )
                            self.condition.notify()
                except ConnectionError as error:
                    # A camera that drops out now and then; the next attempt reconnects.
                    if self.running:
                        logger.warning(
                            "Could not open stream %s: %s", label, hide_keys(str(error))
                        )
                except Exception:
                    if self.running:
                        logger.exception("Stream loader crashed for %s", label)
                finally:
                    if loader is not None:
                        try:
                            loader.close()
                        except Exception:
                            logger.info("Failed to close stream loader", exc_info=True)
                sleep(1)
            logger.info("Stream loader finished for %s", label)

        for index, source in enumerate(self.sources):
            thread = Thread(target=run_loader, args=(index, source), daemon=True)
            thread.start()
            self.threads.append(thread)

    def stop(self) -> None:
        logger.info("Stopping StreamBatcher with %d active sources", len(self.sources))
        self.running = False
        with self.condition:
            self.condition.notify_all()
        for loader in self.loaders:
            try:
                if loader is not None:
                    loader.close()
            except Exception:
                logger.info("Failed to close stream loader", exc_info=True)
        self.loaders = []
        for thread in self.threads:
            thread.join()
        self.threads = []
        logger.info("StreamBatcher stopped")

    def is_ready(self) -> bool:
        return len(self.collector.frames) == len(self.sources) or any(
            count >= 2 for count in self.collector.counts().values()
        )

    def log_missing(self, present_sources: set[str]):
        new_missing = set(self.sources) - present_sources
        intersect = new_missing & self.missing_sources
        if intersect:
            logger.warning(
                "Missing frames from sources: %s",
                ", ".join(hide_keys(source) for source in sorted(intersect)),
            )
        self.missing_sources = new_missing

    def __iter__(self):
        logger.debug("StreamBatcher iterator started")
        while self.running:
            with self.condition:
                while not self.is_ready():
                    self.condition.wait()
                snapshot = dict(self.collector.frames)
                self.collector.clear()
            if snapshot:
                logger.debug(
                    "Yielding batch with %d frames from %d sources",
                    len(snapshot),
                    len(self.sources),
                )
                self.log_missing(set(snapshot.keys()))
                yield snapshot
        logger.debug("StreamBatcher iterator stopped")


ultralytics_logger = logging.getLogger("ultralytics")


class _SuppressLoadStreamsFilter(logging.Filter):
    filter_messages = [
        "Waiting for stream ",
        " (no detections), ",
        " postprocess per image at shape (",
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if not message:
            return False
        return not any(part in message for part in self.filter_messages)


ultralytics_logger.addFilter(_SuppressLoadStreamsFilter())
