"""Computer-vision analyzer: objects of forensic interest, faces, scene (FR5).

A thin adapter around the prototype's ``ContentAnalyzer`` in
:mod:`afa_pipeline.analyzers.legacy.content_analyzer`. Detection, the forensic
categories (weapon, drug, vehicle, electronic, document, valuables, person) and
the scene heuristics are unchanged; this module only

* loads the models from local files: YOLO weights from ``AFA_YOLO_WEIGHTS``
  (required; e.g. ``yolov8x.pt``) and, optionally, InceptionV3 weights from
  ``AFA_INCEPTION_WEIGHTS`` (needs TensorFlow);
* keeps ultralytics offline (``YOLO_OFFLINE=1``: no telemetry, no online checks)
  and its settings out of the user's profile (``YOLO_CONFIG_DIR``);
* records the SHA-256 of the weights in every result;
* keeps only face counts and boxes: the prototype's 128-d face encodings are
  biometric data and are not written to the report;
* drops the colour/edge feature vector and the (empty) OCR block.

The analyzer reports content as ``data`` and raises no findings: detected objects
describe what an image shows, not whether it was manipulated.

Requires the optional ``cv`` extra (ultralytics, which brings PyTorch). Face
detection additionally needs ``face_recognition`` (dlib).
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import importlib.util
import io
import logging
import os
import sys
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from afa_pipeline.analyzers.base import AnalysisResult, AnalysisStatus, Analyzer
from afa_pipeline.analyzers.registry import register
from afa_pipeline.ingestion import EvidenceItem

__all__ = ["INCEPTION_WEIGHTS_ENV", "YOLO_WEIGHTS_ENV", "CvAnalyzer"]

logger = logging.getLogger(__name__)

REQUIRED_MODULES = ("ultralytics", "cv2", "numpy")
LEGACY_MODULE = "afa_pipeline.analyzers.legacy.content_analyzer"
YOLO_WEIGHTS_ENV = "AFA_YOLO_WEIGHTS"
INCEPTION_WEIGHTS_ENV = "AFA_INCEPTION_WEIGHTS"
NOT_READABLE = "Could not read image"


def _missing_modules() -> list[str]:
    return [name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None]


@lru_cache(maxsize=8)
def _sha256(path: str, size: int, mtime_ns: int) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _model_info(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"file": path.name, "sha256": _sha256(str(path), stat.st_size, stat.st_mtime_ns)}


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "item") and callable(value.item):  # numpy scalar
        value = value.item()
    if isinstance(value, float):
        return round(value, 4)
    return value


def _undo_ultralytics_pil_patch() -> None:
    """Restore ``PIL.Image.open`` after ultralytics replaced it process-wide.

    On import, ultralytics swaps ``PIL.Image.open`` for a wrapper that, on any file
    Pillow cannot identify, runs ``check_requirements("pi-heif")`` (a pip install
    when online) and then imports ``pi_heif``. That would make the other analyzers
    fail on non-image evidence and could modify the environment during an analysis.
    The CV analyzer passes decoded arrays to YOLO, so it does not need the patch.
    """
    from PIL import Image

    patches = sys.modules.get("ultralytics.utils.patches")
    original = getattr(patches, "_image_open", None)
    if original is not None and Image.open is not original:
        Image.open = original


def _object(detection: dict[str, Any]) -> dict[str, Any]:
    return {
        "class": detection["class"],
        "confidence": detection["confidence"],
        "box": detection["box"],
    }


@register
class CvAnalyzer(Analyzer):
    """Detect objects of forensic interest, faces and scene type with local models."""

    name = "cv"
    version = "0.1.0"

    def __init__(self) -> None:
        """Create the analyzer; models are loaded on first use."""
        self._legacy: Any = None
        self._models: dict[str, Any] = {}

    def _load(self, yolo: Path) -> None:
        os.environ.setdefault("YOLO_OFFLINE", "1")
        os.environ.setdefault(
            "YOLO_CONFIG_DIR", str(Path(tempfile.gettempdir()) / "afa-ultralytics")
        )
        inception_env = os.environ.get(INCEPTION_WEIGHTS_ENV, "").strip()
        inception = Path(inception_env) if inception_env else None
        legacy_module: Any = importlib.import_module(LEGACY_MODULE)
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            self._legacy = legacy_module.ContentAnalyzer(
                yolo_weights=str(yolo),
                inception_weights=str(inception) if inception and inception.is_file() else None,
            )
        for line in captured.getvalue().splitlines():
            logger.info("legacy cv: %s", line)
        _undo_ultralytics_pil_patch()
        overrides = (
            getattr(self._legacy.model, "overrides", None) if self._legacy.model_loaded else None
        )
        if isinstance(overrides, dict):
            # Ultralytics resolves a relative ``runs/`` folder in the working directory
            # even when nothing is saved; point it to an absolute temp path instead.
            overrides.update(
                {
                    "project": str(Path(tempfile.gettempdir()) / "afa-ultralytics-runs"),
                    "save": False,
                }
            )
        self._models = {
            "yolo": _model_info(yolo) if self._legacy.model_loaded else None,
            "inception_v3": _model_info(inception)
            if self._legacy.content_model_loaded and inception
            else None,
            "faces": "face_recognition" if self._legacy.face_recognition_loaded else None,
        }

    def analyze(self, item: EvidenceItem) -> AnalysisResult:
        """Run the prototype's content analysis on ``item``."""
        missing = _missing_modules()
        if missing:
            return self.result(
                item,
                AnalysisStatus.SKIPPED,
                error=f"missing {', '.join(missing)}; install afa-pipeline-core[cv]",
            )
        weights = os.environ.get(YOLO_WEIGHTS_ENV, "").strip()
        if not weights or not Path(weights).is_file():
            return self.result(
                item,
                AnalysisStatus.SKIPPED,
                error=f"no YOLO weights: set {YOLO_WEIGHTS_ENV} to a local .pt file",
            )
        if self._legacy is None:
            self._load(Path(weights))
        if not self._legacy.model_loaded:
            return self.result(
                item, AnalysisStatus.ERROR, error=f"cannot load YOLO weights {weights}"
            )

        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            raw = self._legacy.analyze_image(str(item.path))
        if "error" in raw:
            if raw["error"].startswith(NOT_READABLE):
                return self.result(item, AnalysisStatus.SKIPPED, error="not a recognised image")
            return self.result(item, AnalysisStatus.ERROR, error=raw["error"])

        height, width = raw["dimensions"][:2]
        categories = {
            name: [_object(d) for d in detections]
            for name, detections in raw.get("forensic_categories", {}).items()
        }
        data = {
            "models": self._models,
            "width": width,
            "height": height,
            "objects": [_object(d) for d in raw["objects_detected"]],
            "forensic_categories": categories,
            "sensitive_content": raw["sensitive_content"],
            "faces_detected": raw["faces_detected"],
            "face_boxes": [f["location"] for f in raw.get("faces", [])],
            "scene": raw["scene_classification"],
            "potential_text_regions": raw["potential_text_regions"],
        }
        return self.result(item, AnalysisStatus.OK, data=_jsonable(data))
