import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import requests

from config import KURS_DOLAR

logger = logging.getLogger(__name__)

BASE_URL = "https://nokosnesia.com/api/v1"

_SERVICE_CACHE_TTL = 120.0
_COUNTRY_CACHE_TTL = 600.0
_PRICE_CACHE_TTL = 10.0
_SERVICE_COUNTRY_CACHE_TTL = 60.0
_cache_lock = threading.Lock()
_service_cache = None
_service_cache_at = 0.0
_country_cache = None
_country_cache_at = 0.0
_price_cache = {}
_service_country_cache = {}


def _api_key():
    return os.getenv("NOKOS_API_KEY", "").strip()


def _headers():
    key = _api_key()
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _request(method, path, *, params=None, data=None, timeout=20):
    try:
        response = requests.request(
            method,
            f"{BASE_URL}/{path.lstrip('/')}",
            params=params or {},
            json=data if data is not None else None,
            headers=_headers(),
            timeout=timeout,
        )
        try:
            payload = response.json()
        except Exception:
            payload = {"success": False, "error": response.text[:500] or f"HTTP {response.status_code}"}
        if not response.ok:
            if isinstance(payload, dict):
                payload.setdefault("success", False)
                payload.setdefault("error", f"HTTP {response.status_code}")
                return payload
            return {"success": False, "error": f"HTTP {response.status_code}"}
        return payload if isinstance(payload, dict) else {"success": False, "error": "Respons API tidak valid."}
    except Exception as exc:
        logger.warning("[NOKOSNESIA] %s %s failed: %s", method, path, exc)
        return {"success": False, "error": str(exc)}


def _data_list(result):
    if not isinstance(result, dict):
        return []
    data = result.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "products", "services", "countries", "operators", "platforms", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def _int_value(*values, default=0):
    for value in values:
        try:
            if value is not None and str(value).strip() != "":
                return int(float(value))
        except Exception:
            continue
    return default


def _float_value(*values, default=0.0):
    for value in values:
        try:
            if value is not None and str(value).strip() != "":
                return float(value)
        except Exception:
            continue
    return default


def _service_id(service):
    target = str(service or "").strip().lower()
    for item in get_services() or []:
        if str(item.get("service_code") or "").strip().lower() == target:
            try:
                return int(item.get("service_id") or item.get("platform_id") or item.get("service_code"))
            except Exception:
                return None
    try:
        return int(service)
    except Exception:
        return None


def _country_name(country_id):
    target = str(country_id or "").strip()
    for item in get_countries() or []:
        if str(item.get("country") or "").strip() == target:
            return str(item.get("name") or target)
    return target


def check_api():
    return _request("GET", "balance", timeout=15)


def get_balance():
    result = check_api()
    data = result.get("data") if isinstance(result, dict) else {}
    return _float_value(
        data.get("balance_idr") if isinstance(data, dict) else None,
        data.get("balance") if isinstance(data, dict) else None,
        default=0.0,
    )


def get_services():
    global _service_cache, _service_cache_at
    now = time.monotonic()
    with _cache_lock:
        if _service_cache is not None and now - _service_cache_at < _SERVICE_CACHE_TTL:
            return list(_service_cache)

    result = _request("GET", "catalog/services", timeout=20)
    services = []
    for item in _data_list(result):
        if not isinstance(item, dict):
            continue
        sid = item.get("id")
        platform_id = item.get("platform_id") or item.get("platformId") or sid
        code = str(platform_id if platform_id is not None else sid or "").strip()
        name = str(item.get("name") or item.get("service_name") or item.get("platform_name") or item.get("title") or code).strip()
        if not code:
            continue
        services.append({
            "service_code": code,
            "service_id": sid if sid is not None else platform_id,
            "platform_id": platform_id,
            "service_name": name,
        })

    with _cache_lock:
        _service_cache = list(services)
        _service_cache_at = time.monotonic()
    return services


def get_countries():
    global _country_cache, _country_cache_at
    now = time.monotonic()
    with _cache_lock:
        if _country_cache is not None and now - _country_cache_at < _COUNTRY_CACHE_TTL:
            return list(_country_cache)

    result = _request("GET", "catalog/countries", timeout=20)
    countries = []
    for item in _data_list(result):
        if not isinstance(item, dict):
            continue
        cid = item.get("id")
        if cid is None:
            cid = item.get("country_id")
        if cid is None:
            continue
        name = str(item.get("name") or item.get("country_name") or cid).strip()
        countries.append({
            "country": str(cid).strip(),
            "name": name,
            "prefix": item.get("prefix") or item.get("dial_code"),
            "iso_code": str(item.get("iso") or item.get("iso_code") or "").strip().lower(),
        })

    with _cache_lock:
        _country_cache = list(countries)
        _country_cache_at = time.monotonic()
    return countries


