from __future__ import annotations

from typing import Any

import requests
from requests.exceptions import RequestException
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_BASE_URL,
    CONF_BILL_SUPPLY_CODE,
    CONF_CONTRACT_CODE,
    CONF_SUPPLY_NUMBER,
    CONF_SUPPLY_TYPE,
    DOMAIN,
    REQUEST_TIMEOUT,
    SESSION_PATH,
    SUPPLY_ELECTRICITY,
    SUPPLY_GAS,
    SUPPLY_WATER,
)
from .coordinator import api_headers, portal_parts


SUPPLY_OPTIONS = {
    SUPPLY_WATER: "Water (A)",
    SUPPLY_GAS: "Gas (G)",
    SUPPLY_ELECTRICITY: "Electricity (E)",
}


DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BASE_URL): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_CONTRACT_CODE): str,
        vol.Required(CONF_SUPPLY_TYPE): vol.In(SUPPLY_OPTIONS),
        vol.Required(CONF_SUPPLY_NUMBER): str,
    }
)


class CannotConnect(Exception):
    pass


class InvalidAuth(Exception):
    pass


def _validate_credentials(user_input: dict[str, Any]) -> None:
    root, tenant = portal_parts(user_input[CONF_BASE_URL])
    try:
        response = requests.post(
            f"{root}{SESSION_PATH}",
            headers=api_headers(tenant),
            json={
                "username": user_input[CONF_USERNAME],
                "password": user_input[CONF_PASSWORD],
            },
            timeout=REQUEST_TIMEOUT,
        )
    except RequestException as err:
        raise CannotConnect from err
    if response.status_code in (401, 403):
        raise InvalidAuth
    if not response.ok:
        raise CannotConnect


async def validate_credentials(hass: HomeAssistant, user_input: dict[str, Any]) -> None:
    await hass.async_add_executor_job(_validate_credentials, user_input)


class FimmMeterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> FimmMeterOptionsFlow:
        return FimmMeterOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            unique_id = (
                f"{user_input[CONF_CONTRACT_CODE]}-"
                f"{user_input[CONF_SUPPLY_TYPE]}-"
                f"{user_input[CONF_SUPPLY_NUMBER]}"
            )
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            try:
                await validate_credentials(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            else:
                title = (
                    f"{SUPPLY_OPTIONS[user_input[CONF_SUPPLY_TYPE]]} "
                    f"– {user_input[CONF_SUPPLY_NUMBER]}"
                )
                return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=DATA_SCHEMA,
            errors=errors,
        )


class FimmMeterOptionsFlow(config_entries.OptionsFlow):
    """Opzioni: codice fornitura dell'area bollette (vuoto = bollette disattivate)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            code = (user_input.get(CONF_BILL_SUPPLY_CODE) or "").strip()
            return self.async_create_entry(data={CONF_BILL_SUPPLY_CODE: code})

        current = self.config_entry.options.get(CONF_BILL_SUPPLY_CODE, "")
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {vol.Optional(CONF_BILL_SUPPLY_CODE, default=current): str}
            ),
        )
