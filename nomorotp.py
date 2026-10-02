"""NomorOTP.id adapter for AZHURA Server 4.

The public API uses the SMS-Activate-compatible protocol documented by
NomorOTP.id. API credentials are read only from environment variables.
"""
import os
import time
import threading
import requests
from datetime import datetime, timezone, timedelta

BASE_URL = os.getenv("NOMOROTP_BASE_URL", "https://api.nomorotp.id/").rstrip("/") + "/"
API_KEY = os.getenv("NOMOROTP_API_KEY", "").strip()
PLUS_SERVER = os.getenv("NOMOROTP_PLUS_SERVER", "plus").strip() or "plus"
EXPRESS_SERVER = os.getenv("NOMOROTP_EXPRESS_SERVER", "sh").strip() or "sh"
TIMEOUT = int(os.getenv("NOMOROTP_TIMEOUT", "20") or 20)
CACHE_TTL = float(os.getenv("NOMOROTP_CATALOG_CACHE_TTL", "180") or 180)

_session = requests.Session()
_cache = {}
_cache_lock = threading.Lock()

POPULAR_ALIASES = [
    ("whatsapp", {"whatsapp", "wa"}),
    ("shopee", {"shopee"}),
    ("tiktok", {"tiktok", "tt", "douyin"}),
    ("telegram", {"telegram", "tg"}),
    ("facebook", {"facebook", "fb"}),
    ("instagram", {"instagram", "ig"}),
    ("google", {"google", "gmail", "youtube", "google gmail youtube"}),
    ("tokopedia", {"tokopedia"}),
    ("gojek", {"gojek"}),
    ("grab", {"grab"}),
    ("dana", {"dana"}),
    ("ovo", {"ovo"}),
    ("discord", {"discord"}),
    ("lazada", {"lazada"}),
    ("blibli", {"blibli"}),
]


def _require_key():
    if not API_KEY:
        raise RuntimeError("NOMOROTP_API_KEY belum diatur di Railway.")


def _request(action, method="GET", params=None, data=None):
    _require_key()
    query = dict(params or {})
    query["action"] = action
    headers = {"X-API-Key": API_KEY, "Accept": "application/json"}
    try:
        r = _session.request(
            method,
            BASE_URL,
            params=query,
            data=data,
            headers=headers,
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"NomorOTP tidak dapat dihubungi: {exc}") from exc
    try:
        payload = r.json()
    except Exception as exc:
        raise RuntimeError(f"Respons NomorOTP tidak valid (HTTP {r.status_code}).") from exc
    if r.status_code >= 400 or not payload.get("success"):
        err = payload.get("error")
        if isinstance(err, dict):
            msg = err.get("message") or err.get("code")
        else:
            msg = err
        raise RuntimeError(str(msg or f"NomorOTP HTTP {r.status_code}"))
    return payload


def _cached(key):
    with _cache_lock:
        item = _cache.get(key)
        if item and time.monotonic() - item[0] < CACHE_TTL:
            return item[1]
    return None


def _put(key, value):
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)
    return value


def server_code(kind):
    kind = str(kind or "plus").lower().strip()
    if kind == "express":
        return EXPRESS_SERVER
    return PLUS_SERVER


def server_label(kind):
    return "Server Plus" if str(kind).lower() == "plus" else "Server Express"


def _norm(value):
    return " ".join(str(value or "").lower().replace("_", " ").replace("-", " ").split())


def _service_sort(item):
    code = _norm(item.get("code"))
    name = _norm(item.get("name"))
    for rank, aliases in enumerate(POPULAR_ALIASES):
        if code in aliases[1] or name in aliases[1]:
            return (rank, name, code)
        if any(name.startswith(a + " ") for a in aliases[1]):
            return (rank, name, code)
    return (1000, name, code)


def get_services(kind="plus"):
    code = server_code(kind)
    key = f"services:{code}"
    cached = _cached(key)
    if cached is not None:
        return list(cached)
    data = _request("getServices", params={"server": code}).get("services") or {}
    records = []
    if isinstance(data, dict):
        iterable = data.items()
    elif isinstance(data, list):
        iterable = enumerate(data)
    else:
        iterable = []
    seen = set()
    for k, value in iterable:
        if isinstance(value, dict):
            service_code = str(value.get("code") or value.get("service") or value.get("id") or k).strip()
            name = str(value.get("name") or value.get("title") or value.get("service") or service_code).strip()
        else:
            service_code = str(value or k).strip()
            name = service_code
        if service_code and service_code.lower() not in seen:
            records.append({"code": service_code, "name": name})
            seen.add(service_code.lower())
    records.sort(key=_service_sort)
    return _put(key, records)


def get_countries(kind="plus"):
    code = server_code(kind)
    key = f"countries:{code}"
    cached = _cached(key)
    if cached is not None:
        return list(cached)
    data = _request("getCountries", params={"server": code}).get("countries") or {}
    records = []
    if isinstance(data, dict):
        iterable = data.items()
    elif isinstance(data, list):
        iterable = enumerate(data)
    else:
        iterable = []
    for k, value in iterable:
        if isinstance(value, dict):
            cid = str(value.get("id") or value.get("code") or value.get("country") or k).strip()
            name = str(value.get("name") or value.get("country_name") or value.get("title") or cid).strip()
            iso = str(value.get("iso_code") or value.get("iso") or value.get("country_code") or "").strip()
        else:
            cid = str(k).strip()
            name = str(value).strip()
            iso = ""
        if cid and name:
            records.append({"id": cid, "name": name, "country": cid, "country_name": name, "iso_code": iso})
    records.sort(key=lambda x: (0 if _norm(x["name"]) == "indonesia" else 1, _norm(x["name"])))
    return _put(key, records)


