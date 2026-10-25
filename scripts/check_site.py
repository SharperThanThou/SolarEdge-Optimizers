"""Check the integration's data source against a real SolarEdge site.

Logs in to the monitoring portal, prints the layout the integration sees and
the latest optimizer readings, and runs one health check. Nothing is changed
on the portal.

    python scripts/check_site.py SITE_ID EMAIL [--dump equipment.json]

The password is prompted for, or read from SOLAREDGE_PASSWORD.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
import getpass
import json
import os
from pathlib import Path
import sys

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.solaredge_optimizers.api import (
    CloudSource,
    SourceError,
)
from custom_components.solaredge_optimizers.health import HealthMonitor


def _fmt(value: float | None, unit: str) -> str:
    return "-" if value is None else f"{value:.1f}{unit}"


async def main(args: argparse.Namespace, password: str) -> int:
    """Run the check."""
    async with aiohttp.ClientSession() as session:
        source = CloudSource(session, args.email, password, args.site_id)
        try:
            layout = await source.async_get_layout()
            readings = await source.async_get_readings()
        except SourceError as err:
            print(f"Failed: {type(err).__name__}: {err}", file=sys.stderr)
            return 1

        if args.dump:
            # The raw layout, for turning into a test fixture if parsing is off.
            equipment = await source._client.async_get_equipment(include_inactive=True)
            Path(args.dump).write_text(json.dumps(equipment, indent=2, default=str))
            print(f"Wrote raw equipment to {args.dump}")

    now = datetime.now(UTC)
    health = HealthMonitor().update(layout, readings, now)

    print(
        f"\n{len(layout.inverters)} inverter(s), {len(layout.optimizers)} optimizer(s)"
    )
    by_string: dict[tuple[str | None, str | None], list[str]] = {}
    for serial, opt in layout.optimizers.items():
        by_string.setdefault((opt.inverter_serial, opt.string_name), []).append(serial)

    for (inverter, string_name), serials in sorted(
        by_string.items(), key=lambda item: tuple(str(part) for part in item[0])
    ):
        inv = layout.inverters.get(inverter or "")
        print(f"\n{inv.name if inv else 'No inverter'} / {string_name or 'No string'}")
        for serial in serials:
            opt = layout.optimizers[serial]
            r = readings.get(serial)
            h = health[serial]
            age = (
                f"{(now - r.last_measurement).total_seconds() / 60:.0f} min ago"
                if r and r.last_measurement
                else "never"
            )
            print(
                f"  {opt.name:<10} {serial:<14}"
                f" {_fmt(r and r.power, 'W'):>8}"
                f" {_fmt(r and r.voltage, 'V'):>7}"
                f" {_fmt(r and r.optimizer_voltage, 'V'):>7}"
                f" {_fmt(r and r.current, 'A'):>6}"
                f"  {age:<12} {h.candidate.value} (peers {h.peer_count},"
                f" median {_fmt(h.peer_median, 'W')})"
            )

    missing = set(layout.optimizers) - set(readings)
    if missing:
        print(f"\nNo reading returned for: {', '.join(sorted(missing))}")
    if args.verbose:
        print(
            json.dumps(
                {s: asdict(r) for s, r in readings.items()}, indent=2, default=str
            )
        )
    print(
        "\nThe last column is what this single sample suggests; the integration"
        " only reports a problem after several bad samples in a row."
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("site_id")
    parser.add_argument("email")
    parser.add_argument("--dump", metavar="FILE", help="write the raw layout JSON")
    parser.add_argument("--verbose", action="store_true", help="print raw readings")
    cli_args = parser.parse_args()
    pw = os.environ.get("SOLAREDGE_PASSWORD") or getpass.getpass("Portal password: ")
    sys.exit(asyncio.run(main(cli_args, pw)))
