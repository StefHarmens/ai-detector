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


def control_image(image: ndarray, box: Box, width: int | None = None) -> ndarray:
    """The frame with the mount box drawn on it. On a 4K frame it shows at a
    glance whether the box of the detection stream falls on the right cows,
    which it does not when the two streams run apart. Kept at full size, so
    the numbers on the cows can be read."""
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
    if width and frame_width > width:
        marked = cv2.resize(
            marked, (width, round(height * width / frame_width)), interpolation=cv2.INTER_AREA
        )
    return marked


# BGR: the jumping cow is blue, the mounted cow red.
MOUNTER_COLOR = (255, 90, 0)
MOUNTED_COLOR = (0, 0, 230)


def alert_image(
    image: ndarray, mounter: Box, mounted: Box | None, width: int = 1600
) -> ndarray:
    """The frame of the jump for the phone: a blue box around the cow that
    jumps and a red box around the cow that is mounted, each with a tag."""
    height, frame_width = image.shape[:2]
    if frame_width > width:
        image = cv2.resize(
            image, (width, round(height * width / frame_width)), interpolation=cv2.INTER_AREA
        )
        height, frame_width = image.shape[:2]
    marked = image.copy()
    thickness = max(3, frame_width // 400)
    scale = max(0.6, frame_width / 1600)
    tagged = [
        (box, color, label)
        for box, color, label in ((mounted, MOUNTED_COLOR, "BESPRONGEN"), (mounter, MOUNTER_COLOR, "SPRINGT"))
        if box is not None
    ]
    pixels = [
        (int(box[0] * frame_width), int(box[1] * height), int(box[2] * frame_width), int(box[3] * height))
        for box, _, _ in tagged
    ]
    for (x1, y1, x2, y2), (_, color, _) in zip(pixels, tagged):
        cv2.rectangle(marked, (x1, y1), (x2, y2), color, thickness)
    # The tags last, so a box never runs through one.
    for (x1, y1, _, _), (_, color, label) in zip(pixels, tagged):
        (text_w, text_h), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness // 2 + 1)
        top = max(0, y1 - text_h - baseline - 6)
        cv2.rectangle(marked, (x1, top), (x1 + text_w + 12, top + text_h + baseline + 6), color, -1)
        cv2.putText(
            marked, label, (x1 + 6, top + text_h + 2), cv2.FONT_HERSHEY_SIMPLEX,
            scale, (255, 255, 255), thickness // 2 + 1, cv2.LINE_AA,
        )
    return marked
