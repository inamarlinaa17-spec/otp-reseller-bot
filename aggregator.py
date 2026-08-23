import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from provider import get_prices as get_5sim_prices, get_all_products as get_5sim_products
from smspool import (
    get_prices as get_smspool_prices,
    find_service as find_smspool_service,
    get_all_countries as get_smspool_countries,
    get_all_services as get_smspool_services,
    get_suggested_countries as get_smspool_suggested_countries,
)
from smsman import (
    get_country_items as get_smsman_country_items,
    get_countries as get_smsman_countries,
    find_country as find_smsman_country,
    find_application as find_smsman_application,
    get_applications as get_smsman_applications,
    _price_to_usd as smsman_price_to_usd,
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
    """Return the UNION of all provider services with a short cache."""
    global _service_catalog_cache, _service_catalog_cache_at

    now = time.monotonic()
    with _cache_lock:
        if (_service_catalog_cache is not None and
                now - _service_catalog_cache_at < _SERVICE_CATALOG_CACHE_TTL):
            return list(_service_catalog_cache)

    records = []

    # Fetch the three catalogs concurrently. One slow provider no longer
    # blocks the other provider's service list.
    loaders = (
        ("5SIM", get_5sim_products),
        ("SMSPool", get_smspool_services),
        ("SMS-Man", get_smsman_applications),
    )
    with ThreadPoolExecutor(max_workers=3) as executor:
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


def _resolve_smspool_service(service):
    for candidate in _service_candidates(service):
        try:
            found = find_smspool_service(candidate)
            if found and found.get("id") is not None:
                return found
        except Exception:
            continue

    # Raw-catalog fallback; SMSPool permits either ID or service name in
    # /sms/all_stock, so a resolved name is enough for the quote request.
    try:
        target = _canonical_service_key(service)
        for item in get_smspool_services() or []:
            if not isinstance(item, dict):
                continue
            sid = item.get("ID") or item.get("id") or item.get("service_id")
            vals = [item.get("name"), item.get("service"), item.get("title")]
            for value in vals:
                if value and _canonical_service_key(value) == target:
                    return {"id": sid, "name": str(item.get("name") or value)}
    except Exception:
        logger.exception("[AGGREGATOR] SMSPool raw service resolution failed: %s", service)
    return None


def _resolve_smsman_service(service):
    # First use the provider helper.
    for candidate in _service_candidates(service):
        try:
            found = find_smsman_application(candidate)
            if found and found.get("id") is not None:
                return found
        except Exception:
            continue

    # Fallback: scan the raw application catalog with the same canonical
    # normalization used by the aggregator. This catches cases where the
    # provider exposes e.g. code=wa but name=WhatsApp, or a translated name.
    try:
        target = _canonical_service_key(service)
        for item in get_smsman_applications() or []:
            if not isinstance(item, dict):
                continue
            aid = item.get("id") or item.get("application_id")
            vals = [item.get("name"), item.get("title"), item.get("code"), item.get("service")]
            for value in vals:
                if value and _canonical_service_key(value) == target:
                    return {
                        "id": str(aid),
                        "name": str(item.get("name") or value),
                        "code": str(item.get("code") or value),
                    }
    except Exception:
        logger.exception("[AGGREGATOR] SMS-Man raw service resolution failed: %s", service)
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
                "operator": str(operator),
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


def _smspool_all_quotes(service):
    """Fetch SMSPool stock for a canonical service.

    SMSPool accepts either the service ID or service name.  Prefer the
    resolved service name because it is less brittle when the provider
    changes numeric IDs.  If /sms/all_stock returns no rows, fall back to
    /request/suggested_countries so a valid service is not incorrectly
    reported as unavailable.
    """
    found = _resolve_smspool_service(service)
    lookup_service = (
        found.get("name")
        if found and found.get("name")
        else (found.get("id") if found else service)
    )

    data = get_smspool_prices(service=lookup_service)
    country_names = _smspool_country_map()
    quotes = []

    def add_row(cid, value, fallback_name=None, pool=None, allow_zero_stock=False):
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
        if stock <= 0 and allow_zero_stock and cost > 0:
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

    elif isinstance(data, dict):
        # Tolerate {country: {...}} and {country: {pool: {...}}}.
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
                if not isinstance(nested, dict):
                    continue
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

    if quotes:
        return quotes

    # Fallback: suggested countries returns a price even when all_stock is
    # temporarily unavailable. It does not expose stock count, so mark it
    # as 1 only as an availability indicator.
    try:
        suggested = get_smspool_suggested_countries(lookup_service)
        if isinstance(suggested, list):
            for item in suggested:
                if not isinstance(item, dict):
                    continue
                add_row(
                    item.get("country_id") or item.get("country") or item.get("ID"),
                    {
                        "price": item.get("price") or item.get("cost"),
                        "stock": 1,
                        "country_name": item.get("name") or item.get("country_name") or item.get("short_name"),
                    },
                    allow_zero_stock=True,
                )
    except Exception:
        logger.exception("[AGGREGATOR] SMSPool suggested countries fallback failed")

    return quotes


def _smsman_all_quotes(service):
    """Fetch SMS-Man prices using the documented API v2.0 shape."""
    target_app = _resolve_smsman_service(service)
    if not target_app:
        logger.warning("[AGGREGATOR] SMS-Man service not found: %s", service)
        return []

    app_id = target_app.get("id")
    app_code = target_app.get("code") or service
    apps = [app_id, app_code, target_app.get("name")]

    countries = get_smsman_countries()
    data = get_smsman_country_items(app_code)
    quotes = []

    # First use the already-normalized helper used by Server 3.
    for item in data or []:
        cost = _num(item.get("cost"))
        stock = _int(item.get("stock"))
        cid = item.get("country_id") or item.get("country")
        if cid is None or cost <= 0 or stock <= 0:
            continue
        quotes.append({
            "provider": "smsman",
            "country": str(cid),
            "country_name": str(item.get("name") or item.get("country") or cid),
            "service": str(app_code),
            "service_name": str(service),
            "operator": "AUTO",
            "pool": None,
            "cost_usd": cost,
            "stock": stock,
        })

    if quotes:
        return quotes

    # Fallback parser for any API response shape not handled by the helper.
    try:
        from smsman import get_prices as get_smsman_prices
        prices = get_smsman_prices()
        country_names = {}
        for c in countries or []:
            if not isinstance(c, dict):
                continue
            cid = c.get("id") or c.get("country_id")
            if cid is not None:
                country_names[str(cid)] = str(c.get("title") or c.get("name_en") or c.get("name") or cid)

        if isinstance(prices, dict):
            for cid, country_data in prices.items():
                if not isinstance(country_data, dict):
                    continue
                item = None
                for candidate in apps:
                    if candidate is None:
                        continue
                    item = country_data.get(str(candidate)) or country_data.get(candidate)
                    if isinstance(item, dict):
                        break
                if not isinstance(item, dict):
                    continue
                cost = _num(item.get("cost"))
                stock = _int(item.get("count") or item.get("numbers"))
                if cost <= 0 or stock <= 0:
                    continue
                quotes.append({
                    "provider": "smsman",
                    "country": str(cid),
                    "country_name": country_names.get(str(cid), str(cid)),
                    "service": str(app_code),
                    "service_name": str(service),
                    "operator": "AUTO",
                    "pool": None,
                    "cost_usd": smsman_price_to_usd(cost),
                    "stock": stock,
                })
    except Exception:
        logger.exception("[AGGREGATOR] SMS-Man fallback parser failed")

    return quotes


def _all_quotes(service):
    """Fetch all three providers in parallel, with a short-lived cache."""
    cached = _cache_get_quotes(service)
    if cached is not None:
        return cached

    results = []
    providers = (
        ("5SIM", _5sim_all_quotes),
        ("SMSPool", _smspool_all_quotes),
        ("SMS-Man", _smsman_all_quotes),
    )

    # Provider calls are independent, so never wait for them sequentially.
    with ThreadPoolExecutor(max_workers=3) as executor:
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
