"""Constants for the SolarEdge Optimizers integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "solaredge_optimizers"

CONF_SITE_ID: Final = "site_id"

CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_LOW_OUTPUT_PERCENT: Final = "low_output_percent"
CONF_MIN_PEER_POWER: Final = "min_peer_power"
CONF_CONSECUTIVE_SAMPLES: Final = "consecutive_samples"
CONF_STALE_AFTER: Final = "stale_after"

# The inverter uploads optimizer telemetry roughly every 15 minutes, so polling
# much faster than this only adds load on the portal.
DEFAULT_SCAN_INTERVAL: Final = 5  # minutes
MIN_SCAN_INTERVAL: Final = 5  # minutes
DEFAULT_LOW_OUTPUT_PERCENT: Final = 50
DEFAULT_MIN_PEER_POWER: Final = 50  # W
DEFAULT_CONSECUTIVE_SAMPLES: Final = 3
DEFAULT_STALE_AFTER: Final = 120  # minutes
