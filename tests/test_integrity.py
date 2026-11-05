"""Tests for the ELA + JPEG ghost integrity analyzer (ported prototype code)."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest
from PIL import Image

from afa_pipeline.analyzers import AnalysisStatus, IntegrityAnalyzer
from afa_pipeline.analyzers import integrity as integrity_module
from afa_pipeline.cli import main
from afa_pipeline.ingestion import ingest
from tests.conftest import write_image

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("matplotlib")

SIZE = (160, 120)


def _textured(seed: int) -> Image.Image:
    """A photo-like gradient with sensor-style noise."""
    rng = np.random.default_rng(seed)
    w, h = SIZE
    y, x = np.mgrid[0:h, 0:w]
    base = np.stack([(x / w * 200) + 30, (y / h * 180) + 40, ((x + y) / (w + h) * 150) + 60], -1)
    noisy = np.clip(base + rng.normal(0, 18, base.shape), 0, 255).astype(np.uint8)
    return Image.fromarray(noisy)


def _spliced(path: Path) -> Path:
    """Background compressed at q70, a fresh patch pasted in, saved at q90."""
    buffer = io.BytesIO()
    _textured(1).save(buffer, "JPEG", quality=70)
    background = Image.open(buffer).convert("RGB")
    w, h = SIZE
    background.paste(_textured(2).crop((0, 0, w // 3, h // 3)), (w // 2, h // 2))
    background.save(path, "JPEG", quality=90)
    return path


def _clean(path: Path) -> Path:
    _textured(1).save(path, "JPEG", quality=90)
    return path


def test_spliced_image_is_flagged_by_both_methods(tmp_path: Path) -> None:
    result = IntegrityAnalyzer().run(ingest(_spliced(tmp_path / "spliced.jpg")))

    assert result.status is AnalysisStatus.OK
    rules = {f.rule for f in result.findings}
    assert rules == {"integrity.ela", "integrity.jpeg_ghost"}
    ghost = next(f for f in result.findings if f.rule == "integrity.jpeg_ghost")
    assert ghost.evidence["assessment"] == "High probability of manipulation"
    assert 0.0 <= ghost.confidence <= 1.0
    assert result.data["combined"]["agreement"] == "Both methods agree: Manipulation likely"


def test_clean_image_has_no_ghost_finding(tmp_path: Path) -> None:
    result = IntegrityAnalyzer().run(ingest(_clean(tmp_path / "clean.jpg")))

    assert result.status is AnalysisStatus.OK
    assert "integrity.jpeg_ghost" not in {f.rule for f in result.findings}
    assert result.data["jpeg_ghost"]["assessment"] == "No manipulation detected"


def test_result_is_json_safe_and_has_no_temp_paths(tmp_path: Path) -> None:
    result = IntegrityAnalyzer().run(ingest(_spliced(tmp_path / "spliced.jpg")))

    text = json.dumps(result.to_dict())
    assert "afa-integrity-" not in text
    assert len(result.data["jpeg_ghost"]["suspicious_regions"]) <= integrity_module.MAX_REGIONS


def test_output_is_deterministic(tmp_path: Path) -> None:
    item = ingest(_spliced(tmp_path / "spliced.jpg"))

    assert IntegrityAnalyzer().run(item).to_dict() == IntegrityAnalyzer().run(item).to_dict()


def test_evidence_and_working_directory_are_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "evidence").mkdir()
    evidence = _spliced(tmp_path / "evidence" / "spliced.jpg")
    before = evidence.read_bytes()
    mtime = os.stat(evidence).st_mtime_ns
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    IntegrityAnalyzer().run(ingest(evidence))

    assert evidence.read_bytes() == before
    assert os.stat(evidence).st_mtime_ns == mtime
    assert list(cwd.iterdir()) == []
    assert sorted(p.name for p in evidence.parent.iterdir()) == ["spliced.jpg"]


@pytest.mark.parametrize(
    ("name", "content", "reason"),
    [
        ("notes.txt", b"not an image", "not a recognised image"),
        ("empty.jpg", b"", "not a recognised image"),
    ],
)
def test_non_images_are_skipped(tmp_path: Path, name: str, content: bytes, reason: str) -> None:
    path = tmp_path / name
    path.write_bytes(content)

    result = IntegrityAnalyzer().run(ingest(path))

    assert result.status is AnalysisStatus.SKIPPED
    assert result.error == reason


def test_png_is_skipped_even_with_jpg_extension(tmp_path: Path) -> None:
    path = tmp_path / "disguised.jpg"
    _textured(1).save(path, "PNG")

    result = IntegrityAnalyzer().run(ingest(path))

    assert result.status is AnalysisStatus.SKIPPED
    assert result.error == "not a JPEG image (PNG)"


def test_truncated_jpeg_is_an_error(tmp_path: Path) -> None:
    path = write_image(tmp_path / "broken.jpg", size=(256, 256), noise=True)
    path.write_bytes(path.read_bytes()[:1500])

    result = IntegrityAnalyzer().run(ingest(path))

    assert result.status is AnalysisStatus.ERROR
    assert result.error is not None
    assert "truncated" in result.error


def test_missing_dependencies_skip_instead_of_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(integrity_module, "_missing_modules", lambda: ["scipy"])

    result = IntegrityAnalyzer().run(ingest(_clean(tmp_path / "clean.jpg")))

    assert result.status is AnalysisStatus.SKIPPED
    assert result.error == "missing scipy; install afa-pipeline-core[integrity]"


def test_cli_runs_all_registered_analyzers_by_default(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    _spliced(evidence / "spliced.jpg")
    out = tmp_path / "out" / "results.json"

    code = main(["analyze", str(evidence), "--out", str(out), "--log", str(tmp_path / "a.jsonl")])

    assert code == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    results = report["items"][0]["results"]
    assert [r["analyzer"] for r in results] == ["exif", "integrity"]
    assert {f["rule"] for f in results[1]["findings"]} == {"integrity.ela", "integrity.jpeg_ghost"}
