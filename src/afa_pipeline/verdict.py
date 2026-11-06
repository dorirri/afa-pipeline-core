"""Explainable verdict over an ``afa analyze`` report (FR7).

The original prototype has no per-file verdict: its final report only counts
results. Its integrity module, however, grades a case as ``ALERT``, ``CAUTION``
or ``PASSED`` (``ImageIntegrityAnalyzer.generate_report``). This module applies
that vocabulary to the findings of every analyzer:

* **File level.** All findings of a file are combined with a noisy-OR,
  ``score = 1 - prod(1 - confidence)``, i.e. the chance that at least one
  indicator is right if they were independent. ``score < 0.3`` is ``PASSED``,
  ``0.3 <= score < 0.7`` is ``CAUTION`` and ``score >= 0.7`` is ``ALERT``. The
  verdict lists every supporting finding (strongest first) and the analyzers that
  were skipped or failed, so the investigator sees what the verdict rests on.
  Files that no analyzer could process are ``NOT_ANALYSED``.
* **Case level.** As in the prototype: ``ALERT`` if any file is ``ALERT``, else
  ``CAUTION`` if any file is ``CAUTION``, else ``PASSED``. Geo-timeline anomalies,
  if given, are listed for the case.

Confidences are heuristic weights, not calibrated probabilities, and indicators
from related methods (ELA and JPEG ghost) are not independent, so the score ranks
files for review; it does not prove manipulation.

Before judging, the report is checked against the audit log: the chain must be
intact and a ``run_finished`` record must hold the report's SHA-256.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from afa_pipeline import __version__
from afa_pipeline.provenance import AuditLog, verify

__all__ = [
    "ALERT_AT",
    "CAUTION_AT",
    "VerdictError",
    "build_verdict",
    "case_level",
    "combine",
    "file_verdict",
    "level_for",
    "run_verdict",
]

CAUTION_AT = 0.3
ALERT_AT = 0.7
PASSED, CAUTION, ALERT, NOT_ANALYSED = "PASSED", "CAUTION", "ALERT", "NOT_ANALYSED"
_RANK = {PASSED: 0, CAUTION: 1, ALERT: 2}


class VerdictError(Exception):
    """Raised when a verdict cannot be given (bad input, report not in the log)."""


def combine(confidences: Iterable[float]) -> float:
    """Noisy-OR of finding confidences."""
    return 1.0 - math.prod(1.0 - c for c in confidences)


def level_for(score: float) -> str:
    """Map a combined score to ``PASSED``, ``CAUTION`` or ``ALERT``."""
    if score >= ALERT_AT:
        return ALERT
    if score >= CAUTION_AT:
        return CAUTION
    return PASSED


def case_level(levels: Iterable[str]) -> str:
    """Case verdict: the most severe file verdict (``NOT_ANALYSED`` is ignored)."""
    ranked = [_RANK[level] for level in levels if level in _RANK]
    return next(k for k, v in _RANK.items() if v == max(ranked, default=0))


def file_verdict(item: Mapping[str, Any]) -> dict[str, Any]:
    """Judge one item of an ``afa analyze`` report."""
    evidence = item.get("evidence", {})
    results = item.get("results", [])
    indicators = sorted(
        (
            {
                "analyzer": r["analyzer"],
                "rule": f["rule"],
                "confidence": f["confidence"],
                "message": f["message"],
            }
            for r in results
            for f in r.get("findings", [])
        ),
        key=lambda i: (-i["confidence"], i["rule"], i["message"]),
    )
    limitations = [
        f"{r['analyzer']} {r['status']}: {r.get('error') or 'no details'}"
        for r in results
        if r.get("status") != "ok"
    ]
    if "error" in item:
        limitations.insert(0, f"not ingested: {item['error']}")
    analysed = any(r.get("status") == "ok" for r in results)

    score = combine(i["confidence"] for i in indicators)
    level = level_for(score) if analysed or indicators else NOT_ANALYSED
    if level == NOT_ANALYSED:
        explanation = "no analyzer could process this file"
    elif indicators:
        top = "; ".join(f"{i['rule']} ({i['confidence']:.2f})" for i in indicators)
        explanation = f"{len(indicators)} indicator(s), combined score {score:.2f}: {top}"
    else:
        explanation = "no indicators found by the analyzers that ran"
    return {
        "path": evidence.get("path"),
        "sha256": evidence.get("sha256"),
        "level": level,
        "score": round(score, 4),
        "explanation": explanation,
        "indicators": indicators,
        "limitations": limitations,
    }


def build_verdict(
    report: Mapping[str, Any], geo: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Build the file and case verdicts for a report (and optional geo summary)."""
    files = [file_verdict(item) for item in report.get("items", [])]
    counts = {
        level: sum(f["level"] == level for f in files)
        for level in (ALERT, CAUTION, PASSED, NOT_ANALYSED)
    }
    level = case_level(f["level"] for f in files)
    geo_anomalies = list(geo.get("anomalies", [])) if geo else []
    return {
        "tool": {"name": "afa-pipeline-core", "version": __version__},
        "method": {
            "combination": "noisy-OR: 1 - prod(1 - confidence)",
            "thresholds": {CAUTION: CAUTION_AT, ALERT: ALERT_AT},
        },
        "case": {
            "level": level,
            "files": counts,
            "explanation": (
                f"{counts[ALERT]} file(s) ALERT, {counts[CAUTION]} CAUTION, "
                f"{counts[PASSED]} PASSED, {counts[NOT_ANALYSED]} not analysed"
            ),
            "geo_anomalies": geo_anomalies,
        },
        "files": sorted(files, key=lambda f: (-_RANK.get(f["level"], -1), -f["score"], f["path"])),
    }


