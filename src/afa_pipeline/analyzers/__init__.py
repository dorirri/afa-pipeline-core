"""Analyzer plugin interface, registry and built-in analyzers."""

from afa_pipeline.analyzers.base import AnalysisResult, AnalysisStatus, Analyzer, Finding
from afa_pipeline.analyzers.registry import (
    AnalyzerRegistry,
    DuplicateAnalyzerError,
    UnknownAnalyzerError,
    default_registry,
    register,
)

__all__ = [
    "AnalysisResult",
    "AnalysisStatus",
    "Analyzer",
    "AnalyzerRegistry",
    "DuplicateAnalyzerError",
    "Finding",
    "UnknownAnalyzerError",
    "default_registry",
    "register",
]
