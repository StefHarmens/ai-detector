import subprocess
from datetime import datetime, timedelta

import numpy as np
import pytest
from imageio_ffmpeg import get_ffmpeg_exe

from aidetector.media.video import get_image
from aidetector.sources.hires import (
    HiresBuffer,
    HiresRecorder,
    hide_keys,
    hires_buffers,
    hires_detection,
)
from aidetector.sources.recording import Gop, split_jpegs
from aidetector.utils.config import (
    Crop,
    Detection,
    DetectionConfig,
    HiresConfig,
    HiresFrame,
    ImageSet,
)

START = datetime(2026, 10, 2, 8, 0, 0)


def jpeg(width: int = 64, height: int = 36, value: int = 0) -> bytes:
    return get_image(np.full((height, width, 3), value, dtype=np.uint8), 90)


def test_buffer_keeps_only_the_configured_seconds():
    buffer = HiresBuffer(HiresConfig(seconds=30))
    for offset in range(0, 60, 10):
        buffer.add(HiresFrame(START + timedelta(seconds=offset), b""))

    assert [frame.date for frame in buffer.frames] == [
        START + timedelta(seconds=offset) for offset in (20, 30, 40, 50)
    ]
    assert len(buffer.frames_between(START + timedelta(seconds=25), START + timedelta(seconds=45))) == 2


def test_hires_sources_must_match_the_detection_sources():
    detection = DetectionConfig(
        source=["cam-a", "cam-b"], hires=HiresConfig(source=["rtsp://a-4k", None])
    )
    assert list(hires_buffers(detection)) == ["cam-a"]

    with pytest.raises(ValueError, match="one stream"):
        hires_buffers(
            DetectionConfig(source=["cam-a", "cam-b"], hires=HiresConfig(source="rtsp://a"))
        )


def test_hires_detection_scales_the_boxes_to_the_4k_frame():
    detection = Detection(
        START,
        ImageSet(np.zeros((720, 1280, 3), dtype=np.uint8), [Crop(100, 200, 300, 400, "mounting", 0.9)]),
        {"mounting": 0.9},
        camera="Stal",
    )
    detection.hires = [
        HiresFrame(START - timedelta(seconds=5), jpeg(3840, 2160)),
        HiresFrame(START + timedelta(milliseconds=300), jpeg(3840, 2160, 50)),
    ]

    hires = hires_detection(detection)

    assert hires is not None
    assert hires.date == START + timedelta(milliseconds=300)
    assert (hires.images.width, hires.images.height) == (3840, 2160)
    crop = hires.images.crops[0]
    assert (crop.x1, crop.y1, crop.x2, crop.y2) == (300, 600, 900, 1200)
    assert hires.camera == "Stal"


def test_hires_detection_without_frames_is_none():
    detection = Detection(START, ImageSet(np.zeros((36, 64, 3), dtype=np.uint8)), {})
    assert hires_detection(detection) is None


def test_without_hires_source_the_detection_stream_itself_is_kept(monkeypatch):
    import aidetector.sources.hires as hires_module

    detection = DetectionConfig(source=["cam-a", "cam-b"], hires=HiresConfig(fps=1))
    buffers = hires_buffers(detection)
    assert sorted(buffers) == ["cam-a", "cam-b"]
    assert all(buffer.from_detection for buffer in buffers.values())

    buffer = buffers["cam-a"]
    clock = iter([100.0, 100.4, 101.1, 200.0])
    monkeypatch.setattr(hires_module.time, "monotonic", lambda: next(clock))
    frame = np.zeros((2160, 3840, 3), dtype=np.uint8)
    for _ in range(3):
        buffer.feed(frame)

    # At most one frame per second, kept at 4K.
    assert len(buffer.frames) == 2
    assert buffer.frames[0].jpg.shape == (2160, 3840, 3)

    smaller = HiresBuffer(HiresConfig(max_width=2560))
    smaller.feed(frame)
    assert smaller.frames[0].jpg.shape == (1440, 2560, 3)


def test_the_detector_feeds_full_size_frames_to_its_buffers(tmp_path):
    import cv2

    from aidetector.sources.source import SourceProvider

    path = tmp_path / "barn.jpg"
    cv2.imwrite(str(path), np.full((2160, 3840, 3), 80, dtype=np.uint8))
    seen = []
    provider = SourceProvider(
        DetectionConfig(source=[str(path)], frames_width=1280),
        lambda source, frame: seen.append((source, frame.shape)),
    )

    list(provider.iter_batches())

    assert seen == [(str(path), (2160, 3840, 3))]


