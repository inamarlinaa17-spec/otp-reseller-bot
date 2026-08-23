import logging

from provider import get_prices as get_5sim_prices
from smspool import (
    get_prices as get_smspool_prices,
    find_service as find_smspool_service,
    get_all_countries as get_smspool_countries,
)
from smsman import (
    get_quotes as get_smsman_quotes,
    get_country_items as get_smsman_country_items,
    get_countries as get_smsman_countries,
    find_country as find_smsman_country,
    find_application as find_smsman_application,
)

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


def _norm_country(value):
    return " ".join(str(value or "").strip().lower().replace("_", " ").replace("-", " ").split())


def _title_country(value):
    return str(value or "").replace("_", " ").replace("-", " ").title()


def _country_aliases(value):
    key = _norm_country(value)
    aliases = {key}
    # Common provider naming differences.
    special = {
        "united states": {"usa", "us", "united states of america", "america"},
        "united kingdom": {"uk", "gb", "england", "great britain"},
        "south korea": {"korea", "republic of korea", "korea republic"},
        "russia": {"russian federation"},
        "czech republic": {"czechia"},
        "vietnam": {"viet nam"},
        "laos": {"lao people's democratic republic", "lao pdr"},
        "moldova": {"moldova republic of", "republic of moldova"},
        "tanzania": {"united republic of tanzania"},
        "bolivia": {"bolivia plurinational state of"},
        "venezuela": {"venezuela bolivarian republic of"},
        "iran": {"iran islamic republic of"},
        "syria": {"syrian arab republic"},
        "brunei": {"brunei darussalam"},
        "palestine": {"palestine state of"},
    }
    for canonical, vals in special.items():
        if key == canonical or key in vals:
            aliases.add(canonical)
            aliases.update(vals)
    return aliases


def _same_country(a, b):
    aa = _country_aliases(a)
    bb = _country_aliases(b)
    return bool(aa & bb)


def _5sim_country_name(slug):
    return _title_country(slug)


def _smspool_country_map():
    mapping = {}
    try:
        data = get_smspool_countries()
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, dict):
                    name = value.get("name") or value.get("country") or value.get("country_name") or value.get("short_name")
                    cid = value.get("ID") or value.get("id") or key
                else:
                    name = value
                    cid = key
                if name is not None:
                    mapping[str(cid)] = str(name)
        elif isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                cid = item.get("ID") or item.get("id") or item.get("country") or item.get("code")
                name = item.get("name") or item.get("country_name") or item.get("short_name") or item.get("country")
                if cid is not None and name:
                    mapping[str(cid)] = str(name)
    except Exception:
        logger.exception("SMSPool country map error")
    return mapping


def _5sim_all_quotes(service):
    data = get_5sim_prices(product=service)
    if not isinstance(data, dict):
        return []
    root = data.get(service) if isinstance(data.get(service), dict) else data
    quotes = []
    for country_id, country_data in root.items():
        if not isinstance(country_data, dict):
            continue
        for operator, info in country_data.items():
            if not isinstance(info, dict):
                continue
            cost = _num(info.get("cost"))
            stock = _int(info.get("count"))
            if cost <= 0 or stock <= 0:
                continue
            cid = str(country_id)
            quotes.append({
                "provider": "5sim",
                "country": cid,
                "country_name": _5sim_country_name(cid),
                "service": str(service),
                "operator": str(operator),
                "pool": None,
                "cost_usd": cost,
                "stock": stock,
            })
    return quotes


def _smspool_all_quotes(service):
    found = find_smspool_service(service)
    lookup_service = found.get("id") if found and found.get("id") is not None else service
    data = get_smspool_prices(service=lookup_service)
    if not isinstance(data, list):
        return []
    country_names = _smspool_country_map()
    quotes = []
    for item in data:
        if not isinstance(item, dict):
            continue
        cid = item.get("country") or item.get("country_id") or item.get("country_code")
        if cid is None:
            continue
        cost = _num(item.get("cost") or item.get("price") or item.get("amount"))
        stock = _int(item.get("stock") or item.get("count") or item.get("available"))
        if stock <= 0 and cost > 0:
            stock = 1
        if cost <= 0 or stock <= 0:
            continue
        cid = str(cid)
        name = str(item.get("country_name") or country_names.get(cid) or item.get("name") or cid)
        quotes.append({
            "provider": "smspool",
            "country": cid,
            "country_name": name,
            "service": str(service),
            "operator": "AUTO",
            "pool": str(item.get("pool")) if item.get("pool") is not None else None,
            "cost_usd": cost,
            "stock": stock,
        })
    return quotes


def _smsman_all_quotes(service):
    # get_country_items already resolves SMS-Man country/application IDs and
    # returns the provider's stable country ID, while keeping the display name.
    items = get_smsman_country_items(service)
    quotes = []
    for item in items:
        cost = _num(item.get("cost"))
        stock = _int(item.get("stock"))
        cid = item.get("country_id") or item.get("country")
        if not cid or cost <= 0 or stock <= 0:
            continue
        quotes.append({
            "provider": "smsman",
            "country": str(cid),
            "country_name": str(item.get("name") or item.get("country") or cid),
            "service": str(service),
            "operator": "AUTO",
            "pool": None,
            "cost_usd": cost,
            "stock": stock,
        })
    return quotes


def _all_quotes(service):
    quotes = []
    for name, fn in (("5SIM", _5sim_all_quotes), ("SMSPool", _smspool_all_quotes), ("SMS-Man", _smsman_all_quotes)):
        try:
            quotes.extend(fn(service))
        except Exception:
            logger.exception("%s aggregation error: service=%s", name, service)
    return quotes


def get_aggregated_quotes(country, service):
    """Return live quotes for one display country without passing display
    names into provider APIs. Each quote keeps its provider-specific country ID."""
    matches = [q for q in _all_quotes(service) if _same_country(q.get("country_name"), country)]
    return sorted(matches, key=lambda q: (q["cost_usd"], q["provider"], q.get("operator") or ""))


def get_aggregated_countries(service):
    """Return one cheapest live quote per country across all three providers."""
    countries = {}
    for q in _all_quotes(service):
        name = str(q.get("country_name") or q.get("country") or "").strip()
        cost = _num(q.get("cost_usd"))
        stock = _int(q.get("stock"))
        if not name or cost <= 0 or stock <= 0:
            continue
        key = _norm_country(name)
        candidate = {
            "country": name,
            "name": name,
            "cost": cost,
            "stock": stock,
        }
        current = countries.get(key)
        if current is None or candidate["cost"] < current["cost"]:
            countries[key] = candidate

    return sorted(countries.values(), key=lambda x: (
        0 if _norm_country(x["name"]) == "indonesia" else 1,
        _norm_country(x["name"])
    ))
