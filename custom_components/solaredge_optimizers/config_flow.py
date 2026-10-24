"""Config flow for the SolarEdge Optimizers integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_create_clientsession
import voluptuous as vol

from .api import CloudSource, SourceAuthError, SourceError
from .const import (
    CONF_CONSECUTIVE_SAMPLES,
    CONF_LOW_OUTPUT_PERCENT,
    CONF_MIN_PEER_POWER,
    CONF_SCAN_INTERVAL,
    CONF_SITE_ID,
    CONF_STALE_AFTER,
    DEFAULT_CONSECUTIVE_SAMPLES,
    DEFAULT_LOW_OUTPUT_PERCENT,
    DEFAULT_MIN_PEER_POWER,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STALE_AFTER,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)
from .coordinator import SolarEdgeOptimizersConfigEntry

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SITE_ID): str,
        vol.Required(CONF_USERNAME): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.EMAIL, autocomplete="username"
            )
        ),
        vol.Required(CONF_PASSWORD): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.PASSWORD,
                autocomplete="current-password",
            )
        ),
    }
)

REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PASSWORD): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.PASSWORD,
                autocomplete="current-password",
            )
        ),
    }
)


async def _async_validate(
    hass: HomeAssistant, username: str, password: str, site_id: str
) -> str | None:
    """Try the credentials. Return an error key, or None on success."""
    session = async_create_clientsession(hass, auto_cleanup=False)
    try:
        layout = await CloudSource(
            session, username, password, site_id
        ).async_get_layout()
    except SourceAuthError:
        return "invalid_auth"
    except SourceError:
        return "cannot_connect"
    except Exception:
        _LOGGER.exception("Unexpected error validating SolarEdge credentials")
        return "unknown"
    finally:
        session.detach()
    if not layout.optimizers:
        return "no_optimizers"
    return None


class SolarEdgeOptimizersConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SolarEdge Optimizers."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the site ID and portal credentials."""
        errors: dict[str, str] = {}
        if user_input is not None:
            site_id = str(user_input[CONF_SITE_ID]).strip()
            await self.async_set_unique_id(site_id)
            self._abort_if_unique_id_configured()

            error = await _async_validate(
                self.hass,
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                site_id,
            )
            if error is None:
                return self.async_create_entry(
                    title=f"SolarEdge site {site_id}",
                    data={**user_input, CONF_SITE_ID: site_id},
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication after the portal rejected the credentials."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await _async_validate(
                self.hass,
                entry.data[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                entry.data[CONF_SITE_ID],
            )
            if error is None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: SolarEdgeOptimizersConfigEntry,
    ) -> SolarEdgeOptimizersOptionsFlow:
        """Return the options flow."""
        return SolarEdgeOptimizersOptionsFlow()


def _number(
    minimum: float, maximum: float, step: float, unit: str
) -> selector.NumberSelector:
    """Build a number box selector."""
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            unit_of_measurement=unit,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): _number(
            MIN_SCAN_INTERVAL, 60, 1, "min"
        ),
        vol.Required(
            CONF_LOW_OUTPUT_PERCENT, default=DEFAULT_LOW_OUTPUT_PERCENT
        ): _number(5, 95, 1, "%"),
        vol.Required(CONF_MIN_PEER_POWER, default=DEFAULT_MIN_PEER_POWER): _number(
            0, 1000, 1, "W"
        ),
        vol.Required(
            CONF_CONSECUTIVE_SAMPLES, default=DEFAULT_CONSECUTIVE_SAMPLES
        ): _number(1, 20, 1, "samples"),
        vol.Required(CONF_STALE_AFTER, default=DEFAULT_STALE_AFTER): _number(
            30, 1440, 1, "min"
        ),
    }
)


class SolarEdgeOptimizersOptionsFlow(OptionsFlowWithReload):
    """Tune polling and the health check thresholds."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the options form."""
        if user_input is not None:
            return self.async_create_entry(
                data={key: int(value) for key, value in user_input.items()}
            )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, self.config_entry.options
            ),
        )
