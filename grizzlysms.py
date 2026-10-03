"""Grizzly SMS adapter for AZHURA Server 4.

This module is intentionally independent from the existing provider modules.
The provider exposes an SMS-Activate-compatible client API.
"""
import os
import math
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
from urllib.parse import quote

import requests

BASE_URL = "https://api.grizzlysms.com"
HANDLER_URL = f"{BASE_URL}/stubs/handler_api.php"
TIMEOUT = 25
SESSION = requests.Session()

_RATE = None
_RATE_AT = 0.0
_RATE_TTL = 3600.0


class GrizzlyError(RuntimeError):
    pass


class GrizzlyRejected(GrizzlyError):
    """Explicit provider rejection; safe for the caller to refund."""


def _api_key():
    key = os.getenv("GRIZZLYSMS_API_KEY", "").strip()
    if not key:
        raise GrizzlyError("GRIZZLYSMS_API_KEY belum diatur di Railway.")
    return key


def _request(action, **params):
    payload = {"api_key": _api_key(), "action": action}
    payload.update({k: v for k, v in params.items() if v is not None and v != ""})
    response = SESSION.get(HANDLER_URL, params=payload, timeout=TIMEOUT)
    response.raise_for_status()
    data = response.json() if "application/json" in response.headers.get("content-type", "").lower() else response.text
    if isinstance(data, str):
        marker = data.strip()
        if marker.startswith(("BAD_", "NO_", "ERROR_", "WRONG_", "SERVICE_UNAVAILABLE_", "USERS_IP_")):
            if marker in {"NO_NUMBERS", "NO_BALANCE"}:
                raise GrizzlyRejected(marker)
            raise GrizzlyError(marker)
    return data


def _usd_idr():
    global _RATE, _RATE_AT
    if _RATE and time.monotonic() - _RATE_AT < _RATE_TTL:
        return _RATE
    try:
        r = SESSION.get("https://api.frankfurter.dev/v2/rate/USD/IDR", timeout=8)
        r.raise_for_status()
        rate = float(r.json().get("rate") or 0)
        if 10000 < rate < 30000:
            _RATE = rate
            _RATE_AT = time.monotonic()
            return rate
    except Exception:
        pass
    return _RATE or 18000.0


def sell_price_idr(cost_idr):
    cost = Decimal(str(cost_idr or 0))
    if cost <= 0:
        return 0
    margin = Decimal(str(os.getenv("PROFIT_PERCENT", "7"))) / Decimal("100")
    return int((cost * (Decimal("1") + margin)).to_integral_value(rounding=ROUND_CEILING))


def check_api():
    try:
        return {"success": True, "data": get_balance()}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def get_balance():
    data = _request("getBalance")
    if isinstance(data, str):
        parts = data.split(":")
        if len(parts) >= 2 and parts[0] == "ACCESS_BALANCE":
            return float(parts[1])
    if isinstance(data, dict):
        return float(data.get("balance") or data.get("amount") or 0)
    raise GrizzlyError("Respons saldo Grizzly tidak valid.")


def _normalise_service(item, key=None):
    if isinstance(item, dict):
        code = str(item.get("service_code") or item.get("code") or item.get("short_name")
                   or item.get("service") or item.get("id") or key or "").strip()
        name = str(item.get("service_name") or item.get("name") or item.get("label")
                   or code).strip()
    else:
        code = str(key or item or "").strip()
        name = str(item or code).strip()
    return code, name


def _unwrap_collection(data, keys):
    """Unwrap common Grizzly response envelopes without losing legacy shapes."""
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, (list, dict)):
                return value
        # Some endpoints return the useful object under `data`.
        value = data.get("data")
        if isinstance(value, (list, dict)):
            return value
    return data


def get_services():
    data = _unwrap_collection(_request("getServicesList"), ("services", "serviceList", "service_list"))
    out = []
    seen = set()
    if isinstance(data, dict):
        iterable = data.items()
    elif isinstance(data, list):
        iterable = enumerate(data)
    else:
        iterable = []
    for key, item in iterable:
        code, name = _normalise_service(item, key)
        # Never expose a response-envelope key such as `status` as a service.
        if str(code).lower() in {"status", "success", "message", "error", "data"}:
            continue
        if code and code.lower() not in seen:
            out.append({"id": code, "name": name})
            seen.add(code.lower())
    return out


