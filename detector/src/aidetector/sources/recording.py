"""The 4K stream of a camera as it comes in, without decoding it.

FFmpeg copies the stream unchanged into MPEG-TS; that is cut here into GOPs,
the packets from one keyframe to the next. A run of GOPs can be decoded on its
own, so the stream is only decoded when an event needs its frames: recording
takes a few percent of a core, where decoding every 4K frame all day took one
core per camera and still fell behind."""

import logging
import math
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import Thread, Timer

import numpy as np
from imageio_ffmpeg import get_ffmpeg_exe

from aidetector.utils.config import HiresFrame

logger = logging.getLogger(__name__)

PACKET = 188
# Set by the recording command, so the tables need not be read.
PAT_PID = 0
PMT_PID = 0x1000
VIDEO_PID = 0x100
_SYNC = 0x47
_PTS_WRAP = 1 << 33
_PTS_CLOCK = 90_000
# Frame times follow the camera's clock from the first frame on, so they are
# evenly spaced however the packets arrive; when that clock is this far from
# ours (a jump in the stream, or slow drift), they start again from ours.
_MAX_CLOCK_ERROR = timedelta(seconds=2)
# Decoding an event is a few seconds of work at most; never wait for ever.
_DECODE_TIMEOUT = 300
# Threads for decoding and encoding an event: fast enough for a minute of 4K
# at 25 fps in some seconds, while each thread holds 4K frames in memory.
_DECODE_THREADS = 4
_JPEG_START = b"\xff\xd8"
_JPEG_END = b"\xff\xd9"
_READ_SIZE = 1 << 16
# The stream types in the PMT, as FFmpeg's names for the bare streams.
_STREAM_TYPES = {0x1B: "h264", 0x24: "hevc"}


def record_command(source: str) -> list[str]:
    """Copies the video of the stream unchanged to stdout as MPEG-TS."""
    command = [get_ffmpeg_exe(), "-hide_banner", "-loglevel", "warning"]
    if source.lower().startswith("rtsp"):
        command += ["-rtsp_transport", "tcp"]
    return command + [
        "-i",
        source,
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
        # The stream's parameter sets before every keyframe, so a GOP can be
        # decoded without the start of the connection.
        "-bsf:v",
        "dump_extra=freq=keyframe",
        "-f",
        "mpegts",
        "-mpegts_pmt_start_pid",
        str(PMT_PID),
        "-mpegts_start_pid",
        str(VIDEO_PID),
        "-flush_packets",
        "1",
        "pipe:1",
    ]


@dataclass
class Gop:
    """The packets of a stream from one keyframe up to the next."""

    # GOPs of different connections cannot be decoded in one go.
    connection: int
    # PAT and PMT, which tell the decoder which packets hold the video.
    tables: bytes
    data: bytearray = field(default_factory=bytearray)
    # (PTS, time) of each frame, in the order they arrive.
    frames: list[tuple[int, datetime]] = field(default_factory=list)

    @property
    def date(self) -> datetime:
        return self.frames[0][1]


class TsSplitter:
    """Cuts the MPEG-TS of one connection into GOPs."""

    def __init__(self, connection: int = 0, max_clock_error: timedelta = _MAX_CLOCK_ERROR):
        self.connection = connection
        self.max_clock_error = max_clock_error
        self.pending = bytearray()
        self.pat = b""
        self.pmt = b""
        self.current: Gop | None = None
        self.anchor: tuple[int, datetime] | None = None
        # Frames before the first keyframe cannot be decoded and are left out.
        self.skipped = 0

    def feed(self, data: bytes, now: datetime) -> list[Gop]:
        """Takes the bytes FFmpeg wrote and returns the GOPs they completed.
        Only the packets that start a frame are looked at one by one; the
        rest is copied in runs."""
        self.pending += data
        offset = _sync_offset(self.pending)
        if offset:
            del self.pending[:offset]
        usable = len(self.pending) - len(self.pending) % PACKET
        if not usable:
            return []
        raw = bytes(self.pending[:usable])
        del self.pending[:usable]
        packets = np.frombuffer(raw, dtype=np.uint8).reshape(-1, PACKET)
        pids = ((packets[:, 1].astype(np.uint16) & 0x1F) << 8) | packets[:, 2]
        starts = (packets[:, 1] & 0x40) != 0
        marked = np.flatnonzero(
            (pids == PAT_PID) | (pids == PMT_PID) | ((pids == VIDEO_PID) & starts)
        )
        done: list[Gop] = []
        last = 0
        for index in marked.tolist():
            self._append(raw[last * PACKET : index * PACKET])
            packet = raw[index * PACKET : (index + 1) * PACKET]
            pid = int(pids[index])
            if pid == PAT_PID:
                self.pat, last = packet, index + 1
                continue
            if pid == PMT_PID:
                self.pmt, last = packet, index + 1
                continue
            keyframe, pts = frame_start(packet)
            if keyframe and self.pat and self.pmt:
                if self.current is not None and self.current.frames:
                    done.append(self.current)
                self.current = Gop(self.connection, self.pat + self.pmt)
            if self.current is None:
                self.skipped += 1
                last = index + 1
                continue
            self.current.frames.append((pts if pts is not None else -1, self._date(pts, now)))
            last = index
        self._append(raw[last * PACKET :])
        return done

    def _append(self, data: bytes) -> None:
        if self.current is not None and data:
            self.current.data += data

    def _date(self, pts: int | None, now: datetime) -> datetime:
        if pts is None:
            return now
        if self.anchor is not None:
            anchor_pts, anchor_date = self.anchor
            ticks = (pts - anchor_pts) % _PTS_WRAP
            if ticks > _PTS_WRAP // 2:
                # Earlier than the anchor, e.g. a B-frame.
                ticks -= _PTS_WRAP
            date = anchor_date + timedelta(seconds=ticks / _PTS_CLOCK)
            if abs(date - now) <= self.max_clock_error:
                return date
        self.anchor = (pts, now)
        return now


