import pytest

from aidetector.cows.answers import Answer, parse_answers
from aidetector.cows.photos import control_image, side_by_side


def test_two_numbers_are_the_mounter_and_the_mounted_cow():
    assert parse_answers("30 12") == [Answer("30"), Answer("12")]
    assert parse_answers("30, 12") == [Answer("30"), Answer("12")]


def test_unknown_and_new_cows():
    assert parse_answers("? 12") == [Answer(None), Answer("12")]
    assert parse_answers("44 NL123456789 12") == [Answer("44", "NL123456789"), Answer("12")]
    # A life number typed with spaces, followed by the other cow.
    assert parse_answers("44 NL 1234 5678 9 12") == [Answer("44", "NL123456789"), Answer("12")]
    assert parse_answers("#07") == [Answer("7")]


def test_heifers_can_be_answered_by_name():
    assert parse_answers("Anna 12") == [Answer(None, name="Anna"), Answer("12")]
    # "Nel" is a name, not the start of a life number; "Jo" too.
    assert parse_answers("44 Nel") == [Answer("44"), Answer(None, name="Nel")]
    assert parse_answers("? Jo") == [Answer(None), Answer(None, name="Jo")]
    assert parse_answers("44 NL 1234 5678 9 Anna") == [
        Answer("44", "NL123456789"),
        Answer(None, name="Anna"),
    ]


def test_nonsense_is_refused():
    with pytest.raises(ValueError, match="geen nummer of naam"):
        parse_answers("3x")
    with pytest.raises(ValueError, match="levensnummer"):
        parse_answers("44 NL12")


def test_both_cows_in_one_photo_with_labels():
    import numpy as np

    small = np.zeros((100, 60, 3), dtype=np.uint8)
    wide = np.zeros((200, 300, 3), dtype=np.uint8)

    photo = side_by_side([small, wide], ["A", "B"])

    # Both enlarged to at least 360 px, with a label strip on top.
    assert photo.shape[0] > 360
    assert photo.shape[1] > 216 + 540


def test_control_image_draws_the_mount_box_and_stays_small():
    import numpy as np

    frame = np.zeros((2160, 3840, 3), dtype=np.uint8)

    marked = control_image(frame, (0.25, 0.25, 0.5, 0.5))

    assert marked.shape == (1080, 1920, 3)
    assert marked[270, 700, 2] == 255  # red on the top edge of the box
