import requests

from config import (
    FIVESIM_API_KEY,
    KURS_DOLAR,
    PROFIT_PERCENT
)


# =========================================================
# 5SIM API
# =========================================================

BASE_URL = "https://5sim.net/v1"


# =========================================================
# HEADERS
# =========================================================

def get_headers():

    if not FIVESIM_API_KEY:

        return {
            "Accept": "application/json"
        }

    return {
        "Authorization":
            f"Bearer {FIVESIM_API_KEY}",

        "Accept":
            "application/json"
    }


# =========================================================
# CHECK API / PROFILE
# =========================================================

def check_api():

    try:

        response = requests.get(
            f"{BASE_URL}/user/profile",
            headers=get_headers(),
            timeout=15
        )

        response.raise_for_status()

        return {
            "success": True,
            "data": response.json()
        }

    except Exception as error:

        return {
            "success": False,
            "error": str(error)
        }


# =========================================================
# GET BALANCE 5SIM
# =========================================================

def get_balance():

    try:

        response = requests.get(
            f"{BASE_URL}/user/profile",
            headers=get_headers(),
            timeout=15
        )

        response.raise_for_status()

        data = response.json()

        return float(
            data.get(
                "balance",
                0
            )
        )

    except Exception:

        return 0


# =========================================================
# GET COUNTRIES
# =========================================================

def get_all_countries():

    try:

        response = requests.get(
            f"{BASE_URL}/guest/countries",
            headers={
                "Accept": "application/json"
            },
            timeout=20
        )

        response.raise_for_status()

        return response.json()

    except Exception:

        return {}


# =========================================================
# GET PRODUCTS
# =========================================================

def get_products(
    country,
    operator="any"
):

    try:

        response = requests.get(
            f"{BASE_URL}/guest/products/"
            f"{country}/{operator}",
            headers={
                "Accept": "application/json"
            },
            timeout=20
        )

        response.raise_for_status()

        return response.json()

    except Exception:

        return {}


# =========================================================
# GET ALL PRODUCTS
# =========================================================

def get_all_products():

    try:

        response = requests.get(
            f"{BASE_URL}/guest/products",
            headers={
                "Accept": "application/json"
            },
            timeout=20
        )

        response.raise_for_status()

        return response.json()

    except Exception:

        return {}


# =========================================================
# GET PRICES
# =========================================================

def get_prices(
    country=None,
    product=None
):
    """Get 5SIM guest prices.

    This endpoint is public and supports filtering by product/country.
    Errors are logged so Railway logs show the real provider problem
    instead of silently turning an API failure into "stock empty".
    """
    try:
        params = {}

        if country:
            params["country"] = country

        if product:
            params["product"] = product

        response = requests.get(
            f"{BASE_URL}/guest/prices",
            headers={
                "Accept": "application/json"
            },
            params=params,
            timeout=15
        )

        if response.status_code != 200:
            print(
                "[5SIM] prices error "
                f"HTTP {response.status_code}: {response.text[:500]}"
            )
            return {}

        data = response.json()

        if not isinstance(data, dict):
            print(
                "[5SIM] prices returned unexpected format: "
                f"{type(data).__name__}"
            )
            return {}

        return data

    except Exception as error:
        print(f"[5SIM] prices request error: {error}")
        return {}


# =========================================================
# FIND CHEAPEST AVAILABLE OPERATOR
# =========================================================

def get_cheapest_operator(
    country,
    product
):

    try:

        data = get_prices(
            country=country,
            product=product
        )

        country_data = data.get(
            country,
            {}
        )

        product_data = country_data.get(
            product,
            {}
        )

        available = []

        for operator, info in product_data.items():

            if not isinstance(
                info,
                dict
            ):
                continue

            try:

                cost = float(
                    info.get(
                        "cost",
                        0
                    )
                )

                count = int(
                    info.get(
                        "count",
                        0
                    )
                )

            except Exception:

                continue

            if cost <= 0:
                continue

            if count <= 0:
                continue

            available.append({
                "operator": operator,
                "cost": cost,
                "count": count
            })

        if not available:

            return None

        available.sort(
            key=lambda item:
                item["cost"]
        )

        return available[0]

    except Exception:

        return None


# =========================================================
# CALCULATE SELL PRICE
# =========================================================

def hitung_harga_jual(
    harga_dolar
):

    harga_dolar = float(
        harga_dolar
    )

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

    # Dibulatkan ke Rp100
    return int(
        round(
            harga_jual /
            100
        ) * 100
    )


# =========================================================
# BUY NUMBER
# =========================================================

def buy_number(
    country,
    product,
    operator="any"
):

    try:

        url = (
            f"{BASE_URL}/user/buy/"
            f"activation/"
            f"{country}/"
            f"{operator}/"
            f"{product}"
        )

        response = requests.get(
            url,
            headers=get_headers(),
            timeout=30
        )

        if response.status_code != 200:

            try:
                data = response.json()

            except Exception:

                data = {
                    "message":
                        response.text
                }

            return {
                "response":
                    "ERROR",

                "message":
                    data
            }

        return response.json()

    except Exception as error:

        return {
            "response":
                "ERROR",

            "message":
                str(error)
        }


# =========================================================
# CHECK OTP
# =========================================================

def get_sms(
    order_id
):

    try:

        response = requests.get(
            f"{BASE_URL}/user/check/"
            f"{order_id}",
            headers=get_headers(),
            timeout=20
        )

        if response.status_code != 200:

            try:
                data = response.json()

            except Exception:

                data = {
                    "message":
                        response.text
                }

            return {
                "response":
                    "ERROR",

                "message":
                    data
            }

        return response.json()

    except Exception as error:

        return {
            "response":
                "ERROR",

            "message":
                str(error)
        }


# =========================================================
# CANCEL NUMBER
# =========================================================

def cancel_number(
    order_id
):

    try:

        response = requests.get(
            f"{BASE_URL}/user/cancel/"
            f"{order_id}",
            headers=get_headers(),
            timeout=20
        )

        if response.status_code != 200:

            try:
                data = response.json()

            except Exception:

                data = {
                    "message":
                        response.text
                }

            return {
                "response":
                    "ERROR",

                "message":
                    data
            }

        return response.json()

    except Exception as error:

        return {
            "response":
                "ERROR",

            "message":
                str(error)
        }
