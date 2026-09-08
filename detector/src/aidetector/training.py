import argparse
import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".webp"}


@dataclass(frozen=True)
class DatasetSummary:
    good: int
    bad: int
    train: int
    validation: int
    config_path: Path


def _image_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )


def _split(files: list[Path]) -> tuple[list[Path], list[Path]]:
    ranked = sorted(
        files,
        key=lambda path: hashlib.sha256(path.name.encode()).hexdigest(),
    )
    validation_count = max(1, round(len(ranked) * 0.2))
    return ranked[validation_count:], ranked[:validation_count]


def _validate_yolo_labels(contents: str, class_count: int, source: Path) -> str:
    normalized_lines = []
    for line_number, line in enumerate(contents.splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 5:
            raise ValueError(f"{source}:{line_number} must contain 5 values")
        class_id = int(fields[0])
        coordinates = [float(value) for value in fields[1:]]
        if not 0 <= class_id < class_count:
            raise ValueError(f"{source}:{line_number} has unknown class {class_id}")
        if any(value < 0 or value > 1 for value in coordinates):
            raise ValueError(
                f"{source}:{line_number} coordinates must be between 0 and 1"
            )
        normalized_lines.append(" ".join(fields))
    if not normalized_lines:
        raise ValueError(f"Positive sample {source.with_suffix('')} has no boxes")
    return "\n".join(normalized_lines) + "\n"


def _labels_from_metadata(
    metadata_path: Path,
    class_ids: dict[str, int],
) -> str:
    metadata = json.loads(metadata_path.read_text())
    width = int(metadata["width"])
    height = int(metadata["height"])
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid image dimensions in {metadata_path}")

    labels = []
    for box in metadata.get("boxes", []):
        label = box.get("label")
        if label not in class_ids:
            raise ValueError(f"Unknown class '{label}' in {metadata_path}")
        x1 = max(0.0, min(float(box["x1"]), width))
        y1 = max(0.0, min(float(box["y1"]), height))
        x2 = max(0.0, min(float(box["x2"]), width))
        y2 = max(0.0, min(float(box["y2"]), height))
        if x2 <= x1 or y2 <= y1:
            raise ValueError(f"Invalid box in {metadata_path}")
        center_x = ((x1 + x2) / 2) / width
        center_y = ((y1 + y2) / 2) / height
        box_width = (x2 - x1) / width
        box_height = (y2 - y1) / height
        labels.append(
            f"{class_ids[label]} {center_x:.6f} {center_y:.6f} "
            f"{box_width:.6f} {box_height:.6f}"
        )
    if not labels:
        raise ValueError(
            f"Positive sample {metadata_path.with_suffix('')} has no boxes"
        )
    return "\n".join(labels) + "\n"


def _positive_labels(image_path: Path, class_ids: dict[str, int]) -> str:
    yolo_path = image_path.with_suffix(".txt")
    if yolo_path.exists():
        return _validate_yolo_labels(yolo_path.read_text(), len(class_ids), yolo_path)

    metadata_path = image_path.with_suffix(".json")
    if metadata_path.exists():
        return _labels_from_metadata(metadata_path, class_ids)

    raise ValueError(
        f"Positive sample {image_path} needs a matching .json or YOLO .txt label file"
    )


def build_feedback_dataset(
    data_root: Path,
    output_directory: Path,
    class_names: list[str],
) -> DatasetSummary:
    good_files = _image_files(data_root / "good")
    bad_files = _image_files(data_root / "bad")
    if len(good_files) < 2 or len(bad_files) < 2:
        raise ValueError("Training requires at least 2 good and 2 bad images")
    if not class_names or len(set(class_names)) != len(class_names):
        raise ValueError("The YOLO model must provide unique class names")

    class_ids = {name: index for index, name in enumerate(class_names)}
    good_train, good_validation = _split(good_files)
    bad_train, bad_validation = _split(bad_files)
    samples = [
        *(("train", "good", path) for path in good_train),
        *(("train", "bad", path) for path in bad_train),
        *(("val", "good", path) for path in good_validation),
        *(("val", "bad", path) for path in bad_validation),
    ]

    if output_directory.exists():
        shutil.rmtree(output_directory)
    for split in ("train", "val"):
        (output_directory / "images" / split).mkdir(parents=True)
        (output_directory / "labels" / split).mkdir(parents=True)

    for split, feedback, image_path in samples:
        destination_name = f"{feedback}_{image_path.name}"
        destination_image = output_directory / "images" / split / destination_name
        destination_label = (
            output_directory
            / "labels"
            / split
            / Path(destination_name).with_suffix(".txt")
        )
        shutil.copyfile(image_path, destination_image)
        labels = _positive_labels(image_path, class_ids) if feedback == "good" else ""
        destination_label.write_text(labels)

    config_path = output_directory / "data.yaml"
    config_path.write_text(
        json.dumps(
            {
                "path": str(output_directory.resolve()),
                "train": "images/train",
                "val": "images/val",
                "names": {index: name for index, name in enumerate(class_names)},
            },
            indent=2,
        )
    )
    return DatasetSummary(
        good=len(good_files),
        bad=len(bad_files),
        train=len(good_train) + len(bad_train),
        validation=len(good_validation) + len(bad_validation),
        config_path=config_path,
    )


def _model_names(model: Any) -> list[str]:
    names = model.names
    if isinstance(names, dict):
        return [str(names[index]) for index in sorted(names)]
    return [str(name) for name in names]


def _read_detector_config(config_path: Path, detector_index: int) -> tuple[dict, dict]:
    document = json.loads(config_path.read_text())
    try:
        detector = document["detectors"][detector_index]
        yolo = detector["yolo"]
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError(f"No YOLO detector at index {detector_index}") from error
    if yolo.get("task", "detect") != "detect":
        raise ValueError(
            "Feedback training currently supports YOLO detection models only"
        )
    return document, yolo


def train_feedback_model(
    config_path: Path,
    data_root: Path,
    output_path: Path,
    detector_index: int,
    epochs: int,
    batch: int,
    device: str | None,
    update_config: bool,
) -> Path:
    from ultralytics import YOLO

    config_document, yolo_config = _read_detector_config(config_path, detector_index)
    model_reference = str(yolo_config["model"])
    if not model_reference.lower().endswith(".pt"):
        raise ValueError("Training must start from a PyTorch .pt model")

    model = YOLO(model_reference)
    dataset_directory = data_root / ".training-dataset"
    summary = build_feedback_dataset(
        data_root,
        dataset_directory,
        _model_names(model),
    )
    print(
        f"Prepared {summary.train} training and {summary.validation} validation images "
        f"({summary.good} good, {summary.bad} bad)."
    )

    train_options: dict[str, Any] = {
        "data": str(summary.config_path),
        "epochs": epochs,
        "batch": batch,
        "imgsz": int(yolo_config.get("imgsz", 640)),
        "project": str(data_root / ".training-runs"),
        "name": "feedback",
        "exist_ok": True,
    }
    if device:
        train_options["device"] = device
    result = model.train(**train_options)
    best_model = Path(result.save_dir) / "weights" / "best.pt"
    if not best_model.is_file():
        raise FileNotFoundError(f"Training did not produce {best_model}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(best_model, output_path)
    if update_config:
        yolo_config["model"] = str(output_path)
        backup_path = config_path.with_name(f"{config_path.name}.bak")
        temporary_path = config_path.with_name(f".{config_path.name}.tmp")
        shutil.copy2(config_path, backup_path)
        temporary_path.write_text(json.dumps(config_document, indent=4))
        temporary_path.replace(config_path)
        print(f"Updated {config_path}; restart the detector to load the new model.")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fine-tune the configured YOLO model with Telegram feedback."
    )
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--data-root", type=Path, default=Path("."))
    parser.add_argument(
        "--output", type=Path, default=Path("models/cowcatcher-feedback.pt")
    )
    parser.add_argument("--detector-index", type=int, default=0)
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--device")
    parser.add_argument("--update-config", action="store_true")
    args = parser.parse_args()

    output = train_feedback_model(
        config_path=args.config,
        data_root=args.data_root,
        output_path=args.output,
        detector_index=args.detector_index,
        epochs=args.epochs,
        batch=args.batch,
        device=args.device,
        update_config=args.update_config,
    )
    print(f"Saved trained model to {output}")


if __name__ == "__main__":
    main()