def _get_products(*, country_id=None, platform_id=None, operator_id=None, page=1, limit=100):
    params = {"page": int(page), "limit": int(limit), "sort": "price_asc"}
    if country_id not in (None, ""):
        params["country_id"] = country_id
    if platform_id not in (None, ""):
        params["platform_id"] = platform_id
    if operator_id not in (None, ""):
        params["operator_id"] = operator_id
    key = tuple(sorted((str(k), str(v)) for k, v in params.items()))
    now = time.monotonic()
    with _cache_lock:
        cached = _price_cache.get(key)
        if cached and now - cached[0] < _PRICE_CACHE_TTL:
            return cached[1]
    result = _request("GET", "catalog/products", params=params, timeout=8)
    with _cache_lock:
        _price_cache[key] = (time.monotonic(), result)
    return result


def _product_rows(*, country_id=None, platform_id=None, operator_id=None, max_pages=2):
    rows = []
    for page in range(1, max_pages + 1):
        result = _get_products(
            country_id=country_id,
            platform_id=platform_id,
            operator_id=operator_id,
            page=page,
            limit=100,
        )
        batch = _data_list(result)
        if not batch:
            break
        rows.extend(x for x in batch if isinstance(x, dict))
        if len(batch) < 100:
            break
    return rows


def _normalize_product(item):
    if not isinstance(item, dict):
        return None
    product_id = item.get("id")
    catalog_product_id = item.get("catalog_product_id") or item.get("catalogProductId") or product_id
    if catalog_product_id is None:
        return None
    country_id = item.get("country_id") or item.get("countryId")
    platform_id = item.get("platform_id") or item.get("platformId")
    operator_id = item.get("operator_id") or item.get("operatorId")
    stock = _int_value(item.get("quantity"), item.get("available"), item.get("stock"), item.get("count"), default=0)
    price_idr = _float_value(item.get("nokosnesia_price_idr"), item.get("price_idr"), item.get("priceIdr"), default=0.0)
    price_obj = item.get("price") if isinstance(item.get("price"), dict) else {}
    price_usd = _float_value(price_obj.get("amount"), item.get("cost_usd"), default=0.0)
    if price_idr <= 0 and price_usd > 0:
        price_idr = price_usd * float(KURS_DOLAR)
    if price_idr <= 0:
        return None
    return {
        "product_id": product_id,
        "catalog_product_id": catalog_product_id,
        "country_id": country_id,
        "platform_id": platform_id,
        "operator_id": operator_id,
        "operator_name": str(item.get("operator_name") or item.get("operator") or "Any").strip(),
        "name": str(item.get("name") or "").strip(),
        "stock": max(stock, 0),
        "cost_idr": float(price_idr),
        "cost": float(price_idr) / float(KURS_DOLAR),
    }


def get_service_countries(service):
    platform_id = _service_id(service)
    if platform_id is None:
        return []
    cache_key = str(platform_id)
    now = time.monotonic()
    with _cache_lock:
        cached = _service_country_cache.get(cache_key)
        if cached and now - cached[0] < _SERVICE_COUNTRY_CACHE_TTL:
            return [dict(x) for x in cached[1]]
    countries = {str(x.get("country")): x for x in get_countries() or []}
    grouped = {}
    for item in _product_rows(platform_id=platform_id):
        product = _normalize_product(item)
        if not product or product["stock"] <= 0:
            continue
        cid = str(product.get("country_id") or "").strip()
        if not cid:
            continue
        base = countries.get(cid, {"name": cid, "iso_code": ""})
        row = grouped.setdefault(cid, {
            "country": cid,
            "name": base.get("name") or cid,
            "iso_code": base.get("iso_code") or "",
            "prefix": base.get("prefix"),
            "cost": product["cost"],
            "cost_idr": product["cost_idr"],
            "stock": 0,
        })
        row["stock"] += product["stock"]
        if product["cost_idr"] < row["cost_idr"]:
            row["cost_idr"] = product["cost_idr"]
            row["cost"] = product["cost"]
    result = sorted(grouped.values(), key=lambda x: str(x.get("name") or "").lower())
    with _cache_lock:
        _service_country_cache[cache_key] = (time.monotonic(), [dict(x) for x in result])
    return result


def get_operators(country, service):
    """Return live Nokosnesia operators for a country/service."""
    platform_id = _service_id(service)
    if platform_id is None:
        return []
    result = _request(
        "GET",
        "catalog/operators",
        params={"country_id": country, "platform_id": platform_id},
        timeout=8,
    )
    rows = []
    for item in _data_list(result):
        if not isinstance(item, dict):
            continue
        oid = item.get("id") or item.get("operator_id")
        name = str(item.get("name") or item.get("operator_name") or item.get("title") or "").strip()
        if oid is None or not name:
            continue
        rows.append({"id": oid, "name": name})
    return rows


