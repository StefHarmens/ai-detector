import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from numpy import ndarray

logger = logging.getLogger(__name__)

# (x1, y1, x2, y2) as fractions of the frame, so boxes from the detection frame
# and the 4K frame can be compared.
Box = tuple[float, float, float, float]
Frame = tuple[datetime, ndarray]
# What a detector returns per cow: her box as fractions of the image it got,
# optionally with a mask of her pixels in that image.
Detected = Box | tuple[Box, ndarray | None]

# Search this much around the mount box for the two cows.
_SEARCH_SCALE = 2.5
# A cow's center must lie in this much of the mount box.
_MOUNT_GROW = 1.3
# In a crowded barn the cow model also finds pieces of cows (0.07-0.2 of the
# mount box on the farm frames); a whole cow is at least this big. At 0.30 a
# box around the jump itself got in.
_MIN_COW_SIZE = 0.35
# The two cows may overlap this much (IoU), and must cover this share of the
# mount box together.
_MAX_PAIR_OVERLAP = 0.3
_MIN_PAIR_COVER = 0.5
# Frames used on each side of the jump.
_SIDE_FRAMES = 6
_TRACK_IOU = 0.2
# The cow that walks in (or steps off) must move this much more than the
# mounted cow, who stands still.
_ROLE_RATIO = 1.5
_ROLE_MIN_MOTION = 0.15
_CROP_PADDING = 0.1
# A box that nearly covers the mount box is both cows seen as one.
_PAIR_IOU = 0.6
# Share of a cow box inside the mount box to count as the mounted cow.
_MOUNTED_INSIDE = 0.25
# Without her box, this much around the mount box shows her in full.
_MOUNTED_GROW = 1.8
# Grey used around a masked cow, the same as the padding of the embedder.
MASK_FILL = 114


@dataclass
class Found:
    """A cow found near the mount."""

    box: Box
    # Her pixels in the searched part of the frame, which starts at `origin`.
    mask: ndarray | None = None
    origin: tuple[int, int] = (0, 0)


@dataclass
class CowPair:
    # Single-cow images from the frame the pair was found in: as seen, for the
    # farmer, and with everything but the cow grey, for recognition.
    crops: list[ndarray]
    masked: list[ndarray]
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


def union(a: Box, b: Box) -> Box:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def center(box: Box) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def _pixels(image: ndarray, box: Box, padding: float) -> tuple[int, int, int, int]:
    height, width = image.shape[:2]
    padded = grow(box, 1 + padding * 2)
    x1, y1 = int(padded[0] * width), int(padded[1] * height)
    x2, y2 = max(x1 + 1, int(padded[2] * width)), max(y1 + 1, int(padded[3] * height))
    return x1, y1, x2, y2


def crop_box(image: ndarray, box: Box, padding: float = _CROP_PADDING) -> ndarray:
    x1, y1, x2, y2 = _pixels(image, box, padding)
    return image[y1:y2, x1:x2].copy()


def cow_crops(
    image: ndarray, found: Found, padding: float = _CROP_PADDING
) -> tuple[ndarray, ndarray]:
    """Returns the crop of a cow and the same crop with everything but her
    grey. The barn and the cubicles made different cows look alike."""
    x1, y1, x2, y2 = _pixels(image, found.box, padding)
    plain = image[y1:y2, x1:x2].copy()
    if found.mask is None:
        return plain, plain
    cow = np.zeros(plain.shape[:2], dtype=bool)
    origin_x, origin_y = found.origin
    mask_h, mask_w = found.mask.shape[:2]
    left, top = max(x1, origin_x), max(y1, origin_y)
    right, bottom = min(x2, origin_x + mask_w), min(y2, origin_y + mask_h)
    if right > left and bottom > top:
        cow[top - y1 : bottom - y1, left - x1 : right - x1] = found.mask[
            top - origin_y : bottom - origin_y, left - origin_x : right - origin_x
        ]
    masked = plain.copy()
    masked[~cow] = MASK_FILL
    return plain, masked


