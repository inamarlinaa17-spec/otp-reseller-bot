"""NusaOTP API v2 adapter for AZHURA Server 3.

Based on the NusaOTP API v2 documentation supplied for this integration:
- Base URL: https://nusaotp.site/api
- Bearer authentication
- GET /balance
- GET /v2/country
- GET /v2/services
- GET /v2/products?country_id=...&service_id=...
- POST /v2/order
- POST /v2/cancel
- POST /v2/set-webhook

NusaOTP documents realtime OTP delivery through webhook events. There is no
resend or completion endpoint in the supplied v2 documentation, so this
adapter deliberately does not invent one.
"""

import logging
import time

import requests

from config import NUSAOTP_API_KEY, NUSAOTP_WEBHOOK_URL, KURS_DOLAR

logger = logging.getLogger(__name__)

BASE_URL = "https://nusaotp.site/api"
TIMEOUT = 15
_CACHE_TTL = 30.0
_cache = {}


def _headers():
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if NUSAOTP_API_KEY:
        headers["Authorization"] = f"Bearer {NUSAOTP_API_KEY}"
    return headers


def _request(method, path, params=None, json_data=None):
    if not NUSAOTP_API_KEY:
        return {"success": False, "error": {"message": "NUSAOTP_API_KEY belum diatur."}}

    try:
        response = requests.request(
            method,
            f"{BASE_URL}{path}",
            headers=_headers(),
            params=params or {},
            json=json_data,
            timeout=TIMEOUT,
        )
        try:
            data = response.json()
        except Exception:
            data = {"success": False, "error": {"message": response.text[:500]}}

        if not isinstance(data, dict):
            data = {"success": False, "error": {"message": "Response NusaOTP tidak valid."}}

        logger.info(
            "[NUSAOTP] %s %s -> HTTP %s success=%s data_type=%s",
            method, path, response.status_code, data.get("success"),
            type(data.get("data")).__name__,
        )

        if response.status_code >= 400 and data.get("success") is not True:
            error = data.get("error")
            if isinstance(error, dict):
                message = error.get("message") or error.get("code") or f"HTTP {response.status_code}"
            else:
                message = str(error or f"HTTP {response.status_code}")
            data["error"] = {"message": message, "http": response.status_code}
            data["success"] = False

        return data
    except Exception as exc:
        logger.warning("[NUSAOTP] %s %s failed: %s", method, path, exc)
        return {"success": False, "error": {"message": str(exc)}}


def _cached(key):
    item = _cache.get(key)
    if item and time.monotonic() - item[0] < _CACHE_TTL:
        return item[1]
    return None


def _put_cache(key, value):
    _cache[key] = (time.monotonic(), value)
    return value


def _data_list(payload, kind=None):
    """Normalize NusaOTP collection responses.

    The documented examples use ``data: [...]``, but production responses can
    also arrive as a keyed object (for example ``{"1": {...}}``) or wrapped
    one level deeper.  Preserve the provider IDs from object keys so the bot
    never falls back to fake/static services or countries.
    """
    value = payload.get("data") if isinstance(payload, dict) else None

    def normalize(value):
        if isinstance(value, list):
            return value
        if not isinstance(value, dict):
            return []

        # A single provider object.
        if any(k in value for k in ("id", "service_id", "country_id", "product_id", "name", "service_name", "country_name", "product_name")):
            return [value]

        out = []
        for key, item in value.items():
            if isinstance(item, dict):
                item = dict(item)
                if kind == "service" and not any(item.get(k) is not None for k in ("id", "service_id")):
                    item["id"] = key
                elif kind == "country" and not any(item.get(k) is not None for k in ("id", "country_id")):
                    item["id"] = key
                elif kind == "product" and not any(item.get(k) is not None for k in ("id", "product_id")):
                    item["id"] = key
                out.append(item)
            elif isinstance(item, (str, int, float)):
                if kind == "service":
                    out.append({"id": key, "name": str(item)})
                elif kind == "country":
                    out.append({"id": key, "name": str(item)})
        return out

    items = normalize(value)
    if items:
        return items

    if isinstance(value, dict):
        for key in ("items", "countries", "services", "products", "data", "results"):
            nested = value.get(key)
            items = normalize(nested)
            if items:
                return items
    return []


def check_api():
    data = _request("GET", "/balance")
    return {
        "success": bool(data.get("success")),
        "data": data.get("data"),
        "error": data.get("error"),
    }


