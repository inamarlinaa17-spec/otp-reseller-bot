"""NomorOTP.id adapter for AZHURA Server 4.
Only this module talks to NomorOTP. API key is read from NOMOROTP_API_KEY.
"""
import os, requests

BASE = "https://api.nomorotp.id/"
KEY = ""

class NomorOTPError(RuntimeError):
    pass

def _key():
    key=os.getenv("NOMOROTP_API_KEY","").strip()
    if not key:
        raise NomorOTPError("NOMOROTP_API_KEY belum diatur di Railway")
    return key

def call(action, method="GET", params=None, data=None):
    headers={"X-API-Key":_key(),"Accept":"application/json"}
    r=requests.request(method, BASE, params={"action":action, **(params or {})}, data=data, headers=headers, timeout=20)
    try: payload=r.json()
    except Exception: payload={}
    if r.status_code >= 400 or not payload.get("success"):
        raise NomorOTPError(str(payload.get("error") or f"HTTP {r.status_code}"))
    return payload

def server_code(kind):
    return "plus" if str(kind).lower() in {"plus","server_plus"} else "sh"

def get_services(kind):
    d=call("getServices",params={"server":server_code(kind)}).get("services") or {}
    out=[]
    if isinstance(d,dict):
        for k,v in d.items():
            if isinstance(v,dict): code=str(v.get("code") or k).strip(); name=str(v.get("name") or code).strip()
            else: code=str(k).strip(); name=str(v).strip()
            if code: out.append((code,name))
    return out

def get_countries(kind):
    d=call("getCountries",params={"server":server_code(kind)}).get("countries") or {}
    out=[]
    if isinstance(d,dict):
        for code,name in d.items(): out.append({"country":str(code),"name":str(name),"country_name":str(name)})
    elif isinstance(d,list):
        for x in d:
            if isinstance(x,dict):
                code=str(x.get("id") or x.get("country") or x.get("code") or "").strip(); name=str(x.get("name") or x.get("country_name") or code).strip()
                if code: out.append({"country":code,"name":name,"country_name":name})
    # Indonesia is always first when present.
    out.sort(key=lambda x:(0 if x["country"]=="6" or x["name"].lower()=="indonesia" else 1,x["name"].lower()))
    return out

def get_prices(kind, service, country):
    return call("getPrices",params={"server":server_code(kind),"country":int(country) if str(country).isdigit() else country,"service":service}).get("prices") or {}

def _price_obj(prices, service):
    if not isinstance(prices,dict): return None
    v=prices.get(service)
    if isinstance(v,dict): return v
    for k,x in prices.items():
        if str(k).lower()==str(service).lower() and isinstance(x,dict): return x
    return None

def get_availability(kind, service, country):
    d=call("getAvailability",params={"server":server_code(kind),"service":service,"country":int(country) if str(country).isdigit() else country}).get("availability") or []
    if isinstance(d,dict):
        d=[dict(v,operator=k) if isinstance(v,dict) else {"operator":k,"count":v} for k,v in d.items()]
    return d if isinstance(d,list) else []

def get_quotes(kind, service, country):
    """Use the exact documented endpoints: getAvailability first, getPrices as fallback."""
    rows=[]
    try: av=get_availability(kind,service,country)
    except Exception: av=[]
    for x in av:
        if not isinstance(x,dict): continue
        try: stock=int(float(x.get("count") or 0)); cost=float(x.get("cost") or 0)
        except Exception: continue
        if stock<=0 or cost<=0: continue
        rows.append({"operator":str(x.get("operator") or "any"),"stock":stock,"cost_idr":int(round(cost))})
    if rows: return rows
    try: p=_price_obj(get_prices(kind,service,country),service)
    except Exception: p=None
    if isinstance(p,dict):
        try: stock=int(float(p.get("count") or 0)); cost=float(p.get("cost") or 0)
        except Exception: stock=0; cost=0
        if stock>0 and cost>0: rows.append({"operator":"any","stock":stock,"cost_idr":int(round(cost))})
    return rows

def buy_number(kind, service, country, operator="any"):
    data={"server":server_code(kind),"service":service,"country":int(country) if str(country).isdigit() else country}
    if operator and str(operator).lower() not in {"any","all","auto","automatic","-"}: data["operator"]=operator
    return call("getNumber",method="POST",data=data).get("activation") or {}

def get_status(activation_id):
    return call("getStatus",params={"id":activation_id})

def set_status(activation_id,status):
    return call("setStatus",method="POST",data={"id":activation_id,"status":int(status)})

def cancel_activation(activation_id):
    return call("cancelActivation",method="POST",data={"id":activation_id})
