"""Grizzly SMS adapter for AZHURA Server 4.

This module is intentionally independent from the existing provider modules.
The provider exposes an SMS-Activate-compatible client API.
"""
import os
import math
import time
import threading
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.grizzlysms.com"
HANDLER_URL = f"{BASE_URL}/stubs/handler_api.php"
TIMEOUT = 25
# Timeout lebih pendek untuk lookup katalog/harga supaya halaman negara tidak
# menunggu terlalu lama (bot Telegram hanya menunggu ~20-30 detik).
FAST_TIMEOUT = (5, 12)
SESSION = requests.Session()

_CACHE = {}
_CACHE_LOCK = threading.Lock()


def _cached(key, ttl, loader, cache_empty=False):
    """Cache hasil lookup di memori. Jika API gagal, pakai data lama (stale)."""
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    try:
        value = loader()
    except Exception:
        if hit:
            return hit[1]
        raise
    if value or cache_empty:
        with _CACHE_LOCK:
            _CACHE[key] = (time.monotonic(), value)
    return value

_RATE = None
_RATE_AT = 0.0
_RATE_TTL = 3600.0


class GrizzlyError(RuntimeError):
    pass


class GrizzlyRejected(GrizzlyError):
    """Explicit provider rejection; safe for the caller to refund."""


def _api_key():
    key = os.getenv("GRIZZLYSMS_API_KEY", "").strip()
    if not key:
        raise GrizzlyError("GRIZZLYSMS_API_KEY belum diatur di Railway.")
    return key


def _request(action, _timeout=None, **params):
    payload = {"api_key": _api_key(), "action": action}
    payload.update({k: v for k, v in params.items() if v is not None and v != ""})
    response = SESSION.get(HANDLER_URL, params=payload, headers={"User-Agent": "AZHURA-Server4/1.0"}, timeout=_timeout or TIMEOUT)
    response.raise_for_status()
    data = response.json() if "application/json" in response.headers.get("content-type", "").lower() else response.text
    if isinstance(data, str):
        marker = data.strip()
        if marker.startswith(("BAD_", "NO_", "ERROR_", "WRONG_", "SERVICE_UNAVAILABLE_", "USERS_IP_")):
            if marker in {"NO_NUMBERS", "NO_BALANCE"}:
                raise GrizzlyRejected(marker)
            raise GrizzlyError(marker)
    return data


def _usd_idr():
    global _RATE, _RATE_AT
    if _RATE and time.monotonic() - _RATE_AT < _RATE_TTL:
        return _RATE
    try:
        r = SESSION.get("https://api.frankfurter.dev/v2/rate/USD/IDR", timeout=8)
        r.raise_for_status()
        rate = float(r.json().get("rate") or 0)
        if 10000 < rate < 30000:
            _RATE = rate
            _RATE_AT = time.monotonic()
            return rate
    except Exception:
        pass
    return _RATE or 18000.0


def sell_price_idr(cost_idr):
    cost = Decimal(str(cost_idr or 0))
    if cost <= 0:
        return 0
    margin = Decimal(str(os.getenv("PROFIT_PERCENT", "7"))) / Decimal("100")
    return int((cost * (Decimal("1") + margin)).to_integral_value(rounding=ROUND_CEILING))


def check_api():
    try:
        return {"success": True, "data": get_balance()}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def get_balance():
    data = _request("getBalance")
    if isinstance(data, str):
        parts = data.split(":")
        if len(parts) >= 2 and parts[0] == "ACCESS_BALANCE":
            return float(parts[1])
    if isinstance(data, dict):
        return float(data.get("balance") or data.get("amount") or 0)
    raise GrizzlyError("Respons saldo Grizzly tidak valid.")


def _normalise_service(item, key=None):
    if isinstance(item, dict):
        code = str(item.get("service_code") or item.get("code") or item.get("short_name")
                   or item.get("service") or item.get("id") or key or "").strip()
        name = str(item.get("service_name") or item.get("name") or item.get("label")
                   or code).strip()
    else:
        code = str(key or item or "").strip()
        name = str(item or code).strip()
    return code, name


def _unwrap_collection(data, keys):
    """Unwrap common Grizzly response envelopes without losing legacy shapes."""
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, (list, dict)):
                return value
        # Some endpoints return the useful object under `data`.
        value = data.get("data")
        if isinstance(value, (list, dict)):
            return value
    return data


