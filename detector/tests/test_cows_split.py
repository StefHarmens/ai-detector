from datetime import datetime, timedelta

import numpy as np
import pytest

from aidetector.cows.split import CowSplitter, grow, pick_pair

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
