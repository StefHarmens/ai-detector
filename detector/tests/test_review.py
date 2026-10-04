import json
from pathlib import Path

import pytest

from aidetector.review import ReviewSession


def _write_event(root: Path, name: str) -> Path:
    event = root / name
    event.mkdir(parents=True)
    (event / "clean.jpg").write_bytes(f"clean-{name}".encode())
    (event / "best.jpg").write_bytes(f"best-{name}".encode())
    (event / "video.mp4").write_bytes(f"video-{name}".encode())
    (event / "metadata.json").write_text(json.dumps({"event": name}))
    return event


def test_review_session_discovers_only_complete_events(tmp_path):
    source = tmp_path / "import"
    _write_event(source, "event-b")
    _write_event(source, "event-a")
    incomplete = source / "incomplete"
    incomplete.mkdir()
    (incomplete / "clean.jpg").write_bytes(b"image")

    session = ReviewSession(source, tmp_path)

    assert list(session.events) == ["event-a", "event-b"]
    assert session.status()["current"]["name"] == "event-a"


def test_decisions_copy_reclassify_skip_and_resume(tmp_path):
    source = tmp_path / "import"
    _write_event(source, "event-a")
    _write_event(source, "event-b")
    session = ReviewSession(source, tmp_path)

    status = session.decide("event-a", "good")

    assert (tmp_path / "good" / "event-a.jpg").read_bytes() == b"clean-event-a"
    assert json.loads((tmp_path / "good" / "event-a.json").read_text()) == {
        "event": "event-a"
    }
    assert status["current"]["name"] == "event-b"

    session.decide("event-a", "bad")
    assert not (tmp_path / "good" / "event-a.jpg").exists()
    assert (tmp_path / "bad" / "event-a.jpg").exists()

    session.decide("event-b", "skip")
    assert not (tmp_path / "good" / "event-b.jpg").exists()
    assert not (tmp_path / "bad" / "event-b.jpg").exists()
    assert ReviewSession(source, tmp_path).status()["reviewed"] == 2


def test_undo_removes_generated_files_and_restores_event(tmp_path):
    source = tmp_path / "import"
    _write_event(source, "event-a")
    session = ReviewSession(source, tmp_path)
    session.decide("event-a", "good")

    status = session.undo()

    assert status["reviewed"] == 0
    assert status["current"]["name"] == "event-a"
    assert not (tmp_path / "good" / "event-a.jpg").exists()
    assert not (tmp_path / "good" / "event-a.json").exists()


def test_review_session_rejects_unknown_events_and_media(tmp_path):
    source = tmp_path / "import"
    _write_event(source, "event-a")
    session = ReviewSession(source, tmp_path)

    with pytest.raises(ValueError, match="Unknown event"):
        session.decide("../event-a", "good")
    with pytest.raises(ValueError, match="Invalid decision"):
        session.decide("event-a", "maybe")
    with pytest.raises(FileNotFoundError):
        session.media_path("event-a", "metadata.json")


def test_a_judged_event_leaves_the_doubt_folder_and_comes_back_when_taken_back(tmp_path):
    source = tmp_path / "twijfel"
    _write_event(source, "event-a")
    _write_event(source, "event-b")
    session = ReviewSession(source, tmp_path)

    session.decide("event-a", "good")

    assert not (source / "event-a").exists()
    assert (source / ".beoordeeld" / "event-a" / "video.mp4").is_file()
    assert (tmp_path / "good" / "event-a.jpg").read_bytes() == b"clean-event-a"
    # Still known, with its choice and its video, also to a new session.
    again = ReviewSession(source, tmp_path)
    assert list(again.events) == ["event-a", "event-b"]
    assert again.decisions == {"event-a": "good"}
    assert again.media_path("event-a", "video.mp4").read_bytes() == b"video-event-a"
    # Changing the choice works from the judged folder.
    again.decide("event-a", "bad")
    assert (tmp_path / "bad" / "event-a.jpg").read_bytes() == b"clean-event-a"

    again.clear("event-a")

    assert (source / "event-a" / "clean.jpg").is_file()
    assert not (source / ".beoordeeld" / "event-a").exists()
    assert not (tmp_path / "bad" / "event-a.jpg").exists()
    again.decide("event-b", "skip")
    again.undo()
    assert (source / "event-b").is_dir()


def test_events_judged_before_are_moved_out_and_cleared_after_a_month(tmp_path):
    import os
    import time

    source = tmp_path / "twijfel"
    _write_event(source, "event-a")
    _write_event(source, "event-b")
    # As an older version left it: judged, but still in the doubt folder.
    (source / ".review-decisions.json").write_text(
        json.dumps({"decisions": {"event-a": "bad"}, "history": ["event-a"]})
    )
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "event-a.jpg").write_bytes(b"clean-event-a")

    ReviewSession(source, tmp_path)

    assert sorted(path.name for path in source.iterdir() if not path.name.startswith(".")) == ["event-b"]
    month_ago = time.time() - 31 * 24 * 3600
    os.utime(source / ".beoordeeld" / "event-a", (month_ago, month_ago))

    session = ReviewSession(source, tmp_path)

    assert not (source / ".beoordeeld" / "event-a").exists()
    assert list(session.events) == ["event-b"]
    # The example for training stays.
    assert (tmp_path / "bad" / "event-a.jpg").is_file()
