"""Tests for the peer-comparison health check."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from custom_components.solaredge_optimizers.api import OptimizerReading, SiteLayout
from custom_components.solaredge_optimizers.health import (
    HealthMonitor,
    HealthSettings,
    HealthStatus,
)

from .conftest import STRING_A, STRING_B, make_layout, make_readings

SAMPLE = timedelta(minutes=15)
BAD = STRING_A[0]


def run_samples(
    monitor: HealthMonitor,
    layout: SiteLayout,
    start: datetime,
    count: int,
    overrides: dict[str, float | None],
) -> None:
    """Feed ``count`` new samples, each one sample interval apart."""
    for index in range(count):
        when = start + index * SAMPLE
        monitor.update(layout, make_readings(when, overrides=overrides), when)


def test_healthy_site(now: datetime) -> None:
    monitor = HealthMonitor()
    health = monitor.update(make_layout(), make_readings(now), now)
    assert {h.status for h in health.values()} == {HealthStatus.OK}
    assert health[BAD].ratio == 1.0
    assert health[BAD].peer_count == 4


def test_no_output_needs_consecutive_samples(now: datetime) -> None:
    layout = make_layout()
    monitor = HealthMonitor()
    run_samples(monitor, layout, now, 2, {BAD: 0.0})
    health = monitor.health[BAD]
    assert health.status is HealthStatus.UNKNOWN
    assert health.candidate is HealthStatus.NO_OUTPUT
    assert health.streak == 2
    assert not health.problem

    run_samples(monitor, layout, now + 2 * SAMPLE, 1, {BAD: 0.0})
    health = monitor.health[BAD]
    assert health.status is HealthStatus.NO_OUTPUT
    assert health.problem
    assert monitor.health[STRING_A[1]].status is HealthStatus.OK


def test_missing_power_counts_as_zero(now: datetime) -> None:
    monitor = HealthMonitor(HealthSettings(consecutive_samples=1))
    run_samples(monitor, make_layout(), now, 1, {BAD: None})
    assert monitor.health[BAD].status is HealthStatus.NO_OUTPUT


def test_low_output(now: datetime) -> None:
    monitor = HealthMonitor()
    run_samples(monitor, make_layout(), now, 3, {BAD: 120.0})
    health = monitor.health[BAD]
    assert health.status is HealthStatus.LOW_OUTPUT
    assert health.ratio == 0.4


def test_threshold_is_configurable(now: datetime) -> None:
    monitor = HealthMonitor(HealthSettings(low_output_ratio=0.3))
    run_samples(monitor, make_layout(), now, 3, {BAD: 120.0})
    assert monitor.health[BAD].status is HealthStatus.OK


def test_repeated_poll_of_same_sample_does_not_count(now: datetime) -> None:
    layout = make_layout()
    monitor = HealthMonitor()
    readings = make_readings(now, overrides={BAD: 0.0})
    for minutes in (0, 5, 10):
        monitor.update(layout, readings, now + timedelta(minutes=minutes))
    assert monitor.health[BAD].streak == 1
    assert not monitor.health[BAD].problem


def test_interrupted_streak_resets(now: datetime) -> None:
    layout = make_layout()
    monitor = HealthMonitor()
    run_samples(monitor, layout, now, 2, {BAD: 0.0})
    run_samples(monitor, layout, now + 2 * SAMPLE, 1, {})
    run_samples(monitor, layout, now + 3 * SAMPLE, 2, {BAD: 0.0})
    assert monitor.health[BAD].streak == 2
    assert not monitor.health[BAD].problem


def test_recovery_is_immediate(now: datetime) -> None:
    layout = make_layout()
    monitor = HealthMonitor()
    run_samples(monitor, layout, now, 3, {BAD: 0.0})
    assert monitor.health[BAD].problem
    run_samples(monitor, layout, now + 3 * SAMPLE, 1, {})
    assert monitor.health[BAD].status is HealthStatus.OK


def test_low_light_keeps_previous_status(now: datetime) -> None:
    layout = make_layout()
    monitor = HealthMonitor()
    run_samples(monitor, layout, now, 3, {BAD: 0.0})
    dim = {serial: 10.0 for serial in STRING_A + STRING_B} | {BAD: 0.0}
    run_samples(monitor, layout, now + 3 * SAMPLE, 1, dim)
    health = monitor.health[BAD]
    assert health.status is HealthStatus.NO_OUTPUT
    assert health.ratio is None


def test_dusk_does_not_flag(now: datetime) -> None:
    monitor = HealthMonitor()
    dim = {serial: 20.0 for serial in STRING_A + STRING_B} | {BAD: 0.0}
    run_samples(monitor, make_layout(), now, 5, dim)
    assert {h.status for h in monitor.health.values()} == {HealthStatus.UNKNOWN}


def test_compares_within_string(now: datetime) -> None:
    # String B is shaded and makes half the power of string A. Comparing
    # against the site median would flag all of it; per string it is fine.
    shaded = dict.fromkeys(STRING_B, 140.0)
    monitor = HealthMonitor()
    run_samples(monitor, make_layout(), now, 3, shaded)
    assert {h.status for h in monitor.health.values()} == {HealthStatus.OK}


def test_small_string_falls_back_to_site(now: datetime) -> None:
    layout = make_layout()
    # Only one other optimizer shares BAD's string.
    optimizers = {
        serial: opt
        for serial, opt in layout.optimizers.items()
        if serial not in STRING_A[2:]
    }
    layout = replace(layout, optimizers=optimizers)
    monitor = HealthMonitor()
    health = monitor.update(layout, make_readings(now), now)
    assert health[BAD].peer_count == len(optimizers) - 1


def test_not_reporting_waits_for_sunrise(now: datetime) -> None:
    layout = make_layout()
    monitor = HealthMonitor()
    yesterday = now - timedelta(hours=14)

    def poll(when: datetime) -> None:
        readings = make_readings(when)
        readings[BAD] = OptimizerReading(
            serial=BAD, power=5.0, last_measurement=yesterday
        )
        monitor.update(layout, readings, when)

    # The site has just started producing: last night's silence is expected.
    poll(now)
    assert monitor.health[BAD].status is HealthStatus.UNKNOWN
    poll(now + timedelta(hours=1))
    assert not monitor.health[BAD].problem

    poll(now + timedelta(hours=2, minutes=5))
    assert monitor.health[BAD].status is HealthStatus.NOT_REPORTING


def test_whole_site_offline_is_not_an_optimizer_problem(now: datetime) -> None:
    layout = make_layout()
    monitor = HealthMonitor()
    readings = make_readings(now - timedelta(hours=5))
    for hours in range(6):
        monitor.update(layout, readings, now + timedelta(hours=hours))
    assert not any(h.problem for h in monitor.health.values())


def test_removed_optimizer_is_forgotten(now: datetime) -> None:
    layout = make_layout()
    monitor = HealthMonitor()
    monitor.update(layout, make_readings(now), now)
    smaller = replace(
        layout,
        optimizers={s: o for s, o in layout.optimizers.items() if s != BAD},
    )
    health = monitor.update(smaller, make_readings(now + SAMPLE), now + SAMPLE)
    assert BAD not in health
