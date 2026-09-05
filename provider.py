import requests
import time
import threading

from config import (
    FIVESIM_API_KEY,
    KURS_DOLAR,
    PROFIT_PERCENT
)


# =========================================================
# 5SIM API
# =========================================================

BASE_URL = "https://5sim.net/v1"

# Short-lived caches reduce repeated catalog calls when users navigate
# service -> country -> price. Provider data is refreshed frequently so
# price/stock does not become stale for long.
_CACHE_TTL = 15.0
_price_cache = {}
_price_cache_lock = threading.Lock()

def _cached_prices(key):
    with _price_cache_lock:
        item = _price_cache.get(key)
        if item and time.monotonic() - item[0] < _CACHE_TTL:
            return item[1]
        if item:
            _price_cache.pop(key, None)
    return None

def _put_prices_cache(key, value):
    with _price_cache_lock:
        _price_cache[key] = (time.monotonic(), value)
    return value



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
    """Return a complete product catalog from the public price matrix.

    5SIM documents /guest/products/{country}/{operator}, not a global
    /guest/products endpoint. The old implementation called the latter,
    which is why Server 1 could fall back to only the small hard-coded
    service list. The public /guest/prices endpoint exposes the product
    keys for every country, so we build a deduplicated activation catalog
    from that matrix.
    """
    try:
        response = requests.get(
            f"{BASE_URL}/guest/prices",
            headers={"Accept": "application/json"},
            timeout=30
        )

        if response.status_code != 200:
            print(
                "[5SIM] product catalog error "
                f"HTTP {response.status_code}: {response.text[:500]}"
            )
            return {}

        data = response.json()
        catalog = {}

        if not isinstance(data, dict):
            return catalog

        # /guest/prices => {country: {product: {operator: {...}}}}
        # Be tolerant of the alternative {product: {country: ...}} shape.
        for first_key, first_value in data.items():
            if not isinstance(first_value, dict):
                continue

            for second_key, second_value in first_value.items():
                if not isinstance(second_value, dict):
                    continue

                # Country -> product -> operator
                if any(
                    isinstance(v, dict) and (
                        "cost" in v or "count" in v or "rate" in v
                    )
                    for v in second_value.values()
                ):
                    product = str(second_key).strip()
                    if product:
                        catalog.setdefault(product, {"Category": "activation"})
                    continue

                # Product -> country -> operator
                if any(isinstance(v, dict) for v in second_value.values()):
                    product = str(first_key).strip()
                    if product:
                        catalog.setdefault(product, {"Category": "activation"})

        return catalog

    except Exception as error:
        print(f"[5SIM] product catalog request error: {error}")
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
        cache_key = (str(country or "").strip().lower(), str(product or "").strip().lower())
        cached = _cached_prices(cache_key)
        if cached is not None:
            return cached

        params = {}

        if country:
            params["country"] = country

        # 5SIM supports product-only, country-only, and country+product
        # filters. Product-only responses are shaped as:
        #   {product: {country: {operator: {...}}}}
        # while country+product responses are shaped as:
        #   {country: {product: {operator: {...}}}}
        # We normalize both forms below so the rest of the bot can always
        # work with {country: {product: ...}}.
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

        # Normalize the documented response shapes to:
        # {country: {product: {operator: {cost, count, ...}}}}.
        if product:
            target_product = str(product).strip().lower()
            normalized = {}

            # Country -> product -> operator
            for country_name, country_data in data.items():
                if not isinstance(country_data, dict):
                    continue

                matched_key = None
                for product_name in country_data.keys():
                    if str(product_name).strip().lower() == target_product:
                        matched_key = product_name
                        break

                if matched_key is not None and isinstance(country_data.get(matched_key), dict):
                    normalized[str(country_name)] = {
                        str(matched_key): country_data[matched_key]
                    }

            # Product -> country -> operator (the shape returned by the
            # official product-filter endpoint).
            if not normalized:
                product_key = None
                for key in data.keys():
                    if str(key).strip().lower() == target_product:
                        product_key = key
                        break

                product_data = data.get(product_key) if product_key is not None else None
                if isinstance(product_data, dict):
                    for country_name, country_data in product_data.items():
                        if isinstance(country_data, dict):
                            normalized[str(country_name)] = {
                                str(product_key): country_data
                            }

            return _put_prices_cache(cache_key, normalized)

        return _put_prices_cache(cache_key, data)

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
# RESEND OTP
# =========================================================

