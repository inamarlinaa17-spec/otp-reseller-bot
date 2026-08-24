import os
import requests

from config import (
    KURS_DOLAR,
    PROFIT_PERCENT
)


# =========================================================
# SMSPOOL API
# =========================================================

BASE_URL = "https://api.smspool.net"

SMSPOOL_API_KEY = os.getenv(
    "SMSPOOL_API_KEY",
    ""
)


# =========================================================
# HEADERS
# =========================================================

def get_headers():

    return {
        "Accept": "application/json"
    }


# =========================================================
# REQUEST HELPER
# =========================================================

def post_request(
    endpoint,
    data=None,
    timeout=30
):

    if data is None:
        data = {}

    data["key"] = SMSPOOL_API_KEY

    try:

        response = requests.post(
            f"{BASE_URL}{endpoint}",
            data=data,
            headers=get_headers(),
            timeout=timeout
        )

        try:

            result = response.json()

        except Exception:

            result = {
                "success": 0,
                "message": response.text
            }

        return result

    except Exception as error:

        return {
            "success": 0,
            "type": "REQUEST_ERROR",
            "message": str(error)
        }


# =========================================================
# CHECK API
# =========================================================

def check_api():

    if not SMSPOOL_API_KEY:

        return {
            "success": False,
            "error":
                "SMSPOOL_API_KEY belum diatur."
        }

    result = post_request(
        "/request/balance"
    )

    if not result:

        return {
            "success": False,
            "error":
                "Respons SMSPOOL kosong."
        }

    return {
        "success": True,
        "data": result
    }


# =========================================================
# GET BALANCE
# =========================================================

def get_balance():

    if not SMSPOOL_API_KEY:

        return 0.0

    result = post_request(
        "/request/balance"
    )

    try:

        if isinstance(result, dict):

            for key in [
                "balance",
                "credit",
                "funds"
            ]:

                if key in result:

                    return float(
                        result[key]
                    )

    except Exception:

        pass

    return 0.0


# =========================================================
# GET COUNTRIES
# =========================================================

def get_all_countries():
    """Return the current SMSPool country list.

    SMSPool's current public API uses /country/retrieve_all.
    The old /country/list endpoint is not the documented endpoint.
    """
    try:
        response = requests.get(
            f"{BASE_URL}/country/retrieve_all",
            headers=get_headers(),
            timeout=20
        )
        response.raise_for_status()
        result = response.json()
        if isinstance(result, dict) and isinstance(result.get("data"), (list, dict)):
            return result.get("data")
        return result
    except Exception as error:
        print(f"[SMSPOOL] country list error: {error}")
        return []


# =========================================================
# GET SERVICES
# =========================================================

def get_services():
    """Return the current SMSPool service list."""
    try:
        response = requests.get(
            f"{BASE_URL}/service/retrieve_all",
            headers=get_headers(),
            timeout=20
        )
        response.raise_for_status()
        result = response.json()
        if isinstance(result, dict) and isinstance(result.get("data"), (list, dict)):
            return result.get("data")
        return result
    except Exception as error:
        print(f"[SMSPOOL] service list error: {error}")
        return []


# =========================================================
# GET SERVICE LIST
# =========================================================

def get_all_services():
    return get_services()


# =========================================================
# GET PRICES / STOCK
# =========================================================

def get_prices(
    country=None,
    service=None
):
    """Return SMSPool one-time SMS stock records.

    The current documented endpoint is /sms/all_stock.  It returns
    records containing country, service, pool, stock and price.
    Country and service are optional filters.
    """
    data = {}

    if country is not None and str(country).strip():
        data["country"] = str(country).strip()

    if service is not None and str(service).strip():
        data["service"] = str(service).strip()

    result = post_request(
        "/sms/all_stock",
        data=data,
        timeout=30
    )

    if not result:
        print(
            f"[SMSPOOL] all_stock empty: service={service!r} country={country!r}"
        )
        return []

    # API errors are dicts with success=0; callers should treat them
    # as an empty stock result rather than trying to parse them as rows.
    if isinstance(result, dict) and str(result.get("success", "1")) == "0":
        print(
            "[SMSPOOL] stock error: "
            f"{result.get('type') or result.get('message') or result}"
        )
        return []

    # Some SMSPool responses wrap the rows in {success: 1, data: [...]}.
    # Normalize that wrapper here so every caller sees the actual stock rows.
    if isinstance(result, dict):
        payload = result.get("data")
        if isinstance(payload, (list, dict)):
            return payload

    return result