def _sync_offset(buffer: bytearray) -> int:
    """Where the packets start: FFmpeg's output is aligned from its first
    byte, so this only skips something when a read began halfway."""
    if not buffer or buffer[0] == _SYNC:
        return 0
    for offset in range(1, min(len(buffer), PACKET)):
        if buffer[offset] == _SYNC and (
            offset + PACKET >= len(buffer) or buffer[offset + PACKET] == _SYNC
        ):
            return offset
    return min(len(buffer), PACKET)


def frame_start(packet: bytes) -> tuple[bool, int | None]:
    """Whether a packet that starts a frame starts a keyframe (FFmpeg sets the
    random access indicator on those), and the frame's PTS."""
    control = (packet[3] >> 4) & 3
    offset = 4
    keyframe = False
    if control & 2:
        length = packet[4]
        keyframe = length > 0 and bool(packet[5] & 0x40)
        offset = 5 + length
    if not control & 1:
        return keyframe, None
    pes = packet[offset:]
    if len(pes) < 14 or pes[:3] != b"\x00\x00\x01" or not pes[7] & 0x80:
        return keyframe, None
    b = pes[9:14]
    pts = ((b[0] >> 1) & 7) << 30 | b[1] << 22 | (b[2] >> 1) << 15 | b[3] << 7 | b[4] >> 1
    return keyframe, pts


def gops_between(gops: list[Gop], start: datetime, end: datetime) -> list[Gop]:
    """The GOPs with frames from start to end, from the keyframe before start."""
    first = 0
    for index, gop in enumerate(gops):
        if gop.date <= start:
            first = index
    return [gop for gop in gops[first:] if gop.date <= end]


def decode_gops(
    gops: list[Gop],
    start: datetime,
    end: datetime,
    fps: float,
    max_width: int | None,
    quality: int,
    hwaccel: str | None,
) -> list[HiresFrame]:
    """The frames from start to end as JPEG, about fps per second. Each
    connection is decoded on its own: its stream does not run on."""
    frames: list[HiresFrame] = []
    run: list[Gop] = []
    for gop in [*gops, None]:
        if run and (gop is None or gop.connection != run[0].connection):
            frames += _decode_run(run, start, end, fps, max_width, quality, hwaccel)
            run = []
        if gop is not None:
            run.append(gop)
    return frames


