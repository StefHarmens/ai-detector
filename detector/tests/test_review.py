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
