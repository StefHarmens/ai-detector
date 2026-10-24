import numpy as np

from aidetector.cows.photos import MOUNTED_COLOR, MOUNTER_COLOR, alert_image


def pixel(image, x, y):
    return tuple(int(value) for value in image[y, x])


def test_the_jumping_cow_gets_a_blue_box_and_the_mounted_cow_a_red_one():
    frame = np.full((900, 1600, 3), 90, dtype=np.uint8)

    marked = alert_image(frame, (0.1, 0.2, 0.4, 0.5), (0.5, 0.4, 0.8, 0.9))

    # The left edge of each box, halfway down.
    assert pixel(marked, 160, 315) == MOUNTER_COLOR
    assert pixel(marked, 800, 585) == MOUNTED_COLOR
    # The inside is left alone, so the numbers on the cows can be read.
    assert pixel(marked, 300, 315) == (90, 90, 90)
    assert marked.shape == frame.shape


def test_a_big_frame_is_made_smaller_for_the_phone_and_the_original_is_kept():
    frame = np.full((2160, 3840, 3), 90, dtype=np.uint8)

    marked = alert_image(frame, (0.1, 0.2, 0.4, 0.5), None)

    assert marked.shape == (900, 1600, 3)
    assert int(frame[0, 0, 0]) == 90 and frame.shape[1] == 3840


def test_without_a_mounted_cow_only_the_jumper_is_marked():
    frame = np.full((900, 1600, 3), 90, dtype=np.uint8)

    marked = alert_image(frame, (0.1, 0.2, 0.4, 0.5), None)

    assert not (marked == np.array(MOUNTED_COLOR, dtype=np.uint8)).all(axis=2).any()
    assert pixel(marked, 160, 315) == MOUNTER_COLOR
