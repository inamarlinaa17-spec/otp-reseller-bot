import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from provider import get_prices as get_5sim_prices, get_all_products as get_5sim_products
from rumahotp import (
    get_all_quotes as get_rumahotp_all_quotes,
    get_operator_quotes as get_rumahotp_operator_quotes,
)

logger = logging.getLogger(__name__)


# Short-lived caches reduce repeated provider/API calls when users move
# between the same Aggregator screens. The TTL is intentionally short so
# stock/prices stay reasonably fresh.
_AGGREGATOR_CACHE_TTL = 12.0
_SERVICE_CATALOG_CACHE_TTL = 60.0
_cache_lock = Lock()
_quotes_cache = {}
_service_catalog_cache = None
_service_catalog_cache_at = 0.0


def _cache_get_quotes(service):
    now = time.monotonic()
    with _cache_lock:
        entry = _quotes_cache.get(str(service))
        if entry and now - entry[0] < _AGGREGATOR_CACHE_TTL:
            return list(entry[1])
    return None


def _cache_set_quotes(service, quotes):
    with _cache_lock:
        _quotes_cache[str(service)] = (time.monotonic(), list(quotes))


def _clear_aggregator_cache():
    global _service_catalog_cache, _service_catalog_cache_at
    with _cache_lock:
        _quotes_cache.clear()
        _service_catalog_cache = None
        _service_catalog_cache_at = 0.0


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


# User-facing operator normalization. Provider names/IDs are never shown.
_OPERATOR_DISPLAY_ALIASES = {
    "telkomsel": "Telkomsel",
    "indosat": "Indosat",
    "isat": "Indosat",
    "axis": "Axis",
    "three": "Three",
    "3": "Three",
    "tri": "Three",
    "smartfren": "Smartfren",
    "byu": "By.U",
    "by.u": "By.U",
    "xl": "XL",
    "xl axiata": "XL",
    "vodafone": "Vodafone",
    "orange": "Orange",
    "t mobile": "T-Mobile",
    "tmobile": "T-Mobile",
}


def _operator_label(value):
    raw = str(value or "").strip()
    key = _norm(raw)
    if key in {"", "auto", "any", "all", "automatic", "otomatis"}:
        return "AUTO"
    if key in _OPERATOR_DISPLAY_ALIASES:
        return _OPERATOR_DISPLAY_ALIASES[key]
    return raw.replace("_", " ").strip().title()


def _is_displayable_operator(value):
    """Return True only for real carrier/operator labels.

    5SIM can expose virtual pools such as virtual58. Those are useful for
    the aggregator, but they are not mobile operator names. Keep them in
    the catch-all/automatic pool so the user never sees provider internals.
    """
    raw = str(value or "").strip()
    key = _norm(raw)
    if not key or key in {"auto", "any", "all", "automatic", "otomatis"}:
        return False
    if key.startswith("virtual") or key.startswith("pool"):
        return False
    return True


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
    """Normalize service-list responses from both providers."""
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
    """Return the UNION of all provider services with a short cache."""
    global _service_catalog_cache, _service_catalog_cache_at

    now = time.monotonic()
    with _cache_lock:
        if (_service_catalog_cache is not None and
                now - _service_catalog_cache_at < _SERVICE_CATALOG_CACHE_TTL):
            return list(_service_catalog_cache)

    records = []

    # Fetch the two catalogs concurrently. One slow provider no longer
    # blocks the other provider's service list.
    # Multi Server hanya menggabungkan RumahOTP + 5SIM.
    loaders = (
        ("5SIM", get_5sim_products),
                ("RumahOTP", lambda: [{"service_code": x.get("service_code"), "service_name": x.get("service_name")} for x in __import__("rumahotp").get_services()]),
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(loader): name for name, loader in loaders}
        for future in as_completed(futures):
            provider_name = futures[future]
            try:
                data = future.result()
                for rec in _extract_service_records(data):
                    rec["provider"] = provider_name
                    records.append(rec)
            except Exception:
                logger.exception("Aggregator service catalog failed: %s", provider_name)

    grouped = {}
    order = []
    for key, label in DISPLAY_ALIASES.items():
        grouped[key] = (key, label)
        order.append(key)

    for rec in records:
        key = _canonical_service_key(rec["name"])
        if key not in grouped:
            key = _canonical_service_key(rec["code"])
        if key not in grouped:
            label = rec["name"].strip()
            grouped[key] = (key, label)
            order.append(key)

    result = [grouped[k] for k in order if k in grouped]
    with _cache_lock:
        _service_catalog_cache = list(result)
        _service_catalog_cache_at = time.monotonic()
    return result


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






