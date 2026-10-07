"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from afa_pipeline.ingestion import EvidenceItem, ingest


@pytest.fixture
def evidence_item(tmp_path: Path) -> EvidenceItem:
    """A small non-image evidence item."""
    target = tmp_path / "item.bin"
    target.write_bytes(b"synthetic evidence")
    return ingest(target)
