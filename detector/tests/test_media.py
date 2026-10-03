from datetime import datetime

import numpy as np

from aidetector.media.video import generate_mp4, get_crop, get_plot, shrink_image
from aidetector.utils.config import Crop, Detection, ImageSet


def make_detection(crops: list[Crop]) -> Detection:
    image = np.zeros((100, 160, 3), dtype=np.uint8)
    return Detection(datetime(2026, 1, 1, 12, 0, 0), ImageSet(image, crops), {"cow": 0.9})


def test_crop_region_is_union_of_all_crops():
    detection = make_detection(
        [
            Crop(10, 20, 30, 40, label="cow", confidence=0.9),
            Crop(50, 5, 80, 70, label="cow", confidence=0.8),
        ]
    )

    crop = detection.images.crop_region

    assert crop == Crop(10, 5, 80, 70)


def test_get_crop_uses_union_region_for_multiple_crops():
    detection = make_detection([Crop(10, 20, 30, 40), Crop(50, 5, 80, 70)])

    crop = get_crop(detection, aspect_ratio=None, padding=0, plot=False)

    assert crop is not None
    assert crop.shape == (65, 70, 3)


def test_get_plot_draws_multiple_crops():
    detection = make_detection([Crop(10, 20, 30, 40), Crop(50, 5, 80, 70)])

    plotted = get_plot(detection)

    assert plotted.shape == detection.images.jpg.shape
    assert np.any(plotted != detection.images.jpg)


def test_shrink_image_keeps_even_dimensions_and_does_not_upscale():
    image = np.zeros((101, 401, 3), dtype=np.uint8)

    shrunk = shrink_image(image, 200)
    unchanged = shrink_image(shrunk, 400)

    assert shrunk.shape[1] == 200
    assert shrunk.shape[0] % 2 == 0
    assert unchanged.shape == shrunk.shape


def test_generate_mp4_returns_none_without_detections():
    assert generate_mp4([]) is None


def test_telegram_photo_stays_within_telegram_limits(monkeypatch):
    import cv2
    import numpy as np

    from aidetector.media import video

    # Noise compresses worst: 16 MB as a 4K JPEG at quality 100.
    noise = np.random.default_rng(0).integers(0, 256, (2160, 3840, 3), dtype=np.uint8)

    photo = video.telegram_photo(noise)

    assert len(photo) <= video.TELEGRAM_PHOTO_MAX
    decoded = cv2.imdecode(np.frombuffer(photo, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape == (1440, 2560, 3)

    monkeypatch.setattr(video, "TELEGRAM_PHOTO_MAX", 300_000)
    assert len(video.telegram_photo(noise)) <= 300_000


def test_telegram_alert_with_a_4k_crop_fits_telegram(monkeypatch):
    from datetime import datetime

    import cv2
    import numpy as np

    from aidetector.exporters.telegram import TelegramExporter, TelegramFeedbackListener
    from aidetector.media.video import TELEGRAM_PHOTO_MAX, get_image
    from aidetector.utils.config import ChatConfig, Crop, Detection, HiresFrame, ImageSet

    monkeypatch.setattr(TelegramFeedbackListener, "start", lambda self: None)
    exporter = TelegramExporter(
        ChatConfig(token="t", chat="c", include_crop=True, include_video=False)
    )
    now = datetime.now()
    # A box over most of the frame, so the crop is nearly the whole 4K frame.
    best = Detection(
        now,
        ImageSet(np.zeros((720, 1280, 3), dtype=np.uint8), [Crop(20, 20, 1260, 700, "mounting", 0.9)]),
        {"mounting": 0.9},
    )
    noise = np.random.default_rng(1).integers(0, 256, (2160, 3840, 3), dtype=np.uint8)
    best.hires = [HiresFrame(now, get_image(noise, 100))]

    _, files = exporter.get_media_and_files(best, [best], True)

    crop = files["crop"][1]
    assert len(crop) <= TELEGRAM_PHOTO_MAX
    assert max(cv2.imdecode(np.frombuffer(crop, dtype=np.uint8), cv2.IMREAD_COLOR).shape[:2]) <= 2560