def get_prices(kind="plus", country=None, service=None):
    params = {"server": server_code(kind)}
    if country not in (None, ""):
        params["country"] = int(country) if str(country).isdigit() else country
    if service:
        params["service"] = service
    return _request("getPrices", params=params).get("prices") or {}


def _normalize_stock_rows(data):
    """Normalize provider availability/price payloads into operator rows.

    NomorOTP documents getAvailability as the operator-level source, while
    getPrices is the aggregate source (cost + count). In practice an
    aggregator server can return an empty operator matrix even when the
    aggregate price endpoint has stock. Keep both paths so AZHURA does not
    incorrectly tell users that stock is empty.
    """
    if isinstance(data, dict):
        # Common shape: {operator: {count, cost}}
        if all(isinstance(v, dict) for v in data.values()):
            data = [dict(v, operator=k) for k, v in data.items()]
        else:
            data = [data]
    if not isinstance(data, list):
        return []

    rows = []
    for item in data:
        if not isinstance(item, dict):
            continue
        operator = str(
            item.get("operator")
            or item.get("operator_name")
            or item.get("name")
            or item.get("operatorCode")
            or "any"
        ).strip()
        try:
            stock = int(float(item.get("count") or item.get("stock") or item.get("available") or item.get("quantity") or 0))
            cost = float(item.get("cost") or item.get("price") or item.get("amount") or item.get("rate") or 0)
        except (TypeError, ValueError):
            continue
        if cost <= 0 or stock <= 0:
            continue
        rows.append({"operator": operator or "any", "stock": stock, "cost_idr": int(round(cost))})
    rows.sort(key=lambda x: (x["cost_idr"], x["operator"].lower()))
    return rows


def get_availability(kind, service, country):
    code = server_code(kind)
    key = f"availability:{code}:{service}:{country}"
    # Availability uses a short 12-second cache so rapid UI navigation does
    # not hammer the provider while stock/prices remain reasonably live.
    with _cache_lock:
        item = _cache.get(key)
        if item and time.monotonic() - item[0] < 12:
            return list(item[1])

    country_value = int(country) if str(country).isdigit() else country

    # Preferred path: operator-level availability.
    try:
        availability_payload = _request(
            "getAvailability",
            params={"server": code, "service": service, "country": country_value},
        ).get("availability") or []
        rows = _normalize_stock_rows(availability_payload)
    except RuntimeError:
        # Some provider pools may not expose operator availability even though
        # their price/stock endpoint is working. Fall through to getPrices.
        rows = []

    # Important fallback: getPrices is explicitly documented by NomorOTP as
    # returning cost + count for service/country. This prevents a false
    # "stock unavailable" result when the Plus/Express aggregator has stock
    # but no operator matrix.
    if not rows:
        try:
            prices = _request(
                "getPrices",
                params={"server": code, "country": country_value, "service": service},
            ).get("prices") or {}
            target = prices.get(service) if isinstance(prices, dict) else None
            rows = _normalize_stock_rows(target)
            if not rows and isinstance(prices, dict):
                rows = _normalize_stock_rows(prices)
        except RuntimeError:
            rows = []

    rows.sort(key=lambda x: (x["cost_idr"], x["operator"].lower()))
    return _put(key, rows)


def buy_number(kind, service, country, operator=None):
    payload = {
        "server": server_code(kind),
        "service": service,
        "country": int(country) if str(country).isdigit() else country,
    }
    if operator and str(operator).lower() not in {"any", "auto", "all"}:
        payload["operator"] = operator
    data = _request("getNumber", method="POST", data=payload).get("activation") or {}
    if not data.get("id") or not data.get("phone"):
        raise RuntimeError("NomorOTP tidak mengembalikan nomor aktivasi yang lengkap.")
    # The public API documents expires_in=seconds rather than always returning
    # an absolute timestamp. Convert it once so AZHURA history/countdowns can
    # use the same expired_at field as the other providers.
    if not data.get("expires_at") and not data.get("expired_at") and data.get("expires_in"):
        try:
            seconds = int(float(data.get("expires_in")))
            data["expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
        except (TypeError, ValueError):
            pass
    return data


def get_status(activation_id):
    return _request("getStatus", params={"id": activation_id})


def cancel_activation(activation_id):
    return _request("cancelActivation", method="POST", data={"id": activation_id})


def resend_activation(activation_id):
    return _request("setStatus", method="POST", data={"id": activation_id, "status": 3})


def finish_activation(activation_id):
    return _request("setStatus", method="POST", data={"id": activation_id, "status": 6})


def normalize_status(data):
    if not isinstance(data, dict):
        return {"response": "ERROR", "status": "", "otp": None, "sms": []}
    activation = data.get("activation") if isinstance(data.get("activation"), dict) else data
    status = str(activation.get("status") or data.get("status") or data.get("state") or "").strip()
    code = activation.get("code") or activation.get("otp") or data.get("code") or data.get("otp")
    text = activation.get("sms_text") or activation.get("message") or data.get("sms_text") or data.get("message") or ""
    sms = []
    if code:
        sms.append({"code": str(code), "text": str(text)})
    return {
        "response": "OK" if data.get("success", True) else "ERROR",
        "status": status,
        "otp": str(code) if code else None,
        "phone": activation.get("phone") or activation.get("phone_number") or data.get("phone"),
        "expired_at": activation.get("expired_at") or activation.get("expires_at") or data.get("expired_at"),
        "sms": sms,
        "sms_text": text,
    }
