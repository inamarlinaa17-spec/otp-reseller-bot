"""Isolated provider adapters for AZHURA web. Never imports bot config."""
import os, requests, math, time
from urllib.parse import quote
from decimal import Decimal, ROUND_CEILING
S=requests.Session()
RATE=None; RATE_AT=0

def usd_rate():
    global RATE,RATE_AT
    if RATE and time.monotonic()-RATE_AT<3600:return RATE
    try:
        r=S.get('https://api.frankfurter.dev/v2/rate/USD/IDR',timeout=8);r.raise_for_status()
        rate=float(r.json()['rate'])
        if 10000<rate<30000:RATE=rate;RATE_AT=time.monotonic();return rate
    except requests.RequestException:pass
    return RATE or 18000.0

def get5(path,auth=False,params=None):
    h={'Accept':'application/json'}
    if auth:
        key=os.getenv('FIVESIM_API_KEY','').strip()
        if not key:raise RuntimeError('FIVESIM_API_KEY belum tersedia di web')
        h['Authorization']='Bearer '+key
    r=S.get('https://5sim.net/v1/'+path,headers=h,params=params,timeout=25)
    r.raise_for_status();return r.json()

def get2(path,params=None):
    key=os.getenv('RUMAHOTP_API_KEY','').strip()
    if not key:raise RuntimeError('RUMAHOTP_API_KEY belum tersedia di web')
    r=S.get('https://www.rumahotp.io/api/'+path,headers={'x-apikey':key,'Accept':'application/json'},params=params or {},timeout=25)
    r.raise_for_status();d=r.json()
    if not d.get('success'):raise RuntimeError('Provider tidak tersedia')
    return d.get('data')

def catalog(server,kind,service=None):
    if server==1:
        if kind=='services':
            # Full price matrix is used to avoid a truncated hard-coded catalog.
            d=get5('guest/prices');names=set()
            for c,products in d.items():
                if not isinstance(products,dict):continue
                for name,ops in products.items():
                    if isinstance(ops,dict) and any(isinstance(x,dict) and ('cost' in x or 'count' in x) for x in ops.values()):names.add(name)
            return [{'id':x,'name':x.title()} for x in sorted(names)]
        d=get5('guest/prices',params={'product':service})
        return [{'id':x,'name':x.replace('_',' ').title()} for x,v in d.items() if isinstance(v,dict)]
    if server==2:
        if kind=='services':
            return [{'id':str(x.get('id')),'name':str(x.get('name') or x.get('id'))} for x in (get2('v2/services') or []) if isinstance(x,dict) and x.get('id') is not None]
        return [{'id':str(x.get('number_id') or x.get('name')),'name':str(x.get('name') or x.get('number_id'))} for x in (get2('v2/countries',{'service_id':service}) or []) if isinstance(x,dict)]
    raise ValueError('Server tidak valid')

def quote_rows(server,service,country):
    if server==1:
        d=get5('guest/prices',params={'country':country,'product':service});countrydata=d.get(country,{})
        if service in d and country in d[service]:ops=d[service][country]
        else:ops=countrydata.get(service,{}) if isinstance(countrydata,dict) else {}
        rows=[]
        for op,v in ops.items():
            if not isinstance(v,dict):continue
            try:cost=float(v.get('cost') or 0);stock=int(v.get('count') or 0)
            except (ValueError,TypeError):continue
            if cost<=0 or stock<=0 or not math.isfinite(cost):continue
            rows.append({'stock':stock,'cost_idr':math.ceil(cost*usd_rate()),'label':str(op),'metadata':{'operator':op,'cost_usd':cost}})
        return rows
    if server==2:
        items=get2('v2/countries',{'service_id':service}) or []
        item=next((x for x in items if str(x.get('number_id') or x.get('name'))==country),None)
        if not item:return []
        rows=[]
        for v in item.get('pricelist') or []:
            try:cost=float(v.get('price') or v.get('rate') or 0);stock=int(float(v.get('stock') or 0))
            except (ValueError,TypeError):continue
            if cost<=0 or stock<=0 or not math.isfinite(cost) or v.get('provider_id') is None:continue
            rows.append({'stock':stock,'cost_idr':math.ceil(cost),'label':'Pilihan harga','metadata':{'number_id':item.get('number_id'),'provider_id':v['provider_id'],'operator_id':1}})
        return rows
    raise ValueError('Server tidak valid')

def purchase(server,service,country,meta):
    if server==1:
        op=str(meta['operator']);d=get5('user/buy/activation/'+quote(country,safe='')+'/'+quote(op,safe='')+'/'+quote(service,safe=''),auth=True)
        if not d.get('id') or not d.get('phone'):raise RuntimeError('Nomor tidak tersedia')
        return {'id':d['id'],'phone':d['phone'],'expired_at':d.get('expires')}
    if server==2:
        d=get2('v2/orders',{'number_id':meta['number_id'],'provider_id':meta['provider_id'],'operator_id':meta.get('operator_id',1)}) or {}
        if not d.get('order_id') or not d.get('phone_number'):raise RuntimeError('Nomor tidak tersedia')
        return {'id':d['order_id'],'phone':d['phone_number'],'expired_at':d.get('expired_at')}
    raise ValueError('Server tidak valid')

def check_sms(provider,provider_id):
    if provider=='5sim':
        d=get5('user/check/'+quote(str(provider_id),safe=''),auth=True)
        sms=d.get('sms') or []
        return {'otp':str(sms[0].get('code')) if sms and sms[0].get('code') else None,'text':sms[0].get('text') if sms else None}
    d=get2('v1/orders/get_status',{'order_id':provider_id}) or {}
    return {'otp':str(d['otp_code']) if d.get('otp_code') else None,'text':d.get('otp_msg')}