def _get_services_live():
    data = _unwrap_collection(_request("getServicesList", _timeout=FAST_TIMEOUT), ("services", "serviceList", "service_list"))
    out = []
    seen = set()
    if isinstance(data, dict):
        iterable = data.items()
    elif isinstance(data, list):
        iterable = enumerate(data)
    else:
        iterable = []
    for key, item in iterable:
        code, name = _normalise_service(item, key)
        # Never expose a response-envelope key such as `status` as a service.
        if str(code).lower() in {"status", "success", "message", "error", "data"}:
            continue
        if code and code.lower() not in seen:
            out.append({"id": code, "name": name})
            seen.add(code.lower())
    return out


def get_services():
    return _cached("services", 900, _get_services_live)


def _normalise_country(item, key=None):
    if isinstance(item, dict):
        cid = str(item.get("country_id") or item.get("id") or item.get("country")
                    or key or item.get("code") or "").strip()
        name = str(item.get("country_name") or item.get("name") or item.get("english")
                    or item.get("eng") or item.get("englishName") or item.get("eng_name")
                    or item.get("english_name") or item.get("label") or cid).strip()
        iso = str(item.get("iso_code") or item.get("iso") or item.get("country_code")
                   or item.get("code2") or item.get("iso2") or "").strip().upper()
        if not iso:
            candidate_iso = str(item.get("code") or "").strip().upper()
            iso = candidate_iso if len(candidate_iso) == 2 and candidate_iso.isalpha() else ""
    else:
        cid = str(key or item or "").strip()
        name = str(item or cid).strip()
        iso = ""
    return cid, name, iso


def _get_countries_live():
    data = _unwrap_collection(_request("getCountries", _timeout=FAST_TIMEOUT), ("countries", "countryList", "country_list"))
    out = []
    seen = set()
    if isinstance(data, dict):
        iterable = data.items()
    elif isinstance(data, list):
        iterable = enumerate(data)
    else:
        iterable = []
    for key, item in iterable:
        cid, name, iso = _normalise_country(item, key)
        if str(cid).lower() in {"status", "success", "message", "error", "data"}:
            continue
        if cid and cid not in seen:
            out.append({"id": cid, "name": name, "iso_code": iso})
            seen.add(cid)
    return out


