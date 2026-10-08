"""Tests for the EXIF metadata analyzer."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest
from PIL.TiffImagePlugin import IFDRational

from afa_pipeline.analyzers import AnalysisResult, AnalysisStatus, MetadataAnalyzer
from afa_pipeline.analyzers.exif import parse_exif_datetime, parse_gps
from afa_pipeline.ingestion import ingest
from tests.conftest import VALID_GPS, ImageFactory


def analyze(path: Path) -> AnalysisResult:
    return MetadataAnalyzer().run(ingest(path))


def rules(result: AnalysisResult) -> list[str]:
    return [f.rule for f in result.findings]


def finding(result: AnalysisResult, rule: str) -> dict[str, Any]:
    matches = [f.to_dict() for f in result.findings if f.rule == rule]
    assert len(matches) == 1, f"expected one {rule}, got {rules(result)}"
    return matches[0]


def test_clean_camera_image_has_no_findings(make_image: ImageFactory) -> None:
    result = analyze(make_image(gps=VALID_GPS))

    assert result.status is AnalysisStatus.OK
    assert result.findings == ()
    assert result.analyzer == "exif"
    assert result.analyzer_version == MetadataAnalyzer.version


def test_extracts_device_timestamps_and_gps(make_image: ImageFactory) -> None:
    data = analyze(make_image(gps=VALID_GPS)).data

    assert data["format"] == "JPEG"
    assert (data["width"], data["height"]) == (32, 24)
    assert data["make"] == "samsung"
    assert data["model"] == "SM-G991B"
    assert data["datetime_original"] == "2024:05:01 10:00:00"
    assert data["datetime_digitized"] == "2024:05:01 10:00:00"
    assert data["file_modified"] == "2025-01-01T00:00:00+00:00"
    assert data["gps"] == {"latitude": 43.2584722, "longitude": 76.95, "altitude": 850.0}


def test_southern_western_hemisphere_and_below_sea_level(make_image: ImageFactory) -> None:
    gps = {**VALID_GPS, 1: "S", 3: "W", 5: b"\x01"}

    position = analyze(make_image(gps=gps)).data["gps"]

    assert position == {"latitude": -43.2584722, "longitude": -76.95, "altitude": -850.0}


def test_missing_exif_in_jpeg(make_image: ImageFactory) -> None:
    result = analyze(make_image(with_exif=False))

    found = finding(result, "exif.missing")
    assert found["confidence"] == 0.6
    assert found["evidence"] == {"format": "JPEG"}
    assert result.data["has_exif"] is False


def test_missing_exif_in_png_has_lower_confidence(make_image: ImageFactory) -> None:
    result = analyze(make_image("img.png", fmt="PNG", with_exif=False))

    assert finding(result, "exif.missing")["confidence"] == 0.3


def test_present_exif_is_not_reported_missing(make_image: ImageFactory) -> None:
    assert "exif.missing" not in rules(analyze(make_image()))


def test_original_vs_digitized_mismatch(make_image: ImageFactory) -> None:
    result = analyze(make_image(datetime_digitized="2024:05:01 12:30:00"))

    found = finding(result, "exif.timestamp_inconsistency")
    assert found["confidence"] == 0.8
    assert found["evidence"]["difference_seconds"] == 9000


def test_small_difference_within_tolerance_is_ignored(make_image: ImageFactory) -> None:
    result = analyze(make_image(datetime_digitized="2024:05:01 10:00:01"))

    assert "exif.timestamp_inconsistency" not in rules(result)


def test_datetime_modified_after_capture(make_image: ImageFactory) -> None:
    result = analyze(make_image(datetime="2024:06:15 08:00:00"))

    found = finding(result, "exif.timestamp_inconsistency")
    assert found["confidence"] == 0.5
    assert "DateTime" in found["evidence"]


def test_capture_time_after_file_modification(make_image: ImageFactory) -> None:
    stamp = "2025:03:01 10:00:00"
    result = analyze(make_image(datetime=stamp, datetime_original=stamp, datetime_digitized=stamp))

    found = finding(result, "exif.timestamp_inconsistency")
    assert found["confidence"] == 0.6
    assert found["evidence"]["file_modified"] == "2025-01-01T00:00:00+00:00"


def test_capture_time_within_one_day_of_mtime_without_tz_is_ignored(
    make_image: ImageFactory,
) -> None:
    stamp = "2025:01:01 13:00:00"
    result = analyze(make_image(datetime=stamp, datetime_original=stamp, datetime_digitized=stamp))

    assert result.findings == ()


def test_capture_time_with_offset_uses_strict_tolerance(make_image: ImageFactory) -> None:
    stamp = "2025:01:01 13:00:00"
    result = analyze(
        make_image(
            datetime=stamp,
            datetime_original=stamp,
            datetime_digitized=stamp,
            offset_time_original="+05:00",
        )
    )

    found = finding(result, "exif.timestamp_inconsistency")
    assert found["evidence"]["DateTimeOriginal"] == "2025-01-01T13:00:00+05:00"
    assert found["evidence"]["difference_seconds"] == 8 * 3600


def test_malformed_timestamp(make_image: ImageFactory) -> None:
    result = analyze(make_image(datetime_original="2024:13:45 99:00:00"))

    found = finding(result, "exif.timestamp_malformed")
    assert found["evidence"] == {"tag": "DateTimeOriginal", "value": "2024:13:45 99:00:00"}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2024:05:01 10:00:00", "2024-05-01T10:00:00"),
        (b"2024:05:01 10:00:00\x00", "2024-05-01T10:00:00"),
        ("0000:00:00 00:00:00", None),
        ("   ", None),
        (None, None),
        (12345, None),
    ],
)
def test_parse_exif_datetime(value: Any, expected: str | None) -> None:
    parsed = parse_exif_datetime(value)
    assert (parsed.isoformat() if parsed else None) == expected


@pytest.mark.parametrize(
    ("software", "matched"),
    [
        ("Adobe Photoshop 25.0 (Windows)", "adobe photoshop"),
        ("GIMP 2.10.36", "gimp"),
        ("Snapseed 2.0", "snapseed"),
        ("PicsArt", "picsart"),
    ],
)
def test_editing_software_detected(make_image: ImageFactory, software: str, matched: str) -> None:
    found = finding(analyze(make_image(software=software)), "exif.editing_software")

    assert found["confidence"] == 0.9
    assert found["evidence"] == {"tag": "Software", "value": software, "matched": matched}


@pytest.mark.parametrize("software", ["G991BXXU5CVDD", "Android 14", "HDR+ 1.0.540104767zd"])
def test_camera_firmware_is_not_flagged(make_image: ImageFactory, software: str) -> None:
    assert "exif.editing_software" not in rules(analyze(make_image(software=software)))


def test_processing_software_tag_is_checked(make_image: ImageFactory) -> None:
    result = analyze(make_image(processing_software="Lightroom Mobile"))

    assert finding(result, "exif.editing_software")["evidence"]["tag"] == "ProcessingSoftware"


@pytest.mark.parametrize(
    ("gps", "problem"),
    [
        ({1: "N", 2: (43.0, 15.0), 3: "E", 4: (76.0, 57.0, 0.0)}, "expected 3 components"),
        (
            {1: "N", 2: (IFDRational(43, 0), 15.0, 30.0), 3: "E", 4: (76.0, 57.0, 0.0)},
            "zero-denominator",
        ),
        ({1: "N", 2: (95.0, 0.0, 0.0), 3: "E", 4: (76.0, 57.0, 0.0)}, "exceeds 90"),
        ({1: "N", 2: (43.0, 75.0, 0.0), 3: "E", 4: (76.0, 57.0, 0.0)}, "out of range"),
        ({1: "N", 2: (43.0, 15.0, 30.0), 3: "E", 4: (190.0, 0.0, 0.0)}, "exceeds 180"),
        ({1: "X", 2: (43.0, 15.0, 30.0), 3: "E", 4: (76.0, 57.0, 0.0)}, "latitude ref"),
        ({1: "N", 2: (43.0, 15.0, 30.0)}, "latitude or longitude missing"),
    ],
)
def test_malformed_gps(make_image: ImageFactory, gps: dict[int, Any], problem: str) -> None:
    result = analyze(make_image(gps=gps))

    found = finding(result, "exif.gps_malformed")
    assert found["confidence"] == 0.9
    assert any(problem in p for p in found["evidence"]["problems"])
    assert result.status is AnalysisStatus.OK


def test_malformed_gps_is_not_reported_as_zero(make_image: ImageFactory) -> None:
    gps = {1: "N", 2: (IFDRational(43, 0), 15.0, 30.0), 3: "E", 4: (76.0, 57.0, 0.0)}

    assert analyze(make_image(gps=gps)).data["gps"] is None


def test_null_island_is_suspicious(make_image: ImageFactory) -> None:
    gps = {1: "N", 2: (0.0, 0.0, 0.0), 3: "E", 4: (0.0, 0.0, 0.0)}

    found = finding(analyze(make_image(gps=gps)), "exif.gps_malformed")

    assert found["confidence"] == 0.5


def test_no_gps_block_is_not_an_anomaly(make_image: ImageFactory) -> None:
    result = analyze(make_image(gps=None))

    assert result.data["gps"] is None
    assert "exif.gps_malformed" not in rules(result)


class _RaisingRational:
    """Behaves like a zero-denominator rational on Pillow < 12.3."""

    def __float__(self) -> float:
        raise ZeroDivisionError("division by zero")


def test_zero_denominator_that_raises_is_handled() -> None:
    gps = {1: "N", 2: (_RaisingRational(), 15.0, 30.0), 3: "E", 4: (76.0, 57.0, 0.0)}

    position, problems = parse_gps(gps)

    assert position is None
    assert any("zero-denominator" in p for p in problems)
    assert MetadataAnalyzer._gps_finding(gps, problems).evidence["raw"]["GPSLatitude"] == [
        None,
        15.0,
        30.0,
    ]


def test_parse_gps_rejects_text_coordinates() -> None:
    position, problems = parse_gps({1: "N", 2: "43,15,30", 3: "E", 4: (76.0, 57.0, 0.0)})

    assert position is None
    assert any("expected 3 components" in p for p in problems)


def test_parse_gps_rejects_non_numeric_component() -> None:
    position, problems = parse_gps({1: "N", 2: ("a", 1, 2), 3: "E", 4: (76.0, 57.0, 0.0)})

    assert position is None
    assert any("non-numeric" in p for p in problems)


def test_non_image_file_is_skipped(tmp_path: Path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("not an image", encoding="utf-8")

    result = analyze(target)

    assert result.status is AnalysisStatus.SKIPPED
    assert result.error == "not a recognised image"


def test_empty_file_is_skipped(tmp_path: Path) -> None:
    target = tmp_path / "empty.jpg"
    target.write_bytes(b"")

    assert analyze(target).status is AnalysisStatus.SKIPPED


def test_truncated_jpeg_is_reported_with_header_metadata(make_image: ImageFactory) -> None:
    path = make_image(size=(256, 256), noise=True)
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])

    result = analyze(path)

    assert result.status is AnalysisStatus.ERROR
    assert result.error is not None
    assert "truncated" in result.error
    assert result.data["make"] == "samsung"


def test_corrupted_png_is_reported(make_image: ImageFactory) -> None:
    path = make_image("img.png", fmt="PNG", size=(64, 64), noise=True)
    data = bytearray(path.read_bytes())
    data[60:200] = b"\x00" * 140
    path.write_bytes(bytes(data))

    result = analyze(path)

    assert result.status is AnalysisStatus.ERROR
    assert result.error is not None
    assert result.error.startswith("corrupted image")


def test_jpeg_signature_followed_by_garbage(tmp_path: Path) -> None:
    target = tmp_path / "fake.jpg"
    target.write_bytes(b"\xff\xd8\xff\xe0" + os.urandom(512))

    result = analyze(target)

    assert result.status is AnalysisStatus.ERROR
    assert result.error is not None
    assert result.error.startswith("corrupted image")


@pytest.mark.parametrize(
    "header",
    [
        b"GIF89a",
        b"II*\x00",
        b"MM\x00*",
        b"RIFF\x00\x00\x00\x00WEBPVP8 ",
        b"\x00\x00\x00\x18ftypheic",
    ],
)
def test_known_signature_with_broken_body_is_corrupted(tmp_path: Path, header: bytes) -> None:
    target = tmp_path / "broken.img"
    target.write_bytes(header + b"\x00" * 8)

    assert analyze(target).status is AnalysisStatus.ERROR


def test_analysis_does_not_modify_evidence(make_image: ImageFactory) -> None:
    path = make_image(gps=VALID_GPS, software="GIMP 2.10")
    before_bytes = path.read_bytes()
    before = path.stat()

    analyze(path)

    after = path.stat()
    assert path.read_bytes() == before_bytes
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_size == before.st_size


def test_analysis_works_on_read_only_file(make_image: ImageFactory) -> None:
    path = make_image()
    path.chmod(stat.S_IREAD)
    try:
        assert analyze(path).status is AnalysisStatus.OK
    finally:
        path.chmod(stat.S_IREAD | stat.S_IWRITE)


def test_result_is_deterministic(make_image: ImageFactory) -> None:
    path = make_image(gps=VALID_GPS, software="Snapseed", datetime_digitized="2024:05:02 10:00:00")

    assert analyze(path).to_dict() == analyze(path).to_dict()


def test_findings_are_sorted_by_rule(make_image: ImageFactory) -> None:
    result = analyze(
        make_image(
            software="GIMP",
            datetime_digitized="2024:05:02 10:00:00",
            gps={1: "N", 2: (95.0, 0.0, 0.0), 3: "E", 4: (1.0, 0.0, 0.0)},
        )
    )

    assert rules(result) == sorted(rules(result))
    assert len(rules(result)) == 3
