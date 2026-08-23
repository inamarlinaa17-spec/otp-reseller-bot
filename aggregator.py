import logging
import re

from provider import get_prices as get_5sim_prices, get_all_products as get_5sim_products
from smspool import (
    get_prices as get_smspool_prices,
    find_service as find_smspool_service,
    get_all_countries as get_smspool_countries,
    get_all_services as get_smspool_services,
)
from smsman import (
    get_country_items as get_smsman_country_items,
    get_countries as get_smsman_countries,
    find_country as find_smsman_country,
    find_application as find_smsman_application,
    get_applications as get_smsman_applications,
)

logger = logging.getLogger(__name__)


def _num(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def _int(value, default=0):
    try:
        return int(float(value))
    except Exception:
        return default


def _norm(value):
    value = str(value or "").strip().lower()
    value = value.replace("_", " ").replace("-", " ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def _slug(value):
    value = _norm(value).replace(" ", "_")
    return value[:48] or "service"


def _title_country(value):
    return str(value or "").replace("_", " ").replace("-", " ").title()


def _country_aliases(value):
    key = _norm(value)
    aliases = {key}
    special = {
        "united states": {"usa", "us", "united states of america", "america"},
        "united kingdom": {"uk", "gb", "england", "great britain"},
        "south korea": {"korea", "republic of korea", "korea republic"},
        "russia": {"russian federation"},
        "czech republic": {"czechia"},
        "vietnam": {"viet nam"},
        "laos": {"lao peoples democratic republic", "lao pdr"},
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
    return bool(_country_aliases(a) & _country_aliases(b))


def _extract_service_records(data):
    """Normalize service-list responses from all three providers."""
    records = []

    if isinstance(data, dict):
        iterable = data.items()
    elif isinstance(data, list):
        iterable = enumerate(data)
    else:
        return records

    for key, value in iterable:
        if isinstance(value, dict):
            candidates_code = (
                value.get("code"),
                value.get("service"),
                value.get("service_id"),
                value.get("ID"),
                value.get("id"),
                key,
            )
            candidates_name = (
                value.get("name"),
                value.get("title"),
                value.get("service"),
                value.get("code"),
                key,
            )
            code = next((str(x).strip() for x in candidates_code if x is not None and str(x).strip()), "")
            name = next((str(x).strip() for x in candidates_name if x is not None and str(x).strip()), code)
        else:
            code = str(value).strip() or str(key).strip()
            name = code

        if code and name:
            records.append({"code": code, "name": name})
    return records


# Preferred display names for common services. They are only labels;
# provider-specific IDs are resolved separately when a quote is requested.
DISPLAY_ALIASES = {
    "whatsapp": "📱 WhatsApp",
    "telegram": "✈️ Telegram",
    "shopee": "🛒 Shopee",
    "tiktok": "🎵 TikTok",
    "facebook": "📘 Facebook",
    "instagram": "📸 Instagram",
    "google": "🔎 Google / Gmail / YouTube",
    "vercel": "▲ Vercel",
    "uangme": "💰 UangMe",
    "grab": "🚕 Grab",
    "dana": "💳 DANA",
    "gojek": "🟢 Gojek",
    "any": "🌐 Any Other",
    "ovo": "💜 OVO",
    "kopikenangan": "☕ Kopi Kenangan",
    "tokopedia": "🛍 Tokopedia",
}


def _canonical_service_key(name_or_code):
    n = _norm(name_or_code)
    aliases = {
        "wa": "whatsapp",
        "whats app": "whatsapp",
        "tg": "telegram",
        "tt": "tiktok",
        "fb": "facebook",
        "ig": "instagram",
        "gmail": "google",
        "google gmail": "google",
        "google gmail youtube": "google",
        "youtube": "google",
        "uang me": "uangme",
        "kopi kenangan": "kopikenangan",
        "any other": "any",
    }
    return aliases.get(n, _slug(n))


def get_aggregator_service_catalog():
    """Return the UNION of all provider services.

    The returned key is a short canonical key used by Telegram callbacks.
    It is deliberately NOT a provider ID, because each provider uses its
    own service/application ID.
    """
    records = []

    # Keep the calls independent: one provider being down must not hide
    # services from the other two.
    loaders = (
        ("5SIM", get_5sim_products),
        ("SMSPool", get_smspool_services),
        ("SMS-Man", get_smsman_applications),
    )

    for provider_name, loader in loaders:
        try:
            data = loader()
            for rec in _extract_service_records(data):
                rec["provider"] = provider_name
                records.append(rec)
        except Exception:
            logger.exception("Aggregator service catalog failed: %s", provider_name)

    grouped = {}
    order = []

    # First add the common services in the familiar order.
    for key, label in DISPLAY_ALIASES.items():
        grouped[key] = (key, label)
        order.append(key)

    for rec in records:
        key = _canonical_service_key(rec["name"])
        # If the provider only exposes a short code, also try the code.
        if key not in grouped:
            key = _canonical_service_key(rec["code"])
        if key not in grouped:
            label = rec["name"].strip()
            grouped[key] = (key, label)
            order.append(key)

    return [grouped[k] for k in order if k in grouped]


def _service_candidates(service):
    """Generate names/codes that can be used to resolve a provider service."""
    raw = str(service or "").strip()
    n = _norm(raw)
    out = [raw, n]

    aliases = {
        "whatsapp": ["whatsapp", "wa"],
        "telegram": ["telegram", "tg"],
        "shopee": ["shopee"],
        "tiktok": ["tiktok", "tt"],
        "facebook": ["facebook", "fb"],
        "instagram": ["instagram", "ig"],
        "google": ["google", "gmail", "youtube", "google gmail youtube"],
        "vercel": ["vercel"],
        "uangme": ["uangme", "uang me"],
        "grab": ["grab"],
        "dana": ["dana"],
        "gojek": ["gojek"],
        "any": ["any", "any other"],
        "ovo": ["ovo"],
        "kopikenangan": ["kopi kenangan", "kopikenangan"],
        "tokopedia": ["tokopedia"],
    }
    if n in aliases:
        out.extend(aliases[n])
    return list(dict.fromkeys(out))


def _resolve_5sim_service(service):
    data = get_5sim_products()
    if not isinstance(data, dict):
        return None

    candidates = {_norm(x) for x in _service_candidates(service)}
    for key, value in data.items():
        if _norm(key) in candidates:
            return str(key)

        if isinstance(value, dict):
            for field in ("name", "title", "service", "code"):
                if value.get(field) is not None and _norm(value[field]) in candidates:
                    return str(key)
    return None


def _resolve_smspool_service(service):
    for candidate in _service_candidates(service):
        try:
            found = find_smspool_service(candidate)
            if found and found.get("id") is not None:
                return found
        except Exception:
            continue
    return None


def _resolve_smsman_service(service):
    for candidate in _service_candidates(service):
        try:
            found = find_smsman_application(candidate)
            if found and found.get("id") is not None:
                return found
        except Exception:
            continue
    return None


def _smspool_country_map():
    mapping = {}
    try:
        data = get_smspool_countries()
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, dict):
                    name = (
                        value.get("name")
                        or value.get("country")
                        or value.get("country_name")
                        or value.get("short_name")
                    )
                    cid = value.get("ID") or value.get("id") or key
                else:
                    name, cid = value, key
                if name is not None:
                    mapping[str(cid)] = str(name)
        elif isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                cid = item.get("ID") or item.get("id") or item.get("country") or item.get("code")
                name = (
                    item.get("name")
                    or item.get("country_name")
                    or item.get("short_name")
                    or item.get("country")
                )
                if cid is not None and name:
                    mapping[str(cid)] = str(name)
    except Exception:
        logger.exception("SMSPool country map error")
    return mapping


def _5sim_all_quotes(service):
    provider_service = _resolve_5sim_service(service) or str(service)
    data = get_5sim_prices(product=provider_service)
    if not isinstance(data, dict):
        return []

    root = data.get(provider_service) if isinstance(data.get(provider_service), dict) else data
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
                "country": cid,  # provider-specific ID; use this for purchase
                "country_name": _title_country(cid),
                "service": provider_service,  # provider-specific service code
                "service_name": str(service),
                "operator": str(operator),
                "pool": None,
                "cost_usd": cost,
                "stock": stock,
            })

    return quotes


