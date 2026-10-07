"""Read-only evidence intake and SHA-256 hashing (FR1)."""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = [
    "CHUNK_SIZE",
    "EvidenceItem",
    "IngestionError",
    "discover",
    "ingest",
    "sha256_file",
]

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024


class IngestionError(Exception):
    """Raised when a path cannot be taken in as evidence."""


@dataclass(frozen=True)
class EvidenceItem:
    """An evidence file that has been taken in and hashed.

    Attributes:
        path: Location of the original file (never modified).
        sha256: Lower-case hex SHA-256 digest of the file content at intake.
        size: Size of the file in bytes, counted while hashing.
        intake_time: UTC time at which the file was taken in.
    """

    path: Path
    sha256: str
    size: int
    intake_time: datetime

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation with a portable path."""
        return {
            "path": self.path.as_posix(),
            "sha256": self.sha256,
            "size": self.size,
            "intake_time": self.intake_time.isoformat(),
        }


def sha256_file(path: Path, chunk_size: int = CHUNK_SIZE) -> tuple[str, int]:
    """Hash a file in read-only mode.

    Args:
        path: File to hash.
        chunk_size: Number of bytes read per iteration.

    Returns:
        A ``(hex_digest, size_in_bytes)`` tuple.
    """
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def ingest(path: str | os.PathLike[str]) -> EvidenceItem:
    """Take a single file in as evidence.

    Args:
        path: Path to a regular file.

    Returns:
        The hashed :class:`EvidenceItem`.

    Raises:
        IngestionError: If the path does not exist, is not a regular file or
            cannot be read.
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise IngestionError(f"not a regular file: {file_path}")
    try:
        digest, size = sha256_file(file_path)
    except OSError as exc:
        raise IngestionError(f"cannot read {file_path}: {exc}") from exc
    item = EvidenceItem(
        path=file_path,
        sha256=digest,
        size=size,
        intake_time=datetime.now(timezone.utc),
    )
    logger.info("ingested %s sha256=%s size=%d", file_path, digest, size)
    return item


def discover(path: str | os.PathLike[str]) -> list[Path]:
    """List the files to take in, in a deterministic order.

    Directories are walked recursively without following symbolic links; files
    are sorted by their path relative to ``path``.

    Args:
        path: A file or a directory.

    Returns:
        Sorted list of file paths.

    Raises:
        IngestionError: If ``path`` does not exist.
    """
    root = Path(path)
    if root.is_file():
        return [root]
    if not root.is_dir():
        raise IngestionError(f"no such file or directory: {root}")
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        files.extend(Path(dirpath) / name for name in filenames)
    files = [f for f in files if f.is_file() and not f.is_symlink()]
    return sorted(files, key=lambda f: f.relative_to(root).as_posix())
