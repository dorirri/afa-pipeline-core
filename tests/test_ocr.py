"""Tests for the Tesseract OCR analyzer (ported prototype code)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from afa_pipeline.analyzers import AnalysisStatus, OcrAnalyzer
from afa_pipeline.analyzers import ocr as ocr_module
from afa_pipeline.ingestion import ingest

pytesseract = pytest.importorskip("pytesseract")
pytest.importorskip("cv2")

needs_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="tesseract program not installed"
)

CYRILLIC_FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
)


def _text_image(
    path: Path,
    lines: list[str],
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont | None = None,
) -> Path:
    """Black text on white, like a screenshot or a scanned document."""
    font = font or ImageFont.load_default(size=40)
    image = Image.new("RGB", (1200, 80 + 70 * len(lines)), "white")
    draw = ImageDraw.Draw(image)
    for i, line in enumerate(lines):
        draw.text((40, 40 + 70 * i), line, fill="black", font=font)
    image.save(path, "JPEG", quality=95)
    return path


@pytest.fixture
def english(tmp_path: Path) -> Path:
    return _text_image(tmp_path / "chat.jpg", ["Meet at the station 21:30", "Bring the documents"])


@needs_tesseract
def test_extracts_english_text(english: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ocr_module.LANG_ENV, "eng")

    result = OcrAnalyzer().run(ingest(english))

    assert result.status is AnalysisStatus.OK
    assert result.findings == ()
    assert "station" in result.data["text"]
    assert "documents" in result.data["text"]
    assert result.data["languages"] == ["eng"]
    assert result.data["languages_missing"] == []
    assert result.data["words"] == len(result.data["regions"]) >= 6
    region = result.data["regions"][0]
    assert region["confidence"] > 30
    x0, y0, x1, y1 = region["box"]
    assert x0 < x1 and y0 < y1


@needs_tesseract
def test_default_languages_fall_back_to_installed(english: Path) -> None:
    installed = set(pytesseract.get_languages(config=""))

    result = OcrAnalyzer().run(ingest(english))

    assert result.status is AnalysisStatus.OK
    assert set(result.data["languages"]) <= {"kaz", "rus", "eng"} & installed
    assert set(result.data["languages_missing"]) == {"kaz", "rus", "eng"} - installed


@needs_tesseract
def test_kazakh_and_russian_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    installed = set(pytesseract.get_languages(config=""))
    font_path = next((p for p in CYRILLIC_FONTS if os.path.exists(p)), None)
    if not {"kaz", "rus"} <= installed or font_path is None:
        pytest.skip("kaz/rus language packs or a Cyrillic font not available")
    monkeypatch.setenv(ocr_module.LANG_ENV, "kaz+rus+eng")
    font = ImageFont.truetype(font_path, 40)
    lines = ["Встреча у вокзала", "Құжаттарды әкел"]  # noqa: RUF001 - Cyrillic on purpose
    path = _text_image(tmp_path / "doc.jpg", lines, font)

    result = OcrAnalyzer().run(ingest(path))

    assert result.status is AnalysisStatus.OK
    assert "вокзала" in result.data["text"]
    assert "Құжаттарды" in result.data["text"]


@needs_tesseract
def test_unknown_language_is_skipped(english: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ocr_module.LANG_ENV, "xxx")

    result = OcrAnalyzer().run(ingest(english))

    assert result.status is AnalysisStatus.SKIPPED
    assert result.error == "none of the OCR languages ['xxx'] is installed"


@needs_tesseract
@pytest.mark.parametrize("content", [b"not an image", b""])
def test_non_images_are_skipped(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "file.jpg"
    path.write_bytes(content)

    result = OcrAnalyzer().run(ingest(path))

    assert result.status is AnalysisStatus.SKIPPED
    assert result.error == "not a recognised image"


@needs_tesseract
def test_swallowed_legacy_error_becomes_an_error_result(
    english: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(pytesseract, "image_to_data", boom)

    result = OcrAnalyzer().run(ingest(english))

    assert result.status is AnalysisStatus.ERROR
    assert result.error == "OCR failed: boom"


@needs_tesseract
def test_legacy_prints_do_not_reach_stdout(
    english: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    OcrAnalyzer().run(ingest(english))

    assert capsys.readouterr().out == ""


@needs_tesseract
def test_evidence_is_not_modified(english: Path) -> None:
    before = english.read_bytes()
    mtime = os.stat(english).st_mtime_ns

    OcrAnalyzer().run(ingest(english))

    assert english.read_bytes() == before
    assert os.stat(english).st_mtime_ns == mtime


def test_missing_tesseract_program_is_skipped(
    english: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def not_found() -> None:
        raise pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(pytesseract, "get_tesseract_version", not_found)

    result = OcrAnalyzer().run(ingest(english))

    assert result.status is AnalysisStatus.SKIPPED
    assert result.error == "tesseract program is not installed"


def test_missing_modules_are_skipped(english: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocr_module, "_missing_modules", lambda: ["pytesseract"])

    result = OcrAnalyzer().run(ingest(english))

    assert result.status is AnalysisStatus.SKIPPED
    assert result.error == "missing pytesseract; install afa-pipeline-core[ocr]"


def test_requested_languages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ocr_module.LANG_ENV, raising=False)
    assert ocr_module.requested_languages() == ["kaz", "rus", "eng"]
    monkeypatch.setenv(ocr_module.LANG_ENV, " rus+eng ")
    assert ocr_module.requested_languages() == ["rus", "eng"]
