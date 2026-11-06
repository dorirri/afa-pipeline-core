"""Case-level geo-timeline analysis over an ``afa analyze`` report (FR6).

Unlike the per-file analyzers, the prototype's geo-timeline looks at all photos
of a case together: travel segments between consecutive photos, location
clusters, and three anomaly rules. This module

1. reads the JSON report of ``afa analyze`` and builds the metadata file the
   prototype expects (``FileName``, ``DateTime``, ``CameraModel``,
   ``Coordinates``) from the EXIF results; like the prototype's metadata
   extractor, only photos with both a timestamp and a GPS position are used;
2. runs :mod:`afa_pipeline.analyzers.legacy.geo_timeline_analyzer` unchanged,
   which writes its HTML report, maps and chart into the output folder;
3. writes ``geo_timeline.json`` (segments, clusters, anomalies) and records the
   SHA-256 of the input report and of every output file in the audit log.

Anomalies are labelled with rules ``geo.high_speed`` (> 300 km/h),
``geo.time_gap`` (> 30 days between photos) and ``geo.night_activity``
(photos between 23:00 and 05:59).

The maps reference OpenStreetMap tiles and Leaflet from a CDN: generating them is
offline, viewing them needs network access.

Requires the optional ``geo`` extra (pandas, folium, geopy, scikit-learn,
matplotlib).
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import importlib.util
import io
import json
import logging
import warnings
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from afa_pipeline import __version__
from afa_pipeline.provenance import AuditLog

__all__ = [
    "ANOMALY_RULES",
    "GeoTimelineError",
    "build_metadata",
    "classify_anomaly",
    "run_geo_timeline",
]

logger = logging.getLogger(__name__)

REQUIRED_MODULES = ("pandas", "folium", "geopy", "sklearn", "matplotlib", "numpy")
LEGACY_MODULE = "afa_pipeline.analyzers.legacy.geo_timeline_analyzer"
METADATA_FILE = "metadata.json"
SUMMARY_FILE = "geo_timeline.json"

#: Prefix of the prototype's anomaly message -> stable rule id.
ANOMALY_RULES: Mapping[str, str] = {
    "Travel segment": "geo.high_speed",
    "Unusual time gap": "geo.time_gap",
    "Night activity": "geo.night_activity",
}


class GeoTimelineError(Exception):
    """Raised when the geo-timeline cannot run (missing extra, bad report)."""


def _missing_modules() -> list[str]:
    return [name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonable(value: Any) -> Any:
    """Convert pandas/numpy values from the legacy code into plain JSON types."""
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "isoformat"):  # datetime / pandas Timestamp
        return str(value.isoformat())
    if hasattr(value, "item") and callable(value.item):  # numpy scalar
        value = value.item()
    if isinstance(value, float):
        return round(value, 6)
    return value


def classify_anomaly(message: str) -> str:
    """Return the rule id for one of the prototype's anomaly messages."""
    return next(
        (rule for prefix, rule in ANOMALY_RULES.items() if message.startswith(prefix)),
        "geo.other",
    )


def build_metadata(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build the prototype's metadata rows from an ``afa analyze`` report.

    A row is produced for each item whose ``exif`` result has GPS and a
    timestamp (``DateTimeOriginal``, else ``DateTime``).
    """
    rows: list[dict[str, Any]] = []
    for item in report.get("items", []):
        exif = next((r for r in item.get("results", []) if r.get("analyzer") == "exif"), None)
        if exif is None or exif.get("status") != "ok":
            continue
        data = exif.get("data", {})
        gps = data.get("gps")
        timestamp = data.get("datetime_original") or data.get("datetime")
        if not gps or not timestamp:
            continue
        rows.append(
            {
                "FileName": item["evidence"]["path"],
                "DateTime": timestamp,
                "CameraModel": data.get("model"),
                "CameraMake": data.get("make"),
                "Coordinates": [gps["latitude"], gps["longitude"]],
                "Altitude": gps.get("altitude"),
                "sha256": item["evidence"].get("sha256"),
            }
        )
    return rows


def run_geo_timeline(report_path: Path, out_dir: Path, log_path: Path) -> dict[str, Any]:
    """Run the prototype's geo-timeline on ``report_path`` and log it.

    Returns:
        The summary that was written to ``out_dir / geo_timeline.json``.

    Raises:
        GeoTimelineError: If the ``geo`` extra is missing or the report is invalid.
    """
    missing = _missing_modules()
    if missing:
        raise GeoTimelineError(f"missing {', '.join(missing)}; install afa-pipeline-core[geo]")
    try:
        report_bytes = report_path.read_bytes()
        report = json.loads(report_bytes)
    except (OSError, ValueError) as exc:
        raise GeoTimelineError(f"cannot read report {report_path}: {exc}") from exc
    if not isinstance(report, dict) or "items" not in report:
        raise GeoTimelineError(f"{report_path} is not an 'afa analyze' report")

    audit = AuditLog(log_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = build_metadata(report)
    metadata_path = out_dir / METADATA_FILE
    metadata_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", "utf-8")

    import matplotlib

    matplotlib.use("Agg")
    legacy_module: Any = importlib.import_module(LEGACY_MODULE)
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result: Any = None
        anomalies: list[str] = []
        if rows:
            legacy = legacy_module.GeoTimelineAnalyzer(str(metadata_path))
            result = legacy.analyze(str(out_dir))
            if result is not None:
                geo_df = legacy.metadata_df.dropna(subset=["Latitude", "Longitude"])
                anomalies = list(legacy._detect_anomalies(geo_df))
    for line in captured.getvalue().splitlines():
        logger.debug("legacy geo-timeline: %s", line)

    summary: dict[str, Any] = {
        "tool": {"name": "afa-pipeline-core", "version": __version__},
        "report": {
            "path": report_path.as_posix(),
            "sha256": hashlib.sha256(report_bytes).hexdigest(),
        },
        "photos_with_gps_and_time": len(rows),
        "status": "ok" if result is not None else "insufficient_data",
        "anomalies": [{"rule": classify_anomaly(m), "message": m} for m in anomalies],
        "travel_segments": _jsonable(
            [{k: v for k, v in s.items() if k != "speeds"} for s in result["travel_segments"]]
        )
        if result
        else [],
        "location_clusters": _jsonable(result["location_clusters"]) if result else [],
    }
    summary_path = out_dir / SUMMARY_FILE
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n", "utf-8"
    )
    outputs = {
        p.relative_to(out_dir).as_posix(): _sha256(p)
        for p in sorted(out_dir.rglob("*"))
        if p.is_file()
    }
    audit.append(
        "geo_timeline",
        details={
            "report": report_path.as_posix(),
            "report_sha256": summary["report"]["sha256"],
            "status": summary["status"],
            "photos": len(rows),
            "anomalies": [a["rule"] for a in summary["anomalies"]],
            "outputs": outputs,
        },
    )
    summary["audit"] = {"log": log_path.as_posix(), "head": audit.head}
    return summary