def _5sim_all_quotes(service):
    provider_service = _resolve_5sim_service(service) or str(service)
    data = get_5sim_prices(product=provider_service)
    if not isinstance(data, dict):
        return []

    # /guest/prices has appeared in both shapes over time:
    #   {country: {product: {operator: {cost,count}}}}
    # and {product: {country: {operator: {cost,count}}}}.
    # Normalize both shapes before extracting quotes.
    quotes = []

    def add_country_quotes(country_id, country_data):
        if not isinstance(country_data, dict):
            return

        # If a product layer is still present, unwrap it.
        if provider_service in country_data and isinstance(country_data.get(provider_service), dict):
            country_data = country_data[provider_service]

        for operator, info in country_data.items():
            if not isinstance(info, dict):
                continue
            cost = _num(info.get("cost"))
            stock = _int(info.get("count") or info.get("stock") or info.get("available"))
            if cost <= 0 or stock <= 0:
                continue

            cid = str(country_id)
            quotes.append({
                "provider": "5sim",
                "country": cid,  # provider-specific ID; use this for purchase
                "country_name": _title_country(cid),
                "service": provider_service,  # provider-specific service code
                "service_name": str(service),
                "operator": _operator_label(operator),
                "provider_operator": str(operator),
                "pool": None,
                "cost_usd": cost,
                "stock": stock,
            })

    # Product -> country -> operator
    product_root = data.get(provider_service) if isinstance(data.get(provider_service), dict) else None
    if product_root is not None:
        for country_id, country_data in product_root.items():
            add_country_quotes(country_id, country_data)

    # Country -> product -> operator (the common /guest/prices shape)
    if not quotes:
        for country_id, country_data in data.items():
            if not isinstance(country_data, dict):
                continue
            if provider_service in country_data:
                add_country_quotes(country_id, country_data)
            else:
                # Filtered responses may use a provider-specific alias/code.
                # Only unwrap when the nested object actually looks like
                # an operator map.
                for product_key, product_data in country_data.items():
                    if _canonical_service_key(product_key) == _canonical_service_key(service):
                        add_country_quotes(country_id, product_data)
                        break

    return quotes




def _all_quotes(service):
    """Fetch both providers in parallel, with a short-lived cache."""
    cached = _cache_get_quotes(service)
    if cached is not None:
        return cached

    results = []
    # Multi Server hanya mengambil quote live dari RumahOTP dan 5SIM.
    providers = (
        ("RumahOTP", get_rumahotp_all_quotes),
                ("5SIM", _5sim_all_quotes),
    )

    # Provider calls are independent, so never wait for them sequentially.
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(fn, service): name for name, fn in providers}
        for future in as_completed(futures):
            name = futures[future]
            try:
                result = future.result()
                if result:
                    results.extend(result)
                    logger.info("[AGGREGATOR] %s: %d live quotes for service=%s", name, len(result), service)
                else:
                    logger.warning("[AGGREGATOR] %s returned NO STOCK for service=%s", name, service)
            except Exception:
                logger.exception("[AGGREGATOR] %s error: service=%s", name, service)

    _cache_set_quotes(service, results)
    return results


def get_provider_quotes(server, country, service):
    """Return live quotes from exactly one provider for one country/service.

    This is intentionally separate from the Multi Server aggregator so each
    Server 1/2/3 screen uses only its own provider data.
    """
    server = str(server or "").strip().lower()
    if server == "rumahotp":
        try:
            # Operator-aware quotes carry the exact provider/operator pool
            # needed by the RumahOTP order endpoint.
            quotes = get_rumahotp_operator_quotes(country, service)
            if quotes:
                return sorted(quotes, key=lambda q: (_num(q.get("cost_usd")), -_int(q.get("stock"))))
        except Exception:
            logger.exception("[PROVIDER] RumahOTP operator quotes failed")
        quotes = get_rumahotp_all_quotes(service)
    elif server == "5sim":
        quotes = _5sim_all_quotes(service)
    else:
        return []

    matches = [
        q for q in (quotes or [])
        if _same_country(q.get("country_name") or q.get("country"), country)
        or str(q.get("country")) == str(country)
    ]
    return sorted(
        matches,
        key=lambda q: (_num(q.get("cost_usd")), -_int(q.get("stock")), _norm(q.get("operator"))),
    )


def get_aggregated_quotes(country, service, operator=None):
    """Return live provider quotes for one country, optionally for an operator."""
    matches = [q for q in _all_quotes(service) if _same_country(q.get("country_name"), country)]
    if operator and str(operator).upper() not in {"ALL", "AUTO", "ANY"}:
        wanted = _norm(operator)
        # RumahOTP exposes operator IDs only after country/provider selection.
        try:
            for q in get_rumahotp_operator_quotes(country, service):
                if _norm(q.get("operator")) == wanted:
                    matches.append(q)
        except Exception:
            logger.exception("[AGGREGATOR] RumahOTP operator lookup failed")
        matches = [q for q in matches if _norm(q.get("operator")) == wanted]
    return sorted(matches, key=lambda q: (_num(q.get("cost_usd")), -_int(q.get("stock")), q.get("provider") or ""))


def get_rumahotp_operator_quotes_for_country(country, service):
    try:
        return get_rumahotp_operator_quotes(country, service)
    except Exception:
        logger.exception("[AGGREGATOR] RumahOTP operator lookup failed")
        return []


def get_aggregated_countries(service):
    """
    Return one display row per country across ALL providers.

    The displayed price is the cheapest live quote, while the displayed
    stock is the combined stock from every live provider quote for that
    country.  This lets the country menu show the real aggregator picture
    without exposing which provider supplied the cheapest quote.
    """
    grouped = {}

    for q in _all_quotes(service):
        name = str(q.get("country_name") or q.get("country") or "").strip()
        cost = _num(q.get("cost_usd"))
        stock = _int(q.get("stock"))
        if not name or cost <= 0 or stock <= 0:
            continue

        existing = next(
            (item for item in grouped.values() if _same_country(item["name"], name)),
            None,
        )

        if existing is None:
            grouped[name] = {
                "country": name,
                "name": name,
                "cost": cost,
                "stock": stock,
            }
        else:
            # Combine stock from every provider/quote for this country.
            existing["stock"] += stock
            # Keep only the cheapest price for the country display.
            if cost < _num(existing["cost"]):
                existing["cost"] = cost

    countries = list(grouped.values())
    return sorted(
        countries,
        key=lambda x: (
            0 if _norm(x["name"]) == "indonesia" else 1,
            _num(x["cost"]),
            _norm(x["name"]),
        ),
    )