def test_the_first_frames_of_a_camera_are_logged_with_their_size(caplog):
    import logging

    buffer = HiresBuffer(HiresConfig(), "Camera kleine stal")

    with caplog.at_level(logging.INFO, logger="aidetector.sources.hires"):
        buffer.add(HiresFrame(START, jpeg(3840, 2160)))
        buffer.add(HiresFrame(START, jpeg(3840, 2160)))

    assert [record.getMessage() for record in caplog.records] == [
        "High-resolution frames from Camera kleine stal: 3840x2160"
    ]


def test_buffers_are_named_after_the_cameras():
    detection = DetectionConfig(
        source=["rtsps://nvr/a", "rtsps://nvr/b"],
        name=["Stal Links", "Stal Rechts"],
        hires=HiresConfig(source=["rtsps://nvr/a-4k", "rtsps://nvr/b-4k"]),
    )
    from aidetector.detection.detector import camera_names

    buffers = hires_buffers(detection, camera_names(detection))

    assert [buffer.name for buffer in buffers.values()] == ["Stal Links", "Stal Rechts"]


def test_frames_of_a_mount_are_held_until_it_is_handled():
    buffer = HiresBuffer(HiresConfig(seconds=20))
    buffer.hold(START - timedelta(seconds=10))
    # A long mount of 60 s: the frames from before it stay.
    for offset in range(-15, 61, 5):
        buffer.add(HiresFrame(START + timedelta(seconds=offset), b""))
    assert buffer.frames[0].date == START - timedelta(seconds=10)

    buffer.release()
    buffer.add(HiresFrame(START + timedelta(seconds=65), b""))
    assert buffer.frames[0].date == START + timedelta(seconds=45)


def test_a_hold_that_is_never_released_is_capped():
    buffer = HiresBuffer(HiresConfig(seconds=20))
    buffer.hold(START)
    for minute in range(10):
        buffer.add(HiresFrame(START + timedelta(minutes=minute), b""))
    assert buffer.frames[0].date >= START + timedelta(minutes=6)




def test_split_jpegs_keeps_an_incomplete_image_for_the_next_read():
    first, second = jpeg(value=10), jpeg(value=200)
    buffer = bytearray(b"junk" + first + second[:20])

    assert split_jpegs(buffer) == [first]
    buffer += second[20:]
    assert split_jpegs(buffer) == [second]
    assert buffer == bytearray()


def camera(tmp_path, seconds: float = 1.6, size: str = "640x360"):
    """A short camera stream: 25 fps, a keyframe every 0.4 s."""
    video = tmp_path / "camera.h264"
    subprocess.run(
        [
            get_ffmpeg_exe(), "-loglevel", "error", "-f", "lavfi",
            "-i", f"testsrc=size={size}:rate=25:duration={seconds}", "-g", "10",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(video),
        ],
        check=True,
    )
    return str(video)


def test_a_separate_stream_is_recorded_and_only_an_event_is_decoded(tmp_path, caplog):
    import logging

    source = camera(tmp_path)
    recorder = HiresRecorder(source, HiresConfig(source=source, fps=25, hwaccel=None), "Stal Links")

    with caplog.at_level(logging.INFO, logger="aidetector.sources.hires"):
        recorder._read()

    assert len(recorder.gops) == 4
    assert recorder.retry_seconds == 1
    assert "Recording the high-resolution stream of Stal Links: a keyframe every 0.4 s" in caplog.text
    first = recorder.gops[0].date
    frames = recorder.clip(first + timedelta(seconds=0.5), first + timedelta(seconds=1.0))()
    assert len(frames) == 13
    assert frames[0].jpg.shape == (360, 640, 3)


def test_the_clip_is_taken_at_once_but_decoded_later(tmp_path, monkeypatch):
    import aidetector.sources.hires as hires_module

    source = camera(tmp_path)
    recorder = HiresRecorder(source, HiresConfig(source=source, hwaccel=None))
    recorder._read()
    decoded = []
    monkeypatch.setattr(hires_module, "decode_gops", lambda gops, *args: decoded.append(gops) or [])

    clip = recorder.clip(datetime.min, datetime.max)
    # The buffer moves on while the event waits for its export.
    recorder.gops.clear()

    assert decoded == []
    clip()
    assert len(decoded[0]) == 4


