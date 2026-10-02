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


def _to_number(value, default=0.0):
    """Parse provider numbers defensively (int/float/strings such as '1,700' or 'Rp 1.700')."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("Rp", "").replace("IDR", "").strip()
    if not text:
        return default
    # Handle common Indonesian/international separators.
    try:
        if "," in text and "." in text:
            # Assume the last separator is the decimal separator only when the
            # suffix looks fractional; otherwise treat punctuation as grouping.
            if len(text.rsplit(",", 1)[-1]) <= 2 and len(text.rsplit(".", 1)[-1]) > 2:
                text = text.replace(".", "").replace(",", ".")
            else:
                text = text.replace(",", "").replace(".", "")
        elif "." in text:
            # Prices/stocks from Indonesian APIs commonly use 1.700 as 1700.
            tail = text.rsplit(".", 1)[-1]
            text = text.replace(".", "") if len(tail) == 3 else text
        elif "," in text:
            tail = text.rsplit(",", 1)[-1]
            text = text.replace(",", "") if len(tail) == 3 else text.replace(",", ".")
        return float(text)
    except (TypeError, ValueError):
        return default


def _normalize_stock_rows(data):
    """Normalize all known NomorOTP price/availability shapes.

    The documented response is a flat object/list, but aggregator pools can
    expose extra nesting (service -> country -> operator/provider -> quote).
    Walk nested mappings/lists so a valid count/cost pair is never discarded
    merely because the response shape differs between Plus/Express pools.
    """
    rows = []

    def walk(node, inherited_operator="any"):
        if isinstance(node, list):
            for item in node:
                walk(item, inherited_operator)
            return
        if not isinstance(node, dict):
            return

        operator = str(
            node.get("operator")
            or node.get("operator_name")
            or node.get("operatorCode")
            or inherited_operator
            or "any"
        ).strip() or "any"

        stock_raw = (
            node.get("count") if node.get("count") is not None else
            node.get("stock") if node.get("stock") is not None else
            node.get("available") if node.get("available") is not None else
            node.get("quantity") if node.get("quantity") is not None else
            node.get("qty")
        )
        cost_raw = (
            node.get("cost") if node.get("cost") is not None else
            node.get("price") if node.get("price") is not None else
            node.get("amount") if node.get("amount") is not None else
            node.get("rate") if node.get("rate") is not None else
            node.get("price_idr")
        )
        stock = int(max(0, round(_to_number(stock_raw, 0))))
        cost = _to_number(cost_raw, 0)
        if stock > 0 and cost > 0:
            rows.append({
                "operator": operator,
                "stock": stock,
                "cost_idr": int(round(cost)),
            })
            # A node with its own quote is already represented. Do not descend
            # into nested metadata and accidentally duplicate the same stock.
            return

        # No quote at this node: descend and use dictionary keys as a useful
        # operator/provider hint when available.
        for key, value in node.items():
            if key in {
                "success", "prices", "availability", "service", "services",
                "country", "countries", "cost", "price", "count", "stock",
                "available", "quantity", "qty", "amount", "rate", "price_idr",
                "operator", "operator_name", "operatorCode", "name", "code",
            }:
                if key in {"prices", "availability"}:
                    walk(value, inherited_operator)
                elif isinstance(value, (dict, list)):
                    walk(value, inherited_operator)
                continue
            if isinstance(value, (dict, list)):
                child_operator = str(key).strip() or inherited_operator
                walk(value, child_operator)

    walk(data)

    # Deduplicate identical operator/stock/price rows created by nested
    # wrappers while retaining separate operator tiers.
    unique = {}
    for row in rows:
        key = (row["operator"].lower(), row["stock"], row["cost_idr"])
        unique[key] = row
    result = list(unique.values())
    result.sort(key=lambda x: (x["cost_idr"], x["operator"].lower()))
    return result


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

            # Be tolerant if the aggregator ignores one/both filters and
            # returns service -> country -> quote or country -> service ->
            # quote instead of the documented service -> quote shape.
            target = None
            if isinstance(prices, dict):
                service_key = next((k for k in prices if str(k).lower() == str(service).lower()), None)
                country_key = next((k for k in prices if str(k) == str(country_value)), None)
                if service_key is not None:
                    service_data = prices.get(service_key)
                    if isinstance(service_data, dict):
                        nested_country = next((k for k in service_data if str(k) == str(country_value)), None)
                        target = service_data.get(nested_country) if nested_country is not None else service_data
                    else:
                        target = service_data
                elif country_key is not None:
                    country_data = prices.get(country_key)
                    if isinstance(country_data, dict):
                        nested_service = next((k for k in country_data if str(k).lower() == str(service).lower()), None)
                        target = country_data.get(nested_service) if nested_service is not None else country_data
                    else:
                        target = country_data

            rows = _normalize_stock_rows(target) if target is not None else []
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