# =========================================================
# SUGGESTED COUNTRIES PER SERVICE
# =========================================================

def get_suggested_countries(service):
    """Return SMSPool suggested countries with price for a service."""
    result = post_request(
        "/request/suggested_countries",
        data={"service": service},
        timeout=30
    )

    if isinstance(result, dict) and str(result.get("success", "1")) == "0":
        print(
            "[SMSPOOL] suggested countries error: "
            f"{result.get('type') or result.get('message') or result}"
        )
        return []

    return result if isinstance(result, list) else []


# =========================================================
# NORMALIZE SERVICE NAME
# =========================================================

def normalize_service_name(
    service
):

    if not service:

        return ""

    return str(
        service
    ).strip()


# =========================================================
# FIND SERVICE
# =========================================================

def find_service(
    service_name
):
    """Find an SMSPool service by exact/partial name or ID.

    SMSPool's current service-list response uses uppercase ``ID``.
    The old code only checked lowercase ``id``, so it could fail to
    resolve a service ID and then query stock with the wrong value.
    """
    services = get_services()

    if not services:
        return None

    target = normalize_service_name(service_name).lower()

    if isinstance(services, dict):
        iterable = services.items()
        for service_id, value in iterable:
            if isinstance(value, dict):
                sid = value.get("ID", value.get("id", value.get("service_id", service_id)))
                name = value.get("name", value.get("service", str(value)))
            else:
                sid = service_id
                name = value

            if str(sid).lower() == target or str(name).lower() == target:
                return {"id": sid, "name": str(name)}
            if target and target in str(name).lower():
                return {"id": sid, "name": str(name)}

    elif isinstance(services, list):
        for item in services:
            if not isinstance(item, dict):
                continue

            service_id = item.get(
                "ID",
                item.get("id", item.get("service_id"))
            )
            name = item.get(
                "name",
                item.get("service", "")
            )

            if str(service_id).lower() == target or str(name).lower() == target:
                return {"id": service_id, "name": str(name)}

            if target and target in str(name).lower():
                return {"id": service_id, "name": str(name)}

    return None


# =========================================================
# GET SERVICE INFO
# =========================================================

def get_service_info(
    service
):

    found = find_service(
        service
    )

    if not found:

        return None

    return found


# =========================================================
# CALCULATE SELL PRICE
# =========================================================

def hitung_harga_jual(
    harga_dolar
):

    try:

        harga_dolar = float(
            harga_dolar
        )

    except Exception:

        return 0

    if harga_dolar <= 0:

        return 0

    harga_modal_rp = (
        harga_dolar *
        KURS_DOLAR
    )

    keuntungan = (
        harga_modal_rp *
        (
            PROFIT_PERCENT /
            100
        )
    )

    harga_jual = (
        harga_modal_rp +
        keuntungan
    )

    # Pembulatan Rp100
    return int(
        round(
            harga_jual / 100
        ) * 100
    )


# =========================================================
# BUY NUMBER
# =========================================================

def buy_number(
    country,
    service,
    pool=None,
    max_price=None
):

    data = {
        "country":
            country,

        "service":
            service
    }

    if pool is not None:

        data["pool"] = pool

    if max_price is not None:

        data["max_price"] = max_price

    data["activation_type"] = "SMS"

    result = post_request(
        "/purchase/sms",
        data=data,
        timeout=30
    )

    if not result:

        return {
            "response":
                "ERROR",

            "message":
                "Respons SMSPOOL kosong."
        }


    # =====================================================
    # SUCCESS
    # =====================================================

    if str(
        result.get(
            "success",
            0
        )
    ) == "1":

        return {

            "response":
                "SUCCESS",

            "id":
                result.get(
                    "order_id"
                ),

            "phone":
                str(
                    result.get(
                        "number",
                        result.get(
                            "phonenumber",
                            ""
                        )
                    )
                ),

            "cost":
                result.get(
                    "cost",
                    result.get(
                        "price",
                        0
                    )
                ),

            "raw":
                result
        }


    # =====================================================
    # ERROR
    # =====================================================

    return {

        "response":
            "ERROR",

        "message":
            result.get(
                "message",
                result.get(
                    "error",
                    "Gagal membeli nomor SMSPool."
                )
            ),

        "raw":
            result
    }

