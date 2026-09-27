import numpy as np

from aidetector.utils.config import Crop, ImageSet


def make_frame() -> np.ndarray:
    # A smooth gradient, like a barn image, instead of noise that JPEG cannot shrink.
    x = np.linspace(0, 255, 1280, dtype=np.uint8)
    y = np.linspace(0, 255, 720, dtype=np.uint8)
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[..., 0] = x[None, :]
    frame[..., 1] = y[:, None]
    frame[..., 2] = 128
    return frame


def test_frame_is_stored_as_jpeg_and_decoded_on_demand():
    frame = make_frame()

    images = ImageSet(frame)

    assert (images.width, images.height) == (1280, 720)
    assert len(images.jpeg) < frame.nbytes / 10
    decoded = images.jpg
    assert decoded.shape == frame.shape
    assert np.abs(decoded.astype(int) - frame.astype(int)).mean() < 2


def test_with_crops_keeps_the_frame_without_encoding_again():
    images = ImageSet(make_frame(), [Crop(0, 0, 10, 10, label="cow", confidence=0.9)])

    other = images.with_crops([])

    assert other.jpeg is images.jpeg
    assert other.crops == []
    assert len(images.crops) == 1
