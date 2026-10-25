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

# SolarEdge optimizers hold their output at about 1 V (SafeDC) until the
# inverter tells them to produce. Output at or below this while the panel
# itself has voltage, at a time the rest of the site is producing, means the
# optimizer is stuck in that safety mode.
SAFE_MODE_MAX_OUTPUT_VOLTAGE = 2.0
SAFE_MODE_MIN_MODULE_VOLTAGE = 10.0


class HealthStatus(StrEnum):
    """Health of a single optimizer."""

    OK = "ok"
    LOW_OUTPUT = "low_output"
    NO_OUTPUT = "no_output"
    NOT_REPORTING = "not_reporting"
    SAFE_MODE = "safe_mode"
    UNKNOWN = "unknown"


PROBLEM_STATUSES = frozenset(
    {
        HealthStatus.LOW_OUTPUT,
        HealthStatus.NO_OUTPUT,
        HealthStatus.NOT_REPORTING,
        HealthStatus.SAFE_MODE,
    }
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
    # An optimizer whose last report is this much older than the site's
    # newest report is not reporting, whatever the time of day. Catches
    # long-dead optimizers right after a restart or at night.
    long_silence: timedelta = timedelta(hours=24)
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

        newest = max(
            (
                reading.last_measurement
                for serial, reading in readings.items()
                if serial in layout.optimizers and reading.last_measurement is not None
            ),
            default=None,
        )

        by_string: dict[str | None, list[str]] = {}
        for serial in fresh_power:
            by_string.setdefault(layout.optimizers[serial].string_id, []).append(serial)

        for serial, optimizer in layout.optimizers.items():
            previous = self._health.get(serial) or OptimizerHealth(serial=serial)
            reading = readings.get(serial)

            if serial not in fresh_power:
                self._health[serial] = self._assess_missing(
                    previous, reading, now, site_producing, newest
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
            candidate, ratio = self._classify(
                readings[serial], fresh_power[serial], peer_median
            )

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

    def _classify(
        self, reading: OptimizerReading, power: float, peer_median: float | None
    ) -> tuple[HealthStatus, float | None]:
        """Classify one fresh sample against its peers' median power."""
        if peer_median is None or peer_median < self.settings.min_peer_power:
            return HealthStatus.UNKNOWN, None
        ratio = power / peer_median
        if ratio >= self.settings.low_output_ratio:
            return HealthStatus.OK, ratio
        if in_safe_mode(reading):
            return HealthStatus.SAFE_MODE, ratio
        if ratio < NO_OUTPUT_RATIO:
            return HealthStatus.NO_OUTPUT, ratio
        return HealthStatus.LOW_OUTPUT, ratio

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
        newest: datetime | None,
    ) -> OptimizerHealth:
        """Assess an optimizer that has no recent reading."""
        cleared = replace(previous, peer_median=None, peer_count=0, ratio=None)
        if not self._is_silent(reading, now, site_producing, newest):
            return cleared

        # Both silence checks are debounced by their time windows already, so
        # confirm at once. If the last thing it reported was the 1 V safety
        # output, say so: that points at the optimizer rather than its link.
        status = (
            HealthStatus.SAFE_MODE
            if reading is not None and in_safe_mode(reading)
            else HealthStatus.NOT_REPORTING
        )
        streak = cleared.streak + 1 if cleared.candidate is status else 1
        return replace(cleared, status=status, candidate=status, streak=streak)

    def _is_silent(
        self,
        reading: OptimizerReading | None,
        now: datetime,
        site_producing: bool,
        newest: datetime | None,
    ) -> bool:
        """Return True if an optimizer has been quiet for too long."""
        last_measurement = reading.last_measurement if reading else None
        if (
            last_measurement is not None
            and newest is not None
            and newest - last_measurement > self.settings.long_silence
        ):
            # The rest of the site kept reporting long after this one stopped.
            # Comparing with the newest report rather than the clock keeps a
            # whole-site outage from flagging every optimizer.
            return True

        if not site_producing or self._producing_since is None:
            # Night, or the whole site is down: not this optimizer's fault.
            return False

        # Measure silence from when the site started producing, so optimizers
        # that last reported yesterday evening are not flagged at sunrise.
        last_seen = self._producing_since
        if last_measurement is not None:
            last_seen = max(last_seen, last_measurement)
        return now - last_seen > self.settings.stale_after

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


def in_safe_mode(reading: OptimizerReading) -> bool:
    """Return True if a reading shows the optimizer holding its 1 V output."""
    return (
        reading.optimizer_voltage is not None
        and reading.voltage is not None
        and reading.optimizer_voltage <= SAFE_MODE_MAX_OUTPUT_VOLTAGE
        and reading.voltage >= SAFE_MODE_MIN_MODULE_VOLTAGE
    )
