from datetime import datetime, timedelta

import numpy as np
import pytest

from aidetector.cows.split import CowSplitter, grow, pick_pair, union

START = datetime(2026, 10, 2, 8, 0, 0)
MOUNT = (0.40, 0.40, 0.60, 0.70)
REGION = grow(MOUNT, 2.5)
STILL = (0.45, 0.40, 0.55, 0.60)


def frame(index: int) -> np.ndarray:
    # The fill value tells the fake detector which frame it looks at.
    return np.full((720, 1280, 3), index, dtype=np.uint8)


def fake_detector(cows_per_frame: dict[int, list[tuple[float, float, float, float]]]):
    """Returns the given cows (as fractions of the frame) relative to the area
    around the mount that the splitter passes in."""
    width, height = REGION[2] - REGION[0], REGION[3] - REGION[1]

    def detect(image):
        return [
            (
                (box[0] - REGION[0]) / width,
                (box[1] - REGION[1]) / height,
                (box[2] - REGION[0]) / width,
                (box[3] - REGION[1]) / height,
            )
            for box in cows_per_frame.get(int(image[0, 0, 0]), [])
        ]

    return detect


def mover(step: int) -> tuple[float, float, float, float]:
    x = 0.20 + step * 0.06
    return (x, 0.45, x + 0.12, 0.65)


def test_the_cow_that_walks_in_is_the_mounter():
    cows = {index: [STILL, mover(index)] for index in range(4)}
    frames = [(START + timedelta(seconds=index - 4), frame(index)) for index in range(4)]

    pair = CowSplitter(fake_detector(cows)).split(frames, MOUNT, START)

    assert pair is not None
    assert pair.certain
    assert pair.boxes[pair.mounter] == pytest.approx(mover(3))
    assert pair.boxes[1 - pair.mounter] == pytest.approx(STILL)
    assert pair.crops[0].shape[0] > 0 and pair.crops[1].shape[0] > 0
    assert pair.date == START - timedelta(seconds=1)


def test_two_cows_standing_still_give_an_uncertain_role():
    other = (0.52, 0.45, 0.64, 0.65)
    cows = {index: [STILL, other] for index in range(3)}
    frames = [(START + timedelta(seconds=index - 3), frame(index)) for index in range(3)]

    pair = CowSplitter(fake_detector(cows)).split(frames, MOUNT, START)

    assert pair is not None
    assert not pair.certain


def test_frames_during_the_jump_are_never_split():
    frames = [
        (START - timedelta(seconds=1), frame(0)),
        (START + timedelta(seconds=1), frame(1)),
    ]
    cows = {0: [STILL], 1: [STILL, (0.50, 0.42, 0.60, 0.68)]}

    assert CowSplitter(fake_detector(cows)).split(frames, MOUNT, START) is None


def test_no_pair_without_two_cows():
    frames = [(START - timedelta(seconds=1), frame(0))]
    assert CowSplitter(fake_detector({0: [STILL]})).split(frames, MOUNT, START) is None


def test_pick_pair_ignores_cows_elsewhere_and_boxes_around_both():
    far = (0.0, 0.0, 0.1, 0.1)
    around_both = (0.30, 0.30, 0.70, 0.80)
    near = (0.50, 0.45, 0.62, 0.68)

    assert pick_pair([far, around_both, STILL, near], MOUNT) == [STILL, near]


def test_a_cow_lying_next_to_the_mount_is_not_one_of_the_pair():
    # On the barn examples: the pair seen as one box, and a neighbour in a
    # cubicle whose center lies outside the mount box.
    both = (0.41, 0.41, 0.59, 0.69)
    neighbour = (0.52, 0.50, 0.72, 0.62)
    frames = [(START - timedelta(seconds=1), frame(0))]

    assert pick_pair([both, neighbour, STILL], MOUNT) == [STILL]
    assert CowSplitter(fake_detector({0: [both, neighbour, STILL]})).split(frames, MOUNT, START) is None


def test_the_mounted_cow_photo_includes_the_cow_below():
    # The pair seen as one fits the mount box; the cow below sticks out.
    both = (0.41, 0.41, 0.59, 0.69)
    below = (0.50, 0.50, 0.75, 0.66)
    # After JPEG storage the detector also found the mounter's back.
    back = (0.42, 0.41, 0.59, 0.47)
    splitter = CowSplitter(fake_detector({0: [both, back, below]}))

    assert splitter.mounted_region(frame(0), MOUNT) == pytest.approx(union(MOUNT, below))


def test_the_mounted_cow_photo_is_wider_without_her_box():
    lying_far = (0.62, 0.40, 0.80, 0.50)
    splitter = CowSplitter(fake_detector({0: [lying_far]}))

    assert splitter.mounted_region(frame(0), MOUNT) == pytest.approx(grow(MOUNT, 1.8))


def test_frames_after_the_jump_are_the_second_chance():
    # Before the jump the cows stood as one; after it the mounter steps off.
    end = START + timedelta(seconds=5)
    before = [(START - timedelta(seconds=1), frame(0))]
    after = [(end + timedelta(seconds=offset), frame(offset)) for offset in (1, 2, 3)]
    stepping_off = {offset: (0.45 + offset * 0.04, 0.45, 0.57 + offset * 0.04, 0.65) for offset in (1, 2, 3)}
    cows = {0: [STILL], **{offset: [STILL, stepping_off[offset]] for offset in (1, 2, 3)}}

    pair = CowSplitter(fake_detector(cows)).split(before + after, MOUNT, START, end)

    assert pair is not None
    assert pair.date == end + timedelta(seconds=1)
    assert pair.boxes[pair.mounter] == pytest.approx(stepping_off[1])
    assert pair.certain


def test_the_masked_crop_keeps_only_the_cow():
    from aidetector.cows.split import MASK_FILL, Found, cow_crops

    image = np.full((100, 100, 3), 50, dtype=np.uint8)
    mask = np.zeros((40, 40), dtype=bool)
    mask[10:30, 10:30] = True
    # The mask covers the searched area that starts at (20, 20).
    found = Found((0.3, 0.3, 0.5, 0.5), mask, (20, 20))

    plain, masked = cow_crops(image, found, padding=0)

    assert plain.shape == masked.shape == (20, 20, 3)
    assert (plain == 50).all()
    assert (masked == 50).all()  # the box lies fully on the cow
    wide_plain, wide_masked = cow_crops(image, Found((0.2, 0.2, 0.6, 0.6), mask, (20, 20)), padding=0)
    assert wide_masked[0, 0, 0] == MASK_FILL and wide_masked[20, 20, 0] == 50
    assert (wide_plain == 50).all()


def test_the_detector_can_return_masks():
    from aidetector.cows.split import Found

    mask = np.ones((10, 10), dtype=bool)
    splitter = CowSplitter(lambda image: [((0.1, 0.1, 0.5, 0.5), mask)])

    found = splitter._cows(frame(0), MOUNT)

    assert isinstance(found[0], Found) and found[0].mask is mask
    height, width = 720, 1280
    assert found[0].origin == (int(REGION[0] * width), int(REGION[1] * height))