def _load_json(path: Path, what: str) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        data = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise VerdictError(f"cannot read {what} {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise VerdictError(f"{path} is not a {what}")
    return data, hashlib.sha256(raw).hexdigest()


def _check_report_logged(log_path: Path, report_sha256: str) -> None:
    try:
        result = verify(log_path)
    except FileNotFoundError:
        raise VerdictError(f"no such audit log: {log_path}") from None
    if not result.ok:
        raise VerdictError(f"audit log {log_path} is broken: " + "; ".join(result.errors))
    with open(log_path, encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            if (
                record.get("action") == "run_finished"
                and record.get("details", {}).get("report_sha256") == report_sha256
            ):
                return
    raise VerdictError(
        f"report SHA-256 {report_sha256} is not recorded in {log_path}; "
        "the report was modified after the analysis or belongs to another log"
    )


def run_verdict(
    report_path: Path, out: Path, log_path: Path, geo_path: Path | None = None
) -> dict[str, Any]:
    """Check the report against the log, judge it, write ``out`` and log it."""
    report, report_sha = _load_json(report_path, "analysis report")
    if "items" not in report:
        raise VerdictError(f"{report_path} is not an 'afa analyze' report")
    _check_report_logged(log_path, report_sha)
    geo, geo_sha = _load_json(geo_path, "geo-timeline summary") if geo_path else (None, None)
    if geo is not None and geo.get("report", {}).get("sha256") != report_sha:
        raise VerdictError(f"{geo_path} was made from a different report")

    verdict = build_verdict(report, geo)
    verdict["inputs"] = {
        "report": {"path": report_path.as_posix(), "sha256": report_sha},
        "geo_timeline": {"path": geo_path.as_posix(), "sha256": geo_sha} if geo_path else None,
    }
    payload = (json.dumps(verdict, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(payload)

    audit = AuditLog(log_path)
    audit.append(
        "verdict",
        details={
            "report_sha256": report_sha,
            "geo_timeline_sha256": geo_sha,
            "verdict": out.as_posix(),
            "verdict_sha256": hashlib.sha256(payload).hexdigest(),
            "case_level": verdict["case"]["level"],
            "files": verdict["case"]["files"],
        },
    )
    verdict["audit"] = {"log": log_path.as_posix(), "head": audit.head}
    return verdict