def _normalise_country(item, key=None):
    if isinstance(item, dict):
        cid = str(item.get("country_id") or item.get("id") or item.get("country")
                    or key or item.get("code") or "").strip()
        name = str(item.get("country_name") or item.get("name") or item.get("english")
                    or item.get("eng") or item.get("englishName") or item.get("eng_name")
                    or item.get("english_name") or item.get("label") or cid).strip()
        iso = str(item.get("iso_code") or item.get("iso") or item.get("country_code")
                   or item.get("code2") or item.get("iso2") or "").strip().upper()
        if not iso:
            candidate_iso = str(item.get("code") or "").strip().upper()
            iso = candidate_iso if len(candidate_iso) == 2 and candidate_iso.isalpha() else ""
    else:
        cid = str(key or item or "").strip()
        name = str(item or cid).strip()
        iso = ""
    return cid, name, iso


def get_countries():
    data = _unwrap_collection(_request("getCountries"), ("countries", "countryList", "country_list"))
    out = []
    seen = set()
    if isinstance(data, dict):
        iterable = data.items()
    elif isinstance(data, list):
        iterable = enumerate(data)
    else:
        iterable = []
    for key, item in iterable:
        cid, name, iso = _normalise_country(item, key)
        if str(cid).lower() in {"status", "success", "message", "error", "data"}:
            continue
        if cid and cid not in seen:
            out.append({"id": cid, "name": name, "iso_code": iso})
            seen.add(cid)
    return out


def _price_records(data, wanted_service=None, wanted_country=None):
    """Flatten Grizzly/SMS-Activate compatible price responses.

    Grizzly follows the SMS-Activate-compatible JSON shape, but the service
    endpoint has had several response variants over time. Keep parsing
    tolerant so a service can always resolve to its countries and quotes.
    """
    records = []
    seen = set()

    def add(country, service, info):
        if not isinstance(info, dict):
            return
        try:
            cost = float(info.get("cost") or info.get("price") or info.get("rate") or info.get("activationCost") or 0)
            stock = int(float(info.get("count") if info.get("count") is not None else
                              info.get("stock") if info.get("stock") is not None else
                              info.get("available") if info.get("available") is not None else 0))
        except (TypeError, ValueError):
            return
        if not math.isfinite(cost) or cost <= 0:
            return
        key = (str(country), str(service), round(cost, 8), max(stock, 0))
        if key in seen:
            return
        seen.add(key)
        records.append({
            "country": str(country),
            "service": str(service),
            "cost_usd": cost,
            "stock": max(stock, 0),
        })

    def looks_like_quote(info):
        if not isinstance(info, dict):
            return False
        return any(k in info for k in ("cost", "price", "rate", "activationCost")) and any(
            k in info for k in ("count", "stock", "available", "quantity", "qty")
        )

    def walk_nested(obj):
        """Handle both {country:{service:quote}} and {service:{country:quote}}."""
        if not isinstance(obj, dict):
            return
        wanted = str(wanted_service).strip().lower() if wanted_service is not None else ""
        for outer, outer_data in obj.items():
            if not isinstance(outer_data, dict):
                continue
            for inner, info in outer_data.items():
                if looks_like_quote(info):
                    outer_s = str(outer).strip()
                    inner_s = str(inner).strip()
                    ol = outer_s.lower()
                    il = inner_s.lower()
                    if wanted and ol == wanted:
                        add(inner_s, outer_s, info)
                    elif wanted and il == wanted:
                        add(outer_s, inner_s, info)
                    elif str(outer).isdigit() and not str(inner).isdigit():
                        add(outer_s, inner_s, info)
                    elif str(inner).isdigit() and not str(outer).isdigit():
                        add(inner_s, outer_s, info)
                    else:
                        # Default to the canonical country-first shape.
                        add(outer_s, inner_s, info)
                elif isinstance(info, dict):
                    # Optional operator/provider layer.
                    for nested in info.values():
                        if not looks_like_quote(nested):
                            continue
                        outer_s = str(outer).strip()
                        inner_s = str(inner).strip()
                        ol = outer_s.lower()
                        il = inner_s.lower()
                        if wanted and ol == wanted:
                            add(inner_s, outer_s, nested)
                        elif wanted and il == wanted:
                            add(outer_s, inner_s, nested)
                        elif str(outer).isdigit() and not str(inner).isdigit():
                            add(outer_s, inner_s, nested)
                        elif str(inner).isdigit() and not str(outer).isdigit():
                            add(inner_s, outer_s, nested)
                        else:
                            add(outer_s, inner_s, nested)

    def walk_list(obj):
        # Variant: [{country,service,cost,count}, ...]
        if not isinstance(obj, list):
            return
        for item in obj:
            if not isinstance(item, dict):
                continue
            country = item.get("country") or item.get("country_id") or item.get("countryCode") or item.get("country_code")
            service = item.get("service") or item.get("service_code") or item.get("product") or item.get("code")
            if country is not None and service is not None and looks_like_quote(item):
                add(country, service, item)

    # Ignore response envelope fields such as status/success/message.
    if isinstance(data, dict):
        clean = {k: v for k, v in data.items() if str(k).lower() not in {"status", "success", "message", "error"}}
        nested = clean.get("data")
        if isinstance(nested, dict):
            walk_nested(nested)
        elif isinstance(nested, list):
            walk_list(nested)
        if not records:
            walk_nested(clean)
    elif isinstance(data, list):
        walk_list(data)

    if wanted_service is not None:
        target = str(wanted_service).strip().lower()
        records = [r for r in records if r["service"].strip().lower() == target]
    if wanted_country is not None:
        target = str(wanted_country).strip().lower()
        records = [r for r in records if r["country"].strip().lower() == target]
    return records

