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
            "error": "SMSPOOL_API_KEY belum diatur."
        }

    result = post_request(
        "/request/balance"
    )

    if not result:

        return {
            "success": False,
            "error": "Respons SMSPOOL kosong."
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

        # Beberapa response SMSPool
        # mengembalikan balance langsung.
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

    result = post_request(
        "/country/list"
    )

    if not result:

        return {}

    return result


# =========================================================
# GET SERVICES
# =========================================================

def get_services():

    result = post_request(
        "/service/list"
    )

    if not result:

        return {}

    return result


# =========================================================
# GET SERVICE LIST
# =========================================================

def get_all_services():

    return get_services()


# =========================================================
# GET PRICES
# =========================================================

def get_prices(
    country=None,
    service=None
):

    data = {}

    if country:

        data["country"] = country

    if service:

        data["service"] = service

    result = post_request(

        "/request/price",

        data=data

    )

    if not result:

        return {}

    return result


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

    services = get_services()

    if not services:

        return None

    target = (
        normalize_service_name(
            service_name
        ).lower()
    )

    if isinstance(
        services,
        dict
    ):

        # Format:
        # {
        #   "1": "WhatsApp",
        #   ...
        # }

        for service_id, name in (
            services.items()
        ):

            if str(
                name
            ).lower() == target:

                return {
                    "id":
                        service_id,

                    "name":
                        name
                }

            if target in str(
                name
            ).lower():

                return {
                    "id":
                        service_id,

                    "name":
                        name
                }

    elif isinstance(
        services,
        list
    ):

        for item in services:

            if not isinstance(
                item,
                dict
            ):

                continue

            name = str(
                item.get(
                    "name",
                    item.get(
                        "service",
                        ""
                    )
                )
            )

            service_id = item.get(
                "id",
                item.get(
                    "service_id"
                )
            )

            if name.lower() == target:

                return {
                    "id":
                        service_id,

                    "name":
                        name
                }

            if target in name.lower():

                return {
                    "id":
                        service_id,

                    "name":
                        name
                }

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

        harga_dolar
        *
        KURS_DOLAR

    )

    keuntungan = (

        harga_modal_rp
        *
        (
            PROFIT_PERCENT
            /
            100
        )

    )

    harga_jual = (

        harga_modal_rp
        +
        keuntungan

    )

    return int(

        round(
            harga_jual / 100
        )
        *
        100

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

    # -----------------------------------------------------
    # BERHASIL
    # -----------------------------------------------------

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

            "order_id":
                result.get(
                    "order_id"
                ),

            "country":
                result.get(
                    "country"
                ),

            "service":
                result.get(
                    "service"
                ),

            "cost":
                result.get(
                    "cost",
                    0
                ),

            "raw":
                result

        }

    # -----------------------------------------------------
    # GAGAL
    # -----------------------------------------------------

    return {

        "response":
            "ERROR",

        "message":
            result

    }


# =========================================================
# CHECK OTP
# =========================================================

def get_sms(
    order_id
):

    result = post_request(

        "/sms/check",

        data={

            "orderid":
                order_id

        },

        timeout=20

    )

    if not result:

        return {

            "response":
                "ERROR",

            "message":
                "Respons SMSPOOL kosong."

        }

    try:

        status = int(
            result.get(
                "status",
                0
            )
        )

    except Exception:

        status = 0

    # -----------------------------------------------------
    # COMPLETE
    # -----------------------------------------------------

    if status == 3:

        code = result.get(
            "sms",
            ""
        )

        full_sms = result.get(
            "full_sms",
            ""
        )

        return {

            "response":
                "SUCCESS",

            "status":
                status,

            "sms": [

                {

                    "code":
                        str(code),

                    "text":
                        str(
                            full_sms
                        )

                }

            ],

            "code":
                str(code),

            "full_sms":
                str(full_sms),

            "raw":
                result

        }

    # -----------------------------------------------------
    # REFUNDED
    # -----------------------------------------------------

    if status == 6:

        return {

            "response":
                "REFUNDED",

            "status":
                status,

            "message":
                result.get(
                    "message",
                    "Order sudah direfund."
                ),

            "raw":
                result

        }

    # -----------------------------------------------------
    # PENDING
    # -----------------------------------------------------

    return {

        "response":
            "PENDING",

        "status":
            status,

        "sms": [],

        "time_left":
            result.get(
                "time_left"
            ),

        "expiration":
            result.get(
                "expiration"
            ),

        "raw":
            result

    }


# =========================================================
# CANCEL NUMBER
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

        timeout=20

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

            "message":
                result.get(
                    "message",
                    "Order berhasil dibatalkan."
                ),

            "raw":
                result

        }

    return {

        "response":
            "ERROR",

        "message":
            result

    }


# =========================================================
# ACTIVE ORDERS
# =========================================================

def get_active_orders():

    result = post_request(

        "/request/active",

        timeout=20

    )

    if not result:

        return []

    if isinstance(
        result,
        list
    ):

        return result

    return []


# =========================================================
# GET ORDER
# =========================================================

def get_order(
    order_id
):

    result = post_request(

        "/sms/check",

        data={

            "orderid":
                order_id

        },

        timeout=20

    )

    return result


# =========================================================
# PROVIDER NAME
# =========================================================

def get_provider_name():

    return "SMSPOOL"
