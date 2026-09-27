import sys

import aidetector
import aidetector.training


def test_train_feedback_subcommand_dispatches_without_starting_detector(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "argv", ["aidetector.command", "train-feedback", "--help"])
    monkeypatch.setattr(
        aidetector.multiprocessing,
        "freeze_support",
        lambda: calls.append("freeze-support"),
    )
    monkeypatch.setattr(aidetector.training, "main", lambda: calls.append(sys.argv))
    monkeypatch.setattr(aidetector, "start", lambda: calls.append("detector"))

    aidetector.main()

    assert calls == ["freeze-support", ["aidetector.command", "--help"]]


def test_review_feedback_subcommand_dispatches_without_starting_detector(monkeypatch):
    import aidetector.review

    calls = []
    monkeypatch.setattr(
        sys, "argv", ["aidetector.command", "review-feedback", "--source", "twijfel"]
    )
    monkeypatch.setattr(aidetector.multiprocessing, "freeze_support", lambda: None)
    monkeypatch.setattr(aidetector.review, "main", lambda: calls.append(sys.argv))
    monkeypatch.setattr(aidetector, "start", lambda: calls.append("detector"))

    aidetector.main()

    assert calls == [["aidetector.command", "--source", "twijfel"]]
