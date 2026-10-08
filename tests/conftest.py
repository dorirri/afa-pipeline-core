"""Shared pytest fixtures; all images are generated synthetically."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from afa_pipeline.ingestion import EvidenceItem, ingest

ImageFactory = Callable[..., Path]

VALID_GPS: dict[int, Any] = {
    1: "N",
    2: (43.0, 15.0, 30.5),
    3: "E",
    4: (76.0, 57.0, 0.0),
    5: b"\x00",
    6: 850.0,
}

DEFAULT_MTIME = 1_735_689_600  # 2025-01-01T00:00:00Z


def write_image(
    path: Path,
    *,
    fmt: str = "JPEG",
    size: tuple[int, int] = (32, 24),
    with_exif: bool = True,
    make: str | None = "samsung",
    model: str | None = "SM-G991B",
    software: str | None = "G991BXXU5CVDD",
    processing_software: str | None = None,
    datetime: str | None = "2024:05:01 10:00:00",
    datetime_original: str | None = "2024:05:01 10:00:00",
    datetime_digitized: str | None = "2024:05:01 10:00:00",
    offset_time_original: str | None = None,
    gps: dict[int, Any] | None = None,
    mtime: int | None = DEFAULT_MTIME,
    noise: bool = False,
) -> Path:
    """Write a synthetic image with the requested EXIF tags."""
    if noise:
        image = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))
    else:
        image = Image.new("RGB", size, (200, 30, 30))
    kwargs: dict[str, Any] = {}
    if with_exif:
        exif = Image.Exif()
        ifd0 = {
            0x000B: processing_software,
            0x010F: make,
            0x0110: model,
            0x0131: software,
            0x0132: datetime,
        }
        for tag, value in ifd0.items():
            if value is not None:
                exif[tag] = value
        exif_ifd = exif.get_ifd(0x8769)
        for tag, value in {
            0x9003: datetime_original,
            0x9004: datetime_digitized,
            0x9011: offset_time_original,
        }.items():
            if value is not None:
                exif_ifd[tag] = value
        if gps is not None:
            exif.get_ifd(0x8825).update(gps)
        kwargs["exif"] = exif
    image.save(path, fmt, **kwargs)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def make_image(tmp_path: Path) -> ImageFactory:
    """Return a factory that writes synthetic images into ``tmp_path``."""

    def factory(name: str = "img.jpg", **kwargs: Any) -> Path:
        return write_image(tmp_path / name, **kwargs)

    return factory


@pytest.fixture
def evidence_item(tmp_path: Path) -> EvidenceItem:
    """A small non-image evidence item."""
    target = tmp_path / "item.bin"
    target.write_bytes(b"synthetic evidence")
    return ingest(target)
