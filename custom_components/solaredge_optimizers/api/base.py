"""The interface every optimizer data source implements."""

from __future__ import annotations

from typing import Protocol

from .models import OptimizerReading, SiteLayout


class SourceError(Exception):
    """The source could not be reached or returned unusable data."""


class SourceAuthError(SourceError):
    """The source rejected the credentials."""


class OptimizerSource(Protocol):
    """Provides the site layout and live optimizer readings.

    Implementations raise :class:`SourceAuthError` for rejected credentials
    and :class:`SourceError` for anything else that went wrong.
    """

    async def async_get_layout(self) -> SiteLayout:
        """Return the inverters and optimizers that make up the site."""

    async def async_get_readings(self) -> dict[str, OptimizerReading]:
        """Return the latest reading for every optimizer, keyed by serial."""
