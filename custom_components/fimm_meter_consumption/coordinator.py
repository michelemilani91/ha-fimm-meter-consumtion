from __future__ import annotations

from datetime import date, datetime, timedelta
import logging
import re
from typing import Any

import requests
from requests.exceptions import RequestException

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    BILLS_AVERAGE_DAYS,
    BILLS_KEYS_PATH,
    BILLS_PATH,
    CONF_BASE_URL,
    CONF_BILL_SUPPLY_CODE,
    CONF_CONTRACT_CODE,
    CONF_SUPPLY_NUMBER,
    CONF_SUPPLY_TYPE,
    DEFAULT_TENANT,
    DOMAIN,
    PORTAL_CONTEXT,
    READINGS_PATH,
    REQUEST_TIMEOUT,
    SESSION_PATH,
    TARIFF_CATEGORY,
    TARIFF_VAT,
    TARIFFS_PATH,
    TENANT_HEADER,
)

_LOGGER = logging.getLogger(__name__)


def portal_parts(base_url: str) -> tuple[str, str]:
    """Da 'https://host/PortaleDV/CADF' (o '.../PortaleDV') ricava (radice, tenant).

    Le API dello sportello >= 4.0 stanno tutte sotto la radice e vogliono il tenant
    nell'header X-Tenant; un URL senza tenant usa quello di default.
    """
    base = base_url.rstrip("/")
    idx = base.find(PORTAL_CONTEXT)
    if idx < 0:
        return base, DEFAULT_TENANT
    root = base[: idx + len(PORTAL_CONTEXT)]
    rest = base[idx + len(PORTAL_CONTEXT):].strip("/").split("/")
    tenant = rest[0] if rest and rest[0] and not rest[0].startswith("api") else DEFAULT_TENANT
    return root, tenant.upper()


def api_headers(tenant: str) -> dict[str, str]:
    return {TENANT_HEADER: tenant, "Accept": "application/json"}


class _CadfClient:
    """Sessione autenticata verso lo sportello (cookie di sessione, tenant nell'header)."""

    def __init__(self, entry: ConfigEntry) -> None:
        self.username = entry.data[CONF_USERNAME]
        self.password = entry.data[CONF_PASSWORD]
        self.root, self.tenant = portal_parts(entry.data[CONF_BASE_URL])
        self.session = requests.Session()
        self.session.headers.update(api_headers(self.tenant))
        self.authenticated = False

    def authenticate(self) -> None:
        """POST /api/v1/session: 201 con la sessione, 401 credenziali errate, 403 account disabilitato.

        Solo un rifiuto esplicito delle credenziali è ConfigEntryAuthFailed; un errore di rete
        o del server è UpdateFailed, così HA ritenta invece di scaricare le entità.
        """
        self.authenticated = False
        try:
            response = self.session.post(
                f"{self.root}{SESSION_PATH}",
                json={"username": self.username, "password": self.password},
                timeout=REQUEST_TIMEOUT,
            )
        except RequestException as err:
            raise UpdateFailed(f"Login request failed: {err}") from err
        if response.status_code in (401, 403):
            raise ConfigEntryAuthFailed(
                f"Login refused ({response.status_code}): {_problem(response)}"
            )
        if not response.ok:
            raise UpdateFailed(f"Login failed ({response.status_code}): {_problem(response)}")
        self.authenticated = True

    def get(self, path: str, params: Any) -> requests.Response:
        """GET autenticata su {root}{path} con login automatico e un retry su 401."""
        if not self.authenticated:
            self.authenticate()
        url = f"{self.root}{path}"
        response = self.session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        if response.status_code == 401:
            self.authenticate()
            response = self.session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response


class FimmMeterCoordinator(DataUpdateCoordinator[float]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(hours=6),
        )
        self._client = _CadfClient(entry)
        self._contract_code = entry.data[CONF_CONTRACT_CODE]
        self._supply_type = entry.data[CONF_SUPPLY_TYPE]
        self._supply_number = entry.data[CONF_SUPPLY_NUMBER]

    async def _async_update_data(self) -> float:
        return await self.hass.async_add_executor_job(self._fetch)

    def _readings_params(self) -> dict[str, str]:
        return _readings_params(self._contract_code, self._supply_type, self._supply_number)

    def _fetch(self) -> float:
        try:
            payload = self._client.get(READINGS_PATH, self._readings_params()).json()
        except RequestException as err:
            raise UpdateFailed(f"API request failed: {err}") from err
        except ValueError as err:
            raise UpdateFailed(f"Invalid JSON in response: {err}") from err

        try:
            value = _readings_list(payload)[0]["lettMis"]
        except (KeyError, IndexError, TypeError) as err:
            raise UpdateFailed(f"Unexpected payload shape: {err}") from err
        if value is None:
            raise UpdateFailed("Empty reading in API response")
        return float(value)


class FimmBillsCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Legge le bollette dallo sportello e calcola il costo reale €/m³ degli ultimi 12 mesi.

    Le bollette CADF non riportano i m³ (sono acconti trimestrali + conguagli), quindi:
    prezzo = somma `totaleFattura` emesse negli ultimi 365 giorni
             / m³ consumati in un anno secondo le letture del contatore.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_bills",
            update_interval=timedelta(hours=12),
        )
        self._client = _CadfClient(entry)
        self._contract_code = entry.data[CONF_CONTRACT_CODE]
        self._supply_type = entry.data[CONF_SUPPLY_TYPE]
        self._supply_number = entry.data[CONF_SUPPLY_NUMBER]
        self._supply_code = entry.options.get(CONF_BILL_SUPPLY_CODE)

    async def _async_update_data(self) -> dict[str, Any]:
        return await self.hass.async_add_executor_job(self._fetch)

    def _fetch(self) -> dict[str, Any]:
        try:
            keys_payload = self._client.get(
                BILLS_KEYS_PATH,
                {
                    "supplyCode": self._supply_code,
                    "serviceCode": self._supply_type,
                    "contractCode": self._contract_code,
                },
            ).json()
            keys = _extract_keys(keys_payload)
            if not keys:
                raise UpdateFailed(f"No bill keys in response: {str(keys_payload)[:300]}")
            bills_payload = self._client.get(BILLS_PATH, [("keys", k) for k in keys]).json()
            readings_payload = self._client.get(
                READINGS_PATH,
                _readings_params(self._contract_code, self._supply_type, self._supply_number),
            ).json()
        except RequestException as err:
            raise UpdateFailed(f"Bills request failed: {err}") from err
        except ValueError as err:
            raise UpdateFailed(f"Invalid JSON in bills response: {err}") from err

        bills = parse_bills(bills_payload)
        readings = parse_readings(readings_payload)
        return compute_average(bills, readings, date.today())


class FimmTariffCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Scarica una volta al mese le tariffe pubbliche (ARERA) usate dal calcolatore CADF."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_tariffs",
            update_interval=timedelta(days=30),
        )
        self._root, self._tenant = portal_parts(entry.data[CONF_BASE_URL])

    @property
    def url(self) -> str:
        return f"{self._root}{TARIFFS_PATH}"

    async def _async_update_data(self) -> dict[str, Any]:
        return await self.hass.async_add_executor_job(self._fetch)

    def _fetch(self) -> dict[str, Any]:
        try:
            response = requests.get(
                self.url, headers=api_headers(self._tenant), timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
            payload = response.json()
        except RequestException as err:
            raise UpdateFailed(f"Tariffs request failed: {err}") from err
        except ValueError as err:
            raise UpdateFailed(f"Invalid JSON in tariffs response: {err}") from err
        data = parse_tariffs(payload, TARIFF_CATEGORY, date.today())
        if not data["bands"]:
            raise UpdateFailed(f"Tariff category {TARIFF_CATEGORY} not found")
        return data


# ---------------------------------------------------------------- parsing helpers


def _problem(response: requests.Response) -> str:
    """Il messaggio dell'ApiProblem {code, message, errors[]} se c'è, altrimenti il testo."""
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(payload, dict):
        return f"{payload.get('code')}: {payload.get('message')}"
    return str(payload)[:200]


def _readings_params(contract_code: str, supply_type: str, supply_number: str) -> dict[str, str]:
    return {
        "serviceCode": supply_type,
        "contractCode": contract_code,
        "supplyProgressive": supply_number,
    }


def _readings_list(payload: Any) -> list[Any]:
    """GET /api/v1/meter-readings risponde una lista; la vecchia azione la avvolgeva in 'letture'."""
    if isinstance(payload, dict):
        return payload.get("letture") or []
    return payload if isinstance(payload, list) else []


def _extract_keys(payload: Any) -> list[str]:
    out: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            if re.fullmatch(r"[A-Z]+\|\d+", node):
                out.append(node)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)

    walk(payload)
    return list(dict.fromkeys(out))


def _to_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.strptime(value.strip()[:10], "%d/%m/%Y").date()
    except ValueError:
        return None


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_bills(payload: Any) -> list[dict[str, Any]]:
    fatture = payload.get("fatture", []) if isinstance(payload, dict) else payload or []
    bills = []
    for f in fatture:
        if not isinstance(f, dict):
            continue
        rate = f.get("rate") or [{}]
        bills.append(
            {
                "key": f.get("key"),
                "date": _to_date(f.get("dataEmissione")),
                "amount": _to_float(f.get("totaleFattura")),
                "type": f.get("descrizioneModalitaAddebito"),
                "status": f.get("descrizioneStatoIncasso"),
                "due": rate[0].get("dataScadenza"),
                "number": f.get("numFiscaleFattura"),
            }
        )
    bills.sort(key=lambda b: b["date"] or date.min, reverse=True)
    return bills


