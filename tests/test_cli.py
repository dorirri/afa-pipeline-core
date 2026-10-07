"""End-to-end tests for the ``afa`` command-line interface."""

from __future__ import annotations

import hashlib
import json
import runpy
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from afa_pipeline import __version__
from afa_pipeline.cli import main
from afa_pipeline.provenance import verify
from tests.conftest import VALID_GPS, write_image


@pytest.fixture
def evidence_dir(tmp_path: Path) -> Path:
    """A folder of synthetic evidence: good, suspicious, broken and non-image files."""
    root = tmp_path / "evidence"
    (root / "DCIM").mkdir(parents=True)
    write_image(root / "DCIM" / "IMG_0001.jpg", gps=VALID_GPS)
    write_image(root / "DCIM" / "IMG_0002.jpg", software="Adobe Photoshop 25.0")
    write_image(root / "screenshot.png", fmt="PNG", with_exif=False)
    (root / "notes.txt").write_text("not an image", encoding="utf-8")
    (root / "empty.jpg").write_bytes(b"")
    broken = write_image(root / "DCIM" / "IMG_0003.jpg", size=(256, 256), noise=True)
    broken.write_bytes(broken.read_bytes()[:1500])
    return root


def run_cli(evidence: Path, out_dir: Path) -> tuple[int, dict[str, Any], Path]:
    out = out_dir / "results.json"
    log = out_dir / "audit.jsonl"
    code = main(["analyze", str(evidence), "--out", str(out), "--log", str(log)])
    return code, json.loads(out.read_text(encoding="utf-8")), log