def _smspool_all_quotes(service):
    found = _resolve_smspool_service(service)
    lookup_service = found.get("id") if found else service

    data = get_smspool_prices(service=lookup_service)
    country_names = _smspool_country_map()
    quotes = []

    def add_row(cid, value, fallback_name=None, pool=None):
        if not isinstance(value, dict):
            return

        cost = _num(
            value.get("cost")
            or value.get("price")
            or value.get("amount")
        )
        stock = _int(
            value.get("stock")
            or value.get("count")
            or value.get("available")
        )
        if stock <= 0 and cost > 0:
            stock = 1
        if cost <= 0 or stock <= 0 or cid is None:
            return

        cid = str(cid)
        quotes.append({
            "provider": "smspool",
            "country": cid,
            "country_name": str(
                value.get("country_name")
                or country_names.get(cid)
                or value.get("name")
                or fallback_name
                or cid
            ),
            "service": str(lookup_service),
            "service_name": str(service),
            "operator": "AUTO",
            "pool": (
                str(value.get("pool"))
                if value.get("pool") is not None
                else pool
            ),
            "cost_usd": cost,
            "stock": stock,
        })

    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            add_row(
                item.get("country")
                or item.get("country_id")
                or item.get("country_code"),
                item,
            )
        return quotes

    if not isinstance(data, dict):
        return quotes

    # Be tolerant of {country: {pool/service: {price...}}} responses.
    for country_id, country_data in data.items():
        if not isinstance(country_data, dict):
            continue

        direct_cost = (
            country_data.get("cost")
            or country_data.get("price")
            or country_data.get("amount")
        )
        if direct_cost is not None:
            add_row(country_id, country_data)
            continue

        candidates = []
        for pool, nested in country_data.items():
            if isinstance(nested, dict):
                cost = _num(
                    nested.get("cost")
                    or nested.get("price")
                    or nested.get("amount")
                )
                stock = _int(
                    nested.get("stock")
                    or nested.get("count")
                    or nested.get("available")
                )
                if cost > 0 and stock > 0:
                    candidates.append((cost, stock, pool, nested))

        if candidates:
            _, _, pool, nested = min(candidates, key=lambda x: x[0])
            add_row(country_id, nested, pool=pool)

    return quotes


