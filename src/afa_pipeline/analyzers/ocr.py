"""OCR analyzer: Tesseract text extraction (FR4).

A thin adapter around the prototype's OCR code in
:mod:`afa_pipeline.analyzers.legacy.content_ocr`. The OCR call and its filtering
(words with Tesseract confidence above 30) are unchanged; this module only

* chooses the Tesseract languages: ``AFA_OCR_LANG`` (default ``kaz+rus+eng``),
  reduced to the installed ones, with the missing ones listed in ``data``;
* reads the evidence read-only into memory and hands it to the legacy code;
* captures the legacy ``print`` output, so it does not mix with the CLI output,
  and turns its swallowed "Error in OCR" into an ``error`` result.

The analyzer reports extracted text as ``data`` and raises no findings.

Requires the optional ``ocr`` extra (pytesseract, opencv-python-headless) and the
``tesseract`` program with the language packs (``tesseract-ocr-kaz``,
``tesseract-ocr-rus`` on Debian/Ubuntu; ``tesseract-lang`` on Homebrew).
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import io
import logging
import os
from typing import Any

from afa_pipeline.analyzers.base import AnalysisResult, AnalysisStatus, Analyzer
from afa_pipeline.analyzers.registry import register
from afa_pipeline.ingestion import EvidenceItem

__all__ = ["DEFAULT_LANGUAGES", "LANG_ENV", "OcrAnalyzer", "requested_languages"]

logger = logging.getLogger(__name__)

REQUIRED_MODULES = ("pytesseract", "cv2", "numpy")
LEGACY_MODULE = "afa_pipeline.analyzers.legacy.content_ocr"
LANG_ENV = "AFA_OCR_LANG"
DEFAULT_LANGUAGES = "kaz+rus+eng"
LEGACY_ERROR_PREFIX = "Error in OCR:"


def _missing_modules() -> list[str]:
    return [name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None]


def requested_languages() -> list[str]:
    """Return the Tesseract languages requested through ``AFA_OCR_LANG``."""
    value = os.environ.get(LANG_ENV, "").strip() or DEFAULT_LANGUAGES
    return [lang for lang in value.split("+") if lang]


@register
class OcrAnalyzer(Analyzer):
    """Extract text from images with the prototype's Tesseract OCR."""

    name = "ocr"
    version = "0.1.0"

    def analyze(self, item: EvidenceItem) -> AnalysisResult:
        """Run OCR on ``item`` and return the text and word boxes as data."""
        missing = _missing_modules()
        if missing:
            return self.result(
                item,
                AnalysisStatus.SKIPPED,
                error=f"missing {', '.join(missing)}; install afa-pipeline-core[ocr]",
            )

        import cv2
        import numpy as np
        import pytesseract

        legacy_module: Any = importlib.import_module(LEGACY_MODULE)
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            legacy = legacy_module.ContentOcr()
        if not legacy.ocr_available:
            return self.result(
                item, AnalysisStatus.SKIPPED, error="tesseract program is not installed"
            )

        requested = requested_languages()
        installed = set(pytesseract.get_languages(config=""))
        languages = [lang for lang in requested if lang in installed]
        if not languages:
            return self.result(
                item,
                AnalysisStatus.SKIPPED,
                error=f"none of the OCR languages {requested} is installed",
            )
        legacy.lang = "+".join(languages)

        # Read-only, in memory; cv2.imread cannot open non-ASCII paths on Windows.
        buffer = np.fromfile(item.path, dtype=np.uint8)
        img = cv2.imdecode(buffer, cv2.IMREAD_COLOR) if buffer.size else None
        if img is None:
            return self.result(item, AnalysisStatus.SKIPPED, error="not a recognised image")

        with contextlib.redirect_stdout(captured):
            ocr = legacy.detect_text_ocr(img)
        for line in captured.getvalue().splitlines():
            logger.debug("legacy ocr: %s", line)

        data: dict[str, Any] = {
            "languages": languages,
            "languages_missing": [lang for lang in requested if lang not in installed],
            "text": ocr["text"],
            "words": len(ocr["regions"]),
            "regions": [
                {
                    "text": r["text"],
                    "confidence": int(r["confidence"]),
                    "box": [int(v) for v in r["box"]],
                }
                for r in ocr["regions"]
            ],
        }
        error = next(
            (
                line.removeprefix(LEGACY_ERROR_PREFIX).strip()
                for line in captured.getvalue().splitlines()
                if line.startswith(LEGACY_ERROR_PREFIX)
            ),
            None,
        )
        if error is not None:
            return self.result(item, AnalysisStatus.ERROR, data=data, error=f"OCR failed: {error}")
        return self.result(item, AnalysisStatus.OK, data=data)
