import json
from pathlib import Path

import pytest

from aidetector.training import build_feedback_dataset, train_feedback_model


def _write_sample(
    root: Path,
    feedback: str,
    name: str,
    boxes: list[dict] | None = None,
) -> None:
    directory = root / feedback
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.jpg").write_bytes(b"jpg")
    if boxes is not None:
        (directory / f"{name}.json").write_text(
            json.dumps({"width": 200, "height": 100, "boxes": boxes})
        )


def test_build_feedback_dataset_converts_boxes_and_negatives(tmp_path):
    box = {"x1": 20, "y1": 10, "x2": 100, "y2": 50, "label": "cow"}
    _write_sample(tmp_path, "good", "positive-1", [box])
    _write_sample(tmp_path, "good", "positive-2", [box])
    _write_sample(tmp_path, "bad", "negative-1")
    _write_sample(tmp_path, "bad", "negative-2")

    output = tmp_path / "dataset"
    summary = build_feedback_dataset(tmp_path, output, ["cow"])

    assert summary.good == 2
    assert summary.bad == 2
    assert summary.train == 2
    assert summary.validation == 2
    positive_labels = [
        path.read_text() for path in (output / "labels").rglob("good_*.txt")
    ]
    negative_labels = [
        path.read_text() for path in (output / "labels").rglob("bad_*.txt")
    ]
    assert positive_labels == ["0 0.300000 0.300000 0.400000 0.400000\n"] * 2
    assert negative_labels == ["", ""]
    dataset_config = json.loads(summary.config_path.read_text())
    assert dataset_config["names"] == {"0": "cow"}


def test_build_feedback_dataset_requires_labels_for_manual_good_images(tmp_path):
    _write_sample(tmp_path, "good", "unlabelled-1")
    _write_sample(tmp_path, "good", "unlabelled-2")
    _write_sample(tmp_path, "bad", "negative-1")
    _write_sample(tmp_path, "bad", "negative-2")

    with pytest.raises(ValueError, match="needs a matching"):
        build_feedback_dataset(tmp_path, tmp_path / "dataset", ["cow"])


def test_train_feedback_model_saves_model_and_updates_config(tmp_path, monkeypatch):
    box = {"x1": 20, "y1": 10, "x2": 100, "y2": 50, "label": "cow"}
    _write_sample(tmp_path, "good", "positive-1", [box])
    _write_sample(tmp_path, "good", "positive-2", [box])
    _write_sample(tmp_path, "bad", "negative-1")
    _write_sample(tmp_path, "bad", "negative-2")
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"detectors": [{"yolo": {"model": "base.pt", "imgsz": 640}}]})
    )
    calls = []

    class FakeYOLO:
        def __init__(self, model):
            self.names = {0: "cow"}
            calls.append(("load", model))

        def train(self, **kwargs):
            calls.append(("train", kwargs))
            save_directory = tmp_path / "run"
            weights = save_directory / "weights"
            weights.mkdir(parents=True)
            (weights / "best.pt").write_bytes(b"trained")
            return type("Result", (), {"save_dir": save_directory})()

    monkeypatch.setattr("ultralytics.YOLO", FakeYOLO)
    output_path = tmp_path / "models" / "feedback.pt"

    result = train_feedback_model(
        config_path=config_path,
        data_root=tmp_path,
        output_path=output_path,
        detector_index=0,
        epochs=5,
        batch=2,
        device="cpu",
        update_config=True,
    )

    assert result == output_path
    assert output_path.read_bytes() == b"trained"
    assert calls[0] == ("load", "base.pt")
    assert calls[1][1]["epochs"] == 5
    assert calls[1][1]["device"] == "cpu"
    updated_config = json.loads(config_path.read_text())
    assert updated_config["detectors"][0]["yolo"]["model"] == str(output_path)
    backup_config = json.loads((tmp_path / "config.json.bak").read_text())
    assert backup_config["detectors"][0]["yolo"]["model"] == "base.pt"
