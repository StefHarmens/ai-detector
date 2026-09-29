import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from numpy import ndarray

logger = logging.getLogger(__name__)

# (x1, y1, x2, y2) as fractions of the frame, so boxes from the detection frame
# and the 4K frame can be compared.
Box = tuple[float, float, float, float]
Frame = tuple[datetime, ndarray]

# Search this much around the mount box for the two cows.
_SEARCH_SCALE = 2.5
# Share of a cow box that must lie inside the (slightly larger) mount box.
_MIN_INSIDE = 0.35
_MOUNT_GROW = 1.3
_MAX_PRE_FRAMES = 6
_TRACK_IOU = 0.2
# The mounter must move this much more than the mounted cow, who stands still.
_ROLE_RATIO = 1.5
_ROLE_MIN_MOTION = 0.15
_CROP_PADDING = 0.1
# A box that nearly covers the mount box is both cows seen as one.
_PAIR_IOU = 0.6


@dataclass
class CowPair:
    # Single-cow images, from the frame the pair was found in.
    crops: list[ndarray]
    boxes: list[Box]
    # Index of the cow that jumps, a guess when not certain.
    mounter: int
    certain: bool
    date: datetime


def area(box: Box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def intersection(a: Box, b: Box) -> float:
    return area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def iou(a: Box, b: Box) -> float:
    overlap = intersection(a, b)
    union = area(a) + area(b) - overlap
    return overlap / union if union > 0 else 0.0


def grow(box: Box, scale: float) -> Box:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    half_w, half_h = (box[2] - box[0]) * scale / 2, (box[3] - box[1]) * scale / 2
    return (
        max(0.0, cx - half_w),
        max(0.0, cy - half_h),
        min(1.0, cx + half_w),
        min(1.0, cy + half_h),
    )


def center(box: Box) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def crop_box(image: ndarray, box: Box, padding: float = _CROP_PADDING) -> ndarray:
    height, width = image.shape[:2]
    padded = grow(box, 1 + padding * 2)
    x1, y1 = int(padded[0] * width), int(padded[1] * height)
    x2, y2 = max(x1 + 1, int(padded[2] * width)), max(y1 + 1, int(padded[3] * height))
    return image[y1:y2, x1:x2].copy()


def inside(point: tuple[float, float], box: Box) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def pick_pair(boxes: list[Box], mount: Box) -> list[Box]:
    """Returns the two cows that lie most inside the mount box, largest share
    first, or fewer when there are not two.

    On the barn examples a cow lying in a cubicle next to the mount was picked
    as one of the two; her center lies outside the mount box, so each cow's
    center must lie inside it. Asking the farmer is better than a wrong photo
    in a cow folder."""
    region = grow(mount, _MOUNT_GROW)
    scored = [
        (intersection(box, region) / area(box), box)
        for box in boxes
        if area(box) > 0
        # A box around both cows is not a single cow.
        and area(box) < area(mount) * 1.5
        and iou(box, mount) < _PAIR_IOU
        and inside(center(box), mount)
    ]
    scored = [item for item in scored if item[0] >= _MIN_INSIDE]
    scored.sort(key=lambda item: item[0], reverse=True)
    return [box for _, box in scored[:2]]


def motion(track: list[Box]) -> float:
    """Distance the box center moved, relative to the box height."""
    if len(track) < 2:
        return 0.0
    height = max(1e-6, sum(box[3] - box[1] for box in track) / len(track))
    return (
        sum(math.dist(center(a), center(b)) for a, b in zip(track, track[1:]))
        / height
    )


class CowSplitter:
    """Finds the two cows of a mount with a generic cow detector and guesses
    which one jumps: the cow standing in heat stays still, the other moves in.

    Only frames from before the jump are used: during it the detector sees the
    pair as one cow or picks a lying neighbour, and a wrong photo in a cow
    folder spoils the recognition."""

    def __init__(self, detect: Callable[[ndarray], list[Box]]):
        # Returns the cows in an image as fractions of that image.
        self.detect = detect

    def split(
        self, frames: list[Frame], mount: Box, mount_start: datetime
    ) -> CowPair | None:
        frames = sorted(frames, key=lambda frame: frame[0])
        before = [frame for frame in frames if frame[0] < mount_start][-_MAX_PRE_FRAMES:]
        # All cows per frame: the mounter often walks in from outside the box.
        found = [(frame, self._cows(frame[1], mount)) for frame in before]
        pairs = [
            (frame, pair)
            for frame, pair in ((frame, pick_pair(cows, mount)) for frame, cows in found)
            if len(pair) == 2
        ]
        if not pairs:
            logger.info("Could not find two separate cows for this mount")
            return None

        (date, image), pair = pairs[-1]
        earlier = [item for item in found if item[0][0] < date]
        tracks = [self._track(box, earlier) for box in pair]
        motions = [motion(track) for track in tracks]
        mounter = int(motions[1] > motions[0])
        slow, fast = sorted(motions)
        certain = fast >= _ROLE_MIN_MOTION and fast >= slow * _ROLE_RATIO
        logger.info(
            "Split mount into two cows, motion %.2f and %.2f, %s role",
            motions[0],
            motions[1],
            "certain" if certain else "uncertain",
        )
        return CowPair(
            crops=[crop_box(image, box) for box in pair],
            boxes=pair,
            mounter=mounter,
            certain=certain,
            date=date,
        )

    def _cows(self, image: ndarray, mount: Box) -> list[Box]:
        """Runs the detector on the area around the mount, which keeps the cows
        large enough to be found in a wide barn view."""
        height, width = image.shape[:2]
        region = grow(mount, _SEARCH_SCALE)
        x1, y1 = int(region[0] * width), int(region[1] * height)
        x2, y2 = int(region[2] * width), int(region[3] * height)
        if x2 - x1 < 8 or y2 - y1 < 8:
            return []
        region_w, region_h = region[2] - region[0], region[3] - region[1]
        return [
            (
                region[0] + box[0] * region_w,
                region[1] + box[1] * region_h,
                region[0] + box[2] * region_w,
                region[1] + box[3] * region_h,
            )
            for box in self.detect(image[y1:y2, x1:x2])
        ]

    @staticmethod
    def _track(box: Box, found: list[tuple[Frame, list[Box]]]) -> list[Box]:
        """Follows a cow back through the frames before the mount."""
        track = [box]
        for _, boxes in reversed(found):
            matches = [(iou(track[-1], other), other) for other in boxes]
            best = max(matches, default=(0.0, None), key=lambda item: item[0])
            if best[1] is not None and best[0] >= _TRACK_IOU:
                track.append(best[1])
        return list(reversed(track))


def yolo_cow_detector(model_path: str, confidence: float) -> Callable[[ndarray], list[Box]]:
    from ultralytics import YOLO

    model = YOLO(model_path)
    names = model.names if isinstance(model.names, dict) else dict(enumerate(model.names))
    cow_classes = [int(key) for key, name in names.items() if str(name) == "cow"]
    if not cow_classes:
        raise ValueError(f"cows.segment_model {model_path} has no class 'cow'")

    def detect(image: ndarray) -> list[Box]:
        height, width = image.shape[:2]
        boxes = model.predict(
            image, classes=cow_classes, conf=confidence, verbose=False
        )[0].boxes
        if boxes is None:
            return []
        # Tensor and ndarray both copy to plain floats with tolist().
        return [
            (x1 / width, y1 / height, x2 / width, y2 / height)
            for x1, y1, x2, y2 in boxes.xyxy.tolist()
        ]

    return detect
