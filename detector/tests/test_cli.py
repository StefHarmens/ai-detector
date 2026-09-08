import sys

import aidetector
import aidetector.training


def test_train_feedback_subcommand_dispatches_without_starting_detector(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "argv", ["aidetector.command", "train-feedback", "--help"])
    monkeypatch.setattr(aidetector.training, "main", lambda: calls.append(sys.argv))
    monkeypatch.setattr(aidetector, "start", lambda: calls.append("detector"))

    aidetector.main()

    assert calls == [["aidetector.command", "--help"]]
