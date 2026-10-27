from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_BILL_SUPPLY_CODE, DOMAIN, PLATFORMS
from .coordinator import FimmBillsCoordinator, FimmMeterCoordinator, FimmTariffCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = FimmMeterCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    bills = None
    if entry.options.get(CONF_BILL_SUPPLY_CODE):
        bills = FimmBillsCoordinator(hass, entry)
        # Le bollette sono opzionali: un errore non deve bloccare la lettura del contatore
        await bills.async_refresh()

    # Tariffe pubbliche: aggiornate una volta al mese, un errore non blocca l'integrazione
    tariffs = FimmTariffCoordinator(hass, entry)
    await tariffs.async_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "meter": coordinator,
        "bills": bills,
        "tariffs": tariffs,
    }
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded
