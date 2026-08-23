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

    # -----------------------------------------------------
    # FORMAT DICT
    #
    # {
    #     "1": "WhatsApp",
    #     "2": "Telegram"
    # }
    # -----------------------------------------------------

    if isinstance(
        services,
        dict
    ):

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

    # -----------------------------------------------------
    # FORMAT LIST
    #
    # [
    #     {
    #         "id": 1,
    #         "name": "WhatsApp"
    #     }
    # ]
    # -----------------------------------------------------

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


    # -----------------------------------------------------
    # Normalisasi response
    # -----------------------------------------------------

    if isinstance(
        result,
        dict
    ):

        # OTP langsung
        for key in [
            "code",
            "otp",
            "sms",
            "sms_code"
        ]:

            value = result.get(
                key
            )

            if value:

                return {

                    "response":
                        "SUCCESS",

                    "code":
                        str(value),

                    "raw":
                        result
                }


        # SMSPool terkadang mengembalikan
        # SMS dalam field message/text.
        for key in [
            "message",
            "text"
        ]:

            value = result.get(
                key
            )

            if value:

                return {

                    "response":
                        "SMS",

                    "message":
                        str(value),

                    "raw":
                        result
                }


    # -----------------------------------------------------
    # BELUM ADA SMS
    # -----------------------------------------------------

    return {

        "response":
            "WAITING",

        "raw":
            result
    }


# =========================================================
# CANCEL ORDER
# =========================================================

def cancel_number(
    order_id
):

    result = post_request(
        "/request/cancel",
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

    countries = get_all_countries()

    if not countries:

        return []


    result = []


    # -----------------------------------------------------
    # Ambil daftar country
    # -----------------------------------------------------

    if isinstance(
        countries,
        dict
    ):

        iterable = (
            countries.items()
        )

        for country_id, country_info in iterable:

            country_name = str(
                country_info
            )

            price_data = get_prices(
                country=country_id,
                service=service
            )

            if not price_data:

                continue

            result.append({

                "country":
                    str(country_id),

                "name":
                    country_name,

                "price":
                    price_data

            })


    elif isinstance(
        countries,
        list
    ):

        for item in countries:

            if not isinstance(
                item,
                dict
            ):

                continue

            country_id = item.get(
                "id",
                item.get(
                    "country"
                )
            )

            country_name = item.get(
                "name",
                item.get(
                    "country_name",
                    str(country_id)
                )
            )

            if not country_id:

                continue

            price_data = get_prices(
                country=country_id,
                service=service
            )

            if not price_data:

                continue

            result.append({

                "country":
                    str(country_id),

                "name":
                    str(country_name),

                "price":
                    price_data

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
