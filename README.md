# AI Detector

An AI-powered detection system that watches video streams and alerts you when something is found — with a smart double-check step to filter out false alarms.

## What it does

1. **Watches** one or more cameras or video files continuously.
2. **Detects** objects using a YOLO model (fast, runs locally).
3. **Verifies** detections by asking an AI a question you define (e.g. *"Is there really a person?"*) — skipping this step is fine if you don't need it.
4. **Alerts** you via Telegram, saves images/video to disk, or calls a webhook.

## Components

| Component | Description |
| :-------- | :---------- |
| **[Detector](detector/README.md)** | The core service. Runs object detection and optional AI verification. Fully configurable via a single `config.json`. |
| **Web** *(in development)* | A frontend for monitoring live streams and reviewing past detections. |

## Quick Start

The `example/` folder has everything you need to try it out:

```bash
cd example
docker compose up -d
docker compose logs -f aidetector web
```

Open [http://localhost](http://localhost) to use the web UI.

> See **[detector/README.md](detector/README.md)** for full configuration instructions.