def _decode_run(
    gops: list[Gop],
    start: datetime,
    end: datetime,
    fps: float,
    max_width: int | None,
    quality: int,
    hwaccel: str | None,
) -> list[HiresFrame]:
    # The frames in the order they are shown, which the decoder gives them in.
    dates = sorted(date for gop in gops for _, date in gop.frames)
    inside = [index for index, date in enumerate(dates) if start <= date <= end]
    if not inside:
        return []
    codec = stream_codec(gops[0].tables)
    if codec is None:
        logger.warning("The 4K stream is neither H.264 nor HEVC and cannot be decoded")
        return []
    first, last = inside[0], inside[-1]
    step = frame_step(dates, fps)
    process = subprocess.Popen(
        decode_command(codec, max_width, quality, hwaccel, first, last, step),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdin, stdout, stderr = process.stdin, process.stdout, process.stderr
    assert stdin is not None and stdout is not None and stderr is not None
    timer = Timer(_DECODE_TIMEOUT, process.kill)
    timer.start()

    def write() -> None:
        try:
            for gop in gops:
                stdin.write(elementary_stream(gop.data))
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                stdin.close()
            except OSError:
                pass

    errors: list[str] = []

    def read_errors() -> None:
        for raw in stderr:
            line = raw.decode(errors="replace").strip()
            if line:
                errors.append(line)

    writer = Thread(target=write, daemon=True)
    reader = Thread(target=read_errors, daemon=True)
    writer.start()
    reader.start()
    images: list[bytes] = []
    buffer = bytearray()
    try:
        for chunk in iter(lambda: stdout.read(_READ_SIZE), b""):
            buffer += chunk
            images += split_jpegs(buffer)
    finally:
        code = process.wait()
        timer.cancel()
        writer.join(timeout=5)
        reader.join(timeout=5)
    if not images:
        logger.warning(
            "Could not decode the 4K frames of an event (exit %s): %s",
            code,
            " | ".join(errors[:3]) or "no message",
        )
        return []
    # The n-th image is the n-th frame selected. Should the decoder skip a
    # damaged frame, the times after it are one frame (some 40 ms) off.
    shown = range(first, last + 1, step)
    return [HiresFrame(dates[index], jpeg) for index, jpeg in zip(shown, images)]


def frame_step(dates: list[datetime], fps: float) -> int:
    """Every how many frames of the camera one is taken for about fps per
    second. A bit more than fps, else a 25 fps camera gives every third
    frame for 10 fps (8.3 per second) instead of every second."""
    gaps = sorted(
        (later - earlier).total_seconds() for earlier, later in zip(dates, dates[1:])
    )
    interval = gaps[len(gaps) // 2] if gaps else 0
    if interval <= 0:
        return 1
    return max(1, math.ceil(0.75 / fps / interval - 1e-9))


def stream_codec(tables: bytes) -> str | None:
    """The codec of the video, from the stream type in the PMT."""
    for offset in range(0, len(tables) - PACKET + 1, PACKET):
        packet = tables[offset : offset + PACKET]
        if ((packet[1] & 0x1F) << 8 | packet[2]) != PMT_PID or not packet[1] & 0x40:
            continue
        try:
            start = 4 + (1 + packet[4] if (packet[3] >> 4) & 2 else 0)
            section = packet[start + 1 + packet[start] :]
            info = ((section[10] & 0x0F) << 8) | section[11]
            return _STREAM_TYPES.get(section[12 + info])
        except IndexError:
            return None
    return None


def elementary_stream(data: bytes | bytearray) -> bytes:
    """The video itself from MPEG-TS packets: their payload without the
    packet and PES headers. The static Linux build of FFmpeg that
    imageio-ffmpeg ships (7.0.2) crashes on any MPEG-TS it reads, while a
    bare H.264 or HEVC stream decodes everywhere."""
    view = memoryview(data)
    parts = []
    for offset in range(0, len(view) - PACKET + 1, PACKET):
        packet = view[offset : offset + PACKET]
        if ((packet[1] & 0x1F) << 8 | packet[2]) != VIDEO_PID:
            continue
        control = (packet[3] >> 4) & 3
        if not control & 1:
            continue
        start = 4 + (1 + packet[4] if control & 2 else 0)
        if packet[1] & 0x40:
            if start + 9 > PACKET or packet[start : start + 3] != b"\x00\x00\x01":
                continue
            start += 9 + packet[start + 8]
        if start < PACKET:
            parts.append(packet[start:])
    return b"".join(parts)


def decode_command(
    codec: str,
    max_width: int | None,
    quality: int,
    hwaccel: str | None,
    first: int,
    last: int,
    step: int,
) -> list[str]:
    command = [get_ffmpeg_exe(), "-hide_banner", "-nostats", "-loglevel", "warning"]
    if hwaccel:
        command += ["-hwaccel", hwaccel]
    # FFmpeg's qscale 2 (best) to 31 (worst), mapped from a JPEG quality.
    qscale = max(2, min(31, round(31 - (quality / 100) * 29)))
    width = f"'min(iw\\,{max_width})'" if max_width else "iw"
    filters = [
        # Only the frames of the event, though decoding starts at the
        # keyframe before it, and every step-th of those.
        f"select='between(n\\,{first}\\,{last})*not(mod(n-{first}\\,{step}))'",
        # One fixed format for the JPEG encoder, in the full colour range
        # that JPEG uses: the hardware decoder gives another one.
        f"scale={width}:-2:out_range=full",
        "format=yuv420p",
    ]
    return command + [
        "-threads",
        str(_DECODE_THREADS),
        "-f",
        codec,
        "-i",
        "pipe:0",
        "-an",
        "-vf",
        ",".join(filters),
        "-fps_mode",
        "passthrough",
        "-c:v",
        "mjpeg",
        "-threads",
        str(_DECODE_THREADS),
        "-color_range",
        "pc",
        # Also accept a limited-range picture, should one slip through.
        "-strict",
        "unofficial",
        "-q:v",
        str(qscale),
        "-f",
        "image2pipe",
        "pipe:1",
    ]


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
