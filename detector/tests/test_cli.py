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