def _price_records(data, wanted_service=None, wanted_country=None):
    """Normalize Grizzly's SMS-Activate-compatible price matrices.

    Grizzly currently exposes several compatible price response shapes. In
    particular, when ``service`` is supplied the selected service may be
    returned as ``country -> quote`` instead of ``country -> service -> quote``.
    The previous parser only understood the latter, which made the service
    page load correctly but left the country page empty. This parser accepts
    both forms, plus provider/operator layers and list-based variants.
    """
    records = []
    seen = set()
    wanted = str(wanted_service).strip().lower() if wanted_service is not None else ""
    wanted_country_s = str(wanted_country).strip().lower() if wanted_country is not None else ""

    def _num(v, default=0.0):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def _stock(info):
        if not isinstance(info, dict):
            return 0
        for k in ("count", "stock", "available", "quantity", "qty", "total", "amount"):
            if k in info and info.get(k) is not None:
                try:
                    return max(0, int(float(info.get(k))))
                except (TypeError, ValueError):
                    pass
        return 0

    def _cost(info):
        if not isinstance(info, dict):
            return 0.0
        for k in ("cost", "price", "rate", "activationCost", "amount", "sum"):
            if info.get(k) is not None:
                value = _num(info.get(k), 0.0)
                if value > 0:
                    return value
        return 0.0

    def _is_quote(info):
        return isinstance(info, dict) and _cost(info) > 0

    def add(country, service, info, inherited_stock=None):
        if not _is_quote(info):
            return
        country = str(country or "").strip()
        service = str(service or wanted_service or "").strip()
        if not country or not service:
            return
        if wanted and service.lower() != wanted:
            return
        if wanted_country_s and country.lower() != wanted_country_s:
            return
        cost = _cost(info)
        stock = _stock(info)
        if inherited_stock is not None and stock <= 0:
            try:
                stock = max(0, int(float(inherited_stock)))
            except (TypeError, ValueError):
                pass
        key = (country, service.lower(), round(cost, 8), stock)
        if key in seen:
            return
        seen.add(key)
        records.append({
            "country": country,
            "service": service,
            "cost_usd": cost,
            "stock": stock,
        })

    def walk_provider_layer(country, service, info):
        # Provider/operator-specific quote map, e.g.
        # {"providers": {"1": {"cost":..., "count":...}}}
        if not isinstance(info, dict):
            return False
        provider_keys = ("providers", "providerMap", "providerPrices", "provider", "operators", "operatorMap")
        found = False
        for pk in provider_keys:
            node = info.get(pk)
            if isinstance(node, dict):
                for payload in node.values():
                    if isinstance(payload, dict) and _is_quote(payload):
                        add(country, service, payload)
                        found = True
            elif isinstance(node, list):
                for payload in node:
                    if isinstance(payload, dict) and _is_quote(payload):
                        add(country, service, payload)
                        found = True
        return found

    def walk_matrix(obj, context_country=None, context_service=None):
        if isinstance(obj, list):
            for item in obj:
                if not isinstance(item, dict):
                    continue
                country = item.get("country") or item.get("country_id") or item.get("countryCode") or item.get("country_code") or context_country
                service = item.get("service") or item.get("service_code") or item.get("product") or item.get("serviceId") or context_service or wanted_service
                if _is_quote(item) and country and service:
                    add(country, service, item)
                # Some list entries contain a nested quote/matrix.
                for key in ("data", "result", "prices", "countries", "services"):
                    nested = item.get(key)
                    if isinstance(nested, (dict, list)):
                        walk_matrix(nested, country, service)
            return

        if not isinstance(obj, dict):
            return

        # Direct quote: country/service are inherited from the matrix path.
        if _is_quote(obj) and context_country and context_service:
            add(context_country, context_service, obj)
            return

        for outer, value in obj.items():
            ol = str(outer).strip()
            if ol.lower() in {"status", "success", "message", "error"}:
                continue

            # Country-first / service-first matrix node.
            if isinstance(value, dict):
                # If this node itself is a quote, support country -> quote and
                # service -> quote forms. With a selected service, the former
                # is the important case used by getPrices(service=...).
                if _is_quote(value):
                    if context_country:
                        add(context_country, context_service or wanted_service or ol, value)
                    elif ol.isdigit() and wanted_service:
                        # service-first shape: {service: {country: quote}}
                        # reaches here with the country key in ``ol``.
                        add(ol, wanted_service, value)
                    elif context_service:
                        # country -> quote when service is inherited from the
                        # request or an enclosing service node.
                        add(ol, context_service, value)
                    elif wanted_service:
                        add(ol, wanted_service, value)
                    continue

                # Provider/operator wrapper directly under a country/service.
                if walk_provider_layer(context_country or (ol if ol.isdigit() else None),
                                       context_service or (ol if not ol.isdigit() else wanted_service), value):
                    # Keep traversing: a response may contain both aggregate
                    # and provider-specific price data.
                    pass

                # Determine which side of the matrix is the service.
                if wanted and ol.lower() == wanted:
                    # service -> country -> quote
                    walk_matrix(value, context_country=context_country, context_service=ol)
                    continue

                # Numeric country keys are overwhelmingly the canonical form.
                if ol.isdigit():
                    walk_matrix(value, context_country=ol, context_service=context_service or wanted_service)
                    continue

                # If a named service key is present in the node, walk it as a
                # country-first structure; otherwise recurse defensively.
                if wanted and wanted in {str(k).strip().lower() for k in value.keys()}:
                    node = next((v for k, v in value.items() if str(k).strip().lower() == wanted), None)
                    if isinstance(node, dict):
                        if _is_quote(node):
                            add(ol, wanted, node)
                        else:
                            walk_matrix(node, context_country=ol, context_service=wanted)
                    continue

                # Generic country/service nested structure.
                walk_matrix(value,
                            context_country=context_country or (ol if context_country is None and not ol.lower() in {"data", "result", "prices", "countries"} else None),
                            context_service=context_service or (ol if context_service is None and not ol.isdigit() else wanted_service))
            elif isinstance(value, list):
                walk_matrix(value, context_country=context_country, context_service=context_service or (ol if not ol.isdigit() else wanted_service))

    # Unwrap common response envelopes first, but also inspect the full object
    # because some Grizzly responses put prices beside status/data fields.
    if isinstance(data, dict):
        for key in ("data", "result", "prices", "countries", "services"):
            nested = data.get(key)
            if isinstance(nested, (dict, list)):
                walk_matrix(nested)
        walk_matrix({k: v for k, v in data.items() if str(k).lower() not in {"status", "success", "message", "error"}})
    elif isinstance(data, list):
        walk_matrix(data)

    # A final targeted pass handles the very common {country: {service: quote}}
    # and {country: quote} forms without relying on numeric country IDs.
    if isinstance(data, dict):
        for country, node in data.items():
            if str(country).lower() in {"status", "success", "message", "error", "data", "result", "prices", "countries"}:
                continue
            if not isinstance(node, dict):
                continue
            if wanted:
                direct = node.get(wanted_service)
                if isinstance(direct, dict) and _is_quote(direct):
                    add(country, wanted_service, direct)
                elif _is_quote(node):
                    add(country, wanted_service, node)
            elif _is_quote(node):
                add(country, context_service or "", node)

    return records

