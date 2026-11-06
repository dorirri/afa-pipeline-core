"""Tests for the computer-vision analyzer (ported prototype code).

CI has no model weights, so most tests replace ``ultralytics`` (and
``face_recognition``) with small fakes. ``test_real_weights_detect_a_bus`` runs
the real model when ``AFA_YOLO_WEIGHTS`` points at a weights file.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import types
from pathlib import Path
from typing import Any, ClassVar

import pytest

from afa_pipeline.analyzers import AnalysisStatus, CvAnalyzer
from afa_pipeline.analyzers import cv as cv_module
from afa_pipeline.ingestion import ingest
from tests.conftest import write_image

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")

LEGACY = "afa_pipeline.analyzers.legacy.content_analyzer"


class _Values:
    def __init__(self, values: list[Any]) -> None:
        self._values = values

    def tolist(self) -> list[Any]:
        return self._values


class FakeYolo:
    """Mimics ``ultralytics.YOLO``: fixed detections, filtered by ``conf``."""

    loads: ClassVar[list[str]] = []
    detections: ClassVar[list[tuple[str, float, list[float]]]] = [
        ("knife", 0.91, [10.4, 12.0, 30.0, 40.0]),
        ("person", 0.83, [0.0, 0.0, 20.0, 60.0]),
        ("cup", 0.30, [5.0, 5.0, 9.0, 9.0]),
    ]
    names: ClassVar[dict[int, str]] = {0: "knife", 1: "person", 2: "cup"}

    def __init__(self, weights: str) -> None:
        FakeYolo.loads.append(weights)
        self.overrides: dict[str, Any] = {}

    def __call__(self, img: Any, conf: float, verbose: bool) -> list[Any]:
        kept = [d for d in self.detections if d[1] >= conf]
        ids = {v: k for k, v in self.names.items()}
        boxes = types.SimpleNamespace(
            xyxy=_Values([d[2] for d in kept]),
            conf=_Values([d[1] for d in kept]),
            cls=_Values([float(ids[d[0]]) for d in kept]),
        )
        return [types.SimpleNamespace(boxes=boxes, names=self.names)]


@pytest.fixture
def fake_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Install fake ultralytics/face_recognition and point AFA_YOLO_WEIGHTS at a file."""
    FakeYolo.loads = []
    monkeypatch.setitem(sys.modules, "ultralytics", types.SimpleNamespace(YOLO=FakeYolo))
    faces = types.SimpleNamespace(
        face_locations=lambda rgb: [(5, 25, 30, 8)],
        face_encodings=lambda rgb, locations: [np.full(128, 0.5) for _ in locations],
    )
    monkeypatch.setitem(sys.modules, "face_recognition", faces)
    monkeypatch.setattr(cv_module, "_missing_modules", list)
    weights = tmp_path / "models" / "fake.pt"
    weights.parent.mkdir()
    weights.write_bytes(b"fake weights")
    monkeypatch.setenv(cv_module.YOLO_WEIGHTS_ENV, str(weights))
    monkeypatch.delenv(cv_module.INCEPTION_WEIGHTS_ENV, raising=False)
    return weights


@pytest.fixture
def photo(tmp_path: Path) -> Path:
    return write_image(tmp_path / "photo.jpg", size=(64, 64), noise=True)


def test_objects_categories_and_model_hash(fake_models: Path, photo: Path) -> None:
    result = CvAnalyzer().run(ingest(photo))

    assert result.status is AnalysisStatus.OK
    assert result.findings == ()
    data = result.data
    assert [o["class"] for o in data["objects"]] == ["knife", "person"]
    assert data["objects"][0] == {"class": "knife", "confidence": 0.91, "box": [10, 12, 30, 40]}
    assert sorted(data["forensic_categories"]) == ["person", "weapon"]
    assert data["forensic_categories"]["weapon"][0]["class"] == "knife"
    assert data["models"]["yolo"] == {
        "file": "fake.pt",
        "sha256": hashlib.sha256(b"fake weights").hexdigest(),
    }
    assert data["models"]["inception_v3"] is None
    assert (data["width"], data["height"]) == (64, 64)
    assert set(data["scene"]) >= {"likely_outdoor", "likely_night", "brightness_level"}


def test_faces_keep_boxes_but_not_encodings(fake_models: Path, photo: Path) -> None:
    result = CvAnalyzer().run(ingest(photo))

    assert result.data["faces_detected"] == 1
    assert result.data["face_boxes"] == [[8, 5, 25, 30]]
    text = json.dumps(result.to_dict())
    assert "encoding" not in text
    assert "image_features" not in text
    assert "ocr_results" not in text


def test_model_is_loaded_once_per_run(fake_models: Path, tmp_path: Path) -> None:
    analyzer = CvAnalyzer()
    for i in range(3):
        analyzer.run(ingest(write_image(tmp_path / f"p{i}.jpg")))

    assert FakeYolo.loads == [str(fake_models)]


