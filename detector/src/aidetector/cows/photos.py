import cv2
import numpy as np
from numpy import ndarray

from aidetector.cows.split import Box

_GAP = 8
_BACKGROUND = (38, 36, 32)
_LABEL = (255, 255, 255)
# Small crops from a wide barn view are enlarged to at least this height.
_MIN_HEIGHT = 360
_MAX_HEIGHT = 720


def side_by_side(images: list[ndarray], labels: list[str]) -> ndarray:
    """Puts the photos of both cows in one image with a label above each, so
    one message shows both."""
    height = min(_MAX_HEIGHT, max(_MIN_HEIGHT, max(image.shape[0] for image in images)))
    strip = max(36, height // 12)
    scale = strip / 48
    thickness = max(2, round(strip / 20))
    tiles = []
    for image, label in zip(images, labels):
        width = max(1, round(image.shape[1] * height / image.shape[0]))
        resized = cv2.resize(
            image,
            (width, height),
            interpolation=cv2.INTER_CUBIC if height > image.shape[0] else cv2.INTER_AREA,
        )
        (text_w, text_h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
        width = max(width, text_w + strip)
        tile = np.full((height + strip, width, 3), _BACKGROUND, dtype=np.uint8)
        tile[strip:, :resized.shape[1]] = resized
        cv2.putText(
            tile,
            label,
            ((width - text_w) // 2, (strip + text_h) // 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            scale,
            _LABEL,
            thickness,
            cv2.LINE_AA,
        )
        tiles.append(tile)
    gap = np.full((height + strip, _GAP, 3), _BACKGROUND, dtype=np.uint8)
    parts = [tiles[0]]
    for tile in tiles[1:]:
        parts += [gap, tile]
    return np.hstack(parts)


def control_image(image: ndarray, box: Box, width: int = 1920) -> ndarray:
    """The frame with the mount box drawn on it. On a 4K frame it shows at a
    glance whether the box of the detection stream falls on the right cows,
    which it does not when the two streams run apart."""
    height, frame_width = image.shape[:2]
    marked = image.copy()
    thickness = max(2, frame_width // 640)
    cv2.rectangle(
        marked,
        (int(box[0] * frame_width), int(box[1] * height)),
        (int(box[2] * frame_width), int(box[3] * height)),
        (0, 0, 255),
        thickness,
    )
    if frame_width > width:
        marked = cv2.resize(
            marked, (width, round(height * width / frame_width)), interpolation=cv2.INTER_AREA
        )
    return marked