def get_balance():
    data = _request("GET", "/balance")
    value = data.get("data")
    if isinstance(value, dict):
        value = value.get("balance") or value.get("saldo") or value.get("amount")
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def get_countries():
    cached = _cached("countries")
    if cached is not None:
        return cached
    data = _request("GET", "/v2/country")
    if not data.get("success"):
        logger.warning("[NUSAOTP] countries unavailable: %s", data.get("error"))
        return []
    result = []
    for item in _data_list(data, kind="country"):
        if not isinstance(item, dict):
            continue
        cid = (
            item.get("id") or item.get("country_id") or item.get("countryId")
            or item.get("code") or item.get("country_code")
        )
        name = (
            item.get("name") or item.get("country_name") or item.get("countryName")
            or item.get("title") or item.get("label") or cid
        )
        if cid is None or not str(name).strip():
            continue
        result.append({
            **item,
            "id": str(cid),
            "name": str(name).strip(),
            "iso_code": str(item.get("iso_code") or item.get("iso") or item.get("code") or "").lower(),
        })
    return _put_cache("countries", result)


def get_services():
    cached = _cached("services")
    if cached is not None:
        return cached
    data = _request("GET", "/v2/services")
    if not data.get("success"):
        logger.warning("[NUSAOTP] services unavailable: %s", data.get("error"))
        return []
    result = []
    for item in _data_list(data, kind="service"):
        if not isinstance(item, dict):
            continue
        sid = (
            item.get("id") or item.get("service_id") or item.get("serviceId")
            or item.get("code") or item.get("service_code")
        )
        name = (
            item.get("name") or item.get("service_name") or item.get("serviceName")
            or item.get("title") or item.get("label") or sid
        )
        if sid is None or not str(name).strip():
            continue
        result.append({
            **item,
            "id": str(sid),
            "name": str(name).strip(),
        })
    return _put_cache("services", result)


def find_service(service):
    target = str(service or "").strip().lower()
    for item in get_services():
        if str(item.get("id") or "").strip().lower() == target:
            return item
        if str(item.get("name") or "").strip().lower() == target:
            return item
    return None


def find_country(country):
    target = str(country or "").strip().lower()
    for item in get_countries():
        values = {
            str(item.get("id") or "").strip().lower(),
            str(item.get("name") or "").strip().lower(),
            str(item.get("iso_code") or "").strip().lower(),
        }
        if target in values:
            return item
    return None


def get_products(country_id, service_id):
    key = f"products:{country_id}:{service_id}"
    cached = _cached(key)
    if cached is not None:
        return cached

    data = _request(
        "GET",
        "/v2/products",
        params={"country_id": country_id, "service_id": service_id},
    )
    if not data.get("success"):
        logger.warning(
            "[NUSAOTP] products unavailable country=%s service=%s: %s",
            country_id,
            service_id,
            data.get("error"),
        )
        return []

    result = []
    for item in _data_list(data, kind="product"):
        if not isinstance(item, dict):
            continue
        pid = item.get("id") or item.get("product_id") or item.get("productId")
        name = (
            item.get("name") or item.get("product_name") or item.get("productName")
            or item.get("title") or f"Product {pid}"
        )
        price = (
            item.get("price") or item.get("cost") or item.get("price_idr")
            or item.get("priceIdr") or item.get("selling_price")
        )
        try:
            price = float(price)
        except Exception:
            continue
        if pid is None or price <= 0:
            continue
        raw_stock = (
            item.get("stock") if item.get("stock") is not None else
            item.get("quantity") if item.get("quantity") is not None else
            item.get("count") if item.get("count") is not None else
            item.get("stock_count")
        )
        try:
            stock = int(float(raw_stock)) if raw_stock is not None else 1
        except Exception:
            stock = 1
        available = item.get("available")
        if available is None:
            available = item.get("is_available")
        if available is False:
            stock = 0
        result.append({
            **item,
            "id": str(pid),
            "name": str(name).strip(),
            "price_idr": price,
            "stock": max(stock, 0),
            "country_id": str(country_id),
            "service_id": str(service_id),
        })
    return _put_cache(key, result)


