# AZHURA ID independent web service. Existing Telegram bot files are unchanged.
# Live web purchases are opt-in until staged provider, DB and refund tests succeed.
#
import os, sys, time, json, hmac, hashlib, secrets, uuid, math
from pathlib import Path
from datetime import datetime, timezone
import requests, psycopg
from psycopg.rows import dict_row
from flask import Flask, request, jsonify, send_from_directory, session
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from database import create_pending_order, refund_order, save_provider_order
# Web pricing is isolated from bot config: importing provider.py also imports
# config.py, whose bot-only required variables can crash this web service.
from decimal import Decimal, ROUND_CEILING

def hitung_harga_jual_idr(harga_modal_rp):
    try:
        cost = Decimal(str(harga_modal_rp))
        if cost <= 0:
            return 0
        margin = Decimal(os.getenv('PROFIT_PERCENT', '7')) / Decimal('100')
        return int((cost * (Decimal('1') + margin)).to_integral_value(rounding=ROUND_CEILING))
    except (ValueError, ArithmeticError):
        return 0
from premotp import get_services, get_countries, get_offers, create_order as prem_buy, get_order as prem_status
app=Flask(__name__,static_folder='static',static_url_path='/static')
app.secret_key=os.environ['WEB_SESSION_SECRET']
app.config.update(SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SECURE=os.getenv('WEB_DEV')!='1',SESSION_COOKIE_SAMESITE='Lax',PERMANENT_SESSION_LIFETIME=86400,MAX_CONTENT_LENGTH=16384)
BOT_TOKEN=os.environ['BOT_TOKEN']; DB=os.environ['DATABASE_URL']; BOT_USERNAME=os.environ.get('BOT_USERNAME','').lstrip('@')
GOOGLE_ID=os.getenv('GOOGLE_CLIENT_ID',''); ENABLE_ORDER=os.getenv('WEB_ORDER_ENABLED','false').lower()=='true'
def conn():return psycopg.connect(DB,row_factory=dict_row)
def query(sql,args=()):
    with conn() as db:return db.execute(sql,args).fetchall()
def uid():return session.get('uid')
def err(msg,status=400):return jsonify(error=msg),status
@app.before_request
def csrf():
    if request.method in ('POST','PUT','DELETE') and uid() and request.path not in ('/api/auth/telegram','/api/auth/google'):
        if not hmac.compare_digest(str(request.headers.get('X-CSRF-Token','')),str(session.get('csrf','missing'))):return err('Sesi tidak valid. Muat ulang halaman.',403)
def login(user):
    session.clear();session.permanent=True;session['uid']=int(user);session['csrf']=secrets.token_urlsafe(32)
