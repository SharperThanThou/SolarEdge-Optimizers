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
| Health | `ok`, `low_output`, `no_output`, `not_reporting`, `safe_mode` or `unknown`. |
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
  An optimizer whose last report is more than 24 h older than the site's
  newest report is flagged straight away, day or night, so long-dead
  optimizers show up right after a restart.
* **Safe mode**: the optimizer is holding its output at about 1 V while its
  panel has voltage. SolarEdge optimizers do this until the inverter tells
  them to produce, so it's normal at night but a fault while the rest of the
  site is producing. It replaces no/low output or not reporting when the
  latest reading shows it, because it points at the optimizer itself rather
  than shading or a communication problem.
* **Unknown**: not enough light to judge. Comparisons only happen once the
  peer median reaches *minimum peer power* (default 50 W), so dawn, dusk and
  heavy cloud stay quiet.

A problem is only reported after *consecutive bad samples* (default 3) new
samples in a row agree, which filters out passing shadows. Re-polling the same
sample doesn't count. One good sample clears a problem. All thresholds can be
changed from the integration's **Configure** dialog.

### Ignoring an optimizer

When an optimizer is replaced, the portal sometimes keeps listing the old
unit, which then reports a problem forever. To ignore it, open its device
(**Settings → Devices & services → SolarEdge Optimizers**), choose **⋮ →
Disable**. A disabled optimizer is left out of the health check and isn't
used as anyone's peer. Enable it again to resume monitoring. The proper fix
is still to ask your installer to remove the old unit from the site layout.

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
# Python 3.13 or newer, same as Home Assistant.
python3.13 -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
pytest
ruff check . && ruff format --check .
```

### Checking against your own site

`scripts/check_site.py` logs in with your portal credentials and prints the
layout and readings exactly as the integration sees them, plus what one
health check would conclude. It changes nothing on the portal.

```bash
python scripts/check_site.py SITE_ID you@example.com
python scripts/check_site.py SITE_ID you@example.com --dump equipment.json
```

`--dump` saves the raw layout JSON, which is the thing to attach to an issue
if optimizers end up in the wrong string. Serial numbers are included, so
review it before sharing.

The health logic (`health.py`) and the data models (`api/models.py`) don't
depend on Home Assistant, so they're easy to test on their own.

## Security

A security review of this integration and its `solaredge-web` dependency
(version 0.5.0) was done on 2026-10-24. No serious issues were found. The
known risks below were judged low and accepted as they are.

### What was checked

- **Login.** `solaredge-web` uses the same OAuth (PKCE) login as SolarEdge's
  web app. It only sends credentials to, and only accepts login codes from,
  `solaredge.com` hosts, and it leaves TLS certificate checks on.
- **Logging.** Neither this integration nor `solaredge-web` logs passwords,
  tokens or cookies, including at debug level. Logs show URLs and status codes.
- **Session isolation.** The portal login lives in a cookie jar of its own,
  separate from Home Assistant's shared HTTP session.
- **Diagnostics.** Downloaded diagnostics redact the username and password.
- **Known vulnerabilities.** `solaredge-web` 0.5.0 had no published advisories
  (checked with `pip-audit`).

### Known risks

- **Credentials at rest.** Like every Home Assistant integration, the portal
  email and password are stored unencrypted in
  `.storage/core.config_entries`. Treat Home Assistant backups as sensitive.
- **Unpinned dependency.** The manifest requires `solaredge-web>=0.5.0`, so a
  new install gets the latest release. A compromised future release would be
  installed automatically. Pinning an exact version would prevent that, but
  it could conflict with Home Assistant's built-in SolarEdge integration,
  which pins its own version.
- **Site ID isn't validated.** The site ID is placed in request URLs as typed.
  Only the account owner enters it, under their own login, so it can't be
  used to reach anyone else's data.
- **No HTTPS check on the login form.** `solaredge-web` checks that the login
  form posts to a `solaredge.com` host but not that it uses HTTPS. A
  password would only be sent unencrypted if SolarEdge's own login page
  pointed at an `http://` address.
- **CI actions aren't pinned.** The hassfest and HACS workflows use the
  `@master` and `@main` tags of those actions. The workflows have read-only
  permissions and no secrets.
- **Diagnostics include identifiers.** The site ID and optimizer serial
  numbers aren't redacted. Neither grants access without the password, but
  review a diagnostics file before posting it publicly. The same applies to
  `scripts/check_site.py --dump` output.

### Reporting a vulnerability

Please report security problems privately through GitHub's
[security advisories](https://github.com/SharperThanThou/SolarEdge-Optimizers/security/advisories/new)
rather than in a public issue.

## License

Apache License 2.0. See [LICENSE](LICENSE).