def resend_otp(order_id):
    """5SIM activation has no official server-side resend endpoint.

    5SIM's documented activation API exposes buy/check/finish/ban and
    reuse operations, but not a resend operation for an existing
    activation. Returning a clear error here prevents the bot from
    pretending that polling/checking is a resend or accidentally buying
    a second activation.
    """
    return {
        "response": "ERROR",
        "error": (
            "Server 1 (5SIM) tidak menyediakan endpoint resmi "
            "untuk request resend OTP pada order activation yang sama."
        ),
        "provider": "5sim",
        "order_id": str(order_id or ""),
        "unsupported": True,
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


# =========================================================
# GROUP PRICE/STOCK OPTIONS
# =========================================================

def get_price_options(country, product):
    """Return 5SIM price tiers with stock merged across operators.

    Multiple operators can publish the exact same provider price. For the
    user-facing bot we merge those quantities into one tier while retaining
    the operator list so checkout can try the available operators in turn.
    """
    data = get_prices(country=country, product=product)
    if not isinstance(data, dict):
        return []

    country_data = data.get(country)
    if not isinstance(country_data, dict):
        target_country = str(country).strip().lower()
        for key, value in data.items():
            if str(key).strip().lower() == target_country:
                country_data = value
                break
    if not isinstance(country_data, dict):
        return []

    product_data = country_data.get(product)
    if not isinstance(product_data, dict):
        target_product = str(product).strip().lower()
        for key, value in country_data.items():
            if str(key).strip().lower() == target_product:
                product_data = value
                break
    if not isinstance(product_data, dict):
        return []

    groups = {}
    for operator, info in product_data.items():
        if not isinstance(info, dict):
            continue
        try:
            cost = float(info.get("cost") or 0)
            count = int(info.get("count") or 0)
        except Exception:
            continue
        if cost <= 0:
            continue
        # Normalize tiny floating-point differences so equal displayed
        # prices are merged reliably.
        key = round(cost, 6)
        group = groups.setdefault(key, {
            "cost": cost,
            "stock": 0,
            "operators": [],
        })
        group["stock"] += max(count, 0)
        if str(operator).strip():
            group["operators"].append(str(operator).strip())

    result = list(groups.values())
    result.sort(key=lambda item: item["cost"])
    return result


def get_price_options_for_operator(country, product, operator):
    """Return 5SIM price tiers for one exact operator, cheapest first."""
    data = get_prices(country=country, product=product)
    if not isinstance(data, dict):
        return []

    country_data = data.get(country)
    if not isinstance(country_data, dict):
        target_country = str(country).strip().lower()
        for key, value in data.items():
            if str(key).strip().lower() == target_country:
                country_data = value
                break
    if not isinstance(country_data, dict):
        return []

    product_data = country_data.get(product)
    if not isinstance(product_data, dict):
        target_product = str(product).strip().lower()
        for key, value in country_data.items():
            if str(key).strip().lower() == target_product:
                product_data = value
                break
    if not isinstance(product_data, dict):
        return []

    target = str(operator or '').strip().lower()
    rows = []
    for op_name, info in product_data.items():
        if str(op_name).strip().lower() != target:
            continue
        if not isinstance(info, dict):
            continue
        try:
            cost = float(info.get('cost') or 0)
            count = int(info.get('count') or 0)
        except Exception:
            continue
        if cost <= 0:
            continue
        rows.append({
            'cost': cost,
            'stock': max(count, 0),
            'operators': [str(op_name).strip()],
        })

    rows.sort(key=lambda item: item['cost'])
    return rows


def buy_number_any_operator(country, product, operators):
    """Try the merged 5SIM operator tier without exposing operators to users."""
    operators = [str(x).strip() for x in (operators or []) if str(x).strip()]
    if not operators:
        operators = ["any"]

    last_error = None
    for operator in operators:
        result = buy_number(country, product, operator)
        if result and result.get("response") != "ERROR" and result.get("id") and result.get("phone"):
            return result
        last_error = result

    return last_error or {
        "response": "ERROR",
        "message": "Nomor tidak tersedia."
    }
