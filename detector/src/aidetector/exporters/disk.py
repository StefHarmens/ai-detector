import json
from dataclasses import asdict
from pathlib import Path

from aidetector.exporters.exporter import Exporter
from aidetector.media.video import generate_mp4, get_crop, get_image, get_plot
from aidetector.sources.hires import hires_detection
from aidetector.utils.config import (
    Detection,
    DiskConfig,
    get_date_path,
    get_timestamped_filename,
    max_confidence,
)
from pydantic.dataclasses import dataclass


HIRES_BEST = "hires-best.jpg"


class DiskExporter(Exporter[DiskConfig]):
    directory: Path | None

    def __init__(self, config: DiskConfig):
        super().__init__(config)
        self.directory = Path("detections") / config.directory if config.directory else None
        if self.directory:
            self.directory.mkdir(parents=True, exist_ok=True)

    def filtered_export(
        self,
        best_detection: Detection,
        detections: list[Detection],
        validated: bool | None,
    ):
        self.logger.info(f"Saving {len(detections)} photos to disk")
        timestamp = get_date_path(best_detection, "seconds")
        subfolder = (
            "approved"
            if validated
            else "rejected"
            if validated is False
            else "unvalidated"
        )

        confidence_max = max(best_detection.confidence.items(), key=lambda x: x[1])
        directory = self.directory or Path("detections") / confidence_max[0]
        directory.mkdir(parents=True, exist_ok=True)

        timestamped_directory = (
            # One flat folder per event, which review-feedback reads directly.
            directory / f"{timestamp} {best_detection.camera or ''}".strip()
            if self.config.review
            else directory / subfolder / timestamp
        )
        timestamped_directory.mkdir(parents=True, exist_ok=True)
        if self.config.strategy == "ALL":
            for result in detections:
                image_name = get_timestamped_filename(result)
                image_path = timestamped_directory / image_name
                with open(image_path, "wb") as f:
                    f.write(get_image(result.images.jpg))
        if best_detection:
            image_path = timestamped_directory / "best.jpg"
            with open(image_path, "wb") as f:
                f.write(get_image(get_plot(best_detection)))
            clean_image_path = timestamped_directory / "clean.jpg"
            with open(clean_image_path, "wb") as f:
                f.write(get_image(best_detection.images.jpg))
        hires = hires_detection(best_detection)
        hires_crop = (
            get_crop(hires, aspect_ratio=None, padding=self.config.crop_padding, plot=False)
            if hires
            else None
        )
        if hires_crop is not None:
            with open(timestamped_directory / "hires.jpg", "wb") as f:
                f.write(get_image(hires_crop, 95))
        if hires is not None:
            # The whole 4K frame with the boxes, for the web page.
            with open(timestamped_directory / HIRES_BEST, "wb") as f:
                f.write(get_image(get_plot(hires), 90))
        video = generate_mp4(
            detections,
            padding=self.config.crop_padding,
            hires=best_detection.hires,
            # Near lossless: lossless 4K takes far too much disk.
            crf=18 if best_detection.hires else 0,
        )
        if video:
            video_path = timestamped_directory / "video.mp4"
            with open(video_path, "wb") as f:
                f.write(video)
        crop_region = best_detection.images.crop_region
        height, width = best_detection.images.height, best_detection.images.width
        metadata: Metadata = Metadata(
            timestamp=timestamp,
            validated=validated,
            confidence=max_confidence(best_detection.confidence),
            confidences=best_detection.confidence,
            detections=len(detections),
            start=detections[0].date.isoformat(),
            end=detections[-1].date.isoformat(),
            duration=(detections[-1].date - detections[0].date).total_seconds(),
            crop={
                "x1": crop_region.x1,
                "y1": crop_region.y1,
                "x2": crop_region.x2,
                "y2": crop_region.y2,
            }
            if crop_region
            else None,
            camera=best_detection.camera,
            hires=hires is not None,
            width=width,
            height=height,
            boxes=[
                {
                    "x1": crop.x1,
                    "y1": crop.y1,
                    "x2": crop.x2,
                    "y2": crop.y2,
                    "label": crop.label,
                }
                for crop in best_detection.images.crops
                if crop.label
            ],
        )
        metadata_path = timestamped_directory / "metadata.json"
        with open(metadata_path, "w") as f:
            json.dump(asdict(metadata), f)


@dataclass
class Metadata:
    timestamp: str
    validated: bool | None
    confidence: float
    confidences: dict[str, float]
    detections: int
    start: str
    end: str
    duration: float
    crop: dict[str, int] | None = None
    camera: str | None = None
    # Whether hires.jpg and hires-best.jpg hold the 4K frame.
    hires: bool = False
    width: int | None = None
    height: int | None = None
    boxes: list[dict[str, int | str]] | None = None