def test_analyze_folder_end_to_end(
    evidence_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, report, _ = run_cli(evidence_dir, tmp_path / "out")

    assert code == 0
    assert report["tool"] == {"name": "afa-pipeline-core", "version": __version__}
    assert report["summary"] == {"files": 6, "ok": 3, "skipped": 2, "errors": 1, "findings": 2}
    by_name = {Path(i["evidence"]["path"]).name: i for i in report["items"]}
    assert by_name["IMG_0002.jpg"]["results"][0]["findings"][0]["rule"] == "exif.editing_software"
    assert by_name["screenshot.png"]["results"][0]["findings"][0]["rule"] == "exif.missing"
    assert by_name["IMG_0003.jpg"]["results"][0]["status"] == "error"
    assert by_name["notes.txt"]["results"][0]["status"] == "skipped"
    for item in report["items"]:
        for result in item["results"]:
            assert result["analyzer_version"] == "1.0.0"

    output = capsys.readouterr().out
    assert "SKIP" in output
    assert "ERR " in output
    assert "6 file(s): 3 analysed, 2 skipped, 1 error(s), 2 finding(s)" in output


def test_audit_log_covers_every_step(evidence_dir: Path, tmp_path: Path) -> None:
    _, _, log = run_cli(evidence_dir, tmp_path / "out")

    result = verify(log)
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    actions = [r["action"] for r in records]

    assert result.ok
    assert actions[0] == "run_started"
    assert actions[-1] == "run_finished"
    assert actions.count("ingest") == 6
    assert actions.count("analyze") == 6
    analyze = next(r for r in records if r["action"] == "analyze")
    assert analyze["analyzer"] == "exif"
    assert analyze["analyzer_version"] == "1.0.0"
    assert len(analyze["input_sha256"]) == 64


def test_report_hash_is_bound_into_audit_log(evidence_dir: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    run_cli(evidence_dir, out_dir)

    last = json.loads((out_dir / "audit.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    report_hash = hashlib.sha256((out_dir / "results.json").read_bytes()).hexdigest()
    assert last["details"]["report_sha256"] == report_hash


def test_evidence_is_not_modified(evidence_dir: Path, tmp_path: Path) -> None:
    files = sorted(p for p in evidence_dir.rglob("*") if p.is_file())
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}

    run_cli(evidence_dir, tmp_path / "out")

    after_files = sorted(p for p in evidence_dir.rglob("*") if p.is_file())
    assert after_files == files
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files} == before


def _strip_volatile(report: dict[str, Any]) -> dict[str, Any]:
    """Remove wall-clock timestamps."""
    report = json.loads(json.dumps(report))
    del report["run"]["started"], report["run"]["finished"]
    for item in report["items"]:
        item["evidence"].pop("intake_time", None)
    return report


def test_output_is_deterministic(evidence_dir: Path, tmp_path: Path) -> None:
    _, first, _ = run_cli(evidence_dir, tmp_path / "run1")
    _, second, _ = run_cli(evidence_dir, tmp_path / "run2")

    assert _strip_volatile(first) == _strip_volatile(second)
    text = (tmp_path / "run1" / "results.json").read_text(encoding="utf-8")
    assert text == json.dumps(first, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def test_single_file(tmp_path: Path) -> None:
    image = write_image(tmp_path / "one.jpg")

    code, report, _ = run_cli(image, tmp_path / "out")

    assert code == 0
    assert report["summary"]["files"] == 1


def test_rerun_extends_existing_log(evidence_dir: Path, tmp_path: Path) -> None:
    _, _, log = run_cli(evidence_dir, tmp_path / "out")
    first_count = verify(log).records

    run_cli(evidence_dir, tmp_path / "out")

    assert verify(log).ok
    assert verify(log).records == 2 * first_count


def test_unreadable_file_is_reported(
    evidence_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from afa_pipeline.ingestion import IngestionError, ingest

    real_ingest = ingest

    def flaky_ingest(path: Path) -> Any:
        if path.name == "notes.txt":
            raise IngestionError(f"cannot read {path}: permission denied")
        return real_ingest(path)

    monkeypatch.setattr("afa_pipeline.cli.ingest", flaky_ingest)

    code, report, log = run_cli(evidence_dir, tmp_path / "out")

    assert code == 0
    failed = [i for i in report["items"] if "error" in i]
    assert len(failed) == 1
    assert "permission denied" in failed[0]["error"]
    assert "ingest_failed" in log.read_text(encoding="utf-8")


def test_output_inside_evidence_folder_is_refused(
    evidence_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["analyze", str(evidence_dir), "--out", str(evidence_dir / "r.json")])

    assert code == 2
    assert "must not point inside the evidence folder" in capsys.readouterr().err
    assert not (evidence_dir / "r.json").exists()


def test_missing_input(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["analyze", str(tmp_path / "nope"), "--out", str(tmp_path / "r.json")])

    assert code == 1
    assert "no such file or directory" in capsys.readouterr().err


def test_unknown_analyzer(evidence_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "r.json"
    log = tmp_path / "a.jsonl"
    args = ["analyze", str(evidence_dir), "--out", str(out), "--log", str(log)]

    assert main([*args, "--analyzer", "ocr"]) == 2
    assert main([*args, "--analyzer", "exif"]) == 0


def test_corrupt_existing_log_is_refused(evidence_dir: Path, tmp_path: Path) -> None:
    log = tmp_path / "audit.jsonl"
    log.write_text("garbage\n", encoding="utf-8")

    code = main(
        ["analyze", str(evidence_dir), "--out", str(tmp_path / "r.json"), "--log", str(log)]
    )

    assert code == 1
    assert log.read_text(encoding="utf-8") == "garbage\n"


def test_verify_log_command(
    evidence_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, _, log = run_cli(evidence_dir, tmp_path / "out")
    capsys.readouterr()

    assert main(["verify-log", str(log)]) == 0
    assert "chain intact" in capsys.readouterr().out

    lines = log.read_text(encoding="utf-8").splitlines(keepends=True)
    lines[3] = lines[3].replace('"ingest"', '"ingest_failed"')
    log.write_text("".join(lines), encoding="utf-8")

    assert main(["verify-log", str(log)]) == 1
    assert "line 4: record_hash mismatch" in capsys.readouterr().out


def test_verify_log_missing_file(tmp_path: Path) -> None:
    assert main(["verify-log", str(tmp_path / "missing.jsonl")]) == 1


def test_list_analyzers(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["analyzers"]) == 0
    assert capsys.readouterr().out.strip() == "exif 1.0.0"


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"afa {__version__}"


def test_verbose_flag_sets_logging(tmp_path: Path) -> None:
    image = write_image(tmp_path / "one.jpg")
    out = tmp_path / "out"
    assert (
        main(
            [
                "-vv",
                "analyze",
                str(image),
                "--out",
                str(out / "r.json"),
                "--log",
                str(out / "a.jsonl"),
            ]
        )
        == 0
    )


def test_python_dash_m_entry_point(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["afa", "analyzers"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("afa_pipeline", run_name="__main__")
    assert exc.value.code == 0


def test_console_script_in_subprocess(tmp_path: Path) -> None:
    image = write_image(tmp_path / "one.jpg")
    proc = subprocess.run(  # noqa: S603 - fixed arguments, no shell
        [
            sys.executable,
            "-m",
            "afa_pipeline",
            "analyze",
            str(image),
            "--out",
            str(tmp_path / "o" / "r.json"),
            "--log",
            str(tmp_path / "o" / "a.jsonl"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "1 file(s): 1 analysed" in proc.stdout
