"""RumahOTP provider adapter.

Uses the official RumahOTP API v2 for services/countries/operators/orders,
and v1 for order status/cancel. API key is read from RUMAHOTP_API_KEY.
"""
import json
import logging
import requests
import time
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import RUMAHOTP_API_KEY, KURS_DOLAR

logger = logging.getLogger(__name__)
BASE_URL = "https://www.rumahotp.io/api"
TIMEOUT = 15
_CACHE_TTL = 300.0
_cache = {}


def _headers():
    return {"x-apikey": RUMAHOTP_API_KEY, "Accept": "application/json"}


def _get(path, params=None):
    """GET RumahOTP with a small 429 retry; never turn an API error into cached empty stock."""
    if not RUMAHOTP_API_KEY:
        return {"success": False, "error": {"message": "RUMAHOTP_API_KEY belum diatur."}}

    last_error = None
    for attempt in range(2):
        try:
            r = requests.get(
                f"{BASE_URL}{path}",
                headers=_headers(),
                params=params or {},
                timeout=TIMEOUT,
            )

            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After")
                try:
                    delay = max(1.0, min(float(retry_after), 5.0))
                except (TypeError, ValueError):
                    delay = 2.0
                time.sleep(delay)
                continue

            r.raise_for_status()
            data = r.json()
            if not isinstance(data, dict):
                return {"success": False, "error": {"message": "Response RumahOTP tidak valid."}}
            return data

        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.25)

    logger.warning("[RUMAHOTP] request %s failed: %s", path, last_error)
    return {"success": False, "error": {"message": str(last_error or "Request gagal.")}}


def check_api():
    data = _get("/v1/user/balance")
    return {"success": bool(data.get("success")), "data": data.get("data"), "error": data.get("error")}


def get_balance():
    data = _get("/v1/user/balance")
    try:
        return float((data.get("data") or {}).get("balance") or 0)
    except Exception:
        return 0.0


def _cached(key):
    item = _cache.get(key)
    if item and time.monotonic() - item[0] < _CACHE_TTL:
        return item[1]
    return None

def _put_cache(key, value):
    _cache[key] = (time.monotonic(), value)
    return value

def get_services():
    cached = _cached("services")
    if cached is not None:
        return cached

    data = _get("/v2/services")
    if not data.get("success"):
        logger.warning("[RUMAHOTP] services unavailable: %s", data.get("error"))
        return []

    value = data.get("data")
    if not isinstance(value, list):
        logger.warning("[RUMAHOTP] services returned unexpected data.")
        return []

    return _put_cache("services", value)