def _smsman_all_quotes(service):
    target_app = _resolve_smsman_service(service)
    if not target_app:
        return []

    # get_country_items(service) already parses the v2 get-prices response.
    # Pass the provider's application ID/name so the lookup is unambiguous.
    items = get_smsman_country_items(
        target_app.get("code")
        or target_app.get("name")
        or target_app.get("id")
    )

    quotes = []
    for item in items:
        cost = _num(item.get("cost"))
        stock = _int(item.get("stock"))
        cid = item.get("country_id") or item.get("country")
        if not cid or cost <= 0 or stock <= 0:
            continue

        quotes.append({
            "provider": "smsman",
            "country": str(cid),  # provider-specific country ID
            "country_name": str(item.get("name") or item.get("country") or cid),
            "service": str(
                target_app.get("code")
                or target_app.get("id")
                or service
            ),
            "service_name": str(service),
            "operator": "AUTO",
            "pool": None,
            "cost_usd": cost,
            "stock": stock,
        })

    return quotes


def _all_quotes(service):
    """Fetch all three providers. A failure in one provider never blocks the others."""
    quotes = []
    for name, fn in (
        ("5SIM", _5sim_all_quotes),
        ("SMSPool", _smspool_all_quotes),
        ("SMS-Man", _smsman_all_quotes),
    ):
        try:
            result = fn(service)
            if result:
                quotes.extend(result)
            else:
                logger.info("[AGGREGATOR] %s returned no stock for service=%s", name, service)
        except Exception:
            logger.exception("[AGGREGATOR] %s error: service=%s", name, service)
    return quotes


def get_aggregated_quotes(country, service):
    """Return every live provider quote for one display country, cheapest first."""
    matches = [
        q for q in _all_quotes(service)
        if _same_country(q.get("country_name"), country)
    ]
    return sorted(
        matches,
        key=lambda q: (
            _num(q.get("cost_usd")),
            -_int(q.get("stock")),
            q.get("provider") or "",
        ),
    )


def get_aggregated_countries(service):
    """Return one cheapest live quote per country across all three providers."""
    countries = []

    for q in _all_quotes(service):
        name = str(q.get("country_name") or q.get("country") or "").strip()
        cost = _num(q.get("cost_usd"))
        stock = _int(q.get("stock"))
        if not name or cost <= 0 or stock <= 0:
            continue

        candidate = {
            "country": name,
            "name": name,
            "cost": cost,
            "stock": stock,
            "provider": q.get("provider"),
        }

        existing_index = next(
            (i for i, item in enumerate(countries) if _same_country(item["name"], name)),
            None,
        )

        if existing_index is None:
            countries.append(candidate)
        elif cost < _num(countries[existing_index]["cost"]):
            countries[existing_index] = candidate

    return sorted(
        countries,
        key=lambda x: (
            0 if _norm(x["name"]) == "indonesia" else 1,
            _num(x["cost"]),
            _norm(x["name"]),
        ),
    )
