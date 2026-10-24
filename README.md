# SolarEdge Optimizers

A Home Assistant custom integration that pulls **per-optimizer** data from a
SolarEdge site and flags optimizers that look unhealthy.

## Why the cloud portal?

SolarEdge inverters don't expose optimizer data locally. Modbus TCP (SunSpec)
only reports inverter, meter and battery values, and the official Monitoring
API has no per-optimizer telemetry. The only practical source is the
monitoring portal (`monitoring.solaredge.com`), which this integration reads
with the same login you use in the browser, through the
[`solaredge-web`](https://github.com/tronikos/solaredge-web) library. Home
Assistant's core SolarEdge integration uses the same library.

Those portal endpoints are **undocumented**, so SolarEdge can change them
without notice. All data access goes through a small `OptimizerSource`
interface (`custom_components/solaredge_optimizers/api/base.py`), so a fix,
or a different backend such as a local one, only has to touch that layer.

## What you get

For each optimizer, as its own device under its inverter:

| Entity | Notes |
| --- | --- |
| Power, Current | Reported as 0 once the latest sample is stale (e.g. at night). |
| Module voltage, Optimizer voltage | Module (panel) side and optimizer output side. |
| Last measurement | When the portal last received data for this optimizer. |
| Health | `ok`, `low_output`, `no_output`, `not_reporting` or `unknown`. |
| Output vs peers | Power as a % of the median of its peers. |
| Problem (binary) | On while the health status is a problem. |

For the site:

| Entity | Notes |
| --- | --- |
| Optimizer problem (binary) | On if any optimizer has a problem. The `unhealthy_optimizers` attribute lists them. |
| Unhealthy optimizers | Count of optimizers with a problem. |
| Latest optimizer measurement | If this stops moving during the day, the inverter or its connection is down. |

## How health is judged

Panels in the same string see roughly the same sun, so each optimizer is
compared with the **median power of the other optimizers in its string**. If
the string has fewer than two other optimizers reporting, the whole site is
used instead.

* **No output**: below 5% of the peer median.
* **Low output**: below the *low output threshold* (default 50%).
* **Not reporting**: no data for *not reporting after* (default 2 h) while the
  rest of the site is producing. The time is counted from when the site
  started producing, so last night's silence doesn't trigger it at sunrise.
* **Unknown**: not enough light to judge. Comparisons only happen once the
  peer median reaches *minimum peer power* (default 50 W), so dawn, dusk and
  heavy cloud stay quiet.

A problem is only reported after *consecutive bad samples* (default 3) new
samples in a row agree, which filters out passing shadows. Re-polling the same
sample doesn't count. One good sample clears a problem. All thresholds can be
changed from the integration's **Configure** dialog.

Known limitation: panels in one string that face different directions, or
that are regularly shaded by a tree or chimney, can look "low" against their
peers. Raise the consecutive sample count or lower the threshold if that
happens.

### Example automation

```yaml
automation:
  - alias: Notify on unhealthy optimizer
    triggers:
      - trigger: state
        entity_id: binary_sensor.solaredge_site_1234567_optimizer_problem
        to: "on"
    actions:
      - action: notify.notify
        data:
          title: SolarEdge optimizer problem
          message: >
            {% for o in state_attr(trigger.entity_id, 'unhealthy_optimizers') %}
            {{ o.name }} ({{ o.serial }}): {{ o.status }}
            {% endfor %}
```

## Installation

### HACS (custom repository)

1. In HACS, open the menu and choose **Custom repositories**.
2. Add `https://github.com/SharperThanThou/SolarEdge-Optimizers` as an
   **Integration**.
3. Install **SolarEdge Optimizers** and restart Home Assistant.

### Manual

Copy `custom_components/solaredge_optimizers` into your Home Assistant
`config/custom_components/` folder and restart.

### Setup

**Settings → Devices & services → Add integration → SolarEdge Optimizers**,
then enter your site ID and your monitoring portal email and password. The
site ID is shown in the portal's site details.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
pytest
ruff check . && ruff format --check .
```

The health logic (`health.py`) and the data models (`api/models.py`) don't
depend on Home Assistant, so they're easy to test on their own.
