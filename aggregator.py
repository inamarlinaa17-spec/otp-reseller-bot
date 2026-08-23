import logging

from provider import get_prices as get_5sim_prices
from smspool import get_prices as get_smspool_prices, find_service as find_smspool_service
from smsman import get_quotes as get_smsman_quotes, get_country_items as get_smsman_country_items

logger = logging.getLogger(__name__)


def _num(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def _int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def get_5sim_quotes(country, service):
    data = get_5sim_prices(country=country, product=service)
    if not isinstance(data, dict):
        return []

    # 5SIM may return {country: {product: {operator: {...}}}}
    country_data = data.get(country, {})
    if service in country_data and isinstance(country_data.get(service), dict):
        product_data = country_data[service]
    elif isinstance(data.get(service), dict):
        product_data = data[service].get(country, {})
    else:
        product_data = country_data

    if not isinstance(product_data, dict):
        return []

    quotes = []
    for operator, info in product_data.items():
        if not isinstance(info, dict):
            continue
        cost = _num(info.get("cost"))
        stock = _int(info.get("count"))
        if cost <= 0 or stock <= 0:
            continue
        quotes.append({
            "provider": "5sim",
            "country": str(country),
            "service": str(service),
            "operator": str(operator),
            "pool": None,
            "cost_usd": cost,
            "stock": stock,
        })
    return quotes


def get_smspool_quotes(country, service):
    found = find_smspool_service(service)
    lookup_service = found.get("id") if found and found.get("id") is not None else service
    data = get_smspool_prices(country=country, service=lookup_service)
    if not isinstance(data, list):
        return []

    quotes = []
    for item in data:
        if not isinstance(item, dict):
            continue
        item_country = item.get("country") or item.get("country_id") or item.get("country_code")
        if item_country is None or str(item_country).lower() != str(country).lower():
            continue
        cost = _num(item.get("cost") or item.get("price") or item.get("amount"))
        stock = _int(item.get("stock") or item.get("count") or item.get("available"))
        if stock <= 0:
            stock = 1 if cost > 0 else 0
        if cost <= 0 or stock <= 0:
            continue
        quotes.append({
            "provider": "smspool",
            "country": str(country),
            "service": str(service),
            "operator": "AUTO",
            "pool": str(item.get("pool")) if item.get("pool") is not None else None,
            "cost_usd": cost,
            "stock": stock,
        })
    return quotes


def get_aggregated_quotes(country, service):
    quotes = []
    try:
        quotes.extend(get_5sim_quotes(country, service))
    except Exception:
        logger.exception("5SIM aggregator error: country=%s service=%s", country, service)
    try:
        quotes.extend(get_smspool_quotes(country, service))
    except Exception:
        logger.exception("SMSPool aggregator error: country=%s service=%s", country, service)
    try:
        quotes.extend(get_smsman_quotes(country, service))
    except Exception:
        logger.exception("SMS-Man aggregator error: country=%s service=%s", country, service)
    return sorted(quotes, key=lambda q: (q["cost_usd"], q["provider"], q.get("operator") or ""))


def get_aggregated_countries(service):
    """Return countries with the cheapest live quote across all providers.

    The returned cost is provider cost in USD; UI applies the global 7% margin.
    Provider identity is intentionally not included in the country-list display.
    """
    countries = {}

    def upsert(item):
        if not item:
            return
        name = str(item.get("name") or item.get("country") or "").strip()
        if not name:
            return
        key = name.lower().replace("_", " ")
        candidate = {
            "country": name,
            "name": name,
            "cost": float(item.get("cost") or item.get("cost_usd") or 0),
            "stock": int(item.get("stock") or item.get("count") or 0),
        }
        if candidate["cost"] <= 0 or candidate["stock"] <= 0:
            return
        current = countries.get(key)
        if current is None or candidate["cost"] < current["cost"]:
            countries[key] = candidate

    try:
        data = get_5sim_prices(product=service)
        if isinstance(data, dict):
            root = data.get(service) if isinstance(data.get(service), dict) else data
            for country, country_data in root.items():
                if not isinstance(country_data, dict):
                    continue
                for info in country_data.values():
                    if isinstance(info, dict):
                        upsert({
                            "country": str(country).replace("_", " ").title(),
                            "name": str(country).replace("_", " ").title(),
                            "cost": _num(info.get("cost")),
                            "stock": _int(info.get("count")),
                        })
    except Exception:
        logger.exception("5SIM country aggregation error: service=%s", service)

    try:
        found = find_smspool_service(service)
        lookup_service = found.get("id") if found and found.get("id") is not None else service
        data = get_smspool_prices(service=lookup_service)
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                country = item.get("country") or item.get("country_id") or item.get("country_code")
                cost = _num(item.get("cost") or item.get("price") or item.get("amount"))
                stock = _int(item.get("stock") or item.get("count") or item.get("available"))
                if country:
                    upsert({
                        "country": str(item.get("country_name") or country),
                        "name": str(item.get("country_name") or country),
                        "cost": cost,
                        "stock": stock,
                    })
    except Exception:
        logger.exception("SMSPool country aggregation error: service=%s", service)

    try:
        for item in get_smsman_country_items(service):
            upsert(item)
    except Exception:
        logger.exception("SMS-Man country aggregation error: service=%s", service)

    return sorted(countries.values(), key=lambda x: (
        0 if str(x["name"]).lower() == "indonesia" else 1,
        str(x["name"]).lower()
    ))