def get_countries():
    return _cached("countries", 3600, _get_countries_live)


def _service_candidates(service):
    """Return likely Grizzly service codes for a user-facing service value.

    Kode yang persis sama dengan katalog dipakai langsung (cepat, 1 kandidat).
    Pencocokan nama hanya dipakai jika kode tidak ditemukan, dan dibatasi.
    """
    raw = str(service or "").strip()
    if not raw:
        return []
    try:
        catalog = get_services() or []
    except Exception:
        catalog = []
    for item in catalog:
        if str(item.get("id") or "").strip().lower() == raw.lower():
            return [str(item.get("id")).strip()]
    candidates = [raw]
    target = " ".join(raw.lower().replace("_", " ").replace("-", " ").split())
    for item in catalog:
        code = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        n = " ".join(name.lower().replace("_", " ").replace("-", " ").split())
        if code and n and (n == target or target in n or n in target):
            if code not in candidates:
                candidates.append(code)
        if len(candidates) >= 4:
            break
    return candidates


def _extract_provider_quotes(data, service, country):
    """Extract every provider/price tier for one service+country.

    Grizzly's V2/V3 matrices can expose several provider prices for the same
    country.  The storefront shows those as separate price tiers.  Do not
    collapse them to the first/cheapest tier.
    """
    rows = []
    seen = set()

    def num(v, default=None):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def integer(v, default=0):
        try:
            return max(0, int(float(v)))
        except (TypeError, ValueError):
            return default

    def add(payload, provider_id=None):
        if not isinstance(payload, dict):
            return
        _pl = _prices_of(payload)
        cost = _pl[0] if _pl else num(payload.get("amount"))
        stock = integer(payload.get("count") or payload.get("qty") or payload.get("available")
                        or payload.get("stock") or payload.get("quantity") or payload.get("total"))
        if cost is None or cost <= 0 or stock <= 0:
            return
        pid = str(payload.get("provider_id") or payload.get("providerId") or payload.get("providerID")
                  or payload.get("id") or provider_id or "").strip()
        key = (str(country), str(service).lower(), pid, round(cost, 8), stock)
        if key in seen:
            return
        seen.add(key)
        rows.append({"country": str(country), "service": str(service), "cost_usd": float(cost),
                     "stock": stock, "provider_id": pid})

    def visit(obj, inherited_country=None, inherited_service=None):
        if isinstance(obj, list):
            for item in obj:
                visit(item, inherited_country, inherited_service)
            return
        if not isinstance(obj, dict):
            return

        current_country = obj.get("country") or obj.get("countryId") or obj.get("country_id") or inherited_country
        current_service = obj.get("service") or obj.get("service_code") or obj.get("product") or inherited_service or service
        if str(current_country) != str(country):
            # Still recurse because the requested country may be a nested key.
            current_country = inherited_country

        # A direct quote row.
        if str(current_country) == str(country) and (str(current_service).lower() == str(service).lower()):
            add(obj)

        for pk in ("providers", "providerMap", "providerPrices", "provider", "operators", "operatorMap"):
            node = obj.get(pk)
            if isinstance(node, dict):
                for pid, payload in node.items():
                    if isinstance(payload, dict):
                        add(payload, pid)
                    elif isinstance(payload, (int, float, str)):
                        add({"cost": payload, "count": obj.get("count") or obj.get("stock")}, pid)
            elif isinstance(node, list):
                for payload in node:
                    add(payload)

        for key, value in obj.items():
            kl = str(key).strip()
            if kl.lower() in {"status", "success", "message", "error"}:
                continue
            child_country = current_country
            child_service = current_service
            if kl.isdigit():
                # Numeric keys in Grizzly matrices are country IDs.
                child_country = kl
            elif kl.lower() == str(service).lower():
                child_service = kl
            if isinstance(value, (dict, list)):
                visit(value, child_country, child_service)

    visit(data)
    return rows


