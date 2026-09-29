import logging
import os
import re
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
# Frames are never held longer than this, even if a mount is not released.
_MAX_HOLD = timedelta(minutes=3)


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

    def __init__(self, source: str | None, config: HiresConfig, name: str = "camera"):
        # None: frames come from the detection stream through feed().
        self.source = source
        self.config = config
        # The camera name for the log; the stream link holds a secret key.
        self.name = name
        self.fed = 0.0
        self.first: datetime | None = None
        self.interval_logged = False
        # Start of the frames to keep while a mount is going on.
        self.held: datetime | None = None
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
        width = frame.shape[1]
        if self.config.max_width and width > self.config.max_width:
            height = round(frame.shape[0] * self.config.max_width / width)
            frame = cv2.resize(
                frame, (self.config.max_width, height), interpolation=cv2.INTER_AREA
            )
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
        if self.config.keyframes_only:
            command += ["-skip_frame", "nokey"]
        # FFmpeg's qscale 2 (best) to 31 (worst), mapped from a JPEG quality.
        qscale = max(2, min(31, round(31 - (self.config.quality / 100) * 29)))
        if self.config.keyframes_only:
            # At most one frame per 1/fps seconds, without the duplicates the
            # fps filter adds when keyframes come less often.
            filters = [
                f"select='isnan(prev_selected_t)+gte(t-prev_selected_t\\,{1 / self.config.fps:g})'"
            ]
        else:
            filters = [f"fps={self.config.fps}"]
        if self.config.max_width:
            filters.append(f"scale='min(iw\\,{self.config.max_width})':-2")
        return command + [
            "-i",
            self.source,
            "-an",
            "-vf",
            ",".join(filters),
            "-fps_mode",
            "vfr",
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
        self._log_start(frame)
        with self.lock:
            self.frames.append(frame)
            oldest = frame.date - timedelta(seconds=self.config.seconds)
            if self.held is not None:
                # Keep the frames of the mount that is still going on, but
                # never more than the longest event could need.
                oldest = max(min(oldest, self.held), frame.date - _MAX_HOLD)
            while self.frames and self.frames[0].date < oldest:
                self.frames.popleft()

    def hold(self, since: datetime) -> None:
        """A mount started: keep the frames from `since` until release()."""
        with self.lock:
            self.held = since if self.held is None else min(self.held, since)

    def release(self) -> None:
        with self.lock:
            self.held = None

    def _log_start(self, frame: HiresFrame) -> None:
        """Logs once which size a camera delivers and how often, since with
        keyframes only the camera's keyframe interval sets the rate."""
        if self.first is None:
            self.first = frame.date
            try:
                height, width = frame.jpg.shape[:2]
                logger.info("High-resolution frames from %s: %dx%d", self.name, width, height)
            except Exception:
                # Only the log line; the frame is kept anyway.
                logger.warning("High-resolution frame from %s cannot be decoded", self.name)
        elif not self.interval_logged and frame.date > self.first:
            self.interval_logged = True
            logger.info(
                "High-resolution frames from %s every %.1f s",
                self.name,
                (frame.date - self.first).total_seconds(),
            )

    def frames_between(self, start: datetime, end: datetime) -> list[HiresFrame]:
        with self.lock:
            return [frame for frame in self.frames if start <= frame.date <= end]

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._read()
            except Exception:
                logger.exception("High-resolution stream failed for %s", self.name)
            self.stop_event.wait(5)

    def _read(self) -> None:
        self.process = subprocess.Popen(
            self.command(), stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdout, stderr = self.process.stdout, self.process.stderr
        assert stdout is not None and stderr is not None
        # Drained all the time, so a stream of decode warnings cannot fill
        # the pipe and stop FFmpeg; the last lines say why it stopped.
        errors: deque[str] = deque(maxlen=3)
        drain = Thread(
            target=lambda: errors.extend(
                line.decode(errors="replace").strip() for line in stderr if line.strip()
            ),
            daemon=True,
        )
        drain.start()
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
            code = self.process.wait()
            drain.join(timeout=2)
            if not self.stop_event.is_set():
                logger.warning(
                    "High-resolution stream of %s stopped (exit %s), retrying in 5 s: %s",
                    self.name,
                    code,
                    hide_keys(" | ".join(errors)) or "no message",
                )


_STREAM_KEY = re.compile(r"(rtsps?://[^/\s]+/)\S*")


def hide_keys(text: str) -> str:
    """Stream links hold a secret key; logs get pasted into chats."""
    return _STREAM_KEY.sub(r"\1<key>", text)


def hires_buffers(
    detection: DetectionConfig, names: dict[str, str] | None = None
) -> dict[str, HiresBuffer]:
    """Returns a buffer per detection source that has a high-resolution stream."""
    if detection.hires is None:
        return {}
    sources = (
        [detection.source] if isinstance(detection.source, str) else detection.source
    )
    names = names or {}

    def name(index: int, source: str) -> str:
        return names.get(source) or f"Camera {index + 1}"

    if detection.hires.source is None:
        return {
            source: HiresBuffer(None, detection.hires, name(index, source))
            for index, source in enumerate(sources)
        }
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
        source: HiresBuffer(hires_source, detection.hires, name(index, source))
        for index, (source, hires_source) in enumerate(zip(sources, hires_sources))
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