# =========================================================
# CHECK ORDER / OTP
# =========================================================

def get_order(
    order_id
):

    result = post_request(
        "/request/active",
        data={
            "orderid":
                order_id
        }
    )

    if not result:

        return {
            "response":
                "ERROR",

            "message":
                "Respons SMSPOOL kosong."
        }

    return result


# =========================================================
# GET SMS / OTP
# =========================================================

def get_sms(
    order_id
):
    """Check one SMSPool order using the documented /sms/check endpoint."""
    result = post_request(
        "/sms/check",
        data={"orderid": order_id},
        timeout=20
    )

    if not result:
        return {
            "response": "ERROR",
            "message": "Respons SMSPOOL kosong."
        }

    if not isinstance(result, dict):
        return {
            "response": "WAITING",
            "raw": result
        }

    status = str(result.get("status", ""))

    # Current SMSPool status: 1=pending, 3=complete, 6=refunded.
    if status == "3":
        code = result.get("sms") or result.get("code") or result.get("otp")
        if code:
            return {
                "response": "SUCCESS",
                "code": str(code),
                "raw": result
            }

    if status == "6":
        return {
            "response": "ERROR",
            "message": result.get("message", "Order SMSPOOL telah direfund."),
            "raw": result
        }

    return {
        "response": "WAITING",
        "raw": result
    }


# =========================================================
# CANCEL ORDER
# =========================================================

def cancel_number(
    order_id
):

    result = post_request(
        "/sms/cancel",
        data={
            "orderid":
                order_id
        },
        timeout=30
    )

    if not result:

        return {

            "response":
                "ERROR",

            "message":
                "Respons SMSPOOL kosong."
        }


    success = str(
        result.get(
            "success",
            0
        )
    )


    if success == "1":

        return {

            "response":
                "SUCCESS",

            "raw":
                result
        }


    return {

        "response":
            "ERROR",

        "message":
            result.get(
                "message",
                result.get(
                    "error",
                    "Gagal membatalkan order."
                )
            ),

        "raw":
            result
    }


# =========================================================
# RELEASE ORDER
# =========================================================

def release_number(
    order_id
):

    result = post_request(
        "/request/release",
        data={
            "orderid":
                order_id
        },
        timeout=30
    )

    if not result:

        return {

            "response":
                "ERROR",

            "message":
                "Respons SMSPOOL kosong."
        }


    if str(
        result.get(
            "success",
            0
        )
    ) == "1":

        return {

            "response":
                "SUCCESS",

            "raw":
                result
        }


    return {

        "response":
            "ERROR",

        "message":
            result.get(
                "message",
                result.get(
                    "error",
                    "Gagal release order."
                )
            ),

        "raw":
            result
    }


# =========================================================
# GET AVAILABLE COUNTRIES FOR SERVICE
# =========================================================

def get_available_countries(
    service
):
    """Return countries with stock for a service using the stock endpoint."""
    found = find_service(service)
    lookup_service = found.get("id") if found and found.get("id") is not None else service
    rows = get_prices(service=lookup_service)

    if not isinstance(rows, list):
        return []

    result = []
    for item in rows:
        if not isinstance(item, dict):
            continue

        country_id = item.get("country")
        country_name = (
            item.get("country_name")
            or item.get("name")
            or item.get("short_name")
            or country_id
        )
        price = item.get("price", item.get("cost", item.get("amount")))
        stock = item.get("stock", item.get("count", item.get("available", 0)))

        try:
            price = float(price or 0)
            stock = int(stock or 0)
        except Exception:
            continue

        if country_id is not None and price > 0 and stock > 0:
            result.append({
                "country": str(country_id),
                "name": str(country_name),
                "price": price,
                "stock": stock,
                "pool": item.get("pool")
            })

    return result


# =========================================================
# GET AVAILABLE SERVICE COUNTRIES
# =========================================================

def get_service_countries(
    service
):

    return get_available_countries(
        service
    )


# =========================================================
# PROVIDER NAME
# =========================================================

def provider_name():

    return "SMSPOOL"


# =========================================================
# PROVIDER KEY
# =========================================================

def provider_key():

    return "smspool"
