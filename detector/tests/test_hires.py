import subprocess
from datetime import datetime, timedelta

import numpy as np
import pytest
from imageio_ffmpeg import get_ffmpeg_exe

from aidetector.media.video import get_image
from aidetector.sources.hires import (
    HiresBuffer,
    hires_buffers,
    hires_detection,
    split_jpegs,
)
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


def test_split_jpegs_keeps_an_incomplete_image_for_the_next_read():
    first, second = jpeg(value=10), jpeg(value=200)
    buffer = bytearray(b"junk" + first + second[:20])

    assert split_jpegs(buffer) == [first]
    buffer += second[20:]
    assert split_jpegs(buffer) == [second]
    assert buffer == bytearray()


def test_buffer_keeps_only_the_configured_seconds():
    buffer = HiresBuffer("rtsp://camera", HiresConfig(source="x", seconds=30))
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


def test_buffer_reads_frames_from_ffmpeg(tmp_path):
    video = tmp_path / "barn.mp4"
    subprocess.run(
        [
            get_ffmpeg_exe(), "-loglevel", "error", "-f", "lavfi",
            "-i", "testsrc=size=640x360:rate=10:duration=3", "-pix_fmt", "yuv420p",
            str(video),
        ],
        check=True,
    )
    buffer = HiresBuffer(
        str(video), HiresConfig(source=str(video), fps=2, hwaccel=None, keyframes_only=False)
    )

    buffer._read()

    assert 4 <= len(buffer.frames) <= 8
    assert buffer.frames[0].jpg.shape == (360, 640, 3)


def test_buffer_keeps_about_fps_frames_per_second(tmp_path):
    video = tmp_path / "barn-25fps.mp4"
    subprocess.run(
        [
            get_ffmpeg_exe(), "-loglevel", "error", "-f", "lavfi",
            "-i", "testsrc=size=640x360:rate=25:duration=2", "-pix_fmt", "yuv420p",
            str(video),
        ],
        check=True,
    )
    buffer = HiresBuffer(
        str(video), HiresConfig(source=str(video), fps=10, hwaccel=None, keyframes_only=False)
    )

    buffer._read()

    # Every second frame of the 25 fps camera, not every third (8.3 per second).
    assert 20 <= len(buffer.frames) <= 26


def test_jpeg_encoder_uses_one_thread():
    buffer = HiresBuffer("rtsps://nvr/key", HiresConfig(source="rtsps://nvr/key"))

    command = buffer.command()

    # Each encoder thread holds 4K frames: a gigabyte per camera otherwise.
    encoder = command.index("mjpeg")
    assert command[encoder + 1 : encoder + 3] == ["-threads", "1"]


def test_keyframes_only_and_at_most_max_width(tmp_path):
    video = tmp_path / "barn-4k.mp4"
    # A keyframe every 0.5 s, like a camera with a short keyframe interval.
    subprocess.run(
        [
            get_ffmpeg_exe(), "-loglevel", "error", "-f", "lavfi",
            "-i", "testsrc=size=3840x2160:rate=10:duration=3", "-g", "5",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(video),
        ],
        check=True,
    )
    buffer = HiresBuffer(
        str(video), HiresConfig(source=str(video), fps=1, hwaccel=None, max_width=2560)
    )

    buffer._read()

    # Six keyframes in 3 s, but at most one frame per second is kept.
    assert 2 <= len(buffer.frames) <= 4
    assert buffer.frames[0].jpg.shape == (1440, 2560, 3)


def test_without_hires_source_the_detection_stream_itself_is_kept(monkeypatch):
    import aidetector.sources.hires as hires_module

    detection = DetectionConfig(source=["cam-a", "cam-b"], hires=HiresConfig(fps=1))
    buffers = hires_buffers(detection)
    assert sorted(buffers) == ["cam-a", "cam-b"]
    assert all(buffer.from_detection for buffer in buffers.values())

    buffer = buffers["cam-a"]
    buffer.start()  # no second stream to read
    assert buffer.thread is None
    clock = iter([100.0, 100.4, 101.1, 200.0])
    monkeypatch.setattr(hires_module.time, "monotonic", lambda: next(clock))
    frame = np.zeros((2160, 3840, 3), dtype=np.uint8)
    for _ in range(3):
        buffer.feed(frame)

    # At most one frame per second, kept at 4K.
    assert len(buffer.frames) == 2
    assert buffer.frames[0].jpg.shape == (2160, 3840, 3)

    smaller = HiresBuffer(None, HiresConfig(max_width=2560))
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


def test_a_stream_that_fails_is_logged_with_its_camera_and_without_its_key(tmp_path, caplog):
    import logging

    link = "rtsps://192.168.1.77:7441/SeCrEtKeY?enableSrtp"
    buffer = HiresBuffer(str(tmp_path / "missing.mp4"), HiresConfig(source="x", hwaccel=None), "Stal Links")

    with caplog.at_level(logging.WARNING, logger="aidetector.sources.hires"):
        buffer._read()

    message = caplog.records[-1].getMessage()
    assert "High-resolution stream of Stal Links stopped" in message
    assert "missing.mp4" in message
    from aidetector.sources.hires import hide_keys

    assert hide_keys(f"Error opening {link}: refused") == "Error opening rtsps://192.168.1.77:7441/<key> refused"


