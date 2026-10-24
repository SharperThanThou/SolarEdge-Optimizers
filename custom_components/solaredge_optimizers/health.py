"""Detect unhealthy optimizers by comparing each one with its peers.

Panels in the same string see roughly the same sun, so an optimizer making
far less power than the other optimizers in its string at the same moment is
suspect. A single bad sample is usually a passing shadow, so a problem is only
confirmed after several consecutive bad samples.

This module has no Home Assistant dependency so it can be reasoned about and
tested on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from statistics import median

from .api.models import OptimizerReading, SiteLayout

# Output below this fraction of the peer median counts as no output at all.
NO_OUTPUT_RATIO = 0.05


class HealthStatus(StrEnum):
    """Health of a single optimizer."""

    OK = "ok"
    LOW_OUTPUT = "low_output"
    NO_OUTPUT = "no_output"
    NOT_REPORTING = "not_reporting"
    UNKNOWN = "unknown"


PROBLEM_STATUSES = frozenset(
    {HealthStatus.LOW_OUTPUT, HealthStatus.NO_OUTPUT, HealthStatus.NOT_REPORTING}
)


@dataclass(frozen=True, slots=True)
class HealthSettings:
    """Tunable thresholds for the health check."""

    # An optimizer below this fraction of its peers' median power is low.
    low_output_ratio: float = 0.5
    # Peers must be producing at least this much (W, median) before a
    # comparison means anything. Keeps dawn, dusk and heavy cloud quiet.
    min_peer_power: float = 50.0
    # Consecutive bad samples needed before a problem is reported.
    consecutive_samples: int = 3
    # How long an optimizer may go without reporting while the rest of the
    # site is producing before it is reported as not reporting.
    stale_after: timedelta = timedelta(hours=2)
    # Fewer producing peers than this in a string falls back to the whole site.
    min_peers: int = 2


@dataclass(frozen=True, slots=True)
class OptimizerHealth:
    """The health assessment for one optimizer.

    ``status`` is the confirmed status; ``candidate`` and ``streak`` show the
    status the latest samples point to and how many in a row agree.
    """

    serial: str
    status: HealthStatus = HealthStatus.UNKNOWN
    candidate: HealthStatus = HealthStatus.UNKNOWN
    streak: int = 0
    peer_median: float | None = None
    peer_count: int = 0
    ratio: float | None = None
    last_sample: datetime | None = None

    @property
    def problem(self) -> bool:
        """Return True if the confirmed status is a problem."""
        return self.status in PROBLEM_STATUSES


class HealthMonitor:
    """Keeps per-optimizer health state across polls."""

    def __init__(self, settings: HealthSettings | None = None) -> None:
        """Initialize the monitor."""
        self.settings = settings or HealthSettings()
        self._health: dict[str, OptimizerHealth] = {}
        self._producing_since: datetime | None = None

    @property
    def health(self) -> dict[str, OptimizerHealth]:
        """Return the latest assessment for every optimizer."""
        return dict(self._health)

    def update(
        self,
        layout: SiteLayout,
        readings: dict[str, OptimizerReading],
        now: datetime,
    ) -> dict[str, OptimizerHealth]:
        """Assess every optimizer in ``layout`` against ``readings``."""
        settings = self.settings
        fresh_power = {
            serial: reading.power or 0.0
            for serial, reading in readings.items()
            if serial in layout.optimizers and self._is_fresh(reading, now)
        }

        site_producing = (
            bool(fresh_power)
            and median(fresh_power.values()) >= settings.min_peer_power
        )
        if not site_producing:
            self._producing_since = None
        elif self._producing_since is None:
            self._producing_since = now

        by_string: dict[str | None, list[str]] = {}
        for serial in fresh_power:
            by_string.setdefault(layout.optimizers[serial].string_id, []).append(serial)

        for serial, optimizer in layout.optimizers.items():
            previous = self._health.get(serial) or OptimizerHealth(serial=serial)
            reading = readings.get(serial)

            if serial not in fresh_power:
                self._health[serial] = self._assess_missing(
                    previous, reading, now, site_producing
                )
                continue

            peers = [
                peer
                for peer in by_string.get(optimizer.string_id, [])
                if peer != serial
            ]
            if optimizer.string_id is None or len(peers) < settings.min_peers:
                peers = [peer for peer in fresh_power if peer != serial]
            peer_median = median(fresh_power[peer] for peer in peers) if peers else None
            power = fresh_power[serial]

            ratio: float | None = None
            if peer_median is None or peer_median < settings.min_peer_power:
                candidate = HealthStatus.UNKNOWN
            else:
                ratio = power / peer_median
                if ratio < NO_OUTPUT_RATIO:
                    candidate = HealthStatus.NO_OUTPUT
                elif ratio < settings.low_output_ratio:
                    candidate = HealthStatus.LOW_OUTPUT
                else:
                    candidate = HealthStatus.OK

            assessed = replace(
                previous,
                peer_median=peer_median,
                peer_count=len(peers),
                ratio=ratio,
            )
            self._health[serial] = self._record_sample(
                assessed, candidate, readings[serial].last_measurement
            )

        for serial in set(self._health) - set(layout.optimizers):
            del self._health[serial]

        return self.health

    def _is_fresh(self, reading: OptimizerReading, now: datetime) -> bool:
        """Return True if the reading is recent enough to compare."""
        return (
            reading.last_measurement is not None
            and now - reading.last_measurement <= self.settings.stale_after
        )

    def _assess_missing(
        self,
        previous: OptimizerHealth,
        reading: OptimizerReading | None,
        now: datetime,
        site_producing: bool,
    ) -> OptimizerHealth:
        """Assess an optimizer that has no recent reading."""
        cleared = replace(previous, peer_median=None, peer_count=0, ratio=None)
        if not site_producing or self._producing_since is None:
            # Night, or the whole site is down: not this optimizer's fault.
            return cleared

        # Measure silence from when the site started producing, so optimizers
        # that last reported yesterday evening are not flagged at sunrise.
        last_seen = self._producing_since
        if reading is not None and reading.last_measurement is not None:
            last_seen = max(last_seen, reading.last_measurement)
        if now - last_seen <= self.settings.stale_after:
            return cleared

        # The stale_after window already debounces this, so confirm at once.
        streak = (
            cleared.streak + 1 if cleared.candidate is HealthStatus.NOT_REPORTING else 1
        )
        return replace(
            cleared,
            status=HealthStatus.NOT_REPORTING,
            candidate=HealthStatus.NOT_REPORTING,
            streak=streak,
        )

    def _record_sample(
        self,
        health: OptimizerHealth,
        candidate: HealthStatus,
        sample_time: datetime | None,
    ) -> OptimizerHealth:
        """Fold one sample into the debounced status."""
        if candidate is HealthStatus.UNKNOWN:
            # Not enough light to judge: keep whatever was known before.
            return health
        if sample_time is not None and sample_time == health.last_sample:
            # The portal has not received a new sample since the last poll.
            return health

        streak = health.streak + 1 if candidate is health.candidate else 1
        status = health.status
        if candidate is HealthStatus.OK or streak >= self.settings.consecutive_samples:
            status = candidate
        return replace(
            health,
            status=status,
            candidate=candidate,
            streak=streak,
            last_sample=sample_time,
        )
