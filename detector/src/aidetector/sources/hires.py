import logging
import os
import subprocess
import time
from collections import deque
from datetime import datetime, timedelta
from threading import Event, Lock, Thread

import cv2
from imageio_ffmpeg import get_ffmpeg_exe
from numpy import ndarray

from aidetector.utils.config import (
    Crop,
    Detection,
    DetectionConfig,
    HiresConfig,
    HiresFrame,
    ImageSet,
)

logger = logging.getLogger(__name__)

_JPEG_START = b"\xff\xd8"
_JPEG_END = b"\xff\xd9"
_READ_SIZE = 1 << 16


def split_jpegs(buffer: bytearray) -> list[bytes]:
    """Takes the complete JPEG images from the start of the buffer and removes
    them from it. FFmpeg's MJPEG output has no embedded thumbnails, so the first
    end marker after a start marker ends the image."""
    images = []
    while True:
        start = buffer.find(_JPEG_START)
        if start < 0:
            buffer.clear()
            return images
        end = buffer.find(_JPEG_END, start + 2)
        if end < 0:
            del buffer[:start]
            return images
        images.append(bytes(buffer[start : end + 2]))
        del buffer[: end + 2]


class HiresBuffer:
    """Reads the high-resolution stream of one camera at a low frame rate and
    keeps the last frames as JPEG, so an event can use the frames from just
    before it started."""

    def __init__(self, source: str | None, config: HiresConfig):
        # None: frames come from the detection stream through feed().
        self.source = source
        self.config = config
        self.fed = 0.0
        self.frames: deque[HiresFrame] = deque()
        self.lock = Lock()
        self.stop_event = Event()
        self.process: subprocess.Popen | None = None
        self.thread: Thread | None = None

    @property
    def from_detection(self) -> bool:
        return self.source is None

    def feed(self, frame: ndarray) -> None:
        """Keeps a full-size frame of the detection stream, at most fps per
        second. Encoding a 4K frame as JPEG takes some tens of milliseconds,
        far less than decoding the stream a second time."""
        now = time.monotonic()
        if now - self.fed < 1 / self.config.fps:
            return
        self.fed = now
        success, encoded = cv2.imencode(
            ".jpg", frame, (int(cv2.IMWRITE_JPEG_QUALITY), self.config.quality)
        )
        if success:
            self.add(HiresFrame(datetime.now(), encoded.tobytes()))

    def command(self) -> list[str]:
        assert self.source is not None
        command = [get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error"]
        if self.source.lower().startswith("rtsp"):
            command += ["-rtsp_transport", "tcp"]
        if self.config.hwaccel:
            command += ["-hwaccel", self.config.hwaccel]
        # Frame threads each hold 4K frames: one thread halves the memory
        # (about 350 MB per camera), and hardware decoding needs no more.
        command += ["-threads", "1"]
        # FFmpeg's qscale 2 (best) to 31 (worst), mapped from a JPEG quality.
        qscale = max(2, min(31, round(31 - (self.config.quality / 100) * 29)))
        return command + [
            "-i",
            self.source,
            "-an",
            "-vf",
            f"fps={self.config.fps}",
            "-c:v",
            "mjpeg",
            "-q:v",
            str(qscale),
            "-f",
            "image2pipe",
            "pipe:1",
        ]

    def start(self) -> None:
        if self.from_detection:
            return
        self.thread = Thread(
            target=self._run, name=f"hires-{(self.source or '')[-12:]}", daemon=True
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        process = self.process
        if process is not None and process.poll() is None:
            process.kill()

    def add(self, frame: HiresFrame) -> None:
        with self.lock:
            self.frames.append(frame)
            oldest = frame.date - timedelta(seconds=self.config.seconds)
            while self.frames and self.frames[0].date < oldest:
                self.frames.popleft()

    def frames_between(self, start: datetime, end: datetime) -> list[HiresFrame]:
        with self.lock:
            return [frame for frame in self.frames if start <= frame.date <= end]

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._read()
            except Exception:
                logger.exception("High-resolution stream failed for a camera")
            self.stop_event.wait(5)

    def _read(self) -> None:
        self.process = subprocess.Popen(
            self.command(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        stdout = self.process.stdout
        assert stdout is not None
        buffer = bytearray()
        try:
            while not self.stop_event.is_set():
                chunk = os.read(stdout.fileno(), _READ_SIZE)
                if not chunk:
                    return
                buffer += chunk
                for jpeg in split_jpegs(buffer):
                    self.add(HiresFrame(datetime.now(), jpeg))
        finally:
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait()


def hires_buffers(detection: DetectionConfig) -> dict[str, HiresBuffer]:
    """Returns a buffer per detection source that has a high-resolution stream."""
    if detection.hires is None:
        return {}
    sources = (
        [detection.source] if isinstance(detection.source, str) else detection.source
    )
    if detection.hires.source is None:
        return {source: HiresBuffer(None, detection.hires) for source in sources}
    hires_sources = (
        [detection.hires.source]
        if isinstance(detection.hires.source, str)
        else detection.hires.source
    )
    if len(hires_sources) != len(sources):
        raise ValueError(
            "detection.hires.source needs one stream (or null) per detection.source,"
            f" got {len(hires_sources)} for {len(sources)} sources"
        )
    return {
        source: HiresBuffer(hires_source, detection.hires)
        for source, hires_source in zip(sources, hires_sources)
        if hires_source
    }


def nearest_frame(frames: list[HiresFrame], date: datetime) -> HiresFrame | None:
    if not frames:
        return None
    return min(frames, key=lambda frame: abs((frame.date - date).total_seconds()))


def scale_crop(crop: Crop, scale_x: float, scale_y: float) -> Crop:
    return Crop(
        round(crop.x1 * scale_x),
        round(crop.y1 * scale_y),
        round(crop.x2 * scale_x),
        round(crop.y2 * scale_y),
        label=crop.label,
        confidence=crop.confidence,
    )


def hires_detection(detection: Detection) -> Detection | None:
    """Returns the detection with the high-resolution frame closest to it and
    its boxes scaled to that frame, or None without high-resolution frames."""
    frame = nearest_frame(detection.hires or [], detection.date)
    if frame is None:
        return None
    try:
        image = frame.jpg
    except ValueError:
        logger.warning("Skipping a high-resolution frame that cannot be decoded")
        return None
    height, width = image.shape[:2]
    scale_x = width / detection.images.width
    scale_y = height / detection.images.height
    return Detection(
        frame.date,
        ImageSet(
            image, [scale_crop(crop, scale_x, scale_y) for crop in detection.images.crops]
        ),
        detection.confidence,
        source=detection.source,
        camera=detection.camera,
    )