_PRICE_KEYS = ("price", "cost", "activationCost", "rate", "sum")
_COUNT_KEYS = ("count", "stock", "available", "quantity", "qty", "total")
_SKIP_KEYS = {"status", "success", "message", "error"}


def _to_float(v):
    try:
        if isinstance(v, bool):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _prices_of(node):
    """Daftar harga dari sebuah node. Grizzly V3 memakai list: "price": [0.14]."""
    for k in _PRICE_KEYS:
        if k not in node:
            continue
        v = node.get(k)
        vals = v if isinstance(v, (list, tuple)) else [v]
        out = []
        for x in vals:
            f = _to_float(x)
            if f and f > 0:
                out.append(f)
        if out:
            return out
    return []


def _quote_of(node):
    """Return (list_of_prices, count) if the dict itself is a price/stock quote."""
    prices = _prices_of(node)
    if not prices:
        return None
    count = None
    for k in _COUNT_KEYS:
        if k in node:
            count = _to_float(node.get(k))
            if count is not None:
                break
    if count is None:
        return None
    return prices, max(0, int(count))


def extract_price_tiers(data, service, country):
    """Ambil SEMUA tier harga/stok untuk satu layanan + negara.

    Format respons Grizzly V2/V3 bisa berbeda-beda (tier bisa berupa daftar,
    peta provider -> quote, atau peta harga -> jumlah). Fungsi ini menelusuri
    seluruh JSON, mengenali node negara/layanan lewat kunci ATAU field di
    dalam node, lalu mengumpulkan setiap quote (harga + stok) yang ditemukan.
    Quote agregat yang punya anak-anak tier tidak ikut dihitung (anaknya saja).
    """
    svc = str(service or "").strip().lower()
    cty = str(country or "").strip().lower()
    rows = []

    def ident(node, *names):
        for n in names:
            v = node.get(n)
            if v not in (None, "") and not isinstance(v, (dict, list)):
                return str(v).strip().lower()
        return None

    def walk(node, has_c, has_s, pid):
        """Return tier rows found under node. has_c/has_s: context matched."""
        out = []
        if isinstance(node, list):
            for item in node:
                out.extend(walk(item, has_c, has_s, pid))
            return out
        if not isinstance(node, dict):
            return out

        c = ident(node, "country", "country_id", "countryId", "countryCode", "country_code")
        sv = ident(node, "service", "service_code", "serviceId", "product")
        if c is not None:
            has_c = has_c or c == cty
            if c != cty and not has_c:
                return out
        if sv is not None:
            has_s = has_s or sv == svc
            if sv != svc and not has_s:
                return out
        pid = ident(node, "provider_id", "providerId", "providerID", "provider") or pid

        # Peta harga -> jumlah, mis. {"0.62": 1510, "0.5": 327}
        if has_c and node and all(not isinstance(v, (dict, list)) for v in node.values()):
            nums = [(_to_float(k), _to_float(v)) for k, v in node.items()]
            if (len(nums) >= 1 and all(a is not None and a > 0 and b is not None and b >= 0 for a, b in nums)
                    and not any(k in node for k in _PRICE_KEYS + _COUNT_KEYS)):
                for (a, b), k in zip(nums, node.keys()):
                    out.append({"price": float(a), "count": int(b), "provider_id": pid or ""})
                return out

        # Telusuri anak-anak lebih dulu.
        child_rows = []
        for key, value in node.items():
            ks = str(key).strip()
            if ks.lower() in _SKIP_KEYS:
                continue
            if not isinstance(value, (dict, list)):
                continue
            kl = ks.lower()
            c_ctx, s_ctx, child_pid = has_c, has_s, pid
            if kl == cty and not has_c:
                c_ctx = True
            elif kl == svc and not has_s:
                s_ctx = True
            elif ks.isdigit() or kl not in {"providers", "providermap", "providerprices", "provider",
                                            "operators", "operatormap", "data", "result", "prices",
                                            "countries", "services", "tiers", "offers", "items", "list"}:
                # Kunci lain di dalam konteks negara+layanan umumnya id provider / harga.
                if has_c and (has_s or True):
                    child_pid = pid or ks
            child_rows.extend(walk(value, c_ctx, s_ctx, child_pid))

        if child_rows:
            return child_rows
        q = _quote_of(node)
        if q and has_c:
            for price in q[0]:
                out.append({"price": price, "count": q[1], "provider_id": pid or ""})
        return out

    raw = walk(data, False, False, "")
    final, seen = [], set()
    for r in raw:
        if r["price"] <= 0 or r["count"] <= 0:
            continue
        key = (round(r["price"], 8), r["count"], r["provider_id"])
        if key in seen:
            continue
        seen.add(key)
        final.append({"country": str(country), "service": str(service), "cost_usd": float(r["price"]),
                      "stock": int(r["count"]), "provider_id": str(r["provider_id"] or "")})
    return final