def get_quotes_for_country(country, service):
    country_item = find_country(country)
    service_item = find_service(service)

    # If the UI already carries a provider numeric ID, do not reject it just
    # because the catalog endpoint did not return the same object shape.
    country_id = str(country_item.get("id")) if country_item else str(country or "").strip()
    service_id = str(service_item.get("id")) if service_item else str(service or "").strip()
    if not country_id or not service_id:
        return []

    products = get_products(country_id, service_id)
    country_name = str((country_item or {}).get("name") or country)
    iso_code = str((country_item or {}).get("iso_code") or "").lower()
    service_name = str((service_item or {}).get("name") or service)

    result = []
    for product in products:
        price_idr = float(product.get("price_idr") or 0)
        stock = int(product.get("stock") or 0)
        if price_idr <= 0:
            continue
        result.append({
            "provider": "nusaotp",
            "country": country_id,
            "country_name": country_name,
            "iso_code": iso_code,
            "service": service_id,
            "service_name": service_name,
            "operator": "any",
            "provider_operator": "any",
            "product_id": str(product.get("id")),
            "product_name": str(product.get("name") or "Server"),
            "price_idr": price_idr,
            "cost_idr": price_idr,
            "cost_usd": price_idr / float(KURS_DOLAR),
            "stock": stock,
            "pool": {
                "country_id": country_id,
                "service_id": service_id,
                "product_id": str(product.get("id")),
            },
        })
    return result


def buy_number(country, service, operator="any", metadata=None):
    meta = metadata or {}
    country_id = str(meta.get("country_id") or country)
    service_id = str(meta.get("service_id") or service)
    product_id = meta.get("product_id")
    if not product_id:
        quotes = get_quotes_for_country(country_id, service_id)
        available = [q for q in quotes if int(q.get("stock") or 0) > 0]
        if not available:
            return {"response": "ERROR", "error": "Stok NusaOTP habis."}
        product_id = min(available, key=lambda q: float(q.get("cost_idr") or 0)).get("product_id")

    data = _request(
        "POST",
        "/v2/order",
        json_data={
            "country_id": int(country_id) if str(country_id).isdigit() else country_id,
            "service_id": int(service_id) if str(service_id).isdigit() else service_id,
            "product_id": int(product_id) if str(product_id).isdigit() else product_id,
        },
    )
    if not data.get("success"):
        error = data.get("error")
        if isinstance(error, dict):
            error = error.get("message") or error.get("code")
        return {"response": "ERROR", "error": str(error or "NusaOTP order gagal."), "raw": data}

    d = data.get("data") or {}
    return {
        "response": "OK",
        "order_id": d.get("order_id"),
        "id": d.get("order_id"),
        "phone": d.get("number"),
        "number": d.get("number"),
        "status": d.get("status"),
        "otp_code": d.get("otp_code"),
        "expired_at": d.get("expires_at") or d.get("expired_at"),
        "raw": data,
    }


def cancel_number(order_id):
    order_id = str(order_id or "").strip()
    if not order_id:
        return {"response": "ERROR", "error": "NusaOTP order ID kosong."}

    data = _request("POST", "/v2/cancel", json_data={"order_id": int(order_id) if order_id.isdigit() else order_id})
    if not data.get("success"):
        error = data.get("error")
        if isinstance(error, dict):
            error = error.get("message") or error.get("code")
        return {"response": "ERROR", "error": str(error or "Cancel NusaOTP gagal."), "raw": data}

    payload = data.get("data") or {}
    status = str(payload.get("status") or "").strip().upper()
    if status not in {"CANCELED", "CANCELLED", "CANCEL"}:
        return {
            "response": "ERROR",
            "error": f"Pembatalan belum dikonfirmasi: {status or 'status kosong'}",
            "raw": data,
        }
    return {
        "response": "OK",
        "provider_status": status,
        "refund": payload.get("refund"),
        "raw": data,
    }


def complete_number(order_id):
    # NusaOTP v2 documentation supplied for this integration does not expose
    # a completion endpoint. Completion is therefore local-only in main.py.
    return {"response": "OK", "provider_status": "LOCAL_ONLY"}


def resend_otp(order_id):
    # Deliberately do not invent a provider endpoint. The supplied NusaOTP v2
    # documentation lists order/cancel/webhook but no resend operation.
    return {
        "response": "ERROR",
        "error": "NusaOTP API v2 belum menyediakan endpoint Resend OTP pada dokumentasi yang digunakan.",
    }


def register_webhook():
    if not NUSAOTP_WEBHOOK_URL:
        logger.warning("[NUSAOTP] NUSAOTP_WEBHOOK_URL belum diatur; webhook OTP belum didaftarkan.")
        return {"success": False, "error": {"message": "NUSAOTP_WEBHOOK_URL belum diatur."}}
    data = _request(
        "POST",
        "/v2/set-webhook",
        json_data={"webhook_url": NUSAOTP_WEBHOOK_URL},
    )
    if data.get("success"):
        logger.info("[NUSAOTP] webhook registered: %s", NUSAOTP_WEBHOOK_URL)
    else:
        logger.warning("[NUSAOTP] webhook registration failed: %s", data.get("error"))
    return data
