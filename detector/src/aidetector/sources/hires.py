import logging
import os
import re
import subprocess
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta
from functools import partial
from threading import Event, Lock, Thread

import cv2
from numpy import ndarray

from aidetector.sources.recording import (
    Gop,
    TsSplitter,
    decode_gops,
    gops_between,
    record_command,
)
from aidetector.utils.config import (
    Crop,
    Detection,
    DetectionConfig,
    HiresConfig,
    HiresFrame,
    ImageSet,
)

logger = logging.getLogger(__name__)

# Frames are never held longer than this, even if a mount is not released.
_MAX_HOLD = timedelta(minutes=3)
# Wait before reading a stream again; shorter when it was giving frames.
_RETRY_SECONDS = 5.0
_RETRY_AFTER_FRAMES_SECONDS = 1.0
_READ_SIZE = 1 << 16
# Frames without a keyframe before them, about 10 s of a camera: then the
# stream is probably not one FFmpeg marks keyframes in, and that is logged.
_FRAMES_WITHOUT_KEYFRAME = 250

# The frames of an event, made when the export needs them.
Clip = Callable[[], list[HiresFrame]]


class _Kept:
    """The 4K frames of one camera from the last seconds, and those of a
    mount that is still going on."""

    def __init__(self, config: HiresConfig, name: str):
        self.config = config
        # The camera name for the log; the stream link holds a secret key.
        self.name = name
        # Start of the frames to keep while a mount is going on.
        self.held: datetime | None = None
        self.lock = Lock()

    def hold(self, since: datetime) -> None:
        """A mount started: keep the frames from `since` until release()."""
        with self.lock:
            self.held = since if self.held is None else min(self.held, since)

    def release(self) -> None:
        with self.lock:
            self.held = None

    def _oldest(self, now: datetime) -> datetime:
        oldest = now - timedelta(seconds=self.config.seconds)
        if self.held is not None:
            # Keep the frames of the mount that is still going on, but
            # never more than the longest event could need.
            oldest = max(min(oldest, self.held), now - _MAX_HOLD)
        return oldest


class HiresBuffer(_Kept):
    """Keeps full-size frames of the detection stream as JPEG, when that
    stream is already the 4K one, so an event can use the frames from just
    before it started."""

    from_detection = True

    def __init__(self, config: HiresConfig, name: str = "camera"):
        super().__init__(config, name)
        self.fed = 0.0
        self.logged = False
        self.frames: deque[HiresFrame] = deque()

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

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

    def add(self, frame: HiresFrame) -> None:
        self._log_start(frame)
        with self.lock:
            self.frames.append(frame)
            oldest = self._oldest(frame.date)
            while self.frames and self.frames[0].date < oldest:
                self.frames.popleft()

    def _log_start(self, frame: HiresFrame) -> None:
        """Logs once which size the camera delivers."""
        if self.logged:
            return
        self.logged = True
        try:
            height, width = frame.jpg.shape[:2]
            logger.info("High-resolution frames from %s: %dx%d", self.name, width, height)
        except Exception:
            # Only the log line; the frame is kept anyway.
            logger.warning("High-resolution frame from %s cannot be decoded", self.name)

    def frames_between(self, start: datetime, end: datetime) -> list[HiresFrame]:
        with self.lock:
            return [frame for frame in self.frames if start <= frame.date <= end]

    def clip(self, start: datetime, end: datetime) -> Clip:
        frames = self.frames_between(start, end)
        return lambda: frames