def test_ultralytics_is_kept_offline(
    fake_models: Path, photo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("YOLO_OFFLINE", raising=False)
    monkeypatch.delenv("YOLO_CONFIG_DIR", raising=False)

    CvAnalyzer().run(ingest(photo))

    assert os.environ["YOLO_OFFLINE"] == "1"
    assert os.environ["YOLO_CONFIG_DIR"].endswith("afa-ultralytics")


def test_ultralytics_never_resolves_runs_in_the_working_directory(
    fake_models: Path, photo: Path
) -> None:
    analyzer = CvAnalyzer()
    analyzer.run(ingest(photo))

    overrides = analyzer._legacy.model.overrides
    assert overrides["save"] is False
    assert Path(overrides["project"]).is_absolute()


def test_ultralytics_pil_patch_is_undone(
    fake_models: Path, photo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PIL import Image

    original = Image.open

    def patched(*args: Any, **kwargs: Any) -> Any:
        raise ModuleNotFoundError("No module named 'pi_heif'")

    monkeypatch.setitem(
        sys.modules, "ultralytics.utils.patches", types.SimpleNamespace(_image_open=original)
    )
    monkeypatch.setattr(Image, "open", patched)

    CvAnalyzer().run(ingest(photo))

    assert Image.open is original


def test_legacy_prints_do_not_reach_stdout(
    fake_models: Path, photo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    CvAnalyzer().run(ingest(photo))

    assert capsys.readouterr().out == ""


def test_non_image_is_skipped(fake_models: Path, tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("not an image", encoding="utf-8")

    result = CvAnalyzer().run(ingest(path))

    assert result.status is AnalysisStatus.SKIPPED
    assert result.error == "not a recognised image"


def test_unloadable_weights_are_an_error(
    fake_models: Path, photo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(weights: str) -> None:
        raise RuntimeError("bad checkpoint")

    monkeypatch.setitem(sys.modules, "ultralytics", types.SimpleNamespace(YOLO=broken))

    result = CvAnalyzer().run(ingest(photo))

    assert result.status is AnalysisStatus.ERROR
    assert result.error is not None
    assert result.error.startswith("cannot load YOLO weights")


@pytest.mark.parametrize("value", ["", "/nonexistent/yolov8x.pt"])
def test_missing_weights_are_skipped(
    fake_models: Path, photo: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(cv_module.YOLO_WEIGHTS_ENV, value)

    result = CvAnalyzer().run(ingest(photo))

    assert result.status is AnalysisStatus.SKIPPED
    assert result.error == "no YOLO weights: set AFA_YOLO_WEIGHTS to a local .pt file"


def test_missing_extra_is_skipped(photo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cv_module, "_missing_modules", lambda: ["ultralytics"])

    result = CvAnalyzer().run(ingest(photo))

    assert result.status is AnalysisStatus.SKIPPED
    assert result.error == "missing ultralytics; install afa-pipeline-core[cv]"


def test_evidence_is_not_modified(fake_models: Path, photo: Path) -> None:
    before = photo.read_bytes()
    mtime = os.stat(photo).st_mtime_ns

    CvAnalyzer().run(ingest(photo))

    assert photo.read_bytes() == before
    assert os.stat(photo).st_mtime_ns == mtime
    assert sorted(p.name for p in photo.parent.iterdir()) == ["models", "photo.jpg"]


@pytest.mark.skipif(
    not os.environ.get("AFA_YOLO_WEIGHTS"), reason="AFA_YOLO_WEIGHTS not set (real model)"
)
def test_real_weights_detect_a_bus() -> None:
    ultralytics = pytest.importorskip("ultralytics")
    bus = Path(ultralytics.__file__).parent / "assets" / "bus.jpg"

    result = CvAnalyzer().run(ingest(bus))

    assert result.status is AnalysisStatus.OK
    assert "bus" in {o["class"] for o in result.data["objects"]}
    assert "vehicle" in result.data["forensic_categories"]


@pytest.mark.skipif(
    not os.environ.get("AFA_YOLO_WEIGHTS"), reason="AFA_YOLO_WEIGHTS not set (real model)"
)
def test_real_ultralytics_does_not_break_other_analyzers(tmp_path: Path) -> None:
    from afa_pipeline.analyzers import IntegrityAnalyzer, MetadataAnalyzer

    pytest.importorskip("ultralytics")
    notes = tmp_path / "notes.txt"
    notes.write_text("not an image", encoding="utf-8")

    CvAnalyzer().run(ingest(write_image(tmp_path / "photo.jpg")))

    for analyzer in (MetadataAnalyzer(), IntegrityAnalyzer()):
        result = analyzer.run(ingest(notes))
        assert result.status is AnalysisStatus.SKIPPED, result.error
