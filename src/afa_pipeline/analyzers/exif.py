"""EXIF metadata analyzer with anomaly detection (FR2).

Rules: ``exif.missing``, ``exif.timestamp_inconsistency``,
``exif.timestamp_malformed``, ``exif.editing_software``, ``exif.gps_malformed``.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from PIL import Image, UnidentifiedImageError

from afa_pipeline.analyzers.base import AnalysisResult, AnalysisStatus, Analyzer, Finding
from afa_pipeline.analyzers.registry import register
from afa_pipeline.ingestion import EvidenceItem

__all__ = [
    "EDITING_SOFTWARE",
    "MetadataAnalyzer",
    "parse_exif_datetime",
    "parse_gps",
]

logger = logging.getLogger(__name__)

TAG_PROCESSING_SOFTWARE = 0x000B
TAG_MAKE = 0x010F
TAG_MODEL = 0x0110
TAG_SOFTWARE = 0x0131
TAG_DATETIME = 0x0132
IFD_EXIF = 0x8769
IFD_GPS = 0x8825
TAG_DATETIME_ORIGINAL = 0x9003
TAG_DATETIME_DIGITIZED = 0x9004
TAG_OFFSET_TIME_ORIGINAL = 0x9011
GPS_LATITUDE_REF = 0x0001
GPS_LATITUDE = 0x0002
GPS_LONGITUDE_REF = 0x0003
GPS_LONGITUDE = 0x0004
GPS_ALTITUDE_REF = 0x0005
GPS_ALTITUDE = 0x0006

EXIF_DATETIME_FORMAT = "%Y:%m:%d %H:%M:%S"

EDITING_SOFTWARE: tuple[str, ...] = (
    "adobe photoshop",
    "photoshop",
    "lightroom",
    "gimp",
    "snapseed",
    "picsart",
    "facetune",
    "pixelmator",
    "affinity photo",
    "paint.net",
    "canva",
    "vsco",
    "meitu",
    "airbrush",
    "photoscape",
    "fotor",
    "luminar",
    "darktable",
    "rawtherapee",
    "capture one",
)

CAMERA_TOLERANCE = timedelta(seconds=2)
FILE_TIME_TOLERANCE_NO_TZ = timedelta(hours=26)
FILE_TIME_TOLERANCE_TZ = timedelta(minutes=1)

EXIF_NATIVE_FORMATS = frozenset({"JPEG", "MPO", "TIFF", "HEIF", "WEBP"})

_DECODE_ERRORS = (OSError, SyntaxError, ValueError, EOFError, Image.DecompressionBombError)

_IMAGE_SIGNATURES: tuple[tuple[int, bytes], ...] = (
    (0, b"\xff\xd8\xff"),  # JPEG
    (0, b"\x89PNG\r\n\x1a\n"),  # PNG
    (0, b"GIF8"),  # GIF
    (0, b"II*\x00"),  # TIFF / DNG, little-endian
    (0, b"MM\x00*"),  # TIFF / DNG, big-endian
    (8, b"WEBP"),  # WebP (RIFF container)
    (4, b"ftyp"),  # HEIC / HEIF / AVIF (ISO-BMFF container)
)


def _has_image_signature(header: bytes) -> bool:
    """Return whether ``header`` starts like a known image format."""
    return any(header[offset : offset + len(magic)] == magic for offset, magic in _IMAGE_SIGNATURES)


def _clean(value: Any) -> Any:
    """Convert an EXIF value into a JSON-friendly, trimmed form."""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value.replace("\x00", "").strip() or None
    if isinstance(value, tuple):
        return [_clean(v) for v in value]
    try:
        number = float(value)
    except ZeroDivisionError:
        return None
    except (TypeError, ValueError):
        return str(value)
    return number if math.isfinite(number) else None


def parse_exif_datetime(value: Any) -> datetime | None:
    """Parse an EXIF ``YYYY:MM:DD HH:MM:SS`` string.

    Returns:
        A naive :class:`datetime`, or ``None`` if the value is missing or malformed.
    """
    text = _clean(value)
    if not isinstance(text, str):
        return None
    try:
        return datetime.strptime(text[:19], EXIF_DATETIME_FORMAT)
    except ValueError:
        return None


def _parse_offset(value: Any) -> timezone | None:
    """Parse an EXIF ``OffsetTime*`` value such as ``+05:00``."""
    text = _clean(value)
    if not isinstance(text, str) or len(text) != 6 or text[0] not in "+-" or text[3] != ":":
        return None
    try:
        hours, minutes = int(text[1:3]), int(text[4:6])
    except ValueError:
        return None
    sign = 1 if text[0] == "+" else -1
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def _to_float(value: Any) -> float | None:
    """Convert an EXIF rational/number into a finite float, else ``None``."""
    if isinstance(value, (str, bytes)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return number if math.isfinite(number) else None


def _dms_to_degrees(value: Any, limit: float, label: str, problems: list[str]) -> float | None:
    """Convert a degrees/minutes/seconds triple into decimal degrees."""
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        problems.append(f"{label}: expected 3 components, got {_clean(value)!r}")
        return None
    parts = [_to_float(v) for v in value]
    if any(p is None for p in parts):
        problems.append(f"{label}: non-numeric or zero-denominator component")
        return None
    degrees, minutes, seconds = (p for p in parts if p is not None)
    if min(degrees, minutes, seconds) < 0 or minutes >= 60 or seconds >= 60:
        problems.append(f"{label}: component out of range {degrees}/{minutes}/{seconds}")
        return None
    decimal = degrees + minutes / 60.0 + seconds / 3600.0
    if decimal > limit:
        problems.append(f"{label}: {decimal:.6f} exceeds {limit}")
        return None
    return decimal


def parse_gps(gps: Mapping[int, Any]) -> tuple[dict[str, float] | None, list[str]]:
    """Decode a GPS IFD.

    Args:
        gps: Mapping of GPS tag number to raw value.

    Returns:
        ``(position, problems)`` where ``position`` holds ``latitude``,
        ``longitude`` and optionally ``altitude``, or is ``None`` when the
        coordinates cannot be decoded; ``problems`` lists every defect found.
    """
    problems: list[str] = []
    if GPS_LATITUDE not in gps or GPS_LONGITUDE not in gps:
        problems.append("latitude or longitude missing")
        return None, problems

    lat = _dms_to_degrees(gps[GPS_LATITUDE], 90.0, "latitude", problems)
    lon = _dms_to_degrees(gps[GPS_LONGITUDE], 180.0, "longitude", problems)
    lat_ref = _clean(gps.get(GPS_LATITUDE_REF))
    lon_ref = _clean(gps.get(GPS_LONGITUDE_REF))
    if lat_ref not in ("N", "S"):
        problems.append(f"latitude ref: invalid value {lat_ref!r}")
    if lon_ref not in ("E", "W"):
        problems.append(f"longitude ref: invalid value {lon_ref!r}")
    if lat is None or lon is None:
        return None, problems

    position: dict[str, float] = {
        "latitude": round(-lat if lat_ref == "S" else lat, 7),
        "longitude": round(-lon if lon_ref == "W" else lon, 7),
    }
    altitude = _to_float(gps.get(GPS_ALTITUDE))
    if altitude is not None:
        below_sea = gps.get(GPS_ALTITUDE_REF) in (1, b"\x01")
        position["altitude"] = round(-altitude if below_sea else altitude, 3)
    if position["latitude"] == 0.0 and position["longitude"] == 0.0:
        problems.append("position is exactly 0,0 (null island)")
    return position, problems


@register
class MetadataAnalyzer(Analyzer):
    """Extract EXIF metadata and flag anomalies relevant to image provenance."""

    name = "exif"
    version = "1.0.0"

    def analyze(self, item: EvidenceItem) -> AnalysisResult:
        """Extract EXIF fields from ``item`` and report anomalies.

        Non-image files are ``skipped``; corrupted or truncated images give an
        ``error`` result with whatever metadata could be read.
        """
        mtime = datetime.fromtimestamp(os.stat(item.path).st_mtime, tz=timezone.utc)
        data: dict[str, Any] = {}
        findings: tuple[Finding, ...] = ()
        with open(item.path, "rb") as handle:
            try:
                image = Image.open(handle)
            except UnidentifiedImageError as exc:
                handle.seek(0)
                if _has_image_signature(handle.read(16)):
                    return self._corrupted(item, exc, data, findings)
                return self.result(item, AnalysisStatus.SKIPPED, error="not a recognised image")
            except _DECODE_ERRORS as exc:
                return self._corrupted(item, exc, data, findings)
            with image:
                try:
                    data, findings = self._extract(image, mtime)
                    image.load()
                except _DECODE_ERRORS as exc:
                    return self._corrupted(item, exc, data, findings)
        return self.result(item, AnalysisStatus.OK, data=data, findings=findings)

    def _corrupted(
        self,
        item: EvidenceItem,
        exc: BaseException,
        data: dict[str, Any],
        findings: tuple[Finding, ...],
    ) -> AnalysisResult:
        """Report a corrupted image, keeping any metadata recovered so far."""
        logger.warning("corrupted image %s: %s", item.path, exc)
        return self.result(
            item,
            AnalysisStatus.ERROR,
            data=data,
            findings=findings,
            error=f"corrupted image: {exc}",
        )

    def _extract(
        self, image: Image.Image, mtime: datetime
    ) -> tuple[dict[str, Any], tuple[Finding, ...]]:
        exif = image.getexif()
        exif_ifd = exif.get_ifd(IFD_EXIF)
        gps_ifd = exif.get_ifd(IFD_GPS)

        position, gps_problems = parse_gps(gps_ifd) if gps_ifd else (None, [])
        data: dict[str, Any] = {
            "format": image.format,
            "width": image.width,
            "height": image.height,
            "has_exif": len(exif) > 0,
            "make": _clean(exif.get(TAG_MAKE)),
            "model": _clean(exif.get(TAG_MODEL)),
            "software": _clean(exif.get(TAG_SOFTWARE)),
            "datetime": _clean(exif.get(TAG_DATETIME)),
            "datetime_original": _clean(exif_ifd.get(TAG_DATETIME_ORIGINAL)),
            "datetime_digitized": _clean(exif_ifd.get(TAG_DATETIME_DIGITIZED)),
            "offset_time_original": _clean(exif_ifd.get(TAG_OFFSET_TIME_ORIGINAL)),
            "file_modified": mtime.isoformat(),
            "gps": position,
        }

        findings: list[Finding] = []
        findings += self._check_missing(data)
        findings += self._check_timestamps(exif, exif_ifd, mtime)
        findings += self._check_software(exif)
        if gps_ifd and gps_problems:
            findings.append(self._gps_finding(gps_ifd, gps_problems))
        findings.sort(key=lambda f: (f.rule, f.message))
        return data, tuple(findings)

    @staticmethod
    def _check_missing(data: Mapping[str, Any]) -> list[Finding]:
        if data["has_exif"]:
            return []
        native = data["format"] in EXIF_NATIVE_FORMATS
        return [
            Finding(
                rule="exif.missing",
                message=(
                    "no EXIF metadata; it may have been stripped by an editor, "
                    "messenger or screenshot tool"
                ),
                confidence=0.6 if native else 0.3,
                evidence={"format": data["format"]},
            )
        ]

    @staticmethod
    def _check_timestamps(
        exif: Mapping[int, Any], exif_ifd: Mapping[int, Any], mtime: datetime
    ) -> list[Finding]:
        raw = {
            "DateTimeOriginal": exif_ifd.get(TAG_DATETIME_ORIGINAL),
            "DateTimeDigitized": exif_ifd.get(TAG_DATETIME_DIGITIZED),
            "DateTime": exif.get(TAG_DATETIME),
        }
        findings: list[Finding] = []
        parsed: dict[str, datetime] = {}
        for tag, value in raw.items():
            if value is None:
                continue
            ts = parse_exif_datetime(value)
            if ts is None:
                findings.append(
                    Finding(
                        rule="exif.timestamp_malformed",
                        message=f"{tag} is not a valid EXIF date/time",
                        confidence=0.7,
                        evidence={"tag": tag, "value": _clean(value)},
                    )
                )
            else:
                parsed[tag] = ts

        def compare(a: str, b: str, confidence: float, reason: str) -> None:
            if a in parsed and b in parsed:
                delta = abs(parsed[a] - parsed[b])
                if delta > CAMERA_TOLERANCE:
                    findings.append(
                        Finding(
                            rule="exif.timestamp_inconsistency",
                            message=f"{a} and {b} differ by {delta}; {reason}",
                            confidence=confidence,
                            evidence={
                                a: parsed[a].isoformat(),
                                b: parsed[b].isoformat(),
                                "difference_seconds": int(delta.total_seconds()),
                            },
                        )
                    )

        compare("DateTimeOriginal", "DateTimeDigitized", 0.8, "a camera writes both together")
        compare("DateTimeOriginal", "DateTime", 0.5, "the file was modified after capture")

        original = parsed.get("DateTimeOriginal")
        if original is not None:
            tz = _parse_offset(exif_ifd.get(TAG_OFFSET_TIME_ORIGINAL))
            if tz is not None:
                captured = original.replace(tzinfo=tz)
                tolerance = FILE_TIME_TOLERANCE_TZ
            else:
                captured = original.replace(tzinfo=timezone.utc)
                tolerance = FILE_TIME_TOLERANCE_NO_TZ
            if captured - mtime > tolerance:
                findings.append(
                    Finding(
                        rule="exif.timestamp_inconsistency",
                        message="DateTimeOriginal is later than the file modification time",
                        confidence=0.6,
                        evidence={
                            "DateTimeOriginal": captured.isoformat(),
                            "file_modified": mtime.isoformat(),
                            "difference_seconds": int((captured - mtime).total_seconds()),
                        },
                    )
                )
        return findings

    @staticmethod
    def _check_software(exif: Mapping[int, Any]) -> list[Finding]:
        findings: list[Finding] = []
        for tag_name, tag in (
            ("Software", TAG_SOFTWARE),
            ("ProcessingSoftware", TAG_PROCESSING_SOFTWARE),
        ):
            value = _clean(exif.get(tag))
            if not isinstance(value, str):
                continue
            lowered = value.lower()
            match = next((name for name in EDITING_SOFTWARE if name in lowered), None)
            if match is not None:
                findings.append(
                    Finding(
                        rule="exif.editing_software",
                        message=f"{tag_name} tag names an image editor",
                        confidence=0.9,
                        evidence={"tag": tag_name, "value": value, "matched": match},
                    )
                )
        return findings

    @staticmethod
    def _gps_finding(gps_ifd: Mapping[int, Any], problems: list[str]) -> Finding:
        only_null_island = all(p.startswith("position is exactly 0,0") for p in problems)
        return Finding(
            rule="exif.gps_malformed",
            message="GPS block present but position is "
            + ("suspicious" if only_null_island else "invalid"),
            confidence=0.5 if only_null_island else 0.9,
            evidence={
                "problems": problems,
                "raw": {
                    "GPSLatitudeRef": _clean(gps_ifd.get(GPS_LATITUDE_REF)),
                    "GPSLatitude": _clean(gps_ifd.get(GPS_LATITUDE)),
                    "GPSLongitudeRef": _clean(gps_ifd.get(GPS_LONGITUDE_REF)),
                    "GPSLongitude": _clean(gps_ifd.get(GPS_LONGITUDE)),
                },
            },
        )