def inside(point: tuple[float, float], box: Box) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def _overlap_box(a: Box, b: Box) -> Box:
    if intersection(a, b) == 0:
        return (0.0, 0.0, 0.0, 0.0)
    return (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))


def pick_pair(boxes: Sequence[Box], mount: Box) -> list[Box]:
    """Returns the two cows of the mount, or none when no two separate whole
    cows fill the mount box together.

    Judged by the farmer on 15 real mounts, the old choice (the two boxes
    lying most inside the mount box) was right twice: in a crowded barn small
    pieces of cows lie fully inside and won, giving blurry close-ups, the same
    cow twice or a neighbour. Only whole cows count now, the pair must not be
    one cow seen twice, and together they must cover the mount box; that was
    right 11 times. No pair means no photo in a cow folder, which is better
    than a wrong one."""
    region = grow(mount, _MOUNT_GROW)
    cows = [
        box
        for box in boxes
        if area(mount) * _MIN_COW_SIZE <= area(box) < area(mount) * 1.5
        # A box around both cows is not a single cow.
        and iou(box, mount) < _PAIR_IOU
        and inside(center(box), region)
    ]
    best: tuple[float, list[Box]] | None = None
    for index, first in enumerate(cows):
        for second in cows[index + 1 :]:
            overlap = iou(first, second)
            if overlap > _MAX_PAIR_OVERLAP:
                continue
            cover = (
                intersection(first, mount)
                + intersection(second, mount)
                - intersection(_overlap_box(first, second), mount)
            ) / area(mount)
            if cover < _MIN_PAIR_COVER:
                continue
            score = cover - overlap
            if best is None or score > best[0]:
                best = (score, [first, second])
    return best[1] if best else []


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
    which one jumps: the cow standing in heat stays still, the other walks in
    before the jump and steps off after it.

    Frames during the jump are not used: then the detector sees the pair as
    one cow or picks a lying neighbour, and a wrong photo in a cow folder
    spoils the recognition. Frames before the jump come first, those after it
    are the second chance."""

    def __init__(self, detect: Callable[[ndarray], Sequence[Detected]]):
        self.detect = detect

    def split(
        self,
        frames: list[Frame],
        mount: Box,
        mount_start: datetime,
        mount_end: datetime | None = None,
    ) -> CowPair | None:
        frames = sorted(frames, key=lambda frame: frame[0])
        before = [frame for frame in frames if frame[0] < mount_start][-_SIDE_FRAMES:]
        after = (
            [frame for frame in frames if frame[0] > mount_end][:_SIDE_FRAMES]
            if mount_end is not None
            else []
        )
        # Nearest to the jump first on each side, so the cows are still close.
        for side, ordered in (("before", before[::-1]), ("after", after)):
            pair = self._split_side(ordered, mount)
            if pair is not None:
                logger.info("Split mount into two cows using frames %s the jump", side)
                return pair
        logger.info("Could not find two separate cows for this mount")
        return None

    def _split_side(self, frames: list[Frame], mount: Box) -> CowPair | None:
        """Uses the frame nearest to the jump that shows two separate cows, and
        the frames further away to see which cow moved."""
        # All cows per frame: the mounter often comes from outside the box.
        found = [(frame, self._cows(frame[1], mount)) for frame in frames]
        for index, ((date, image), cows) in enumerate(found):
            boxes = [cow.box for cow in cows]
            pair = [cows[boxes.index(box)] for box in pick_pair(boxes, mount)]
            if len(pair) < 2:
                continue
            further = [[cow.box for cow in others] for _, others in found[index + 1 :]]
            motions = [motion(self._track(cow.box, further)) for cow in pair]
            mounter = int(motions[1] > motions[0])
            slow, fast = sorted(motions)
            certain = fast >= _ROLE_MIN_MOTION and fast >= slow * _ROLE_RATIO
            logger.info(
                "Cows moved %.2f and %.2f, %s role",
                motions[0],
                motions[1],
                "certain" if certain else "uncertain",
            )
            crops = [cow_crops(image, cow) for cow in pair]
            return CowPair(
                crops=[plain for plain, _ in crops],
                masked=[masked for _, masked in crops],
                boxes=[cow.box for cow in pair],
                mounter=mounter,
                certain=certain,
                date=date,
            )
        return None

    def mounted_region(self, image: ndarray, mount: Box) -> Box:
        """The area that shows the mounted cow in full on a frame of the jump.
        The mount box fits the mounter; the cow below sticks out of it with her
        head. She is the cow that is partly inside the mount box and reaches
        furthest out of it: the detector also finds pieces of the mounter, such
        as her back, that lie fully inside and add nothing. Without such a cow a
        wider area is used."""
        outside = [
            (area(box) - intersection(box, mount), box)
            for box in (cow.box for cow in self._cows(image, mount))
            if area(box) > 0
            and iou(box, mount) < _PAIR_IOU
            and intersection(box, mount) / area(box) >= _MOUNTED_INSIDE
        ]
        reach, box = max(outside, default=(0.0, None), key=lambda item: item[0])
        if box is not None and reach > 0:
            return union(mount, box)
        return grow(mount, _MOUNTED_GROW)

    def _cows(self, image: ndarray, mount: Box) -> list[Found]:
        """Runs the detector on the area around the mount, which keeps the cows
        large enough to be found in a wide barn view."""
        height, width = image.shape[:2]
        region = grow(mount, _SEARCH_SCALE)
        x1, y1 = int(region[0] * width), int(region[1] * height)
        x2, y2 = int(region[2] * width), int(region[3] * height)
        if x2 - x1 < 8 or y2 - y1 < 8:
            return []
        region_w, region_h = region[2] - region[0], region[3] - region[1]
        found = []
        for item in self.detect(image[y1:y2, x1:x2]):
            box, mask = item if len(item) == 2 else (item, None)
            found.append(
                Found(
                    (
                        region[0] + box[0] * region_w,
                        region[1] + box[1] * region_h,
                        region[0] + box[2] * region_w,
                        region[1] + box[3] * region_h,
                    ),
                    mask,
                    (x1, y1),
                )
            )
        return found

    @staticmethod
    def _track(box: Box, frames: list[list[Box]]) -> list[Box]:
        """Follows a cow through the frames, in the given order."""
        track = [box]
        for boxes in frames:
            best = max(
                ((iou(track[-1], other), other) for other in boxes),
                default=(0.0, None),
                key=lambda item: item[0],
            )
            if best[1] is not None and best[0] >= _TRACK_IOU:
                track.append(best[1])
        return track


def yolo_cow_detector(
    model_path: str, confidence: float
) -> Callable[[ndarray], list[Detected]]:
    """A COCO model with the class "cow". A segmentation model (such as
    yolo11s-seg.pt) also gives the pixels of each cow."""
    import torch
    from ultralytics import YOLO

    model = YOLO(model_path)
    names = model.names if isinstance(model.names, dict) else dict(enumerate(model.names))
    cow_classes = [int(key) for key, name in names.items() if str(name) == "cow"]
    if not cow_classes:
        raise ValueError(f"cows.segment_model {model_path} has no class 'cow'")

    def detect(image: ndarray) -> list[Detected]:
        height, width = image.shape[:2]
        result = model.predict(
            image,
            classes=cow_classes,
            conf=confidence,
            verbose=False,
            # Masks at the size of the image, not of the model input.
            retina_masks=True,
        )[0]
        if result.boxes is None:
            return []
        # Tensor and ndarray both copy to plain floats with tolist().
        boxes = [
            (x1 / width, y1 / height, x2 / width, y2 / height)
            for x1, y1, x2, y2 in result.boxes.xyxy.tolist()
        ]
        if result.masks is None:
            return list(boxes)
        data = result.masks.data
        if isinstance(data, torch.Tensor):
            data = data.cpu().numpy()
        masks = np.asarray(data) > 0.5
        return [(box, mask) for box, mask in zip(boxes, masks)]

    return detect