def parse_readings(payload: Any) -> list[tuple[date, float]]:
    out = []
    for r in _readings_list(payload):
        d = _to_date(r.get("dataLett")) if isinstance(r, dict) else None
        v = _to_float(r.get("lettMis")) if isinstance(r, dict) else None
        if d and v is not None:
            out.append((d, v))
    out.sort()
    return out


def compute_average(
    bills: list[dict[str, Any]], readings: list[tuple[date, float]], today: date
) -> dict[str, Any]:
    cutoff = today - timedelta(days=BILLS_AVERAGE_DAYS)
    year_bills = [b for b in bills if b["date"] and b["date"] > cutoff and b["amount"]]
    amount = sum(b["amount"] for b in year_bills)

    m3_year = None
    reading_from = reading_to = None
    if len(readings) >= 2:
        last_d, last_v = readings[-1]
        target = last_d - timedelta(days=BILLS_AVERAGE_DAYS)
        first_d, first_v = min(readings[:-1], key=lambda r: abs((r[0] - target).days))
        days = (last_d - first_d).days
        if days > 0 and last_v > first_v:
            m3_year = (last_v - first_v) / days * BILLS_AVERAGE_DAYS
            reading_from, reading_to = first_d.isoformat(), last_d.isoformat()

    def _ser(b: dict[str, Any]) -> dict[str, Any]:
        return {**b, "date": b["date"].isoformat() if b["date"] else None}

    return {
        "bills": [_ser(b) for b in bills],
        "last": _ser(bills[0]) if bills else None,
        "amount_12m": round(amount, 2),
        "bills_12m": len(year_bills),
        "m3_year": round(m3_year, 2) if m3_year else None,
        "readings_from": reading_from,
        "readings_to": reading_to,
        "average_price": round(amount / m3_year, 4) if m3_year and amount else None,
    }


# ------------------------------------------------------------------ tariffe ARERA


def _current_rates(voce: dict[str, Any], today: date) -> list[dict[str, Any]]:
    """Per ogni scaglione tiene la tariffa più recente già in vigore."""
    best: dict[Any, dict[str, Any]] = {}
    for s in voce.get("scaglioni") or []:
        start = _to_date(s.get("dataEntrataInVigore")) or date.min
        if start > today:
            continue
        key = s.get("progressivoScaglione")
        if key not in best or start >= best[key]["_start"]:
            best[key] = {**s, "_start": start}
    return [best[k] for k in sorted(best, key=lambda k: (k is None, k))]


def _rate(voci: dict[str, dict], code: str, today: date) -> float:
    rates = _current_rates(voci[code], today) if code in voci else []
    return float(rates[0]["tariffa"]) if rates else 0.0


def parse_tariffs(payload: Any, category: str, today: date) -> dict[str, Any]:
    cats = {c.get("codiceCategoriaTar"): c for c in (payload or {}).get("categorie", [])}
    cat = cats.get(category) or {}
    gen = cats.get("GEN") or {}
    cv = {v.get("codiceVoce"): v for v in cat.get("voci", [])}
    gv = {v.get("codiceVoce"): v for v in gen.get("voci", [])}

    bands = []
    lower = 0.0
    for s in _current_rates(cv["QV-ACQ"], today) if "QV-ACQ" in cv else []:
        upper = float(s["sogliaSuperiore"])
        bands.append(
            {
                "from": lower,
                "to": None if upper >= 1e9 else upper,
                "price": round(float(s["tariffa"]), 6),
            }
        )
        lower = upper

    perequative = sum(
        _rate(gv, code, today)
        for code in gv
        if code.startswith("UI") and code[2:3].isdigit()
    )
    fixed = {
        "acquedotto": _rate(cv, "QF-ACQ", today),
        "fognatura": _rate(gv, "QF-FOG", today),
        "depurazione": _rate(gv, "QF-DEP", today),
        "fondo_fughe": _rate(gv, "FOFU", today),
    }
    starts = [
        s["_start"]
        for v in list(cv.values()) + list(gv.values())
        for s in _current_rates(v, today)
        if s["_start"] != date.min
    ]
    return {
        "category": category,
        "category_name": cat.get("descrizioneCategoriaTar"),
        "valid_from": max(starts).isoformat() if starts else None,
        "checked_on": (payload or {}).get("dataVigore"),
        "calculation": (cv.get("QV-ACQ") or {}).get("descrizioneCalcolo"),
        "vat": TARIFF_VAT,
        "bands": bands,
        "sewer": round(_rate(gv, "QV-FOG", today) + _rate(gv, "QV-FOG2", today), 6),
        "treatment": round(_rate(gv, "QV-DEP", today) + _rate(gv, "QV-DEP2", today), 6),
        "equalization": round(perequative, 6),
        "fixed_per_year": {k: round(v, 2) for k, v in fixed.items()},
        "fixed_total_per_year": round(sum(fixed.values()), 2),
    }
