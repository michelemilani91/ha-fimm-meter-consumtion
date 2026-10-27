# FIMM Meter Consumption for Home Assistant

Custom integration that reads the water (or gas / electricity) meter of a CADF supply from the
customer portal (easy4-cwm, `PortaleDV`) and derives consumption, bill costs and the current tariff.

## Features

- Login with the portal credentials (session cookie, tenant in the `X-Tenant` header).
- Meter reading polled every 6 hours; consumption computed as the delta between readings.
- Bills (optional): real average price €/m³ over the last 12 months and the last bill amount.
- Public ARERA tariff of the tenant, refreshed monthly.
- Config flow + options flow, translations in English and Italian.

## Requirements

Portal running **easy4-cwm 4.0 or newer** (`api/v1` only). Version 0.3.x of this integration
targeted the pre-4.0 portal (`api/v2/auth/login`, `getletture.json`) and no longer works.

## Installation

1. Add this repository as a [custom repository](https://hacs.xyz/docs/faq/custom_repositories/)
   in HACS (category: Integration):
   ```
   https://github.com/michelemilani91/ha-fimm-meter-consumtion
   ```
2. Restart Home Assistant.
3. **Settings → Devices & Services → Add Integration → FIMM Meter Consumption**.

## Configuration

| Field | Example |
|---|---|
| API base URL | `https://sportello-online.cadf.it/PortaleDV/CADF` |
| Username / Password | portal credentials |
| Contract code | the contract (`codiceContratto`) |
| Supply type | `A` water, `G` gas, `E` electricity |
| Supply number | the supply progressive (`progressivoFornitura`) |

Options: **Bills supply code** (the `supplyCode` used by the portal's bills area, e.g. `90045396`).
Leave it empty to disable the bill sensors.

The base URL may be given with or without the tenant segment; the integration derives the
`/PortaleDV` root and the tenant (default `CADF`) from it.

## Portal API used

| Purpose | Request |
|---|---|
| Login | `POST {root}/api/v1/session` with `{"username", "password"}` |
| Readings | `GET {root}/api/v1/meter-readings?serviceCode&contractCode&supplyProgressive` |
| Bill keys | `GET {root}/api/v1/bills/keys?supplyCode&serviceCode&contractCode` |
| Bills | `GET {root}/api/v1/bills?keys=...` |
| Tariffs (public) | `GET {root}/api/v1/bill-split/tariffs` |

Every call carries `X-Tenant: <tenant>`; answers are plain lists / objects (no `esito` envelope).

## Sensors

| Entity | Content |
|---|---|
| `sensor.<device>_meter_reading` | meter value (m³ or kWh), `total_increasing` |
| `sensor.<device>_consumption` | delta between the last two readings |
| `sensor.<device>_average_price` | €/m³ = bills of the last 12 months / yearly m³ from readings |
| `sensor.<device>_last_bill_amount` | amount of the latest bill, with date, number and status |
| `sensor.<device>_tariff` | ARERA variable price of the base band, VAT included; bands in attributes |

## License

MIT
