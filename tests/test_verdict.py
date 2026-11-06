"""Tests for the explainable verdict."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from afa_pipeline.cli import main
from afa_pipeline.provenance import verify
from afa_pipeline.verdict import (
    VerdictError,
    build_verdict,
    case_level,
    combine,
    file_verdict,
    level_for,
    run_verdict,
)
from tests.conftest import write_image


def _result(analyzer: str, status: str = "ok", *confidences: float, **extra: Any) -> dict[str, Any]:
    return {
        "analyzer": analyzer,
        "status": status,
        "findings": [
            {"rule": f"{analyzer}.r{i}", "confidence": c, "message": f"m{i}"}
            for i, c in enumerate(confidences)
        ],
        **extra,
    }


def _item(path: str, *results: dict[str, Any]) -> dict[str, Any]:
    return {"evidence": {"path": path, "sha256": "ab" * 32}, "results": list(results)}


def test_combine_is_noisy_or() -> None:
    assert combine([]) == 0.0
    assert combine([0.6]) == pytest.approx(0.6)
    assert combine([0.8, 0.8]) == pytest.approx(0.96)
    assert combine([0.9, 0.5]) == pytest.approx(0.95)


@pytest.mark.parametrize(
    ("score", "level"),
    [(0.0, "PASSED"), (0.29, "PASSED"), (0.3, "CAUTION"), (0.69, "CAUTION"), (0.7, "ALERT")],
)
def test_level_thresholds(score: float, level: str) -> None:
    assert level_for(score) == level


def test_case_level_is_the_most_severe_file() -> None:
    assert case_level(["PASSED", "CAUTION", "NOT_ANALYSED"]) == "CAUTION"
    assert case_level(["PASSED", "ALERT", "CAUTION"]) == "ALERT"
    assert case_level(["NOT_ANALYSED"]) == "PASSED"
    assert case_level([]) == "PASSED"


def test_file_verdict_lists_indicators_strongest_first_and_limitations() -> None:
    item = _item(
        "a.jpg",
        _result("exif", "ok", 0.5, 0.9),
        _result("integrity", "skipped", error="not a JPEG image (PNG)"),
    )

    verdict = file_verdict(item)

    assert verdict["level"] == "ALERT"
    assert verdict["score"] == pytest.approx(0.95)
    assert [i["confidence"] for i in verdict["indicators"]] == [0.9, 0.5]
    assert verdict["indicators"][0]["analyzer"] == "exif"
    assert verdict["limitations"] == ["integrity skipped: not a JPEG image (PNG)"]
    assert "2 indicator(s), combined score 0.95" in verdict["explanation"]


def test_clean_file_passes() -> None:
    verdict = file_verdict(_item("a.jpg", _result("exif"), _result("integrity")))

    assert verdict["level"] == "PASSED"
    assert verdict["explanation"] == "no indicators found by the analyzers that ran"


def test_unprocessable_files_are_not_analysed() -> None:
    skipped = file_verdict(_item("n.txt", _result("exif", "skipped", error="not an image")))
    failed = {"evidence": {"path": "x.jpg"}, "error": "permission denied", "results": []}

    assert skipped["level"] == "NOT_ANALYSED"
    assert file_verdict(failed)["level"] == "NOT_ANALYSED"
    assert file_verdict(failed)["limitations"] == ["not ingested: permission denied"]


def test_findings_from_a_failed_analyzer_still_count() -> None:
    verdict = file_verdict(_item("a.jpg", _result("exif", "error", 0.7, error="truncated")))

    assert verdict["level"] == "ALERT"
    assert verdict["limitations"] == ["exif error: truncated"]


def test_build_verdict_orders_files_and_counts_levels() -> None:
    report = {
        "items": [
            _item("clean.jpg", _result("exif")),
            _item("edited.jpg", _result("exif", "ok", 0.9)),
            _item("stripped.jpg", _result("exif", "ok", 0.6)),
            _item("notes.txt", _result("exif", "skipped", error="not an image")),
        ]
    }
    geo = {"anomalies": [{"rule": "geo.high_speed", "message": "fast"}]}

    verdict = build_verdict(report, geo)

    assert [f["path"] for f in verdict["files"]] == [
        "edited.jpg",
        "stripped.jpg",
        "clean.jpg",
        "notes.txt",
    ]
    assert verdict["case"]["level"] == "ALERT"
    assert verdict["case"]["files"] == {"ALERT": 1, "CAUTION": 1, "PASSED": 1, "NOT_ANALYSED": 1}
    assert verdict["case"]["geo_anomalies"] == geo["anomalies"]


@pytest.fixture
def analysed(tmp_path: Path) -> tuple[Path, Path]:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    write_image(evidence / "original.jpg")
    write_image(evidence / "edited.jpg", software="Adobe Photoshop 25.0")
    report, log = tmp_path / "out" / "results.json", tmp_path / "out" / "audit.jsonl"
    args = ["analyze", str(evidence), "--out", str(report), "--log", str(log)]
    assert main([*args, "--analyzer", "exif"]) == 0
    return report, log


def test_cli_end_to_end(
    analysed: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report, log = analysed
    out = tmp_path / "verdict.json"
    capsys.readouterr()

    assert main(["verdict", str(report), "--out", str(out), "--log", str(log)]) == 0

    verdict = json.loads(out.read_text(encoding="utf-8"))
    by_name = {Path(f["path"]).name: f for f in verdict["files"]}
    assert by_name["edited.jpg"]["level"] == "ALERT"
    assert by_name["original.jpg"]["level"] == "PASSED"
    assert verdict["case"]["level"] == "ALERT"
    assert verdict["inputs"]["report"]["sha256"] == hashlib.sha256(report.read_bytes()).hexdigest()
    output = capsys.readouterr().out
    assert "exif.editing_software" in output
    assert "case: ALERT" in output
    assert "verified against the audit log" in output


def test_verdict_is_logged_and_deterministic(analysed: tuple[Path, Path], tmp_path: Path) -> None:
    report, log = analysed

    first = run_verdict(report, tmp_path / "v1.json", log)
    run_verdict(report, tmp_path / "v2.json", log)

    assert (tmp_path / "v1.json").read_bytes() == (tmp_path / "v2.json").read_bytes()
    assert verify(log).ok
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    logged = [r for r in records if r["action"] == "verdict"]
    assert len(logged) == 2
    assert logged[0]["details"]["case_level"] == first["case"]["level"] == "ALERT"
    assert (
        logged[0]["details"]["verdict_sha256"]
        == hashlib.sha256((tmp_path / "v1.json").read_bytes()).hexdigest()
    )


def test_modified_report_is_refused(analysed: tuple[Path, Path], tmp_path: Path) -> None:
    report, log = analysed
    tampered = tmp_path / "tampered.json"
    tampered.write_text(
        report.read_text(encoding="utf-8").replace("exif.editing_software", "exif.gone"),
        encoding="utf-8",
    )
    before = log.read_bytes()

    with pytest.raises(VerdictError, match="not recorded"):
        run_verdict(tampered, tmp_path / "v.json", log)
    assert log.read_bytes() == before
    assert not (tmp_path / "v.json").exists()


def test_broken_or_missing_log_is_refused(analysed: tuple[Path, Path], tmp_path: Path) -> None:
    report, log = analysed
    lines = log.read_text(encoding="utf-8").splitlines(keepends=True)
    broken = tmp_path / "broken.jsonl"
    broken.write_text("".join(lines[:1] + lines[2:]), encoding="utf-8")

    with pytest.raises(VerdictError, match="broken"):
        run_verdict(report, tmp_path / "v.json", broken)
    with pytest.raises(VerdictError, match="no such audit log"):
        run_verdict(report, tmp_path / "v.json", tmp_path / "missing.jsonl")


def test_geo_summary_from_another_report_is_refused(
    analysed: tuple[Path, Path], tmp_path: Path
) -> None:
    report, log = analysed
    geo = tmp_path / "geo_timeline.json"
    geo.write_text(json.dumps({"report": {"sha256": "0" * 64}, "anomalies": []}), "utf-8")

    with pytest.raises(VerdictError, match="different report"):
        run_verdict(report, tmp_path / "v.json", log, geo)


def test_geo_anomalies_are_listed(analysed: tuple[Path, Path], tmp_path: Path) -> None:
    report, log = analysed
    geo = tmp_path / "geo_timeline.json"
    anomaly = {"rule": "geo.high_speed", "message": "Travel segment 1 shows unusually high speed"}
    sha = hashlib.sha256(report.read_bytes()).hexdigest()
    geo.write_text(json.dumps({"report": {"sha256": sha}, "anomalies": [anomaly]}), "utf-8")

    verdict = run_verdict(report, tmp_path / "v.json", log, geo)

    assert verdict["case"]["geo_anomalies"] == [anomaly]
    assert (
        verdict["inputs"]["geo_timeline"]["sha256"] == hashlib.sha256(geo.read_bytes()).hexdigest()
    )


@pytest.mark.parametrize("content", ["not json", "[1, 2]", '{"no": "items"}'])
def test_invalid_report_fails_cleanly(
    tmp_path: Path, content: str, capsys: pytest.CaptureFixture[str]
) -> None:
    report = tmp_path / "results.json"
    report.write_text(content, encoding="utf-8")

    assert main(["verdict", str(report), "--log", str(tmp_path / "a.jsonl")]) == 1
    assert "error:" in capsys.readouterr().err
