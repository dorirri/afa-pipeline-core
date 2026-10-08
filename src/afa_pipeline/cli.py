"""Command-line interface: ``afa analyze`` and ``afa verify-log``.

Example::

    afa analyze ./evidence --out results.json --log audit.jsonl
    afa verify-log audit.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from afa_pipeline import __version__
from afa_pipeline.analyzers import AnalysisStatus, default_registry
from afa_pipeline.analyzers.registry import UnknownAnalyzerError
from afa_pipeline.ingestion import IngestionError, discover, ingest
from afa_pipeline.provenance import AuditLog, AuditLogError, verify

__all__ = ["build_parser", "main", "run_analysis"]

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def run_analysis(
    source: Path,
    out: Path,
    log_path: Path,
    analyzer_names: list[str] | None = None,
) -> dict[str, Any]:
    """Ingest and analyse every file under ``source``.

    Args:
        source: An evidence file or directory (read-only).
        out: Where to write the JSON report.
        log_path: Audit log to create or extend.
        analyzer_names: Analyzers to run; all registered analyzers if ``None``.

    Returns:
        The report that was written to ``out``.
    """
    analyzers = default_registry.create(analyzer_names)
    audit = AuditLog(log_path)
    started = datetime.now(timezone.utc).isoformat()
    files = discover(source)
    audit.append(
        "run_started",
        details={
            "source": source.as_posix(),
            "files": len(files),
            "analyzers": {a.name: a.version for a in analyzers},
        },
    )

    items: list[dict[str, Any]] = []
    summary = {"files": len(files), "ok": 0, "skipped": 0, "errors": 0, "findings": 0}
    for path in files:
        try:
            item = ingest(path)
        except IngestionError as exc:
            logger.warning("%s", exc)
            audit.append("ingest_failed", details={"path": path.as_posix(), "error": str(exc)})
            items.append({"evidence": {"path": path.as_posix()}, "error": str(exc), "results": []})
            summary["errors"] += 1
            continue
        audit.append(
            "ingest",
            input_sha256=item.sha256,
            details={"path": item.path.as_posix(), "size": item.size},
        )

        results = []
        for analyzer in analyzers:
            result = analyzer.run(item)
            audit.append(
                "analyze",
                analyzer=result.analyzer,
                analyzer_version=result.analyzer_version,
                input_sha256=item.sha256,
                details={
                    "status": result.status.value,
                    "rules": [f.rule for f in result.findings],
                    "error": result.error,
                },
            )
            results.append(result.to_dict())
            summary["findings"] += len(result.findings)
            _print_result(item.path, result.status, result.error, len(result.findings))

        statuses = {r["status"] for r in results}
        if AnalysisStatus.ERROR.value in statuses:
            summary["errors"] += 1
        elif statuses == {AnalysisStatus.SKIPPED.value}:
            summary["skipped"] += 1
        else:
            summary["ok"] += 1
        items.append({"evidence": item.to_dict(), "results": results})

    report: dict[str, Any] = {
        "tool": {"name": "afa-pipeline-core", "version": __version__},
        "run": {
            "started": started,
            "finished": datetime.now(timezone.utc).isoformat(),
            "source": source.as_posix(),
            "analyzers": [{"name": a.name, "version": a.version} for a in analyzers],
        },
        "summary": summary,
        "items": items,
    }
    payload = (json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(payload)
    audit.append(
        "run_finished",
        details={
            "summary": summary,
            "report": out.as_posix(),
            "report_sha256": hashlib.sha256(payload).hexdigest(),
        },
    )
    report["audit"] = {"log": log_path.as_posix(), "head": audit.head}
    return report


def _print_result(path: Path, status: AnalysisStatus, error: str | None, findings: int) -> None:
    label = {"ok": "OK  ", "skipped": "SKIP", "error": "ERR "}[status.value]
    detail = f"{findings} finding(s)" if status is AnalysisStatus.OK else (error or "")
    print(f"{label} {path.as_posix()}  {detail}")


def _cmd_analyze(args: argparse.Namespace) -> int:
    source = Path(args.path)
    if not source.exists():
        print(f"error: no such file or directory: {source}", file=sys.stderr)
        return EXIT_FAILURE
    root = source if source.is_dir() else source.parent
    for label, target in (("--out", Path(args.out)), ("--log", Path(args.log))):
        if source.is_dir() and _is_within(target, root):
            print(
                f"error: {label} must not point inside the evidence folder ({target})",
                file=sys.stderr,
            )
            return EXIT_USAGE
    try:
        report = run_analysis(source, Path(args.out), Path(args.log), args.analyzer)
    except UnknownAnalyzerError as exc:
        print(
            f"error: unknown analyzer {exc}; available: {default_registry.names()}", file=sys.stderr
        )
        return EXIT_USAGE
    except AuditLogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    s = report["summary"]
    print(
        f"\n{s['files']} file(s): {s['ok']} analysed, {s['skipped']} skipped, "
        f"{s['errors']} error(s), {s['findings']} finding(s)"
    )
    print(f"report:     {args.out}")
    print(f"audit log:  {args.log}")
    print(f"audit head: {report['audit']['head']}")
    return EXIT_OK


def _cmd_verify_log(args: argparse.Namespace) -> int:
    try:
        result = verify(args.log, expected_head=args.expected_head)
    except FileNotFoundError:
        print(f"error: no such file: {args.log}", file=sys.stderr)
        return EXIT_FAILURE
    if result.ok:
        print(f"OK: {result.records} record(s), chain intact, head {result.head}")
        return EXIT_OK
    print(f"FAILED: {len(result.errors)} problem(s) in {args.log}")
    for error in result.errors:
        print(f"  - {error}")
    return EXIT_FAILURE


def _cmd_list(_: argparse.Namespace) -> int:
    for name in default_registry.names():
        print(f"{name} {default_registry.get(name).version}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser for the ``afa`` command."""
    parser = argparse.ArgumentParser(
        prog="afa",
        description="AFA-Pipeline core: forensic analysis of images from Android devices.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="log more (-v info, -vv debug)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", help="analyse an image or a folder of images")
    analyze.add_argument("path", help="evidence file or folder (opened read-only)")
    analyze.add_argument("--out", default="results.json", help="JSON report (default: %(default)s)")
    analyze.add_argument("--log", default="audit.jsonl", help="audit log (default: %(default)s)")
    analyze.add_argument(
        "--analyzer",
        action="append",
        metavar="NAME",
        help="run only this analyzer (repeatable; default: all)",
    )
    analyze.set_defaults(func=_cmd_analyze)

    verify_log = sub.add_parser("verify-log", help="check the hash chain of an audit log")
    verify_log.add_argument("log", help="audit log to verify")
    verify_log.add_argument("--expected-head", help="record_hash the last record must have")
    verify_log.set_defaults(func=_cmd_verify_log)

    analyzers = sub.add_parser("analyzers", help="list the registered analyzers")
    analyzers.set_defaults(func=_cmd_list)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point of the ``afa`` console script."""
    args = build_parser().parse_args(argv)
    level = {0: logging.WARNING, 1: logging.INFO}.get(args.verbose, logging.DEBUG)
    logging.basicConfig(
        level=level, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr
    )
    return int(args.func(args))