def get_prices(country=None, service=None):
    """Return the live price matrix, including every price tier.

    Semua endpoint (V3/V2/legacy) dipanggil PARALEL dan hasilnya digabung,
    sehingga semua tier harga tetap terbaca tanpa menunggu satu per satu.
    Panggilan tanpa filter (matriks besar) hanya dipakai jika panggilan
    berfilter tidak menghasilkan apa pun. Hasil di-cache singkat.
    """
    cache_key = ("prices", str(country or ""), str(service or "").lower())
    return _cached(cache_key, 20, lambda: _get_prices_live(country, service))


_DEBUG_LOGGED = {}


def _log_raw_once(action, service, country, data):
    """Catat respons mentah (dipotong) agar format API bisa diperiksa di log Railway."""
    key = (action, str(service), str(country))
    now = time.monotonic()
    if now - _DEBUG_LOGGED.get(key, 0) < 600:
        return
    _DEBUG_LOGGED[key] = now
    try:
        snippet = json.dumps(data, ensure_ascii=False)[:2500] if not isinstance(data, str) else data[:2500]
    except Exception:
        snippet = str(data)[:2500]
    logger.warning("GRIZZLY PRICE RAW %s service=%s country=%s: %s", action, service, country, snippet)


def _get_prices_live(country=None, service=None):
    service_candidates = _service_candidates(service) if service else [None]
    # Urutan prioritas: V3 (tier per provider) > V2 > legacy (agregat saja).
    actions = ("getPricesV3", "getPricesV2", "getPrices")

    def run(action, svc, filtered):
        params = {}
        if filtered:
            if country not in (None, ""):
                params["country"] = country
            if svc not in (None, ""):
                params["service"] = svc
        data = _request(action, _timeout=FAST_TIMEOUT, **params)
        rows = []
        if country and service:
            rows = extract_price_tiers(data, svc or service, country)
            if not rows:
                rows = _extract_provider_quotes(data, svc or service or "", country)
        if not rows:
            rows = _price_records(data, wanted_service=svc or service, wanted_country=country)
        if country and service and len(rows) <= 1:
            _log_raw_once(action, svc or service, country, data)
        return rows

    def collect(jobs):
        by_action, errors = {}, []
        if not jobs:
            return by_action, errors
        with ThreadPoolExecutor(max_workers=min(len(jobs), 6)) as pool:
            futures = [(job[0], pool.submit(run, *job)) for job in jobs]
            for action, fut in futures:
                try:
                    rows = fut.result()
                except Exception as exc:
                    errors.append(exc)
                    continue
                bucket = by_action.setdefault(action, [])
                seen = {(str(r.get("country")), str(r.get("service") or "").lower(),
                         str(r.get("provider_id") or ""), round(float(r.get("cost_usd") or 0), 8),
                         int(r.get("stock") or 0)) for r in bucket}
                for row in rows:
                    key = (str(row.get("country")), str(row.get("service") or "").lower(),
                           str(row.get("provider_id") or ""), round(float(row.get("cost_usd") or 0), 8),
                           int(row.get("stock") or 0))
                    if key not in seen:
                        seen.add(key)
                        bucket.append(row)
        return by_action, errors

    def pick(by_action):
        best, best_key = [], (-1, 0)
        for rank, action in enumerate(actions):
            rows = by_action.get(action) or []
            if rows and (len(rows), -rank) > best_key:
                best, best_key = rows, (len(rows), -rank)
        return best

    # Tahap 1: panggilan berfilter (cepat, kecil).
    by_action, errors = collect([(a, svc, True) for a in actions for svc in service_candidates])
    merged = pick(by_action)
    if not merged:
        # Tahap 2: matriks penuh untuk endpoint yang mengabaikan filter.
        more, more_errors = collect([(a, svc, False) for a in actions for svc in service_candidates[:1]])
        merged, errors = pick(more), errors + more_errors

    if merged:
        merged.sort(key=lambda r: float(r.get("cost_usd") or 0))
        return merged
    if errors:
        raise errors[-1]
    return []


def _country_lookup():
    try:
        return {str(x["id"]): x for x in get_countries()}
    except Exception:
        return {}


