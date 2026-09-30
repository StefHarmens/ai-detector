from datetime import datetime, timedelta

import cv2
import numpy as np

from aidetector.cows.service import event_frames
from aidetector.media.video import generate_mp4
from aidetector.utils.config import Crop, Detection, HiresFrame, ImageSet

START = datetime(2026, 10, 2, 8, 0, 0)


def detections() -> list[Detection]:
    image = np.zeros((72, 128, 3), dtype=np.uint8)
    return [
        Detection(
            START + timedelta(seconds=second),
            ImageSet(image, [Crop(32, 18, 96, 54, label="mount", confidence=0.9)]),
            {"mount": 0.9},
        )
        for second in range(3)
    ]


def hires(seconds: float, fps: float) -> list[HiresFrame]:
    frame = np.full((720, 1280, 3), 80, dtype=np.uint8)
    jpeg = cv2.imencode(".jpg", frame)[1].tobytes()
    return [
        HiresFrame(START + timedelta(seconds=index / fps), jpeg)
        for index in range(int(seconds * fps))
    ]


def video_size(video: bytes, tmp_path) -> tuple[int, int, int]:
    path = tmp_path / "video.mp4"
    path.write_bytes(video)
    capture = cv2.VideoCapture(str(path))
    count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    size = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    capture.release()
    return (*size, count)


def test_the_video_is_made_from_the_4k_frames(tmp_path):
    video = generate_mp4(detections(), crf=18, padding=0, hires=hires(3, 4))

    # The mount's place, cut from the frames 10x as large: 640x360, not 64x36;
    # the 4K frames come 4 per second, the detection frames 1.
    width, height, count = video_size(video, tmp_path)
    assert (width, height) == (640, 360)
    assert count == 12


def test_without_4k_frames_the_detection_frames_are_used(tmp_path):
    video = generate_mp4(detections(), padding=0, hires=[])

    width, height, count = video_size(video, tmp_path)
    assert (width, height) == (64, 36)
    assert count == 3


def test_recognition_keeps_about_one_4k_frame_per_second():
    best = detections()[1]
    best.hires = hires(10, 4)
    frames = event_frames(best, detections())

    dates = [date for date, _ in frames.jpegs]
    assert all((b - a).total_seconds() >= 0.95 for a, b in zip(dates, dates[1:]))
