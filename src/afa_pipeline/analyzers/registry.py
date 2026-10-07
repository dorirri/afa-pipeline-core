"""Registry through which analyzer plugins are discovered (FR9)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from importlib.metadata import entry_points

from afa_pipeline.analyzers.base import Analyzer

__all__ = [
    "ENTRY_POINT_GROUP",
    "AnalyzerRegistry",
    "DuplicateAnalyzerError",
    "UnknownAnalyzerError",
    "default_registry",
    "register",
]

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "afa_pipeline.analyzers"


class DuplicateAnalyzerError(ValueError):
    """Raised when two analyzers are registered under the same name."""


class UnknownAnalyzerError(KeyError):
    """Raised when an analyzer name is not registered."""


class AnalyzerRegistry:
    """Maps analyzer names to analyzer classes."""

    def __init__(self) -> None:
        """Create an empty registry."""
        self._analyzers: dict[str, type[Analyzer]] = {}

    def register(self, analyzer_cls: type[Analyzer]) -> type[Analyzer]:
        """Register an analyzer class. Can be used as a class decorator.

        Args:
            analyzer_cls: A concrete :class:`Analyzer` subclass.

        Returns:
            The same class, unchanged.

        Raises:
            TypeError: If ``analyzer_cls`` is not a concrete analyzer.
            DuplicateAnalyzerError: If its name is already taken by another class.
        """
        if not (isinstance(analyzer_cls, type) and issubclass(analyzer_cls, Analyzer)):
            raise TypeError(f"{analyzer_cls!r} is not an Analyzer subclass")
        if getattr(analyzer_cls, "__abstractmethods__", None):
            raise TypeError(f"{analyzer_cls.__name__} is abstract")
        existing = self._analyzers.get(analyzer_cls.name)
        if existing is not None and existing is not analyzer_cls:
            raise DuplicateAnalyzerError(
                f"analyzer name '{analyzer_cls.name}' already registered by {existing.__qualname__}"
            )
        self._analyzers[analyzer_cls.name] = analyzer_cls
        logger.debug("registered analyzer %s v%s", analyzer_cls.name, analyzer_cls.version)
        return analyzer_cls

    def get(self, name: str) -> type[Analyzer]:
        """Return the analyzer class registered under ``name``."""
        try:
            return self._analyzers[name]
        except KeyError:
            raise UnknownAnalyzerError(name) from None

    def names(self) -> list[str]:
        """Return the registered names in sorted order."""
        return sorted(self._analyzers)

    def create(self, names: list[str] | None = None) -> list[Analyzer]:
        """Instantiate analyzers, sorted by name for a deterministic pipeline order.

        Args:
            names: Analyzer names to create; all registered analyzers if ``None``.
        """
        selected = sorted(names) if names is not None else self.names()
        return [self.get(name)() for name in selected]

    def load_entry_points(self, group: str = ENTRY_POINT_GROUP) -> list[str]:
        """Register analyzers advertised by installed packages.

        Returns:
            Names of the analyzers that were loaded.
        """
        loaded: list[str] = []
        for ep in sorted(entry_points(group=group), key=lambda e: e.name):
            cls = self.register(ep.load())
            loaded.append(cls.name)
        return loaded

    def __contains__(self, name: object) -> bool:
        """Return whether ``name`` is registered."""
        return name in self._analyzers

    def __iter__(self) -> Iterator[str]:
        """Iterate over registered names in sorted order."""
        return iter(self.names())

    def __len__(self) -> int:
        """Return the number of registered analyzers."""
        return len(self._analyzers)


default_registry = AnalyzerRegistry()

register = default_registry.register
