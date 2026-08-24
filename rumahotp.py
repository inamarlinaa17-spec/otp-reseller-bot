"""RumahOTP provider adapter.

Uses the official RumahOTP API v2 for services/countries/operators/orders,
and v1 for order status/cancel. API key is read from RUMAHOTP_API_KEY.
"""
import json
import logging
import requests
import time

from config import RUMAHOTP_API_KEY, KURS_DOLAR

logger = logging.getLogger(__name__)
BASE_URL = "https://www.rumahotp.io/api"
TIMEOUT = 15
_CACHE_TTL = 30.0
_cache = {}


def _headers():
    return {"x-apikey": RUMAHOTP_API_KEY, "Accept": "application/json"}


def _get(path, params=None):
    if not RUMAHOTP_API_KEY:
        return {"success": False, "error": {"message": "RUMAHOTP_API_KEY belum diatur."}}
    try:
        r = requests.get(f"{BASE_URL}{path}", headers=_headers(), params=params or {}, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            return {"success": False, "error": {"message": "Response RumahOTP tidak valid."}}
        return data
    except Exception as exc:
        logger.warning("[RUMAHOTP] request %s failed: %s", path, exc)
        return {"success": False, "error": {"message": str(exc)}}


def check_api():
    data = _get("/v1/user/balance")
    return {"success": bool(data.get("success")), "data": data.get("data"), "error": data.get("error")}


def get_balance():
    data = _get("/v1/user/balance")
    try:
        return float((data.get("data") or {}).get("balance") or 0)
    except Exception:
        return 0.0


def _cached(key):
    item = _cache.get(key)
    if item and time.monotonic() - item[0] < _CACHE_TTL:
        return item[1]
    return None

def _put_cache(key, value):
    _cache[key] = (time.monotonic(), value)
    return value

def get_services():
    cached = _cached("services")
    if cached is not None:
        return cached
    data = _get("/v2/services")
    value = data.get("data") if data.get("success") and isinstance(data.get("data"), list) else []
    return _put_cache("services", value)


def find_service(service):
    target = str(service or "").strip().lower().replace("_", " ")
    aliases = {"google": {"google", "gmail", "youtube", "google gmail youtube"}, "whatsapp": {"whatsapp", "wa"}, "telegram": {"telegram", "tg"}, "facebook": {"facebook", "fb"}, "instagram": {"instagram", "ig"}, "tiktok": {"tiktok", "tt"}, "shopee": {"shopee"}}
    wanted = aliases.get(target, {target})
    for item in get_services():
        code = str(item.get("service_code") or item.get("id") or "").strip()
        name = str(item.get("service_name") or item.get("name") or "").strip()
        if name.lower() in wanted or code.lower() in wanted or target == name.lower():
            return {"id": code, "name": name, **item}
    return None


def get_countries(service_id):
    key = f"countries:{service_id}"
    cached = _cached(key)
    if cached is not None:
        return cached
    data = _get("/v2/countries", {"service_id": service_id})
    value = data.get("data") if data.get("success") and isinstance(data.get("data"), list) else []
    return _put_cache(key, value)


def find_country(country, service_id):
    target = str(country or "").strip().lower().replace("_", " ")
    aliases = {"vietnam": {"vietnam", "viet nam"}, "united states": {"united states", "usa", "us", "united states of america"}, "united kingdom": {"united kingdom", "uk", "gb"}}
    wanted = aliases.get(target, {target})
    for item in get_countries(service_id):
        name = str(item.get("name") or "").strip()
        iso = str(item.get("iso_code") or "").strip().lower()
        if name.lower() in wanted or iso in wanted or target == name.lower():
            return item
    return None


def _country_quotes(country_item, service_name, service_id=None):
    quotes=[]
    if not isinstance(country_item, dict): return quotes
    country_name=str(country_item.get("name") or country_item.get("iso_code") or "")
    number_id=country_item.get("number_id")
    for row in country_item.get("pricelist") or []:
        if not isinstance(row, dict): continue
        provider_id=row.get("provider_id")
        price=float(row.get("price") or 0)
        stock=int(float(row.get("stock") or 0))
        if provider_id is None or price <= 0 or stock <= 0: continue
        quotes.append({
            "provider":"rumahotp", "country":str(number_id or country_name), "country_name":country_name,
            "service":str(service_id or service_name),
            "service_name":str(service_name), "operator":"AUTO", "provider_operator":"any",
            "pool":json.dumps({"number_id":number_id,"provider_id":provider_id,"operator_id":1}, separators=(",",":")),
            "cost_usd":price/float(KURS_DOLAR), "cost_idr":price, "stock":stock,
        })
    return quotes


def get_all_quotes(service):
    found=find_service(service)
    if not found: return []
    sid=found.get("id")
    result=[]
    for country in get_countries(sid):
        result.extend(_country_quotes(country, found.get("name") or service, sid))
    return result


def get_operator_quotes(country, service):
    """Return live operator-specific quotes for one country/service."""
    found=find_service(service)
    if not found: return []
    sid=found.get("id")
    item=find_country(country, sid)
    if not item: return []
    out=[]
    for row in item.get("pricelist") or []:
        if not isinstance(row,dict): continue
        provider_id=row.get("provider_id")
        price=float(row.get("price") or 0); stock=int(float(row.get("stock") or 0))
        if provider_id is None or price<=0 or stock<=0: continue
        cache_key = f"operators:{str(item.get('name')).lower()}:{provider_id}"
        operators = _cached(cache_key)
        if operators is None:
            ops=_get("/v2/operators", {"country":item.get("name"),"provider_id":provider_id})
            operators=ops.get("data") if ops.get("success") and isinstance(ops.get("data"),list) else []
            _put_cache(cache_key, operators)
        for op in operators:
            opid=op.get("id"); name=str(op.get("name") or "any").strip()
            if opid is None or not name: continue
            # Keep an explicit operator only; AUTO/any stays available via the catch-all.
            key=name.lower().replace("_"," ").strip()
            if key in {"any","all","auto","automatic"}: continue
            out.append({
                "provider":"rumahotp", "country":str(item.get("number_id") or item.get("name")),
                "country_name":str(item.get("name") or country), "service":str(sid), "service_name":str(service),
                "operator":name, "provider_operator":name,
                "pool":json.dumps({"number_id":item.get("number_id"),"provider_id":provider_id,"operator_id":opid}, separators=(",",":")),
                "cost_usd":price/float(KURS_DOLAR), "cost_idr":price, "stock":stock,
            })
    return out


def _metadata(pool):
    try: return json.loads(pool or "{}")
    except Exception: return {}


def buy_number(country, service, operator="any", metadata=None):
    found=find_service(service)
    if not found: return {"response":"ERROR", "error":"Layanan RumahOTP tidak ditemukan."}
    item=find_country(country, found.get("id"))
    if not item: return {"response":"ERROR", "error":"Negara RumahOTP tidak ditemukan."}
    meta=metadata or {}
    provider_id=meta.get("provider_id")
    operator_id=meta.get("operator_id")
    number_id=meta.get("number_id") or item.get("number_id")
    if not provider_id:
        rows=item.get("pricelist") or []
        active=[x for x in rows if isinstance(x,dict) and int(float(x.get("stock") or 0))>0 and float(x.get("price") or 0)>0]
        if not active: return {"response":"ERROR", "error":"Stok RumahOTP habis."}
        chosen=min(active,key=lambda x:float(x.get("price") or 0)); provider_id=chosen.get("provider_id")
    if not operator_id:
        operator_id=1
    data=_get("/v2/orders", {"number_id":number_id,"provider_id":provider_id,"operator_id":operator_id})
    if not data.get("success"):
        return {"response":"ERROR", "error":(data.get("error") or {}).get("message","RumahOTP order gagal.")}
    d=data.get("data") or {}
    return {"id":d.get("order_id"),"order_id":d.get("order_id"),"phone":d.get("phone_number"),"number":d.get("phone_number"),"response":"OK"}


def get_sms(order_id):
    data=_get("/v1/orders/get_status", {"order_id":order_id})
    if not data.get("success"): return {"response":"ERROR", "error":(data.get("error") or {}).get("message","Gagal mengecek RumahOTP.")}
    d=data.get("data") or {}; status=str(d.get("status") or "").lower()
    code=d.get("otp_code"); text=d.get("otp_msg") or ""
    if code:
        return {"response":"OK", "sms":[{"code":str(code),"text":text}], "status":status}
    return {"response":"OK", "sms":[], "status":status}


def cancel_number(order_id):
    data=_get("/v1/orders/set_status", {"order_id":order_id,"status":"cancel"})
    return {"response":"OK"} if data.get("success") else {"response":"ERROR", "error":(data.get("error") or {}).get("message","Cancel RumahOTP gagal.")}
