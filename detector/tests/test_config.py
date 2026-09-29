import json
from pathlib import Path

import pytest

from aidetector.utils.config import Config, confidence_matches, matching_confidences


def test_example_config_validates():
    repo_root = Path(__file__).resolve().parents[2]
    config_json = json.loads((repo_root / "example/config.json").read_text())

    config = Config(**config_json)

    assert len(config.detectors) == 1
    assert config.detectors[0].detection.source == ["sprong24.mp4"]
    assert config.detectors[0].exporters is not None


def test_confidence_helpers_support_global_and_per_class_thresholds():
    confidence = {"cow": 0.8, "horse": 0.3}

    assert confidence_matches(confidence, 0.75)
    assert not confidence_matches(confidence, 0.9)
    assert confidence_matches(confidence, {"horse": 0.2})
    assert matching_confidences(confidence, {"cow": 0.7, "horse": 0.7}) == ["cow"]


def test_schema_urls_use_own_repository():
    from aidetector.utils.config import schema_url, template_url

    assert "StefHarmens/ai-detector" in schema_url
    assert "StefHarmens/ai-detector" in template_url


def test_a_config_without_detectors_says_which_folder_was_read(tmp_path, monkeypatch):
    from aidetector.utils.config import load_config, schema_url

    # What a first start in the wrong folder (e.g. Downloads) leaves behind.
    (tmp_path / "config.json").write_text(json.dumps({"$schema": schema_url}))
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError) as error:
        load_config()

    message = str(error.value)
    assert "detectors: Field required" in message
    assert f"next to the program, in {tmp_path}" in message
    assert "same folder as your config.json" in message


def test_a_missing_comma_is_pointed_out_without_showing_secrets():
    from aidetector.utils.config import json_error_hint

    text = (
        '{\n'
        '\t"detection": {\n'
        '\t\t"source": ["rtsps://192.168.1.1:7441/SeCrEtKeY"]\n'
        '\t\t"hires": {"source": ["rtsps://192.168.1.1:7441/OtHeRkEy"]}\n'
        '\t},\n'
        '\t"telegram": {"token": "123:ABC", "chat": "42"}\n'
        '}'
    )
    with pytest.raises(json.JSONDecodeError) as error:
        json.loads(text)

    hint = json_error_hint(text, error.value)

    assert hint.splitlines() == [
        'Line 4: \t\t"hires": {"source": ["rtsps://192.168.1.1:7441/<key>"]}',
        "Line 4: \t\t^",
        'A comma is probably missing at the end of line 3: "source": ["rtsps://192.168.1.1:7441/<key>"]',
    ]
    assert "SeCrEtKeY" not in hint and "OtHeRkEy" not in hint