def _norm(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()

def find_service(service):
    """Resolve a RumahOTP service by exact service_code or service_name.

    The previous matcher treated the requested value as an alias of itself,
    which made values such as ``google``/``shopee`` accidentally resolve to
    the first service returned by the API.
    """
    target_raw = str(service or "").strip()
    target = _norm(target_raw)

    aliases = {
        "google": {"google", "gmail", "youtube", "google gmail youtube"},
        "whatsapp": {"whatsapp", "wa"},
        "telegram": {"telegram", "tg"},
        "facebook": {"facebook", "fb"},
        "instagram": {"instagram", "ig"},
        "tiktok": {"tiktok", "tt"},
        "shopee": {"shopee"},
    }

    for item in get_services():
        code = str(item.get("service_code") or item.get("id") or "").strip()
        name = str(item.get("service_name") or item.get("name") or "").strip()
        code_norm = _norm(code)
        name_norm = _norm(name)

        # Exact API code is the primary key. This is important because
        # RumahOTP service codes are numeric (e.g. 13 for WhatsApp).
        if target_raw == code or target == code_norm:
            return {"id": code, "name": name or code, **item}

        if target == name_norm:
            return {"id": code, "name": name or code, **item}

        for alias, names in aliases.items():
            if target == alias and name_norm in {_norm(x) for x in names}:
                return {"id": code, "name": name or code, **item}
            if target == _norm(name) and name_norm in {_norm(x) for x in names}:
                return {"id": code, "name": name or code, **item}

    return None

def get_countries(service_id):
    key = f"countries:{service_id}"
    cached = _cached(key)
    if cached is not None:
        return cached

    data = _get("/v2/countries", {"service_id": service_id})
    if not data.get("success"):
        logger.warning(
            "[RUMAHOTP] countries unavailable for service=%s: %s",
            service_id,
            data.get("error"),
        )
        return []

    value = data.get("data")
    if not isinstance(value, list):
        logger.warning(
            "[RUMAHOTP] countries returned unexpected data for service=%s",
            service_id,
        )
        return []

    # Only successful API responses are cached. A temporary 429/error must
    # never become a fake "stock kosong" result for the next 30 seconds.
    return _put_cache(key, value)


def find_country(country, service_id):
    target = str(country or "").strip().lower().replace("_", " ")
    aliases = {"vietnam": {"vietnam", "viet nam"}, "united states": {"united states", "usa", "us", "united states of america"}, "united kingdom": {"united kingdom", "uk", "gb"}}
    wanted = aliases.get(target, {target})
    for item in get_countries(service_id):
        name = str(item.get("name") or "").strip()
        iso = str(item.get("iso_code") or "").strip().lower()
        number_id = str(item.get("number_id") or "").strip()
        if name.lower() in wanted or iso in wanted or target == name.lower() or (number_id and target == number_id):
            return item
    return None


def _country_quotes(country_item, service_name, service_id=None):
    """Return every provider/server price, including zero-stock price tiers."""
    quotes = []
    if not isinstance(country_item, dict):
        return quotes

    country_name = str(country_item.get("name") or country_item.get("iso_code") or "")
    number_id = country_item.get("number_id")

    for row in country_item.get("pricelist") or []:
        if not isinstance(row, dict):
            continue

        provider_id = row.get("provider_id")
        price = float(row.get("price") or row.get("rate") or 0)
        stock = int(float(row.get("stock") or 0))
        server_id = row.get("server_id")

        if provider_id is None or price <= 0:
            continue

        quotes.append({
            "provider": "rumahotp",
            "country": str(number_id or country_name),
            "country_name": country_name,
            "iso_code": str(country_item.get("iso_code") or "").lower(),
            "service": str(service_id or service_name),
            "service_name": str(service_name),
            "operator": "any",
            "provider_operator": "any",
            "provider_id": str(provider_id),
            "server_id": str(server_id or "2"),
            "price_idr": price,
            "available": bool(row.get("available", stock > 0)) and stock > 0,
            "pool": json.dumps({
                "number_id": number_id,
                "provider_id": provider_id,
                "operator_id": 1,
                "server_id": server_id,
            }, separators=(",", ":")),
            "cost_usd": price / float(KURS_DOLAR),
            "cost_idr": price,
            "stock": stock,
        })

    return quotes

def get_all_quotes(service):
    """Return every live country/provider quote for a RumahOTP service."""
    found = find_service(service)
    if not found:
        logger.warning("[RUMAHOTP] service not found: %s", service)
        return []

    sid = found.get("id")
    result = []
    countries = get_countries(sid)

    for country in countries:
        result.extend(
            _country_quotes(
                country,
                found.get("name") or service,
                sid,
            )
        )

    return result


def get_quotes_for_country(country, service):
    """Return every RumahOTP provider/server price for one country/service."""
    found = find_service(service)
    if not found:
        return []
    item = find_country(country, found.get("id"))
    if not item:
        return []
    return _country_quotes(item, found.get("name") or service, found.get("id"))


def get_operator_quotes(country, service):
    """Return live operator-specific quotes for one country/service.

    Operator endpoints for multiple price tiers are independent. Resolve
    uncached tiers in parallel so the Telegram callback does not wait for a
    long chain of sequential HTTP requests.
    """
    found = find_service(service)
    if not found:
        return []
    sid = found.get("id")
    item = find_country(country, sid)
    if not item:
        return []

    rows = []
    for row in item.get("pricelist") or []:
        if not isinstance(row, dict):
            continue
        provider_id = row.get("provider_id")
        price = float(row.get("price") or row.get("rate") or 0)
        stock = int(float(row.get("stock") or 0))
        if provider_id is None or price <= 0 or stock <= 0:
            continue
        rows.append((row, provider_id, price, stock))

    def resolve(row_info):
        row, provider_id, price, stock = row_info
        cache_key = f"operators:{str(item.get('name')).lower()}:{provider_id}"
        operators = _cached(cache_key)
        if operators is None:
            ops = _get("/v2/operators", {
                "country": item.get("name"),
                "provider_id": provider_id,
            })
            operators = ops.get("data") if ops.get("success") and isinstance(ops.get("data"), list) else []
            _put_cache(cache_key, operators)
        return row, provider_id, price, stock, operators

    resolved = []
    if len(rows) > 1:
        with ThreadPoolExecutor(max_workers=min(6, len(rows))) as executor:
            futures = [executor.submit(resolve, row_info) for row_info in rows]
            for future in as_completed(futures):
                try:
                    resolved.append(future.result())
                except Exception:
                    logger.exception("[RUMAHOTP] operator lookup failed")
    else:
        for row_info in rows:
            try:
                resolved.append(resolve(row_info))
            except Exception:
                logger.exception("[RUMAHOTP] operator lookup failed")

    out = []
    for row, provider_id, price, stock, operators in resolved:
        for op in operators or []:
            opid = op.get("id")
            name = str(op.get("name") or "any").strip()
            if opid is None or not name:
                continue
            key = name.lower().replace("_", " ").strip()
            if key in {"any", "all", "auto", "automatic"}:
                continue
            out.append({
                "provider": "rumahotp",
                "country": str(item.get("number_id") or item.get("name")),
                "country_name": str(item.get("name") or country),
                "service": str(sid),
                "service_name": str(service),
                "operator": name,
                "provider_operator": name,
                "provider_id": str(provider_id),
                "server_id": str(row.get("server_id") or "2"),
                "pool": json.dumps({
                    "number_id": item.get("number_id"),
                    "provider_id": provider_id,
                    "operator_id": opid,
                    "server_id": row.get("server_id"),
                }, separators=(",", ":")),
                "cost_usd": price / float(KURS_DOLAR),
                "cost_idr": price,
                "stock": stock,
            })
    return out


def _metadata(pool):
    try: return json.loads(pool or "{}")
    except Exception: return {}


def buy_number(country, service, operator="any", metadata=None):
    found=find_service(service)
    if not found: return {"response":"ERROR", "error":"Layanan RumahOTP tidak ditemukan."}
    item=find_country(country, found.get("id"))
    if not item: return {"response":"ERROR", "error":"Negara RumahOTP tidak ditemukan."}
    meta=metadata or {}
    provider_id=meta.get("provider_id")
    operator_id=meta.get("operator_id")
    number_id=meta.get("number_id") or item.get("number_id")
    if not provider_id:
        rows=item.get("pricelist") or []
        active=[x for x in rows if isinstance(x,dict) and int(float(x.get("stock") or 0))>0 and float(x.get("price") or 0)>0]
        if not active: return {"response":"ERROR", "error":"Stok RumahOTP habis."}
        chosen=min(active,key=lambda x:float(x.get("price") or 0)); provider_id=chosen.get("provider_id")
    if not operator_id:
        operator_id=1
    data=_get("/v2/orders", {"number_id":number_id,"provider_id":provider_id,"operator_id":operator_id})
    if not data.get("success"):
        return {"response":"ERROR", "error":(data.get("error") or {}).get("message","RumahOTP order gagal.")}
    d=data.get("data") or {}
    return {"id":d.get("order_id"),"order_id":d.get("order_id"),"phone":d.get("phone_number"),"number":d.get("phone_number"),"service":d.get("service"),"country":d.get("country"),"operator":d.get("operator"),"created_at":d.get("created_at"),"expired_at":d.get("expired_at"),"response":"OK"}


def get_sms(order_id):
    data=_get("/v1/orders/get_status", {"order_id":order_id})
    if not data.get("success"): return {"response":"ERROR", "error":(data.get("error") or {}).get("message","Gagal mengecek RumahOTP.")}
    d=data.get("data") or {}; status=str(d.get("status") or "").lower()
    code=d.get("otp_code"); text=d.get("otp_msg") or ""
    return {
        "response":"OK",
        "sms": ([{"code":str(code),"text":text}] if code else []),
        "status":status,
        "created_at":d.get("created_at"),
        "expired_at":d.get("expired_at"),
        "phone":d.get("phone_number"),
        "service":d.get("service"),
        "country":d.get("country"),
    }


def resend_number(order_id):
    """Request a new OTP code while the RumahOTP order is still active."""
    data = _get("/v1/orders/set_status", {"order_id": order_id, "status": "resend"})
    if not data.get("success"):
        return {"response":"ERROR", "error":(data.get("error") or {}).get("message", "Resend OTP gagal."), "raw":data}
    payload=data.get("data") or {}
    status=str(payload.get("status") or "").strip().lower()
    return {"response":"OK", "provider_status":status, "raw":data}


def cancel_number(order_id):
    """Request cancellation and return the provider acknowledgement.

    RumahOTP exposes cancellation through /v1/orders/set_status.  A local
    refund must never be issued merely because the HTTP request completed;
    the API response must explicitly confirm the order was moved to a cancel
    state.  The caller can then safely decide whether to refund the user.
    """
    data = _get(
        "/v1/orders/set_status",
        {"order_id": order_id, "status": "cancel"},
    )

    if not data.get("success"):
        return {
            "response": "ERROR",
            "error": (data.get("error") or {}).get(
                "message", "Cancel RumahOTP gagal."
            ),
            "raw": data,
        }

    payload = data.get("data") or {}
    provider_status = str(payload.get("status") or "").strip().lower()

    # RumahOTP's set_status example returns status=cancel.  Accept both
    # 'cancel' and the status spelling used by get_status ('canceled').
    if provider_status not in {"cancel", "canceled", "cancelled"}:
        return {
            "response": "ERROR",
            "error": (
                "RumahOTP belum mengonfirmasi pembatalan "
                f"(status={provider_status or 'unknown'})."
            ),
            "provider_status": provider_status,
            "raw": data,
        }

    logger.info(
        "[RUMAHOTP] cancel confirmed order_id=%s status=%s",
        order_id,
        provider_status,
    )
    return {
        "response": "OK",
        "provider_status": provider_status,
        "raw": data,
    }


def get_cheapest_quote(country, service):
    """Return the cheapest live (in-stock) RumahOTP quote for a country/service."""
    quotes = get_quotes_for_country(country, service)
    active = [q for q in quotes if int(q.get("stock") or 0) > 0]
    if not active:
        return None
    return min(active, key=lambda x: float(x.get("cost_idr") or x.get("cost_usd") or 0))