def get_prices(country=None, service=None):
    last_error = None
    # Prefer the standard endpoint; fall back to newer variants when an
    # account/region exposes only one of them.
    for action in ("getPrices", "getPricesV2", "getPricesV3"):
        try:
            data = _request(action, country=country, service=service)
            rows = _price_records(data, wanted_service=service, wanted_country=country)
            if rows or action == "getPricesV3":
                return rows
        except Exception as exc:
            last_error = exc
    if last_error:
        raise last_error
    return []


def _country_lookup():
    try:
        return {str(x["id"]): x for x in get_countries()}
    except Exception:
        return {}


def get_catalog():
    services = get_services()
    if services:
        return services
    # Fallback to service codes observed in the live price matrix.
    seen = {}
    for row in get_prices():
        code = row["service"]
        seen.setdefault(code.lower(), {"id": code, "name": code})
    return list(seen.values())


def get_service_countries(service):
    rows = get_prices(service=service)
    names = _country_lookup()
    grouped = {}
    for row in rows:
        cid = str(row["country"]).strip()
        if not cid:
            continue
        c = grouped.setdefault(cid, {"id": cid, "name": cid, "iso_code": "", "stock": 0, "cost_usd": 0})
        if cid in names:
            c["name"] = names[cid].get("name") or c["name"]
            c["iso_code"] = names[cid].get("iso_code") or c["iso_code"]
        # Some compatible responses use the country name as the key. In that
        # case the key itself is already the best display name.
        if c["name"] == cid and not cid.isdigit():
            c["name"] = cid.replace("_", " ").title()
        # Official Grizzly country table uses 6 for Indonesia. Keep this
        # fallback so Indonesia remains correctly named even if the optional
        # getCountries lookup is temporarily unavailable.
        if cid == "6":
            c["name"] = "Indonesia"
            c["iso_code"] = "ID"
        c["stock"] += max(0, int(row["stock"]))
        if row["stock"] > 0 and (not c["cost_usd"] or row["cost_usd"] < c["cost_usd"]):
            c["cost_usd"] = row["cost_usd"]
    # Only countries with a real stock entry are shown, matching the other servers.
    out = [x for x in grouped.values() if x["stock"] > 0]
    out.sort(key=lambda x: (0 if str(x.get("name","")).strip().lower() == "indonesia" else 1, str(x.get("name","")).lower()))
    return out


def get_quotes(service, country):
    rows = get_prices(country=country, service=service)
    # Grizzly does not expose an end-user operator field in the standard price
    # matrix, so each returned price tier is presented as "Semua Operator".
    out = []
    for row in rows:
        if row["stock"] <= 0:
            continue
        cost_idr = int(math.ceil(row["cost_usd"] * _usd_idr()))
        out.append({
            "stock": int(row["stock"]),
            "cost_idr": cost_idr,
            "label": "Semua Operator",
            "metadata": {
                "country": str(country),
                "service": str(service),
                "cost_usd": float(row["cost_usd"]),
            },
        })
    out.sort(key=lambda x: x["cost_idr"])
    return out


def get_number(service, country, max_price=None):
    try:
        data = _request("getNumberV2", service=service, country=country, maxPrice=max_price)
    except GrizzlyRejected:
        raise
    except GrizzlyError:
        raise
    if not isinstance(data, dict):
        # V1 compatibility fallback.
        data = _request("getNumber", service=service, country=country, maxPrice=max_price)
    if isinstance(data, str):
        parts = data.split(":")
        if len(parts) >= 3 and parts[0] == "ACCESS_NUMBER":
            activation_id, phone = parts[1], parts[2]
            started = datetime.now(timezone.utc)
            return {
                "id": activation_id,
                "phone": phone,
                "expired_at": (started + timedelta(minutes=20)).isoformat(),
            }
        raise GrizzlyRejected(data)
    activation_id = str(data.get("activationId") or data.get("act_id") or data.get("id") or "").strip()
    phone = str(data.get("phoneNumber") or data.get("number") or data.get("phone") or "").strip()
    if not activation_id or not phone:
        raise GrizzlyRejected("Provider tidak mengembalikan nomor.")
    raw_time = data.get("activationTime") or data.get("activation_time")
    started = None
    if raw_time:
        try:
            started = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
        except Exception:
            started = None
    started = started or datetime.now(timezone.utc)
    return {
        "id": activation_id,
        "phone": phone,
        "expired_at": (started + timedelta(minutes=20)).isoformat(),
        "can_get_another_sms": str(data.get("canGetAnotherSms") or "1") in {"1", "true", "True"},
        "raw": data,
    }