def test_the_gop_still_coming_in_is_part_of_the_clip(tmp_path):
    source = camera(tmp_path)
    recorder = HiresRecorder(source, HiresConfig(source=source, fps=25, hwaccel=None))
    recorder._read()
    # As if the connection were still open: the last GOP is not finished.
    recorder.splitter.current = recorder.gops.pop()

    frames = recorder.clip(datetime.min, datetime.max)()

    assert len(frames) == 40


def test_without_frames_from_the_hardware_the_event_is_decoded_without(tmp_path, caplog):
    import logging

    source = camera(tmp_path)
    recorder = HiresRecorder(source, HiresConfig(source=source, hwaccel="no-such-hardware"), "Stal Links")
    recorder._read()

    with caplog.at_level(logging.WARNING):
        frames = recorder.clip(datetime.min, datetime.max)()

    assert frames
    assert "Decoding the 4K frames of Stal Links again without hardware decoding" in caplog.text


def test_a_stream_that_fails_is_logged_with_its_camera_and_without_its_key(tmp_path, caplog):
    import logging

    link = "rtsps://192.168.1.77:7441/SeCrEtKeY?enableSrtp"
    missing = str(tmp_path / "missing.ts")
    recorder = HiresRecorder(missing, HiresConfig(source=missing), "Stal Links")

    with caplog.at_level(logging.WARNING, logger="aidetector.sources.hires"):
        recorder._read()

    message = caplog.records[-1].getMessage()
    assert "High-resolution stream of Stal Links stopped" in message
    assert "missing.ts" in message
    assert recorder.retry_seconds == 5
    assert hide_keys(f"Error opening {link}: refused") == "Error opening rtsps://192.168.1.77:7441/<key> refused"


def gop(date: datetime) -> Gop:
    return Gop(1, b"", bytearray(), [(0, date)])


def test_the_recording_keeps_the_seconds_and_the_keyframe_before_them():
    recorder = HiresRecorder("rtsp://camera", HiresConfig(seconds=12))
    recorder.gops.extend(gop(START + timedelta(seconds=offset)) for offset in range(0, 30, 5))

    recorder._trim(START + timedelta(seconds=25))

    # 13 s is 12 s ago: the GOP from 10 s holds it.
    assert [g.date for g in recorder.gops] == [START + timedelta(seconds=s) for s in (10, 15, 20, 25)]


def test_the_recording_of_a_mount_is_held_until_it_is_handled():
    recorder = HiresRecorder("rtsp://camera", HiresConfig(seconds=12))
    recorder.hold(START + timedelta(seconds=3))
    recorder.gops.extend(gop(START + timedelta(seconds=offset)) for offset in range(0, 100, 5))

    recorder._trim(START + timedelta(seconds=95))
    assert recorder.gops[0].date == START

    recorder.release()
    recorder._trim(START + timedelta(seconds=95))
    assert recorder.gops[0].date == START + timedelta(seconds=80)


def test_the_keyframe_interval_is_not_measured_on_the_first_gop(monkeypatch, caplog):
    import logging

    import aidetector.sources.hires as hires_module

    class Process:
        stdout = type("Stdout", (), {"fileno": lambda self: 0})()
        stderr: list[bytes] = []

        def poll(self):
            return 0

        def wait(self):
            return 0

    recorder = HiresRecorder("rtsp://camera", HiresConfig(), "Camera kleine stal")
    # As on the farm: the first GOP after connecting is a single frame.
    short = Gop(1, b"", bytearray(), [(0, START)])
    full = Gop(1, b"", bytearray(), [(n, START + timedelta(seconds=0.04 * n)) for n in range(125)])
    completed = iter([[short], [full]])
    recorder.splitter.feed = lambda chunk, now: next(completed)
    chunks = iter([b"x", b"x", b""])
    monkeypatch.setattr(hires_module.subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(hires_module.os, "read", lambda fd, size: next(chunks))
    monkeypatch.setattr(hires_module, "TsSplitter", lambda connection: recorder.splitter)

    with caplog.at_level(logging.INFO, logger="aidetector.sources.hires"):
        recorder._read()

    assert [r.getMessage() for r in caplog.records if "keyframe every" in r.getMessage()] == [
        "Recording the high-resolution stream of Camera kleine stal: a keyframe every 5.0 s"
    ]
