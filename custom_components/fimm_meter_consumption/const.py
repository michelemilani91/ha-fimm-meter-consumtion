from homeassistant.const import Platform

DOMAIN = "fimm_meter_consumption"
PLATFORMS = [Platform.SENSOR]

CONF_BASE_URL = "base_url"
CONF_CONTRACT_CODE = "contract_code"
CONF_SUPPLY_TYPE = "supply_type"
CONF_SUPPLY_NUMBER = "supply_number"
# Codice fornitura usato dall'area bollette (es. 90045396), diverso dal progressivo fornitura
CONF_BILL_SUPPLY_CODE = "bill_supply_code"

# Sportello easy4-cwm >= 4.0: tutte le API stanno sotto la radice ".../PortaleDV" e il tenant
# (es. CADF) viaggia nell'header X-Tenant, non più nel path. La vecchia linea api/v2 e le
# azioni legacy .json (getletture.json) non esistono più.
PORTAL_CONTEXT = "/PortaleDV"
TENANT_HEADER = "X-Tenant"
DEFAULT_TENANT = "CADF"

SESSION_PATH = "/api/v1/session"
READINGS_PATH = "/api/v1/meter-readings"
BILLS_KEYS_PATH = "/api/v1/bills/keys"
BILLS_PATH = "/api/v1/bills"
BILLS_AVERAGE_DAYS = 365

# Tariffe pubbliche del calcolatore di ripartizione (nessun login)
TARIFFS_PATH = "/api/v1/bill-split/tariffs"
TARIFF_CATEGORY = "A-D.1.1"  # Uso domestico residente
TARIFF_VAT = 0.10

SUPPLY_WATER = "A"
SUPPLY_GAS = "G"
SUPPLY_ELECTRICITY = "E"

REQUEST_TIMEOUT = 30