@app.get('/')
def index():return send_from_directory('static','index.html')
@app.get('/manifest.webmanifest')
def manifest():return jsonify(name='AZHURA ID',short_name='AZHURA',start_url='/',display='standalone',background_color='#070d19',theme_color='#0b1a32',icons=[{'src':'/static/logo.jpg','sizes':'any','type':'image/jpeg'}])
@app.get('/api/config')
def config():return jsonify(bot_username=BOT_USERNAME,google_client_id=GOOGLE_ID,web_order_enabled=ENABLE_ORDER,csrf=session.get('csrf',''))
@app.post('/api/auth/telegram')
def telegram():
    d=request.get_json(silent=True) or {}
    if not isinstance(d,dict):return err('Data tidak valid')
    try:
        items={k:str(v) for k,v in d.items() if k!='hash'}
        if abs(time.time()-int(items.get('auth_date',0)))>300:raise ValueError()
        check='\n'.join(f'{k}={items[k]}' for k in sorted(items))
        digest=hmac.new(hashlib.sha256(BOT_TOKEN.encode()).digest(),check.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(digest,str(d.get('hash',''))):raise ValueError()
        user=int(items['id'])
        if not query('SELECT 1 FROM users WHERE telegram_id=%s',(user,)):return err('Silakan /start bot Telegram dahulu.',403)
        login(user);return jsonify(ok=True,csrf=session['csrf'])
    except (ValueError,KeyError,TypeError):return err('Verifikasi Telegram gagal',401)
def google_claims(credential):
    r=requests.get('https://oauth2.googleapis.com/tokeninfo',params={'id_token':credential},timeout=8)
    r.raise_for_status();d=r.json()
    if d.get('aud')!=GOOGLE_ID or d.get('iss') not in ('accounts.google.com','https://accounts.google.com') or d.get('email_verified') not in (True,'true'):raise ValueError('Token Google tidak valid')
    if int(d.get('exp',0))<=time.time():raise ValueError('Token kedaluwarsa')
    return d
@app.post('/api/auth/google')
def google_login():
    if not GOOGLE_ID:return err('Login Google belum dikonfigurasi',503)
    try:
        d=google_claims((request.get_json(silent=True) or {}).get('credential',''))
        with conn() as db:
            db.execute('CREATE TABLE IF NOT EXISTS azhura_web_google_links (google_sub TEXT PRIMARY KEY, telegram_id BIGINT UNIQUE NOT NULL)')
            rows=db.execute('SELECT telegram_id FROM azhura_web_google_links WHERE google_sub=%s',(d['sub'],)).fetchall()
        if not rows:return err('Masuk dengan Telegram dahulu untuk menautkan Google.',403)
        login(rows[0]['telegram_id']);return jsonify(ok=True,csrf=session['csrf'])
    except (ValueError,requests.RequestException,psycopg.Error):return err('Verifikasi Google gagal',401)
@app.post('/api/auth/google/link')
def google_link():
    if not uid():return err('Login diperlukan',401)
    if not GOOGLE_ID:return err('Google belum diaktifkan',503)
    try:
        d=google_claims((request.get_json(silent=True) or {}).get('credential',''))
        with conn() as db:
            db.execute('CREATE TABLE IF NOT EXISTS azhura_web_google_links (google_sub TEXT PRIMARY KEY, telegram_id BIGINT UNIQUE NOT NULL)')
            existing=db.execute('SELECT telegram_id FROM azhura_web_google_links WHERE google_sub=%s',(d['sub'],)).fetchone()
            if existing and existing['telegram_id']!=uid():return err('Google sudah terhubung ke akun lain',409)
            db.execute('INSERT INTO azhura_web_google_links VALUES (%s,%s) ON CONFLICT (google_sub) DO NOTHING',(d['sub'],uid()))
        return jsonify(ok=True)
    except (ValueError,requests.RequestException,psycopg.Error):return err('Gagal menautkan Google',400)
@app.post('/api/logout')
def logout():session.clear();return jsonify(ok=True)
@app.get('/api/me')
def me():
    if not uid():return err('Login diperlukan',401)
    rows=query('SELECT telegram_id,username,first_name,balance FROM users WHERE telegram_id=%s',(uid(),))
    return jsonify(user=rows[0] if rows else None,csrf=session.get('csrf',''))
@app.get('/api/orders')
def orders():
    if not uid():return err('Login diperlukan',401)
    rows=query('''SELECT order_id,COALESCE(service_name,service) service,COALESCE(country_name,country) country,
        provider,sell_price,status,created_at,otp_code,sms_text,phone,expired_at,refund_status
        FROM orders WHERE telegram_id=%s ORDER BY id DESC LIMIT 40''',(uid(),))
    return jsonify(orders=rows)
def normalize(d):
    if isinstance(d,list):return d
    if isinstance(d,dict):
        return [dict(v, key=k) if isinstance(v,dict) else {'key':k,'name':str(v)} for k,v in d.items()]
    return []
@app.get('/api/prem/services/<kind>')
def services(kind):
    if not uid():return err('Login diperlukan',401)
    if kind not in ('regular','plus'):return err('Pilihan tidak valid')
    try:return jsonify(items=normalize(get_services(kind)))
    except Exception:return err('Katalog belum tersedia',503)
@app.get('/api/prem/countries/<kind>/<service>')
def countries(kind,service):
    if not uid():return err('Login diperlukan',401)
    if kind not in ('regular','plus') or len(service)>100:return err('Pilihan tidak valid')
    try:return jsonify(items=normalize(get_countries(service,kind)))
    except Exception:return err('Negara belum tersedia',503)
def parse_offer(o,key):
    if not isinstance(o,dict):return None
    oid=str(o.get('offer_id') or o.get('id') or o.get('key') or key)
    raw=o.get('price')
    if raw is None:raw=o.get('price_idr',o.get('cost_idr',o.get('amount')))
    try:cost=float(raw or 0);stock=int(o.get('stock') or o.get('available') or 0)
    except (ValueError,TypeError):return None
    if not math.isfinite(cost) or cost<=0:return None
    return dict(offer_id=oid,price=hitung_harga_jual_idr(cost),stock=stock,cost=cost)
@app.get('/api/prem/offers/<kind>/<service>/<country>')
def offers(kind,service,country):
    if not uid():return err('Login diperlukan',401)
    if kind not in ('regular','plus') or max(len(service),len(country))>100:return err('Pilihan tidak valid')
    try:
        raw=get_offers(service,country,kind)
        items=raw.items() if isinstance(raw,dict) else enumerate(raw)
        return jsonify(items=[v for k,o in items if (v:=parse_offer(o,k))])
    except Exception:return err('Harga belum tersedia',503)
# Purchase is explicitly gated: production must not be exposed before end-to-end tests.
@app.post('/api/prem/buy')
def buy():
    if not uid():return err('Login diperlukan',401)
    if not ENABLE_ORDER:return err('Order web belum diaktifkan. Order melalui bot untuk sementara.',503)
    d=request.get_json(silent=True) or {};kind=d.get('kind');service=str(d.get('service',''));country=str(d.get('country',''));oid=str(d.get('offer_id',''));key=str(d.get('request_id',''))
    if kind not in ('regular','plus') or not all((service,country,oid,key)) or max(map(len,(service,country,oid,key)))>100:return err('Pilihan tidak valid')
    # Use DB unique request key to avoid repeated purchases on double taps / retries.
    try:
        with conn() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS azhura_web_requests (telegram_id BIGINT NOT NULL, request_id TEXT NOT NULL, order_id TEXT NOT NULL, PRIMARY KEY(telegram_id,request_id))''')
            prior=db.execute('SELECT order_id FROM azhura_web_requests WHERE telegram_id=%s AND request_id=%s',(uid(),key)).fetchone()
            if prior:return jsonify(order_id=prior['order_id'],duplicate=True)
        live=get_offers(service,country,kind)
        entries=live.items() if isinstance(live,dict) else enumerate(live)
        match=next((p for k,o in entries if (p:=parse_offer(o,k)) and p['offer_id']==oid),None)
        if not match:return err('Penawaran sudah berubah. Muat ulang harga.',409)
        price=match['price'];cost=match['cost'];order_id='OTP-'+uuid.uuid4().hex[:12].upper()
        with conn() as db:
            db.execute('INSERT INTO azhura_web_requests VALUES (%s,%s,%s)',(uid(),key,order_id))
        try:create_pending_order(uid(),order_id,country,service,price,'premotp')
        except ValueError as exc:
            with conn() as db:db.execute('DELETE FROM azhura_web_requests WHERE telegram_id=%s AND request_id=%s',(uid(),key))
            return err(str(exc),409)
        try:
            with conn() as db:db.execute('UPDATE orders SET service_name=%s,country_name=%s WHERE order_id=%s',(service,country,order_id))
            result=prem_buy(order_id,service,country,oid,kind)
            pid=result.get('id');phone=result.get('phone_number') or result.get('phone') or result.get('number')
            if not pid or not phone:raise RuntimeError('Provider tidak mengirim nomor')
            save_provider_order(order_id,str(pid),int(round(cost)),str(phone),result.get('expired_at') or result.get('expires_at'))
            return jsonify(order_id=order_id,phone=phone,price=price)
        except Exception:
            # Provider may have accepted order despite a timeout. Never auto-refund
            # ambiguous requests: they require reconciliation to avoid double spend.
            with conn() as db:db.execute("UPDATE orders SET status='REVIEW' WHERE order_id=%s AND provider_order_id IS NULL",(order_id,))
            return err('Status pembelian perlu diperiksa admin. Jangan mengulangi order.',503)
    except psycopg.Error:return err('Database sementara bermasalah',503)

# Server 1/2 use independent web adapters so bot-only config is never imported.
from web_servers import catalog as web_catalog, quote_rows as web_quotes, purchase as web_purchase, check_sms as web_check_sms

@app.get('/api/server/<int:server>/services')
def server_services(server):
    if not uid():return err('Login diperlukan',401)
    if server not in (1,2):return err('Server tidak valid')
    try:return jsonify(items=web_catalog(server,'services'))
    except Exception:app.logger.exception('Service catalog failed');return err('Katalog sementara tidak tersedia',503)

@app.get('/api/server/<int:server>/countries/<service>')
def server_countries(server,service):
    if not uid():return err('Login diperlukan',401)
    if server not in (1,2) or len(service)>100:return err('Pilihan tidak valid')
    try:return jsonify(items=web_catalog(server,'countries',service))
    except Exception:app.logger.exception('Country catalog failed');return err('Negara sementara tidak tersedia',503)

@app.get('/api/server/<int:server>/quotes/<service>/<country>')
def server_quotes(server,service,country):
    if not uid():return err('Login diperlukan',401)
    if server not in (1,2) or max(len(service),len(country))>100:return err('Pilihan tidak valid')
    try:
        rows=web_quotes(server,service,country)
        # Never trust a price or provider metadata sent by the browser.
        with conn() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS azhura_web_quotes
                (quote_id TEXT PRIMARY KEY, telegram_id BIGINT NOT NULL, server INT NOT NULL,
                 service TEXT NOT NULL,country TEXT NOT NULL,metadata JSONB NOT NULL,
                 cost_idr BIGINT NOT NULL,price_idr BIGINT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''')
            result=[]
            for row in rows:
                if row['stock']<=0:continue
                qid=secrets.token_urlsafe(24);price=hitung_harga_jual_idr(row['cost_idr'])
                if price<=0:continue
                db.execute('INSERT INTO azhura_web_quotes(quote_id,telegram_id,server,service,country,metadata,cost_idr,price_idr) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                    (qid,uid(),server,service,country,json.dumps(row['metadata']),row['cost_idr'],price))
                result.append(dict(quote_id=qid,price=price,stock=row['stock'],label=row['label']))
        return jsonify(items=result)
    except Exception:app.logger.exception('Quote lookup failed');return err('Harga sementara tidak tersedia',503)

@app.post('/api/server/<int:server>/buy')
def server_buy(server):
    if not uid():return err('Login diperlukan',401)
    if server not in (1,2):return err('Server tidak valid')
    if not ENABLE_ORDER:return err('Order web belum diaktifkan',503)
    d=request.get_json(silent=True) or {};qid=str(d.get('quote_id',''));key=str(d.get('request_id',''))
    if not qid or not key or len(qid)>100 or len(key)>100:return err('Pilihan tidak valid')
    try:
        # Transaction-level advisory lock serializes requests for the same user.
        with conn() as db:
            db.execute('SELECT pg_advisory_xact_lock(%s)',(int(uid()),))
            db.execute('''CREATE TABLE IF NOT EXISTS azhura_web_requests
                (telegram_id BIGINT NOT NULL,request_id TEXT NOT NULL,order_id TEXT NOT NULL,
                 PRIMARY KEY(telegram_id,request_id))''')
            prior=db.execute('SELECT order_id FROM azhura_web_requests WHERE telegram_id=%s AND request_id=%s',(uid(),key)).fetchone()
            if prior:return jsonify(order_id=prior['order_id'],duplicate=True)
            q=db.execute('''SELECT * FROM azhura_web_quotes WHERE quote_id=%s AND telegram_id=%s AND server=%s
                 AND created_at>NOW()-INTERVAL '90 seconds' FOR UPDATE''',(qid,uid(),server)).fetchone()
            if not q:return err('Harga kedaluwarsa. Muat ulang harga.',409)
            # A quote may be used once, even if the browser submits a new request ID.
            db.execute('DELETE FROM azhura_web_quotes WHERE quote_id=%s',(qid,))
            order_id='OTP-'+uuid.uuid4().hex[:12].upper()
            db.execute('INSERT INTO azhura_web_requests VALUES(%s,%s,%s)',(uid(),key,order_id))
        try:create_pending_order(uid(),order_id,q['country'],q['service'],q['price_idr'],'5sim' if server==1 else 'rumahotp')
        except ValueError as exc:
            with conn() as db:db.execute('DELETE FROM azhura_web_requests WHERE telegram_id=%s AND request_id=%s',(uid(),key))
            return err(str(exc),409)
        # A provider timeout is ambiguous. Never auto-refund an unknown outcome.
        try:
            result=web_purchase(server,q['service'],q['country'],q['metadata'])
            pid=result.get('id');phone=result.get('phone')
            if not pid or not phone:raise RuntimeError('Provider tidak mengembalikan nomor')
            save_provider_order(order_id,str(pid),int(q['cost_idr']),str(phone),result.get('expired_at'))
            with conn() as db:db.execute('UPDATE orders SET status=%s,service_name=%s,country_name=%s WHERE order_id=%s',('WAITING_OTP',q['service'],q['country'],order_id))
            return jsonify(order_id=order_id,phone=phone,price=q['price_idr'])
        except Exception:
            app.logger.exception('Provider order needs reconciliation: %s',order_id)
            with conn() as db:db.execute("UPDATE orders SET status='REVIEW' WHERE order_id=%s AND provider_order_id IS NULL",(order_id,))
            return err('Status pembelian perlu diperiksa admin. Jangan mengulangi order.',503)
    except psycopg.Error:app.logger.exception('Server order database failed');return err('Database sementara bermasalah',503)

@app.get('/api/server/order/<order_id>/status')
def server_order_status(order_id):
    if not uid():return err('Login diperlukan',401)
    if len(order_id)>80:return err('ID tidak valid')
    rows=query('SELECT order_id,provider,provider_order_id,status,otp_code FROM orders WHERE order_id=%s AND telegram_id=%s',(order_id,uid()))
    if not rows:return err('Order tidak ditemukan',404)
    o=rows[0]
    if o['otp_code'] or not o['provider_order_id'] or o['provider'] not in ('5sim','rumahotp'):return jsonify(order=o)
    try:
        data=web_check_sms(o['provider'],o['provider_order_id'])
        if data.get('otp'):
            from database import save_otp_result
            save_otp_result(order_id,data['otp'],data.get('text'))
            o['otp_code']=data['otp']
        return jsonify(order=o)
    except Exception:app.logger.exception('OTP poll failed');return jsonify(order=o)

@app.get('/api/health')
def health():return jsonify(ok=True,web_order_enabled=ENABLE_ORDER)
if __name__=='__main__':app.run(port=int(os.getenv('PORT','8080')),debug=os.getenv('WEB_DEV')=='1')