class HiresRecorder(_Kept):
    """Records the separate 4K stream of a camera as it comes in, without
    decoding it, and decodes only the seconds of an event (see recording)."""

    from_detection = False

    def __init__(self, source: str, config: HiresConfig, name: str = "camera"):
        super().__init__(config, name)
        self.source = source
        self.gops: deque[Gop] = deque()
        self.splitter = TsSplitter()
        self.connection = 0
        self.interval_logged = False
        self.stop_event = Event()
        self.process: subprocess.Popen | None = None
        self.thread: Thread | None = None
        self.retry_seconds = _RETRY_SECONDS

    def start(self) -> None:
        self.thread = Thread(
            target=self._run, name=f"hires-{self.name}", daemon=True
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        process = self.process
        if process is not None and process.poll() is None:
            process.kill()

    def clip(self, start: datetime, end: datetime) -> Clip:
        """Takes the GOPs of an event now, which is quick, before the buffer
        drops them; decoding them is left to the export."""
        with self.lock:
            gops = list(self.gops)
            current = self.splitter.current
            if current is not None and current.frames:
                # The GOP still coming in, as far as it is.
                gops.append(
                    Gop(current.connection, current.tables, bytearray(current.data), list(current.frames))
                )
        return partial(self._decode, gops_between(gops, start, end), start, end)

    def _decode(self, gops: list[Gop], start: datetime, end: datetime) -> list[HiresFrame]:
        config = self.config
        frames = decode_gops(
            gops, start, end, config.fps, config.max_width, config.quality, config.hwaccel
        )
        if not frames and gops and config.hwaccel:
            logger.warning("Decoding the 4K frames of %s again without hardware decoding", self.name)
            frames = decode_gops(gops, start, end, config.fps, config.max_width, config.quality, None)
        return frames

    def _run(self) -> None:
        while not self.stop_event.is_set():
            self.retry_seconds = _RETRY_SECONDS
            try:
                self._read()
            except Exception:
                logger.exception("High-resolution stream failed for %s", self.name)
            self.stop_event.wait(self.retry_seconds)

    def _read(self) -> None:
        self.connection += 1
        splitter = TsSplitter(self.connection)
        with self.lock:
            self.splitter = splitter
        self.process = subprocess.Popen(
            record_command(self.source), stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdout, stderr = self.process.stdout, self.process.stderr
        assert stdout is not None and stderr is not None
        # Drained all the time, so a stream of warnings cannot fill the pipe
        # and stop FFmpeg. The first lines usually hold the cause, the last
        # ones how it ended.
        first: list[str] = []
        last: deque[str] = deque(maxlen=2)

        def drain_errors() -> None:
            for raw in stderr:
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                if len(first) < 5:
                    first.append(line)
                else:
                    last.append(line)

        drain = Thread(target=drain_errors, daemon=True)
        drain.start()
        gops = 0
        warned = False
        try:
            while not self.stop_event.is_set():
                chunk = os.read(stdout.fileno(), _READ_SIZE)
                if not chunk:
                    return
                now = datetime.now()
                with self.lock:
                    done = splitter.feed(chunk, now)
                    self.gops.extend(done)
                    if done:
                        self._trim(now)
                for gop in done:
                    gops += 1
                    self._log_interval(gop)
                if not warned and splitter.current is None and splitter.skipped >= _FRAMES_WITHOUT_KEYFRAME:
                    warned = True
                    logger.warning(
                        "High-resolution stream of %s has no keyframes that can be found;"
                        " its 4K frames cannot be used",
                        self.name,
                    )
        finally:
            if self.process.poll() is None:
                self.process.kill()
            code = self.process.wait()
            drain.join(timeout=2)
            with self.lock:
                # The last GOP of this connection, up to where it broke off.
                if splitter.current is not None and splitter.current.frames:
                    self.gops.append(splitter.current)
                    gops += 1
                splitter.current = None
            if not self.stop_event.is_set():
                # A stream that was running is back sooner: every second
                # without it is a second without 4K frames for a mount.
                self.retry_seconds = _RETRY_AFTER_FRAMES_SECONDS if gops else _RETRY_SECONDS
                logger.warning(
                    "High-resolution stream of %s stopped (exit %s), retrying in %g s: %s",
                    self.name,
                    code,
                    self.retry_seconds,
                    hide_keys(" | ".join([*first, *last])) or "no message",
                )

    def _trim(self, now: datetime) -> None:
        """Drops the GOPs before the oldest frame to keep, but keeps the one
        that frame is in: it needs the keyframe before it."""
        oldest = self._oldest(now)
        while len(self.gops) >= 2 and self.gops[1].date <= oldest:
            self.gops.popleft()

    def _log_interval(self, gop: Gop) -> None:
        """Logs once how far apart the camera's keyframes are."""
        if self.interval_logged:
            return
        self.interval_logged = True
        seconds = (gop.frames[-1][1] - gop.date).total_seconds()
        logger.info(
            "Recording the high-resolution stream of %s: a keyframe every %.1f s",
            self.name,
            seconds + seconds / max(len(gop.frames) - 1, 1),
        )


_STREAM_KEY = re.compile(r"(rtsps?://[^/\s]+/)\S*")

def hide_keys(text: str) -> str:
    """Stream links hold a secret key; logs get pasted into chats."""
    return _STREAM_KEY.sub(r"\1<key>", text)


def hires_buffers(
    detection: DetectionConfig, names: dict[str, str] | None = None
) -> dict[str, HiresBuffer | HiresRecorder]:
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
            source: HiresBuffer(detection.hires, name(index, source))
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
        source: HiresRecorder(hires_source, detection.hires, name(index, source))
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
