import base64
import io
import os
import threading
import time
from urllib.parse import urlparse

import requests

BASE_URL = os.getenv("PREMOTP_BASE_URL", "https://premotp.biz.id/api/v1").rstrip("/")
API_KEY = os.getenv("PREMOTP_API_KEY", "").strip()


def _request(path, method="GET", payload=None, timeout=20):
    if not API_KEY:
        raise RuntimeError("PREMOTP_API_KEY belum diatur di Railway.")
    r = requests.request(
        method,
        BASE_URL + path,
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    try:
        data = r.json()
    except Exception:
        data = {"success": False, "error": {"message": r.text or "Respons PremOTP tidak valid."}}
    if not r.ok or not data.get("success"):
        err = data.get("error") or {}
        raise RuntimeError(str(err.get("message") or f"PremOTP HTTP {r.status_code}"))
    return data.get("data") or {}


# Cache hanya katalog layanan Server 3. Endpoint negara, harga, order,
# QRIS, dan status tetap memakai jalur API semula (data live).
_SERVICES_CACHE_TTL = 300  # 5 menit
_services_cache = {}
_services_cache_lock = threading.Lock()


def get_services(service_type="regular"):
    current = time.monotonic()
    cached = _services_cache.get(service_type)
    if cached and current - cached[0] < _SERVICES_CACHE_TTL:
        return cached[1]

    # Satu permintaan API saja ketika beberapa user membuka Server 3 bersamaan.
    with _services_cache_lock:
        cached = _services_cache.get(service_type)
        if cached and time.monotonic() - cached[0] < _SERVICES_CACHE_TTL:
            return cached[1]
        try:
            result = _request(f"/catalog/services?type={service_type}", timeout=10)
        except Exception:
            # Katalog terakhir tetap bisa dinavigasi saat API sedang lambat.
            # Negara/harga/stok tetap diperiksa langsung pada tahap berikutnya.
            if cached:
                return cached[1]
            raise
        if result:
            _services_cache[service_type] = (time.monotonic(), result)
        return result


def get_countries(service_key, service_type="regular"):
    from urllib.parse import quote
    return _request(f"/catalog/countries?service_key={quote(str(service_key))}&type={service_type}")


def get_offers(service_key, country_key, service_type="regular"):
    from urllib.parse import quote
    return _request(
        f"/catalog/offers?service_key={quote(str(service_key))}"
        f"&country_key={quote(str(country_key))}&type={service_type}"
    )


def create_order(ref_id, service_key, country_key, offer_id, order_type="regular"):
    return _request("/orders", "POST", {
        "ref_id": ref_id,
        "type": order_type,
        "service_key": service_key,
        "country_key": country_key,
        "offer_id": offer_id,
    })


def get_order(order_id):
    return _request(f"/orders/{order_id}")


def resend_order(order_id):
    return _request(f"/orders/{order_id}/resend", "POST")


def cancel_order(order_id):
    return _request(f"/orders/{order_id}/cancel", "POST")


def create_qris(ref_id, amount):
    return _request("/qris", "POST", {"ref_id": ref_id, "amount": int(amount)})


def get_qris(qris_id):
    return _request(f"/qris/{qris_id}")


def _first(data, keys):
    if not isinstance(data, dict):
        return None
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None


def qris_id(data):
    return _first(data, ["id", "qris_id", "transaction_id", "payment_id", "invoice_id"])


def qris_ref_id(data):
    return _first(data, ["ref_id", "reference", "reference_id", "merchant_ref", "external_id"])


def qris_amount(data):
    value = _first(data, ["amount", "paid_amount", "gross_amount"])
    try:
        return int(float(value)) if value is not None else None
    except Exception:
        return None


def qris_total_payment(data):
    value = _first(data, ["total_payment", "total", "payment_amount", "gross_amount"])
    try:
        return int(float(value)) if value is not None else None
    except Exception:
        return None


def extract_qr_payload(data):
    """Return a QR payload from common response field names used by QRIS APIs."""
    if not isinstance(data, dict):
        return None
    keys = [
        "qr_image", "qr_image_url", "qr_url", "qr_code", "qr_string",
        "qr_content", "qr_data", "qr", "image_url", "payment_qr",
        "payment_url", "checkout_url",
    ]
    value = _first(data, keys)
    if isinstance(value, dict):
        value = _first(value, keys + ["url", "content", "data"])
    return value


def qr_image_bytes(data):
    """Build/download QR image bytes when the API response exposes a URL, data URI, or QR string."""
    value = extract_qr_payload(data)
    if not value:
        return None
    value = str(value).strip()
    if value.startswith("data:image/") and "," in value:
        try:
            return base64.b64decode(value.split(",", 1)[1])
        except Exception:
            return None
    if value.startswith("http://") or value.startswith("https://"):
        try:
            r = requests.get(value, timeout=20)
            r.raise_for_status()
            content_type = str(r.headers.get("Content-Type") or "").lower()
            if content_type.startswith("image/"):
                return r.content
            # If the API exposes a payment/checkout URL instead of an image,
            # encode that URL as a QR so Telegram can still display a scannable code.
            value = value
        except Exception:
            pass
        try:
            import qrcode
            img = qrcode.make(value)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            return None
    # QR payload/string: generate a PNG locally.
    try:
        import qrcode
        img = qrcode.make(value)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None
