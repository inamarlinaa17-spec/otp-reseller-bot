import logging

from provider import get_prices as get_5sim_prices
from smspool import get_prices as get_smspool_prices, find_service as find_smspool_service

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
    return sorted(quotes, key=lambda q: (q["cost_usd"], q["provider"], q.get("operator") or ""))


def get_aggregated_countries(service):
    countries = {}
    # 5SIM: get all prices for service so country discovery remains one request.
    try:
        data = get_5sim_prices(product=service)
        if isinstance(data, dict):
            root = data.get(service) if isinstance(data.get(service), dict) else data
            for country, country_data in root.items():
                if not isinstance(country_data, dict):
                    continue
                available = any(
                    isinstance(info, dict) and _num(info.get("cost")) > 0 and _int(info.get("count")) > 0
                    for info in country_data.values()
                )
                if available:
                    countries.setdefault(str(country), {
                        "country": str(country),
                        "name": str(country).replace("_", " ").title(),
                    })
    except Exception:
        logger.exception("5SIM country aggregation error: service=%s", service)

    # SMSPool: current all_stock endpoint.
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
                if country and cost > 0 and stock > 0:
                    countries.setdefault(str(country), {
                        "country": str(country),
                        "name": str(item.get("country_name") or country),
                    })
    except Exception:
        logger.exception("SMSPool country aggregation error: service=%s", service)

    return list(countries.values())
