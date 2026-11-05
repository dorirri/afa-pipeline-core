"""Integration test: the synthetic generator's images pass through the EXIF analyzer."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from afa_pipeline.analyzers import AnalysisStatus, MetadataAnalyzer
from afa_pipeline.ingestion import ingest

pytest.importorskip("piexif")

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "image_generator.py"


def _generate(out: Path, seed: int = 7) -> None:
    subprocess.run(  # noqa: S603 - fixed interpreter and script path
        [
            sys.executable,
            str(SCRIPT),
            "-o",
            str(out),
            "-c",
            "3",
            "--clusters",
            "1",
            "--seed",
            str(seed),
        ],
        check=True,
        capture_output=True,
    )


def test_generated_images_are_clean_and_carry_gps(tmp_path: Path) -> None:
    _generate(tmp_path)
    summary = json.loads((tmp_path / "generation_summary.json").read_text())
    assert summary["total_images"] == 3

    for entry in summary["images"]:
        result = MetadataAnalyzer().run(ingest(tmp_path / entry["filename"]))
        assert result.status is AnalysisStatus.OK
        assert result.findings == ()
        assert result.data["gps"] is not None
        lat, lon = entry["coordinates"]
        assert result.data["gps"]["latitude"] == pytest.approx(lat, abs=1e-5)
        assert result.data["gps"]["longitude"] == pytest.approx(lon, abs=1e-5)
        assert f"{result.data['make']} {result.data['model']}" == entry["device"]


def test_seed_makes_output_reproducible(tmp_path: Path) -> None:
    _generate(tmp_path / "a")
    _generate(tmp_path / "b")
    assert (tmp_path / "a" / "IMG_0001.jpg").read_bytes() == (
        tmp_path / "b" / "IMG_0001.jpg"
    ).read_bytes()