def _parse_status(data):
    if isinstance(data, dict):
        sms = data.get("sms")
        if isinstance(sms, dict):
            sms = [sms]
        if not isinstance(sms, list):
            sms = data.get("messages") if isinstance(data.get("messages"), list) else []
        otp = data.get("code") or data.get("otp") or data.get("otp_code")
        text = data.get("text") or data.get("sms_text") or data.get("message")
        if sms:
            first = sms[0] if isinstance(sms[0], dict) else {}
            otp = otp or first.get("code") or first.get("otp") or first.get("otp_code")
            text = text or first.get("text") or first.get("message")
        status = str(data.get("status") or data.get("state") or "").upper()
        return {"response": "OK", "status": status, "otp": otp, "text": text, "sms": sms}
    raw = str(data or "").strip()
    if raw.startswith("STATUS_OK:"):
        code = raw.split(":", 1)[1].strip()
        return {"response": "OK", "status": "OK", "otp": code, "text": "", "sms": [{"code": code, "text": ""}]}
    if raw.startswith("STATUS_WAIT_RETRY:"):
        old = raw.split(":", 1)[1].strip()
        return {"response": "OK", "status": "WAIT_RETRY", "otp": None, "previous_code": old, "text": "", "sms": []}
    if raw == "STATUS_WAIT_RESEND":
        return {"response": "OK", "status": "WAIT_RESEND", "otp": None, "text": "", "sms": []}
    if raw == "STATUS_WAIT_CODE":
        return {"response": "OK", "status": "WAIT_CODE", "otp": None, "text": "", "sms": []}
    if raw.startswith("STATUS_CANCEL"):
        return {"response": "OK", "status": "CANCEL", "otp": None, "text": "", "sms": []}
    if raw.startswith(("BAD_", "NO_", "ERROR_", "WRONG_")):
        return {"response": "ERROR", "error": raw, "status": raw, "sms": []}
    return {"response": "OK", "status": raw, "otp": None, "text": "", "sms": []}


def get_sms(activation_id):
    return _parse_status(_request("getStatusV2", id=str(activation_id)))


def _set_status(activation_id, status):
    result = _request("setStatus", id=str(activation_id), status=int(status))
    if isinstance(result, str):
        return {"response": "OK" if result.startswith("ACCESS_") else "ERROR",
                "status": result, "raw": result}
    return {"response": "OK", "status": result.get("status") if isinstance(result, dict) else str(result), "raw": result}


def cancel_number(activation_id):
    result = _set_status(activation_id, 8)
    if result.get("response") != "OK" or result.get("status") not in {"ACCESS_CANCEL", "CANCEL", "SUCCESS", "OK"}:
        return {"response": "ERROR", "error": str(result.get("status") or "Provider belum mengonfirmasi cancel.")}
    return {"response": "OK", "provider_status": str(result.get("status") or "ACCESS_CANCEL")}


def finish_number(activation_id):
    result = _set_status(activation_id, 6)
    if result.get("response") != "OK":
        return {"response": "ERROR", "error": str(result.get("status") or "Provider belum mengonfirmasi selesai.")}
    return {"response": "OK", "provider_status": str(result.get("status") or "ACCESS_ACTIVATION")}


def resend_otp(activation_id):
    result = _set_status(activation_id, 3)
    if result.get("response") != "OK":
        return {"response": "ERROR", "error": str(result.get("status") or "Provider belum mengonfirmasi resend.")}
    return {"response": "OK", "provider_status": str(result.get("status") or "ACCESS_RETRY_GET")}


def status_is_cancelled(data):
    statuses = _status_values(data)
    return any(x in {"access_cancel", "cancel", "cancelled", "canceled", "status_cancel"} for x in statuses)


def _status_values(value):
    found = []
    if isinstance(value, dict):
        for k in ("status", "state", "provider_status"):
            if value.get(k) not in (None, ""):
                found.append(str(value[k]).strip().lower())
        raw = value.get("raw")
        if isinstance(raw, dict):
            found.extend(_status_values(raw))
    elif value not in (None, ""):
        found.append(str(value).strip().lower())
    return found


def verify_cancel(activation_id):
    try:
        return status_is_cancelled(get_sms(activation_id))
    except Exception:
        return False