def get_catalog():
    services = get_services()
    if services:
        return services
    # Fallback to service codes observed in the live price matrix.
    seen = {}
    for row in get_prices():
        code = row["service"]
        seen.setdefault(code.lower(), {"id": code, "name": code})
    return list(seen.values())


def get_service_countries(service):
    """Return live countries with stock for one Grizzly service.

    The selected service page must never depend on the generic country endpoint
    alone. Grizzly's documented price matrix already contains country + service
    + count, so derive availability from that live matrix and use getCountries
    only for names/ISO codes.
    """
    with ThreadPoolExecutor(max_workers=2) as pool:
        names_future = pool.submit(_country_lookup)
        rows = get_prices(service=service)
        try:
            names = names_future.result(timeout=15)
        except Exception:
            names = {}
    grouped = {}
    for row in rows:
        cid = str(row.get("country") or "").strip()
        if not cid:
            continue
        try:
            stock = max(0, int(float(row.get("stock") or 0)))
            cost = float(row.get("cost_usd") or 0)
        except (TypeError, ValueError):
            continue
        if stock <= 0 or cost <= 0:
            continue
        c = grouped.setdefault(cid, {"id": cid, "name": cid, "iso_code": "", "stock": 0, "cost_usd": 0.0})
        if cid in names:
            c["name"] = names[cid].get("name") or c["name"]
            c["iso_code"] = names[cid].get("iso_code") or c["iso_code"]
        if cid == "6":
            c["name"], c["iso_code"] = "Indonesia", "ID"
        elif c["name"] == cid and not cid.isdigit():
            c["name"] = cid.replace("_", " ").title()
        c["stock"] += stock
        if not c["cost_usd"] or cost < c["cost_usd"]:
            c["cost_usd"] = cost

    out = list(grouped.values())
    out.sort(key=lambda x: (0 if str(x.get("name", "")).strip().lower() == "indonesia" else 1,
                            str(x.get("name", "")).lower()))
    return out

def get_quotes(service, country):
    """Semua tier harga + stok untuk satu layanan & negara (sama seperti di web Grizzly)."""
    rows = get_prices(country=country, service=service)
    rate = _usd_idr()
    # Gabungkan baris dengan harga identik (stok dijumlahkan); tier harga berbeda tetap terpisah.
    merged = {}
    for row in rows:
        try:
            stock = int(row.get("stock") or 0)
            cost_usd = float(row.get("cost_usd") or 0)
        except (TypeError, ValueError):
            continue
        if stock <= 0 or cost_usd <= 0:
            continue
        key = round(cost_usd, 6)
        if key in merged:
            merged[key]["stock"] += stock
        else:
            merged[key] = {"stock": stock, "cost_usd": cost_usd}
    out = []
    for item in merged.values():
        cost_idr = int(math.ceil(item["cost_usd"] * rate))
        out.append({
            "stock": int(item["stock"]),
            "cost_idr": cost_idr,
            "label": "Semua Operator",
            "metadata": {
                "country": str(country),
                "service": str(service),
                "cost_usd": float(item["cost_usd"]),
            },
        })
    out.sort(key=lambda x: x["cost_idr"])
    return out


def get_number(service, country, max_price=None):
    try:
        data = _request("getNumberV2", service=service, country=country, maxPrice=max_price)
    except GrizzlyRejected:
        raise
    except GrizzlyError:
        raise
    if not isinstance(data, dict):
        # V1 compatibility fallback.
        data = _request("getNumber", service=service, country=country, maxPrice=max_price)
    if isinstance(data, str):
        parts = data.split(":")
        if len(parts) >= 3 and parts[0] == "ACCESS_NUMBER":
            activation_id, phone = parts[1], parts[2]
            started = datetime.now(timezone.utc)
            return {
                "id": activation_id,
                "phone": phone,
                "expired_at": (started + timedelta(minutes=20)).isoformat(),
            }
        raise GrizzlyRejected(data)
    activation_id = str(data.get("activationId") or data.get("act_id") or data.get("id") or "").strip()
    phone = str(data.get("phoneNumber") or data.get("number") or data.get("phone") or "").strip()
    if not activation_id or not phone:
        raise GrizzlyRejected("Provider tidak mengembalikan nomor.")
    raw_time = data.get("activationTime") or data.get("activation_time")
    started = None
    if raw_time:
        try:
            started = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
        except Exception:
            started = None
    started = started or datetime.now(timezone.utc)
    return {
        "id": activation_id,
        "phone": phone,
        "expired_at": (started + timedelta(minutes=20)).isoformat(),
        "can_get_another_sms": str(data.get("canGetAnotherSms") or "1") in {"1", "true", "True"},
        "raw": data,
    }


