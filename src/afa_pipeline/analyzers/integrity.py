"""Image integrity analyzer: Error Level Analysis + JPEG ghost (FR3).

A thin adapter around the ported prototype code in
:mod:`afa_pipeline.analyzers.legacy.image_integrity`. The algorithms and their
thresholds are unchanged; this module only

* runs them in a private temporary directory, so nothing is written next to the
  evidence or into the working directory;
* converts their dictionaries into JSON-safe ``data`` and :class:`Finding` objects;
* maps "requires JPEG" to ``skipped``.

Rules: ``integrity.ela`` and ``integrity.jpeg_ghost``. A rule fires when the
original assessment is "Potential manipulation detected" or "High probability of
manipulation". The scores are uncalibrated heuristics.

Requires the optional ``integrity`` extra (numpy, scipy, matplotlib).
"""

from __future__ import annotations

import importlib
import importlib.util
import tempfile
from collections.abc import Mapping
from typing import Any

from PIL import Image, UnidentifiedImageError

from afa_pipeline.analyzers.base import AnalysisResult, AnalysisStatus, Analyzer, Finding
from afa_pipeline.analyzers.registry import register
from afa_pipeline.ingestion import EvidenceItem

__all__ = ["REQUIRED_MODULES", "IntegrityAnalyzer"]

REQUIRED_MODULES = ("numpy", "scipy", "matplotlib")

LEGACY_MODULE = "afa_pipeline.analyzers.legacy.image_integrity"

#: Assessments of the original code that count as a manipulation signal.
MANIPULATION_ASSESSMENTS = frozenset(
    {"Potential manipulation detected", "High probability of manipulation"}
)

#: The original code reports confidence as a word; map it into ``[0, 1]``.
CONFIDENCE_LEVELS: Mapping[str, float] = {
    "Low": 0.3,
    "Medium": 0.5,
    "High": 0.8,
    "Very High": 0.9,
}

#: Keep the report small: only the largest ghost regions are listed.
MAX_REGIONS = 5

_PATH_KEYS = frozenset(
    {
        "ela_output_path",
        "heatmap_path",
        "ghost_output_path",
        "suspect_visualization",
        "ela_output",
        "ghost_output",
    }
)


def _missing_modules() -> list[str]:
    return [name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None]


def _not_jpeg_reason(item: EvidenceItem) -> str | None:
    """Return why ``item`` is skipped, or ``None`` if its content is a JPEG.

    The legacy code decides by file extension and fails on non-images, so the
    format is checked from the content first. Truncated JPEGs pass this check and
    are reported as errors by the legacy code, as with the EXIF analyzer.
    """
    try:
        with Image.open(item.path) as image:
            image_format = image.format
    except (UnidentifiedImageError, OSError):
        return "not a recognised image"
    if image_format != "JPEG":
        return f"not a JPEG image ({image_format})"
    return None


def _jsonable(value: Any) -> Any:
    """Convert numpy scalars/containers from the legacy code into plain JSON types."""
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items() if k not in _PATH_KEYS}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "item") and callable(value.item):  # numpy scalar
        value = value.item()
    if isinstance(value, float):
        return round(value, 4)
    return value


def _finding(rule: str, method: str, score_key: str, result: Mapping[str, Any]) -> Finding | None:
    assessment = result.get("assessment")
    if assessment not in MANIPULATION_ASSESSMENTS:
        return None
    label = str(result.get("confidence", "Medium"))
    score = float(result[score_key])
    return Finding(
        rule=rule,
        message=f"{method}: {assessment} (score {score:.2f})",
        confidence=CONFIDENCE_LEVELS.get(label, 0.5),
        evidence={
            "score": round(score, 4),
            "assessment": assessment,
            "original_confidence": label,
        },
    )


@register
class IntegrityAnalyzer(Analyzer):
    """Run the prototype's ELA and JPEG ghost analyses on JPEG evidence."""

    name = "integrity"
    version = "0.1.0"

    def analyze(self, item: EvidenceItem) -> AnalysisResult:
        """Run ELA and JPEG ghost on ``item`` and report manipulation signals."""
        missing = _missing_modules()
        if missing:
            return self.result(
                item,
                AnalysisStatus.SKIPPED,
                error=f"missing {', '.join(missing)}; install afa-pipeline-core[integrity]",
            )

        skip_reason = _not_jpeg_reason(item)
        if skip_reason is not None:
            return self.result(item, AnalysisStatus.SKIPPED, error=skip_reason)

        import matplotlib

        matplotlib.use("Agg")
        # The ported module is untyped; treat it as ``Any`` at this boundary.
        legacy_module: Any = importlib.import_module(LEGACY_MODULE)

        with tempfile.TemporaryDirectory(prefix="afa-integrity-") as workdir:
            legacy = legacy_module.ImageIntegrityAnalyzer(output_dir=workdir)
            ela = legacy.perform_ela(str(item.path))
            if ela["status"] != "success":
                message = str(ela.get("message", "ELA failed"))
                if "requires JPEG" in message:
                    return self.result(item, AnalysisStatus.SKIPPED, error="not a JPEG image")
                return self.result(item, AnalysisStatus.ERROR, error=message)
            ghost = legacy.perform_jpeg_ghost_analysis(str(item.path))
            combined = (
                legacy.combine_ela_and_ghost(ela, ghost) if ghost["status"] == "success" else None
            )

        regions = sorted(ghost.get("suspicious_regions", []), key=lambda r: -int(r["size"]))
        data: dict[str, Any] = {
            "ela": _jsonable(ela),
            "jpeg_ghost": _jsonable(
                {**ghost, "suspicious_regions": regions[:MAX_REGIONS]}
                | {"num_suspicious_regions": len(regions)}
            ),
            "combined": _jsonable(
                {k: v for k, v in combined.items() if not k.startswith("detailed_")}
            )
            if combined
            else None,
        }
        findings = [
            f
            for f in (
                _finding("integrity.ela", "ELA", "manipulation_score", ela),
                _finding("integrity.jpeg_ghost", "JPEG ghost", "suspicion_score", ghost)
                if ghost["status"] == "success"
                else None,
            )
            if f is not None
        ]
        if ghost["status"] != "success":
            return self.result(
                item,
                AnalysisStatus.ERROR,
                data=data,
                findings=tuple(findings),
                error=str(ghost.get("message", "JPEG ghost failed")),
            )
        return self.result(item, AnalysisStatus.OK, data=data, findings=tuple(findings))
