"""Tests for the config and options flows."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solaredge_optimizers.api import SourceAuthError, SourceError
from custom_components.solaredge_optimizers.const import (
    CONF_CONSECUTIVE_SAMPLES,
    CONF_LOW_OUTPUT_PERCENT,
    CONF_MIN_PEER_POWER,
    CONF_SCAN_INTERVAL,
    CONF_SITE_ID,
    CONF_STALE_AFTER,
    DOMAIN,
)

from .conftest import SITE_ID, make_layout

USER_INPUT = {
    CONF_SITE_ID: f" {SITE_ID} ",
    "username": "me@example.com",
    "password": "pw",
}


async def test_user_flow(hass: HomeAssistant, mock_source: AsyncMock) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SITE_ID] == SITE_ID
    assert result["result"].unique_id == SITE_ID


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [
        (SourceAuthError("bad"), "invalid_auth"),
        (SourceError("down"), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant,
    mock_source: AsyncMock,
    side_effect: Exception,
    error: str,
) -> None:
    mock_source.async_get_layout.side_effect = side_effect
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    # The user can fix the problem and try again.
    mock_source.async_get_layout.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_no_optimizers(
    hass: HomeAssistant, mock_source: AsyncMock
) -> None:
    mock_source.async_get_layout.return_value = replace(make_layout(), optimizers={})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["errors"] == {"base": "no_optimizers"}


async def test_already_configured(
    hass: HomeAssistant, mock_source: AsyncMock, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(
    hass: HomeAssistant, mock_source: AsyncMock, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    mock_source.async_get_layout.side_effect = SourceAuthError("bad")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    mock_source.async_get_layout.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": "new"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data["password"] == "new"


async def test_options_flow(
    hass: HomeAssistant, mock_source: AsyncMock, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 10.0,
            CONF_LOW_OUTPUT_PERCENT: 40.0,
            CONF_MIN_PEER_POWER: 75.0,
            CONF_CONSECUTIVE_SAMPLES: 4.0,
            CONF_STALE_AFTER: 180.0,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert config_entry.options[CONF_CONSECUTIVE_SAMPLES] == 4
    settings = config_entry.runtime_data.monitor.settings
    assert settings.low_output_ratio == 0.4
    assert settings.consecutive_samples == 4
