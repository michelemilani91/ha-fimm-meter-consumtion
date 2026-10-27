from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_SUPPLY_NUMBER,
    CONF_SUPPLY_TYPE,
    DOMAIN,
    SUPPLY_ELECTRICITY,
    SUPPLY_GAS,
    SUPPLY_WATER,
)
from .coordinator import FimmBillsCoordinator, FimmMeterCoordinator, FimmTariffCoordinator


SUPPLY_TYPE_TO_DEVICE_CLASS = {
    SUPPLY_WATER: SensorDeviceClass.WATER,
    SUPPLY_GAS: SensorDeviceClass.GAS,
    SUPPLY_ELECTRICITY: SensorDeviceClass.ENERGY,
}

SUPPLY_TYPE_TO_UNIT = {
    SUPPLY_WATER: UnitOfVolume.CUBIC_METERS,
    SUPPLY_GAS: UnitOfVolume.CUBIC_METERS,
    SUPPLY_ELECTRICITY: UnitOfEnergy.KILO_WATT_HOUR,
}


def _device_info(entry: ConfigEntry) -> dict[str, Any]:
    supply_type = entry.data[CONF_SUPPLY_TYPE]
    return {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": f"FIMM Meter {entry.data[CONF_SUPPLY_NUMBER]}",
        "manufacturer": "CADF",
        "model": SUPPLY_TYPE_TO_DEVICE_CLASS[supply_type].value.title(),
    }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    meter: FimmMeterCoordinator = data["meter"]
    bills: FimmBillsCoordinator | None = data["bills"]

    entities: list[SensorEntity] = [
        FimmMeterReadingSensor(meter, entry),
        FimmConsumptionSensor(meter, entry),
    ]
    entities.append(FimmTariffSensor(data["tariffs"], entry))
    if bills is not None:
        entities += [
            FimmAveragePriceSensor(bills, entry),
            FimmLastBillAmountSensor(bills, entry),
        ]
    async_add_entities(entities)


# ------------------------------------------------------------------ letture contatore


class _FimmBase(CoordinatorEntity[FimmMeterCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: FimmMeterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        supply_type = entry.data[CONF_SUPPLY_TYPE]
        self._attr_device_class = SUPPLY_TYPE_TO_DEVICE_CLASS[supply_type]
        self._attr_native_unit_of_measurement = SUPPLY_TYPE_TO_UNIT[supply_type]
        self._attr_device_info = _device_info(entry)


class FimmMeterReadingSensor(_FimmBase):
    _attr_translation_key = "meter_reading"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator: FimmMeterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_meter_reading"

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data


class FimmConsumptionSensor(_FimmBase, RestoreEntity):
    _attr_translation_key = "consumption"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: FimmMeterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_consumption"
        self._attr_device_class = None
        self._previous_reading: float | None = None
        self._consumption: float | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state not in (None, "unknown", "unavailable"):
            try:
                self._consumption = float(last.state)
            except ValueError:
                pass
            prev = (last.attributes or {}).get("previous_reading")
            if prev is not None:
                try:
                    self._previous_reading = float(prev)
                except (TypeError, ValueError):
                    pass

    @callback
    def _handle_coordinator_update(self) -> None:
        current = self.coordinator.data
        if current is not None:
            if self._previous_reading is not None and current != self._previous_reading:
                self._consumption = round(
                    max(current - self._previous_reading, 0), 3
                )
            self._previous_reading = current
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        return self._consumption

    @property
    def extra_state_attributes(self) -> dict[str, float | None]:
        return {"previous_reading": self._previous_reading}


# ------------------------------------------------------------------------- bollette


class _FimmBillsBase(CoordinatorEntity[FimmBillsCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: FimmBillsCoordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = _device_info(entry)

    @property
    def _last(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get("last")


class FimmAveragePriceSensor(_FimmBillsBase):
    """Costo reale €/m³: bollette emesse negli ultimi 12 mesi / m³ annui dalle letture."""

    _attr_native_unit_of_measurement = "€/m³"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:cash-multiple"

    def __init__(self, coordinator: FimmBillsCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "average_price")

    @property
    def native_value(self) -> float | None:
        return (self.coordinator.data or {}).get("average_price")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {
            "amount_12m": data.get("amount_12m"),
            "bills_12m": data.get("bills_12m"),
            "m3_year": data.get("m3_year"),
            "readings_from": data.get("readings_from"),
            "readings_to": data.get("readings_to"),
            "bills": data.get("bills"),
        }


class FimmLastBillAmountSensor(_FimmBillsBase):
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "€"

    def __init__(self, coordinator: FimmBillsCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "last_bill_amount")

    @property
    def native_value(self) -> float | None:
        return self._last["amount"] if self._last else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        last = self._last or {}
        return {k: last.get(k) for k in ("date", "type", "status", "due", "number", "key")}


# ------------------------------------------------------------------------- tariffe


class FimmTariffSensor(CoordinatorEntity[FimmTariffCoordinator], SensorEntity):
    """Tariffa ARERA in vigore. Stato: prezzo variabile completo della fascia base
    (acquedotto + fognatura + depurazione + perequative, IVA inclusa); dettaglio negli attributi."""

    _attr_has_entity_name = True
    _attr_translation_key = "tariff"
    _attr_native_unit_of_measurement = "€/m³"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:file-document-outline"

    def __init__(self, coordinator: FimmTariffCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_tariff"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        if not data or not data.get("bands"):
            return None
        band = data["bands"][1] if len(data["bands"]) > 1 else data["bands"][0]
        variable = band["price"] + data["sewer"] + data["treatment"] + data["equalization"]
        return round(variable * (1 + data["vat"]), 4)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return dict(self.coordinator.data or {})