def _parse_status(data):
    if isinstance(data, dict):
        sms = data.get("sms")
        if isinstance(sms, dict):
            sms = [sms]
        if not isinstance(sms, list):
            sms = data.get("messages") if isinstance(data.get("messages"), list) else []
        otp = data.get("code") or data.get("otp") or data.get("otp_code")
        text = data.get("text") or data.get("sms_text") or data.get("message")
        if sms:
            first = sms[0] if isinstance(sms[0], dict) else {}
            otp = otp or first.get("code") or first.get("otp") or first.get("otp_code")
            text = text or first.get("text") or first.get("message")
        status = str(data.get("status") or data.get("state") or "").upper()
        return {"response": "OK", "status": status, "otp": otp, "text": text, "sms": sms}
    raw = str(data or "").strip()
    if raw.startswith("STATUS_OK:"):
        code = raw.split(":", 1)[1].strip()
        return {"response": "OK", "status": "OK", "otp": code, "text": "", "sms": [{"code": code, "text": ""}]}
    if raw.startswith("STATUS_WAIT_RETRY:"):
        old = raw.split(":", 1)[1].strip()
        return {"response": "OK", "status": "WAIT_RETRY", "otp": None, "previous_code": old, "text": "", "sms": []}
    if raw == "STATUS_WAIT_RESEND":
        return {"response": "OK", "status": "WAIT_RESEND", "otp": None, "text": "", "sms": []}
    if raw == "STATUS_WAIT_CODE":
        return {"response": "OK", "status": "WAIT_CODE", "otp": None, "text": "", "sms": []}
    if raw.startswith("STATUS_CANCEL"):
        return {"response": "OK", "status": "CANCEL", "otp": None, "text": "", "sms": []}
    if raw.startswith(("BAD_", "NO_", "ERROR_", "WRONG_")):
        return {"response": "ERROR", "error": raw, "status": raw, "sms": []}
    return {"response": "OK", "status": raw, "otp": None, "text": "", "sms": []}


def get_sms(activation_id):
    return _parse_status(_request("getStatusV2", id=str(activation_id)))


def _set_status(activation_id, status):
    result = _request("setStatus", id=str(activation_id), status=int(status))
    if isinstance(result, str):
        return {"response": "OK" if result.startswith("ACCESS_") else "ERROR",
                "status": result, "raw": result}
    return {"response": "OK", "status": result.get("status") if isinstance(result, dict) else str(result), "raw": result}


def cancel_number(activation_id):
    result = _set_status(activation_id, 8)
    if result.get("response") != "OK" or result.get("status") not in {"ACCESS_CANCEL", "CANCEL", "SUCCESS", "OK"}:
        return {"response": "ERROR", "error": str(result.get("status") or "Provider belum mengonfirmasi cancel.")}
    return {"response": "OK", "provider_status": str(result.get("status") or "ACCESS_CANCEL")}


def finish_number(activation_id):
    result = _set_status(activation_id, 6)
    if result.get("response") != "OK":
        return {"response": "ERROR", "error": str(result.get("status") or "Provider belum mengonfirmasi selesai.")}
    return {"response": "OK", "provider_status": str(result.get("status") or "ACCESS_ACTIVATION")}


def resend_otp(activation_id):
    result = _set_status(activation_id, 3)
    if result.get("response") != "OK":
        return {"response": "ERROR", "error": str(result.get("status") or "Provider belum mengonfirmasi resend.")}
    return {"response": "OK", "provider_status": str(result.get("status") or "ACCESS_RETRY_GET")}


def status_is_cancelled(data):
    statuses = _status_values(data)
    return any(x in {"access_cancel", "cancel", "cancelled", "canceled", "status_cancel"} for x in statuses)


def _status_values(value):
    found = []
    if isinstance(value, dict):
        for k in ("status", "state", "provider_status"):
            if value.get(k) not in (None, ""):
                found.append(str(value[k]).strip().lower())
        raw = value.get("raw")
        if isinstance(raw, dict):
            found.extend(_status_values(raw))
    elif value not in (None, ""):
        found.append(str(value).strip().lower())
    return found


def verify_cancel(activation_id):
    try:
        return status_is_cancelled(get_sms(activation_id))
    except Exception:
        return False