def get_price_options(country, service, operator_id=None):
    platform_id = _service_id(service)
    if platform_id is None:
        return []
    rows = []
    for item in _product_rows(country_id=country, platform_id=platform_id, operator_id=operator_id):
        product = _normalize_product(item)
        if not product or product["stock"] <= 0:
            continue
        product["country_name"] = _country_name(product.get("country_id") or country)
        rows.append(product)
    rows.sort(key=lambda x: (float(x.get("cost_idr") or 0), -int(x.get("stock") or 0)))
    return rows


def _extract_order_data(result):
    if not isinstance(result, dict):
        return {}
    data = result.get("data")
    return data if isinstance(data, dict) else {}


def buy_number(country, service, operator="any", product=None, idempotency_key=None):
    """Create a Nokosnesia order using a concrete catalog product from the quote."""
    product = product if isinstance(product, dict) else {}
    catalog_product_id = product.get("catalog_product_id") or product.get("product_id")
    if catalog_product_id is None:
        return {"response": "ERROR", "error": "Produk Nokosnesia tidak ditemukan."}

    body = {
        "catalog_product_id": int(catalog_product_id) if str(catalog_product_id).isdigit() else catalog_product_id,
        "service_name": product.get("service_name") or str(service),
        "country_name": product.get("country_name") or _country_name(country),
        "country_id": int(country) if str(country).isdigit() else country,
    }
    platform_id = product.get("platform_id") or _service_id(service)
    if platform_id is not None:
        body["platform_id"] = int(platform_id) if str(platform_id).isdigit() else platform_id
    operator_id = product.get("operator_id")
    if operator_id not in (None, "", "Any", "any"):
        try:
            body["operator_id"] = int(operator_id)
        except Exception:
            pass

    result = _request("POST", "orders/create", data=body, timeout=20)
    if not result.get("success"):
        return {"response": "ERROR", "error": result.get("error") or "Nokosnesia gagal membuat order."}

    data = _extract_order_data(result)
    provider_order_id = data.get("id") or data.get("order_id")
    phone = data.get("phone_number") or data.get("phone") or data.get("number")
    if not provider_order_id or not phone:
        return {"response": "ERROR", "error": "Respons order Nokosnesia tidak lengkap."}

    expired_at = data.get("expired_at") or data.get("expires_at") or data.get("expires")
    if not expired_at:
        expired_at = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()

    price_idr = _float_value(data.get("price_idr"), product.get("cost_idr"), default=0.0)
    return {
        "response": "OK",
        "id": provider_order_id,
        "order_id": provider_order_id,
        "phone": phone,
        "expired_at": expired_at,
        "price_idr": price_idr,
        "status": data.get("status"),
    }


def get_sms(activation_id):
    result = _request("GET", f"orders/{activation_id}", timeout=15)
    if not result.get("success"):
        return {"response": "ERROR", "error": result.get("error") or "Gagal mengambil status Nokosnesia."}
    data = _extract_order_data(result)
    status = str(data.get("status") or "").upper()
    code = data.get("otp_code") or data.get("code")
    if code:
        return {
            "response": "OK",
            "status": status,
            "code": code,
            "sms": data.get("sms") or data.get("text") or "",
            "expired_at": data.get("expired_at") or data.get("expires_at"),
        }
    return {
        "response": "WAITING",
        "status": status,
        "expired_at": data.get("expired_at") or data.get("expires_at"),
    }


def resend_otp(activation_id):
    result = _request("POST", f"orders/{activation_id}/resend", timeout=15)
    if not result.get("success"):
        return {"response": "ERROR", "error": result.get("error") or "Nokosnesia tidak menerima request resend."}
    return {"response": "OK", "status": _extract_order_data(result).get("status")}


def complete_number(activation_id):
    result = _request("POST", f"orders/{activation_id}/finish", timeout=15)
    if not result.get("success"):
        return {"response": "ERROR", "error": result.get("error") or "Nokosnesia tidak menerima penyelesaian order."}
    return {"response": "OK", "status": _extract_order_data(result).get("status")}


def cancel_number(activation_id):
    result = _request("POST", f"orders/{activation_id}/cancel", timeout=15)
    if result.get("success"):
        return {"response": "OK", "status": _extract_order_data(result).get("status")}
    error = str(result.get("error") or "")
    # A 409/terminal provider response can mean the order is already finished,
    # cancelled, or otherwise no longer active. Check once before reporting
    # failure so AZHURA does not refund against an order that is still active.
    status_check = get_sms(activation_id)
    status = str((status_check or {}).get("status") or "").upper()
    if status in {"CANCELLED", "CANCELED", "CANCEL", "EXPIRED", "FINISHED", "COMPLETED", "REFUNDED"}:
        return {"response": "OK", "status": status}
    return {"response": "ERROR", "error": error or "Pembatalan Nokosnesia belum terkonfirmasi."}
