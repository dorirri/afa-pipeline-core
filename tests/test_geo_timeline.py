"""Tests for the case-level geo-timeline (ported prototype code)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from afa_pipeline import geo_timeline
from afa_pipeline.cli import main
from afa_pipeline.geo_timeline import build_metadata, classify_anomaly
from afa_pipeline.provenance import verify
from tests.conftest import write_image

for module in ("pandas", "folium", "geopy", "sklearn", "matplotlib"):
    pytest.importorskip(module)


def _gps(lat: float, lon: float) -> dict[int, Any]:
    """EXIF GPS IFD for a position in the northern/eastern hemisphere."""

    def dms(value: float) -> tuple[float, float, float]:
        degrees = int(value)
        minutes = int((value - degrees) * 60)
        seconds = round(((value - degrees) * 60 - minutes) * 60, 2)
        return (float(degrees), float(minutes), seconds)

    return {1: "N", 2: dms(lat), 3: "E", 4: dms(lon)}


def _photo(root: Path, name: str, when: str, lat: float, lon: float) -> None:
    write_image(
        root / name,
        datetime=when,
        datetime_original=when,
        datetime_digitized=when,
        gps=_gps(lat, lon),
    )


@pytest.fixture
def case(tmp_path: Path) -> tuple[Path, Path]:
    """Analyse a small case: Almaty -> Astana in one hour, then a night photo."""
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    _photo(evidence, "IMG_0001.jpg", "2024:05:01 10:00:00", 43.2389, 76.8897)  # Almaty
    _photo(evidence, "IMG_0002.jpg", "2024:05:01 11:00:00", 51.1605, 71.4704)  # Astana
    _photo(evidence, "IMG_0003.jpg", "2024:05:02 02:30:00", 51.1700, 71.4500)  # Astana, night
    (evidence / "notes.txt").write_text("no gps here", encoding="utf-8")
    out = tmp_path / "out"
    assert (
        main(
            [
                "analyze",
                str(evidence),
                "--out",
                str(out / "results.json"),
                "--log",
                str(out / "audit.jsonl"),
                "--analyzer",
                "exif",
            ]
        )
        == 0
    )
    return out / "results.json", out / "audit.jsonl"


def test_end_to_end_detects_impossible_travel_and_night_photo(
    case: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report, log = case
    capsys.readouterr()
    out = tmp_path / "geo"

    assert main(["geo-timeline", str(report), "--out", str(out), "--log", str(log)]) == 0

    summary = json.loads((out / "geo_timeline.json").read_text(encoding="utf-8"))
    assert summary["status"] == "ok"
    assert summary["photos_with_gps_and_time"] == 3
    rules = [a["rule"] for a in summary["anomalies"]]
    assert "geo.high_speed" in rules
    assert "geo.night_activity" in rules
    flight = summary["travel_segments"][0]
    assert flight["transport_type"] == "Flight"
    assert flight["distance_km"] == pytest.approx(970, rel=0.05)
    assert flight["start_time"] == "2024-05-01T10:00:00"
    for name in ("travel_pattern_report.html", "travel_map.html", "location_timeline.png"):
        assert (out / name).is_file()

    output = capsys.readouterr().out
    assert "3 photo(s) with GPS and time" in output
    assert "geo.high_speed" in output


def test_run_is_recorded_in_the_audit_log(case: tuple[Path, Path], tmp_path: Path) -> None:
    report, log = case
    out = tmp_path / "geo"

    summary = geo_timeline.run_geo_timeline(report, out, log)

    result = verify(log, expected_head=summary["audit"]["head"])
    assert result.ok
    last = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert last["action"] == "geo_timeline"
    assert last["details"]["report_sha256"] == hashlib.sha256(report.read_bytes()).hexdigest()
    outputs = last["details"]["outputs"]
    assert (
        outputs["geo_timeline.json"]
        == hashlib.sha256((out / "geo_timeline.json").read_bytes()).hexdigest()
    )
    assert "travel_pattern_report.html" in outputs


def test_too_few_photos_is_reported_not_failed(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    _photo(evidence, "IMG_0001.jpg", "2024:05:01 10:00:00", 43.2389, 76.8897)
    report = tmp_path / "results.json"
    log = tmp_path / "audit.jsonl"
    main(["analyze", str(evidence), "--out", str(report), "--log", str(log)])

    summary = geo_timeline.run_geo_timeline(report, tmp_path / "geo", log)

    assert summary["status"] == "insufficient_data"
    assert summary["anomalies"] == []
    assert verify(log).ok


def test_build_metadata_keeps_photos_with_gps_and_time() -> None:
    def item(path: str, status: str = "ok", **data: Any) -> dict[str, Any]:
        return {
            "evidence": {"path": path, "sha256": "ab" * 32},
            "results": [{"analyzer": "exif", "status": status, "data": data}],
        }

    gps = {"latitude": 43.2, "longitude": 76.9, "altitude": 800.0}
    report = {
        "items": [
            item(
                "a.jpg", datetime_original="2024:05:01 10:00:00", datetime="x", gps=gps, model="M"
            ),
            item("b.jpg", datetime="2024:05:01 11:00:00", gps=gps),
            item("c.jpg", datetime_original="2024:05:01 12:00:00", gps=None),
            item("d.jpg", gps=gps),
            item("e.jpg", status="error", datetime="2024:05:01 13:00:00", gps=gps),
            {"evidence": {"path": "f.txt"}, "error": "unreadable", "results": []},
        ]
    }

    rows = build_metadata(report)

    assert [r["FileName"] for r in rows] == ["a.jpg", "b.jpg"]
    assert rows[0]["DateTime"] == "2024:05:01 10:00:00"
    assert rows[0]["Coordinates"] == [43.2, 76.9]
    assert rows[0]["CameraModel"] == "M"
    assert rows[1]["DateTime"] == "2024:05:01 11:00:00"


@pytest.mark.parametrize(
    ("message", "rule"),
    [
        ("Travel segment 1 shows unusually high speed (970.00 km/h)", "geo.high_speed"),
        ("Unusual time gap of 137.5 days before photo IMG_0001.jpg", "geo.time_gap"),
        ("Night activity detected: 5 photos taken between 11 PM and 5 AM", "geo.night_activity"),
        ("Something new", "geo.other"),
    ],
)
def test_classify_anomaly(message: str, rule: str) -> None:
    assert classify_anomaly(message) == rule


def test_missing_extra_fails_cleanly(
    case: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report, log = case
    monkeypatch.setattr(geo_timeline, "_missing_modules", lambda: ["folium"])
    before = log.read_bytes()

    code = main(["geo-timeline", str(report), "--out", str(tmp_path / "geo"), "--log", str(log)])

    assert code == 1
    assert log.read_bytes() == before


@pytest.mark.parametrize("content", ["not json", '{"no": "items"}'])
def test_invalid_report_fails_cleanly(
    tmp_path: Path, content: str, capsys: pytest.CaptureFixture[str]
) -> None:
    report = tmp_path / "results.json"
    report.write_text(content, encoding="utf-8")

    code = main(["geo-timeline", str(report), "--out", str(tmp_path / "geo")])

    assert code == 1
    assert "error:" in capsys.readouterr().err


def test_corrupt_audit_log_is_refused(case: tuple[Path, Path], tmp_path: Path) -> None:
    report, _ = case
    log = tmp_path / "bad.jsonl"
    log.write_text("garbage\n", encoding="utf-8")

    code = main(["geo-timeline", str(report), "--out", str(tmp_path / "geo"), "--log", str(log)])

    assert code == 1
    assert log.read_text(encoding="utf-8") == "garbage\n"
