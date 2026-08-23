import logging
import requests

from config import SMSMAN_API_KEY, SMSMAN_RUB_TO_USD

logger = logging.getLogger(__name__)
BASE_URL = "https://api.sms-man.com/control"


def _get(path, params=None):
    if not SMSMAN_API_KEY:
        return None
    params = dict(params or {})
    params["token"] = SMSMAN_API_KEY
    try:
        r = requests.get(f"{BASE_URL}/{path}", params=params, timeout=20)
        if r.status_code != 200:
            logger.warning("[SMSMAN] HTTP %s: %s", r.status_code, r.text[:500])
            return None
        data = r.json()
        if isinstance(data, dict) and data.get("success") is False:
            logger.warning("[SMSMAN] API error: %s", data)
            return None
        return data
    except Exception as exc:
        logger.warning("[SMSMAN] request error: %s", exc)
        return None


def get_balance():
    data = _get("get-balance")
    try:
        return float(data.get("balance", 0)) if isinstance(data, dict) else 0
    except Exception:
        return 0


def get_countries():
    data = _get("countries")
    return data if isinstance(data, list) else []


def get_applications():
    data = _get("applications")
    return data if isinstance(data, list) else []


def _norm(s):
    return str(s or "").strip().lower().replace("_", " ").replace("-", " ")


SERVICE_ALIASES = {
    "whatsapp": {"whatsapp", "wa"},
    "telegram": {"telegram", "tg"},
    "shopee": {"shopee"},
    "tiktok": {"tiktok", "tt"},
    "facebook": {"facebook", "fb"},
    "instagram": {"instagram", "ig"},
    "google": {"google", "gmail", "google gmail youtube", "youtube"},
    "vercel": {"vercel"},
    "uangme": {"uangme", "uang me"},
    "grab": {"grab"},
    "dana": {"dana"},
    "gojek": {"gojek"},
    "any": {"any", "any other"},
    "ovo": {"ovo"},
    "kopikenangan": {"kopi kenangan", "kopikenangan"},
    "tokopedia": {"tokopedia"},
}


def find_country(country):
    target = _norm(country)
    target_id = str(country).strip()
    for item in get_countries():
        if not isinstance(item, dict):
            continue
        cid = item.get("id") or item.get("country_id")
        names = [item.get("title"), item.get("name"), item.get("name_en"), item.get("country")]
        if cid is not None and str(cid).strip() == target_id:
            return {"id": str(cid), "name": next((str(x) for x in names if x), str(country))}
        if any(_norm(x) == target for x in names if x):
            return {"id": str(cid), "name": next((str(x) for x in names if x), str(country))}
    return None


def find_application(service):
    target = _norm(service)
    aliases = SERVICE_ALIASES.get(target, {target})
    for item in get_applications():
        if not isinstance(item, dict):
            continue
        aid = item.get("id") or item.get("application_id")
        vals = [item.get("name"), item.get("title"), item.get("code"), item.get("service")]
        normalized = {_norm(x) for x in vals if x}
        if normalized & aliases:
            return {"id": str(aid), "name": next((str(x) for x in vals if x), target), "code": str(item.get("code") or "")}
    return None


def _price_to_usd(cost):
    # SMS-Man control API examples use RUB-denominated costs.
    return float(cost) * SMSMAN_RUB_TO_USD


def get_prices(country_id=None):
    params = {}
    if country_id is not None:
        params["country_id"] = country_id
    data = _get("get-prices", params)
    return data if isinstance(data, dict) else {}


def get_quote(country, service):
    c = find_country(country)
    a = find_application(service)
    if not c or not a:
        return None
    data = get_prices(c["id"])
    # Response may be keyed by country id or directly by application id.
    country_data = data.get(str(c["id"])) or data.get(c["id"]) or data
    if not isinstance(country_data, dict):
        return None
    item = country_data.get(str(a["id"])) or country_data.get(a["id"])
    if not isinstance(item, dict):
        # Some responses include application_id inside nested records.
        for v in country_data.values():
            if isinstance(v, dict) and str(v.get("application_id")) == str(a["id"]):
                item = v
                break
    if not isinstance(item, dict):
        return None
    try:
        cost_raw = float(item.get("cost", 0) or 0)
        stock = int(item.get("count", item.get("numbers", 0)) or 0)
    except Exception:
        return None
    if cost_raw <= 0 or stock <= 0:
        return None
    return {
        "provider": "smsman",
        "country": str(country),
        "service": str(service),
        "operator": "AUTO",
        "pool": None,
        "cost_usd": _price_to_usd(cost_raw),
        "cost_native": cost_raw,
        "stock": stock,
        "country_id": c["id"],
        "application_id": a["id"],
    }


def get_quotes(country, service):
    q = get_quote(country, service)
    return [q] if q else []


def get_country_items(service):
    apps = get_applications()
    countries = get_countries()
    target_app = find_application(service)
    if not target_app:
        return []
    data = get_prices()
    result = []
    # Most responses: {country_id: {application_id: {cost,count}}}.
    for c in countries:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or c.get("country_id") or "")
        if not cid:
            continue
        cd = data.get(cid) or data.get(c.get("id"))
        if not isinstance(cd, dict):
            continue
        item = cd.get(str(target_app["id"])) or cd.get(target_app["id"])
        if not isinstance(item, dict):
            continue
        try:
            cost_raw = float(item.get("cost", 0) or 0)
            stock = int(item.get("count", item.get("numbers", 0)) or 0)
        except Exception:
            continue
        if cost_raw <= 0 or stock <= 0:
            continue
        name = str(c.get("title") or c.get("name_en") or c.get("name") or cid)
        result.append({
            "country": cid,
            "name": name,
            "cost": _price_to_usd(cost_raw),
            "stock": stock,
            "country_id": cid,
            "application_id": str(target_app["id"]),
        })
    return result


def buy_number(country, service):
    c = find_country(country)
    a = find_application(service)
    if not c or not a:
        return {"response": "ERROR", "message": "SMS-Man country/service mapping not found"}
    data = _get("get-number", {"country_id": c["id"], "application_id": a["id"]})
    if not isinstance(data, dict) or data.get("success") is False or not data.get("request_id"):
        return {"response": "ERROR", "message": data or "SMS-Man get-number failed"}
    return {
        "response": "ACCESS_NUMBER",
        "order_id": str(data["request_id"]),
        "phone": data.get("number"),
        "number": data.get("number"),
    }


def get_sms(order_id):
    data = _get("get-sms", {"request_id": order_id})
    if not isinstance(data, dict):
        return {"response": "ERROR", "message": "SMS-Man get-sms failed"}
    if data.get("error_code") == "wait_sms":
        return {"response": "OK", "sms": []}
    code = data.get("sms_code")
    return {
        "response": "OK",
        "sms": ([{"code": str(code), "text": str(code)}] if code else [])
    }


def cancel_number(order_id):
    data = _get("set-status", {"request_id": order_id, "status": "reject"})
    if not isinstance(data, dict):
        return {"response": "ERROR"}
    return {"response": "OK" if data.get("success") else "ERROR", "data": data}