def test_the_first_frames_of_a_camera_are_logged_with_their_size(caplog):
    import logging

    buffer = HiresBuffer(None, HiresConfig(), "Camera kleine stal")

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
    buffer = HiresBuffer("rtsp://camera", HiresConfig(seconds=20))
    buffer.hold(START - timedelta(seconds=10))
    # A long mount of 60 s: the frames from before it stay.
    for offset in range(-15, 61, 5):
        buffer.add(HiresFrame(START + timedelta(seconds=offset), b""))
    assert buffer.frames[0].date == START - timedelta(seconds=10)

    buffer.release()
    buffer.add(HiresFrame(START + timedelta(seconds=65), b""))
    assert buffer.frames[0].date == START + timedelta(seconds=45)


def test_a_hold_that_is_never_released_is_capped():
    buffer = HiresBuffer("rtsp://camera", HiresConfig(seconds=20))
    buffer.hold(START)
    for minute in range(10):
        buffer.add(HiresFrame(START + timedelta(minutes=minute), b""))
    assert buffer.frames[0].date >= START + timedelta(minutes=6)


def test_sparse_keyframes_switch_the_stream_to_all_frames(caplog):
    import logging

    buffer = HiresBuffer("rtsps://nvr/key", HiresConfig(source="x"), "Stal Links")
    assert "-skip_frame" in buffer.command()

    with caplog.at_level(logging.INFO, logger="aidetector.sources.hires"):
        # UniFi sends a keyframe every 5 s.
        buffer.add(HiresFrame(START, jpeg()))
        buffer.add(HiresFrame(START + timedelta(seconds=5), jpeg()))

    assert buffer.switch_to_all_frames
    assert "-skip_frame" not in buffer.command()
    assert "select=" in " ".join(buffer.command())
    assert "keyframes are too far apart, decoding all frames instead" in caplog.records[-1].getMessage()


def test_frequent_keyframes_stay(caplog):
    buffer = HiresBuffer("rtsps://nvr/key", HiresConfig(source="x"), "Stal Links")
    buffer.add(HiresFrame(START, jpeg()))
    buffer.add(HiresFrame(START + timedelta(seconds=1), jpeg()))

    assert not buffer.switch_to_all_frames
    assert "-skip_frame" in buffer.command()


def test_switching_restarts_the_stream_with_all_frames(tmp_path):
    video = tmp_path / "sparse.mp4"
    # 12 s at 5 fps with a keyframe every 5 s, played in real time.
    subprocess.run(
        [
            get_ffmpeg_exe(), "-loglevel", "error", "-f", "lavfi",
            "-i", "testsrc=size=640x360:rate=5:duration=12", "-g", "25",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(video),
        ],
        check=True,
    )
    buffer = HiresBuffer(str(video), HiresConfig(source=str(video), hwaccel=None))
    # Frames from a file come faster than real time; date them as a camera would.
    dates = iter(START + timedelta(seconds=5 * index) for index in range(100))
    original_add = buffer.add
    buffer.add = lambda frame: original_add(HiresFrame(next(dates), frame.jpeg))

    buffer._read()

    assert not buffer.keyframes and buffer.switch_to_all_frames


def test_the_encoder_always_gets_one_fixed_format():
    for keyframes in (True, False):
        command = HiresBuffer("rtsps://nvr/key", HiresConfig(source="x", keyframes_only=keyframes)).command()
        assert command[command.index("-vf") + 1].endswith(":out_range=full,format=yuv420p")


def test_a_stream_without_frames_falls_back_step_by_step(tmp_path):
    missing = str(tmp_path / "missing.mp4")
    buffer = HiresBuffer(missing, HiresConfig(source=missing), "Stal Links")
    # As on the farm: keyframes were too far apart, so all frames are decoded.
    buffer.keyframes = False

    buffer._read()
    assert buffer.hwaccel is None and not buffer.keyframes

    buffer._read()
    assert buffer.keyframes and buffer.all_frames_failed
    assert "-skip_frame" in buffer.command()

    # Sparse keyframes no longer switch back to all frames.
    buffer.add(HiresFrame(START, jpeg()))
    buffer.add(HiresFrame(START + timedelta(seconds=5), jpeg()))
    assert buffer.keyframes and not buffer.switch_to_all_frames


def test_the_log_keeps_the_first_lines_of_an_error(tmp_path, caplog):
    import logging

    missing = str(tmp_path / "missing.mp4")
    buffer = HiresBuffer(missing, HiresConfig(source=missing, hwaccel=None), "Stal Links")

    with caplog.at_level(logging.WARNING, logger="aidetector.sources.hires"):
        buffer._read()

    assert "missing.mp4" in caplog.records[-1].getMessage()
