# AZHURA ID independent web service. Existing Telegram bot files are unchanged.
# Live web purchases are opt-in until staged provider, DB and refund tests succeed.
#
import os, sys, time, json, hmac, hashlib, secrets, uuid, math
from pathlib import Path
from datetime import datetime, timezone
from base64 import b64encode
from premotp import create_qris as prem_create_qris, qris_id as prem_qris_id, qris_ref_id as prem_qris_ref_id, qris_total_payment as prem_qris_total, qr_image_bytes as prem_qr_image
import requests, psycopg
from psycopg.rows import dict_row
from flask import Flask, request, jsonify, send_from_directory, session, Response
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from database import create_pending_order, refund_order, save_provider_order, fail_and_refund_order, sweep_review_orders
import reseller_core as rc
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
    if request.method in ('POST','PUT','DELETE') and uid() and request.path not in ('/api/auth/telegram','/api/auth/google','/api/rewards/login'):
        if not hmac.compare_digest(str(request.headers.get('X-CSRF-Token','')),str(session.get('csrf','missing'))):return err('Sesi tidak valid. Muat ulang halaman.',403)
def login(user):
    session.clear();session.permanent=True;session['uid']=int(user);session['csrf']=secrets.token_urlsafe(32)
@app.get('/')
def index():return send_from_directory('static','index.html')
@app.get('/manifest.webmanifest')
def manifest():return jsonify(name='AZHURA ID',short_name='AZHURA',start_url='/',display='standalone',background_color='#070d19',theme_color='#0b1a32',icons=[{'src':'/static/logo.jpg','sizes':'any','type':'image/jpeg'}])
@app.get('/api/config')
def config():return jsonify(bot_username=BOT_USERNAME,google_client_id=GOOGLE_ID,web_order_enabled=ENABLE_ORDER,csrf=session.get('csrf',''),cs_username=os.getenv('ADMIN_USERNAME','').strip().lstrip('@'))
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
    return jsonify(user=rows[0] if rows else None,csrf=session.get('csrf',''),is_admin=web_is_admin())
@app.get('/api/deposits')
def web_deposits():
    if not uid():return err('Login diperlukan',401)
    rows=query('SELECT deposit_id,amount,status,payment_method,created_at FROM deposits WHERE telegram_id=%s ORDER BY id DESC LIMIT 30',(uid(),))
    return jsonify(deposits=rows)


# Web-only manual QRIS flow; Telegram bot approval and crediting remain unchanged.
@app.get('/api/deposit/manual/config')
def manual_qris_config():
    if not uid():return err('Login diperlukan',401)
    with conn() as db:
        row=db.execute("SELECT setting_value FROM bot_settings WHERE setting_key='qris_manual_file_id'").fetchone()
        enabled=db.execute("SELECT setting_value FROM bot_settings WHERE setting_key='payment_manual_enabled'").fetchone()
    available=bool(row and row['setting_value']) and (not enabled or str(enabled['setting_value']).lower() in ('1','true','on','yes'))
    return jsonify(available=available,admin_fee=150,unique_min=50,unique_max=150)

@app.get('/api/deposit/manual/qr')
def manual_qris_image():
    if not uid():return err('Login diperlukan',401)
    with conn() as db:
        row=db.execute("SELECT setting_value FROM bot_settings WHERE setting_key='qris_manual_file_id'").fetchone()
    if not row or not row['setting_value']:return err('QRIS manual belum diatur admin',404)
    try:
        meta=requests.get('https://api.telegram.org/bot'+BOT_TOKEN+'/getFile',params={'file_id':row['setting_value']},timeout=10).json()
        if not meta.get('ok'):return err('Gambar QRIS tidak tersedia',503)
        r=requests.get('https://api.telegram.org/file/bot'+BOT_TOKEN+'/'+meta['result']['file_path'],timeout=15)
        r.raise_for_status()
        if len(r.content)>5_000_000:return err('Gambar QRIS terlalu besar',503)
        return Response(r.content,content_type='image/jpeg',headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
    except (requests.RequestException,KeyError,ValueError):return err('Gagal mengambil gambar QRIS',503)

@app.post('/api/deposit/manual/create')
def manual_qris_create():
    if not uid():return err('Login diperlukan',401)
    data=request.get_json(silent=True) or {}
    try:amount=int(data.get('amount',0))
    except (ValueError,TypeError):return err('Nominal tidak valid')
    if amount<1000 or amount>10_000_000:return err('Nominal deposit harus Rp1.000–Rp10.000.000')
    import random
    from psycopg.errors import UniqueViolation
    from datetime import datetime
    for _ in range(12):
        code=random.randint(50,150)
        dep='DEP-'+uuid.uuid4().hex[:12].upper()
        total=amount+150+code
        try:
            with conn() as db:
                row=db.execute("SELECT setting_value FROM bot_settings WHERE setting_key='qris_manual_file_id'").fetchone()
                enabled=db.execute("SELECT setting_value FROM bot_settings WHERE setting_key='payment_manual_enabled'").fetchone()
                if not row or not row['setting_value']:return err('QRIS manual belum diatur admin',503)
                if enabled and str(enabled['setting_value']).lower() not in ('1','true','on','yes'):return err('QRIS manual sedang dinonaktifkan admin',503)
                if db.execute("SELECT 1 FROM deposits WHERE status='PENDING' AND payment_method='MANUAL_QRIS' AND amount=%s AND unique_code=%s LIMIT 1",(amount,code)).fetchone():continue
                db.execute("""INSERT INTO deposits(deposit_id,telegram_id,amount,status,payment_reference,created_at,payment_method,payment_amount,unique_code)
                    VALUES(%s,%s,%s,'PENDING',%s,%s,'MANUAL_QRIS',%s,%s)""",
                    (dep,uid(),amount,row['setting_value'],datetime.now().strftime('%Y-%m-%d %H:%M:%S'),total,code))
            return jsonify(deposit_id=dep,amount=amount,admin_fee=150,unique_code=code,payment_amount=total,status='PENDING')
        except UniqueViolation:continue
        except psycopg.Error:app.logger.exception('Manual QRIS create failed');return err('Gagal membuat deposit',503)
    return err('Kode unik sedang penuh. Coba lagi.',503)

@app.post('/api/deposit/manual/paid')
def manual_qris_paid():
    if not uid():return err('Login diperlukan',401)
    dep=str((request.get_json(silent=True) or {}).get('deposit_id',''))
    if len(dep)>40 or not dep.startswith('DEP-'):return err('ID deposit tidak valid')
    admin=os.getenv('ADMIN_ID','').strip()
    if not admin.isdigit():return err('ADMIN_ID belum diatur di Railway web',503)
    with conn() as db:
        row=db.execute('SELECT deposit_id,status,payment_method,amount,payment_amount,unique_code FROM deposits WHERE deposit_id=%s AND telegram_id=%s',(dep,uid())).fetchone()
        if not row or row['payment_method']!='MANUAL_QRIS':return err('Deposit tidak ditemukan',404)
        if row['status']!='PENDING':return jsonify(status=row['status'])
        db.execute('CREATE TABLE IF NOT EXISTS azhura_web_manual_confirmations (deposit_id TEXT PRIMARY KEY,telegram_id BIGINT NOT NULL,confirmed_at TIMESTAMPTZ NOT NULL DEFAULT NOW())')
        if db.execute('SELECT 1 FROM azhura_web_manual_confirmations WHERE deposit_id=%s',(dep,)).fetchone():
            return jsonify(status='WAITING_ADMIN',message='Konfirmasi sudah dikirim. Menunggu pemeriksaan admin.')
        # Locking is limited to this transaction. Do not credit balance from a user's click.
        payload={'chat_id':admin,'text':f'🔔 KONFIRMASI QRIS MANUAL VIA WEB\n\nUser ID: {uid()}\nDeposit: {dep}\nSaldo: Rp{row["amount"]:,}\nTransfer: Rp{row["payment_amount"]:,}\nKode unik: {row["unique_code"]}\n\nPeriksa mutasi sebelum menyetujui.', 'reply_markup':{'inline_keyboard':[[{'text':'✅ Terima & Tambah Saldo','callback_data':'admin_manual_approve:'+dep}],[{'text':'❌ Tolak','callback_data':'admin_manual_reject:'+dep}]]}}
        try:
            response=requests.post('https://api.telegram.org/bot'+BOT_TOKEN+'/sendMessage',json=payload,timeout=12)
            response.raise_for_status()
            if not response.json().get('ok'):return err('Gagal mengirim konfirmasi ke admin. Coba lagi.',503)
        except (requests.RequestException,ValueError):
            app.logger.exception('Manual QRIS admin notification failed');return err('Notifikasi admin gagal terkirim. Coba lagi.',503)
        db.execute('INSERT INTO azhura_web_manual_confirmations(deposit_id,telegram_id) VALUES(%s,%s)',(dep,uid()))
    return jsonify(status='WAITING_ADMIN',message='Konfirmasi terkirim ke admin. Menunggu verifikasi mutasi.')


@app.post('/api/deposit/cancel')
def web_deposit_cancel():
    if not uid():return err('Login diperlukan',401)
    dep=str((request.get_json(silent=True) or {}).get('deposit_id',''))
    if not dep.startswith('DEP-') or len(dep)>40:return err('ID deposit tidak valid')
    with conn() as db:
        row=db.execute('SELECT payment_method,status FROM deposits WHERE deposit_id=%s AND telegram_id=%s FOR UPDATE',(dep,uid())).fetchone()
        if not row:return err('Invoice tidak ditemukan',404)
        if row['status']!='PENDING':return err('Invoice sudah diproses, tidak dapat dibatalkan',409)
        if row['payment_method']=='MANUAL_QRIS':
            db.execute('CREATE TABLE IF NOT EXISTS azhura_web_manual_confirmations (deposit_id TEXT PRIMARY KEY,telegram_id BIGINT NOT NULL,confirmed_at TIMESTAMPTZ NOT NULL DEFAULT NOW())')
            if db.execute('SELECT 1 FROM azhura_web_manual_confirmations WHERE deposit_id=%s',(dep,)).fetchone():return err('Konfirmasi sudah dikirim ke admin; hubungi admin untuk pembatalan.',409)
        elif row['payment_method']=='PREMOTP_QRIS':
            ref=db.execute('SELECT payment_reference,external_id FROM deposits WHERE deposit_id=%s',(dep,)).fetchone()
            if not ref or not ref['payment_reference']:return err('Invoice provider belum siap.',409)
            try:
                from premotp import get_qris
                live=get_qris(str(ref['payment_reference']))
                state=str(live.get('status') or live.get('payment_status') or (live.get('data') or {}).get('status') or '').lower()
            except Exception:return err('Status provider belum dapat diperiksa. Pembatalan ditunda untuk menghindari kehilangan pembayaran.',503)
            if state in ('paid','success','settlement','settled','completed'):return err('Pembayaran sudah diterima provider. Tunggu saldo masuk.',409)
            if state not in ('pending','waiting','unpaid','created','active','expired','cancelled','canceled','failed'):return err('Status pembayaran belum pasti. Coba lagi.',409)
        elif row['payment_method']=='AUTO':
            return err('Invoice Midtrans tidak dapat dibatalkan melalui web.',409)
        else:return err('Metode deposit tidak didukung',400)
        db.execute("UPDATE deposits SET status='FAILED',confirmed_at=%s WHERE deposit_id=%s AND telegram_id=%s AND status='PENDING'",(datetime.now().strftime('%Y-%m-%d %H:%M:%S'),dep,uid()))
    return jsonify(ok=True,status='FAILED',message='Invoice dibatalkan. Pembayaran yang sudah dikirim tetap perlu diperiksa admin.')

@app.post('/api/deposit/auto/create')
def web_midtrans_create():
    if not uid():return err('Login diperlukan',401)
    key=os.getenv('MIDTRANS_SERVER_KEY','')
    if not key:return err('Midtrans belum dikonfigurasi pada service web',503)
    try:amount=int((request.get_json(silent=True) or {}).get('amount',0))
    except (TypeError,ValueError):return err('Nominal tidak valid')
    if not 1000<=amount<=10_000_000:return err('Nominal harus Rp1.000–Rp10.000.000')
    with conn() as db:
        setting=db.execute("SELECT setting_value FROM bot_settings WHERE setting_key='payment_auto_enabled'").fetchone()
        if setting and str(setting['setting_value']).lower() not in ('1','true','yes','on'):return err('Midtrans dinonaktifkan admin',503)
        dep='DEP-'+uuid.uuid4().hex[:12].upper()
        db.execute("INSERT INTO deposits(deposit_id,telegram_id,amount,status,created_at,payment_method) VALUES(%s,%s,%s,'PENDING',%s,'AUTO')",(dep,uid(),amount,datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    endpoint='https://app.sandbox.midtrans.com/snap/v1/transactions' if os.getenv('MIDTRANS_IS_PRODUCTION','true').lower() in ('false','0','no') else 'https://app.midtrans.com/snap/v1/transactions'
    try:
        r=requests.post(endpoint,json={'transaction_details':{'order_id':dep,'gross_amount':amount},'item_details':[{'id':'DEPOSIT','price':amount,'quantity':1,'name':'Deposit Saldo AZHURA'}],'customer_details':{'first_name':'User '+str(uid())}},headers={'Authorization':'Basic '+b64encode((key+':').encode()).decode(),'Content-Type':'application/json'},timeout=18)
        r.raise_for_status();data=r.json()
        if not data.get('redirect_url') or not data.get('token'):raise ValueError('Respons Midtrans tidak lengkap')
        with conn() as db:db.execute('UPDATE deposits SET payment_reference=%s WHERE deposit_id=%s',(data['token'],dep))
        return jsonify(deposit_id=dep,amount=amount,payment_url=data['redirect_url'],status='PENDING')
    except (requests.RequestException,ValueError):
        app.logger.exception('Midtrans invoice creation failed')
        with conn() as db:db.execute("UPDATE deposits SET status='FAILED' WHERE deposit_id=%s AND status='PENDING'",(dep,))
        return err('Gagal membuat invoice Midtrans',503)

@app.post('/api/deposit/qris/create')
def web_prem_qris_create():
    if not uid():return err('Login diperlukan',401)
    try:amount=int((request.get_json(silent=True) or {}).get('amount',0))
    except (TypeError,ValueError):return err('Nominal tidak valid')
    if not 1000<=amount<=10_000_000:return err('Nominal harus Rp1.000–Rp10.000.000')
    with conn() as db:
        setting=db.execute("SELECT setting_value FROM bot_settings WHERE setting_key='payment_premotp_qris_enabled'").fetchone()
        if setting and str(setting['setting_value']).lower() not in ('1','true','yes','on'):return err('QRIS otomatis dinonaktifkan admin',503)
        dep='DEP-'+uuid.uuid4().hex[:12].upper()
        db.execute("INSERT INTO deposits(deposit_id,telegram_id,amount,status,created_at,payment_method) VALUES(%s,%s,%s,'PENDING',%s,'PREMOTP_QRIS')",(dep,uid(),amount,datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    try:
        data=prem_create_qris(dep,amount);qid=prem_qris_id(data)
        if not qid:raise ValueError('ID QRIS tidak tersedia')
        raw=prem_qr_image(data)
        if not raw:raise ValueError('Gambar QRIS tidak tersedia')
        if len(raw)>3_000_000:raise ValueError('Gambar QRIS terlalu besar')
        ref=prem_qris_ref_id(data) or dep;total=prem_qris_total(data) or amount
        with conn() as db:db.execute('UPDATE deposits SET payment_reference=%s,external_id=%s,payment_amount=%s,payment_total=%s WHERE deposit_id=%s',(str(qid),str(ref),amount,int(total),dep))
        return jsonify(deposit_id=dep,amount=amount,payment_amount=int(total),qr_data='data:image/png;base64,'+b64encode(raw).decode(),status='PENDING')
    except Exception:
        app.logger.exception('Automatic QRIS invoice creation failed')
        with conn() as db:db.execute("UPDATE deposits SET status='FAILED' WHERE deposit_id=%s AND status='PENDING'",(dep,))
        return err('Gagal membuat QRIS otomatis. Silakan coba lagi.',503)

@app.get('/api/terms')
def web_terms():
    return jsonify(sections=[
        {'title':'APA ITU NOKOS?','paragraphs':['Nokos adalah nomor virtual atau sementara untuk menerima kode OTP. Nomor bukan kartu SIM fisik dan tidak selalu dapat digunakan secara permanen.']},
        {'title':'KETENTUAN PEMBELIAN','paragraphs':['Pastikan aplikasi, negara, server, dan harga sesuai sebelum order. Harga dan stok dapat berubah sewaktu-waktu. Setiap nomor memiliki batas waktu penggunaan dan tidak dijamin dapat dipakai kembali.']},
        {'title':'KODE OTP & VERIFIKASI','paragraphs':['Segera gunakan nomor untuk verifikasi dan tunggu OTP. Jangan bagikan OTP kepada siapa pun.']},
        {'title':'REFUND & PEMBATALAN','paragraphs':['Pengembalian saldo mengikuti ketentuan dan status transaksi masing-masing server. Transaksi yang sudah menerima OTP umumnya tidak dapat dibatalkan.']},
        {'title':'RISIKO PENGGUNAAN NOKOS','paragraphs':['Nomor virtual tidak menjamin verifikasi berhasil. Nomor dapat ditolak, dibatasi, atau diblokir oleh aplikasi tujuan. Jangan gunakan untuk akun penting seperti mobile banking atau dompet digital.']},
        {'title':'LARANGAN PENGGUNAAN','paragraphs':['Dilarang menggunakan AZHURA untuk penipuan, spam, penyalahgunaan akun, atau aktivitas yang melanggar hukum.']},
        {'title':'GANGGUAN SERVER','paragraphs':['Gangguan, keterlambatan OTP, atau stok kosong dapat terjadi. Hubungi Customer Service dan sertakan ID transaksi.']},
        {'title':'PERSETUJUAN PENGGUNA','paragraphs':['Dengan melakukan pembelian, pengguna dianggap telah membaca, memahami, dan menyetujui syarat dan ketentuan ini.']}
    ])

@app.get('/api/orders')
def orders():
    if not uid():return err('Login diperlukan',401)
    # Order lama berstatus REVIEW yang tidak pernah mendapat nomor: refund otomatis + FAILED.
    try:sweep_review_orders(uid())
    except Exception:app.logger.exception('Sweep REVIEW gagal')
    rows=query('''SELECT order_id,COALESCE(service_name,service) service,COALESCE(country_name,country) country,
        provider,sell_price,status,created_at,otp_code,previous_otp_code,sms_text,phone,expired_at,refund_status
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
        got_number=False
        try:
            with conn() as db:db.execute('UPDATE orders SET service_name=%s,country_name=%s WHERE order_id=%s',(service,country,order_id))
            result=prem_buy(order_id,service,country,oid,kind)
            pid=result.get('id');phone=result.get('phone_number') or result.get('phone') or result.get('number')
            if not pid or not phone:raise RuntimeError('Provider tidak mengirim nomor')
            got_number=True
            save_provider_order(order_id,str(pid),int(round(cost)),str(phone),result.get('expired_at') or result.get('expires_at'))
            return jsonify(order_id=order_id,phone=phone,price=price)
        except Exception:
            app.logger.exception('Pembelian Server 3 web gagal order=%s got_number=%s',order_id,got_number)
            # Vendor gagal menyiapkan nomor -> order FAILED + saldo kembali.
            if not got_number:
                try:
                    refund=fail_and_refund_order(order_id,'Vendor gagal menyiapkan nomor (web)')
                    return jsonify(error='Gagal mendapatkan nomor. Saldo sudah dikembalikan.',status='FAILED',balance=refund.get('balance')),409
                except Exception:
                    app.logger.exception('Refund otomatis gagal order=%s',order_id)
            # Nomor sudah didapat tapi gagal disimpan: tahan untuk admin (status terpisah agar tidak ikut di-refund otomatis).
            with conn() as db:db.execute("UPDATE orders SET status='PROVIDER_REVIEW' WHERE order_id=%s AND provider_order_id IS NULL",(order_id,))
            return err('Status pembelian perlu diperiksa admin. Jangan mengulangi order.',503)
    except psycopg.Error:return err('Database sementara bermasalah',503)

# Server 1/2 use independent web adapters so bot-only config is never imported.
from azhura_web.web_servers import catalog as web_catalog, quote_rows as web_quotes, purchase as web_purchase, check_sms as web_check_sms, ProviderRejected

@app.get('/api/live-traffic')
def web_live_traffic():
    # Shared order history: Telegram and web, public-safe fields only. No phone/OTP/order ID.
    if not uid():return err('Login diperlukan',401)
    try:
        rows=query("""SELECT o.id,o.created_at,COALESCE(NULLIF(u.first_name,''),NULLIF(u.username,''),'Pengguna') display_name,
            COALESCE(NULLIF(o.service_name,''),NULLIF(o.service,''),'Layanan') service,
            COALESCE(o.provider,'') provider,o.sell_price
            FROM orders o LEFT JOIN users u ON u.telegram_id=o.telegram_id
            WHERE o.created_at IS NOT NULL AND o.sell_price > 0
              AND UPPER(o.status) NOT IN ('FAILED','REFUNDED','CANCELLED','CANCELED','REVIEW','PROVIDER_REVIEW')
            ORDER BY o.id DESC LIMIT 12""")
        items=[]
        for r in rows:
            name=str(r['display_name'] or 'Pengguna').strip()
            masked=(name[:2]+'***') if len(name)>2 else 'Pengguna***'
            provider=str(r['provider'] or '').lower()
            server='Server 1' if provider in ('5sim','fivesim') else 'Server 2' if 'rumah' in provider else 'Server 3' if 'prem' in provider else 'Server 4' if 'grizzly' in provider else 'AZHURA'
            items.append({'id':r['id'],'at':str(r['created_at']),'user':masked,'service':str(r['service'])[:55],
                          'server':server,'price':int(r['sell_price'] or 0)})
        return jsonify(items=items)
    except Exception:
        app.logger.exception('Live traffic read failed')
        return jsonify(items=[])

@app.get('/api/server/<int:server>/services')
def server_services(server):
    if not uid():return err('Login diperlukan',401)
    if server not in (1,2,4):return err('Server tidak valid')
    try:return jsonify(items=web_catalog(server,'services'))
    except Exception:app.logger.exception('Service catalog failed');return err('Katalog sementara tidak tersedia',503)

@app.get('/api/server/<int:server>/countries/<service>')
def server_countries(server,service):
    if not uid():return err('Login diperlukan',401)
    if server not in (1,2,4) or len(service)>100:return err('Pilihan tidak valid')
    try:return jsonify(items=web_catalog(server,'countries',service))
    except Exception:app.logger.exception('Country catalog failed');return err('Negara sementara tidak tersedia',503)

@app.get('/api/server/<int:server>/quotes/<service>/<country>')
def server_quotes(server,service,country):
    if not uid():return err('Login diperlukan',401)
    if server not in (1,2,4) or max(len(service),len(country))>100:return err('Pilihan tidak valid')
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
    if server not in (1,2,4):return err('Server tidak valid')
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
        try:create_pending_order(uid(),order_id,q['country'],q['service'],q['price_idr'],'5sim' if server==1 else 'rumahotp' if server==2 else 'grizzly')
        except ValueError as exc:
            with conn() as db:db.execute('DELETE FROM azhura_web_requests WHERE telegram_id=%s AND request_id=%s',(uid(),key))
            return err(str(exc),409)
        # Vendor gagal menyiapkan nomor (ditolak, timeout, respons tanpa nomor) -> order FAILED + saldo kembali.
        # Hanya jika nomor SUDAH didapat lalu penyimpanan gagal, order ditahan REVIEW (jangan refund nomor yang aktif).
        got_number=False
        try:
            result=web_purchase(server,q['service'],q['country'],q['metadata'])
            pid=result.get('id');phone=result.get('phone')
            if not pid or not phone:raise ProviderRejected('Provider tidak mengembalikan nomor')
            got_number=True
            save_provider_order(order_id,str(pid),int(q['cost_idr']),str(phone),result.get('expired_at'))
            with conn() as db:db.execute('UPDATE orders SET status=%s,service_name=%s,country_name=%s WHERE order_id=%s',('WAITING_OTP',q['service'],q['country'],order_id))
            return jsonify(order_id=order_id,phone=phone,price=q['price_idr'])
        except ProviderRejected as rej:
            app.logger.warning('Provider menolak pembelian order=%s server=%s: %s',order_id,server,rej)
            # Only explicit provider rejection is safe to refund immediately.
            try:
                refund=fail_and_refund_order(order_id,'Provider menolak pembelian web')
                return jsonify(error='Gagal mendapatkan nomor. Saldo sudah dikembalikan.',status='FAILED',balance=refund.get('balance')),409
            except Exception:
                app.logger.exception('Explicit rejection refund failed: %s',order_id)
                return err('Pembelian gagal; pengembalian saldo perlu diperiksa admin.',503)
        except Exception:
            app.logger.exception('Pembelian web gagal order=%s got_number=%s',order_id,got_number)
            if not got_number:
                try:
                    refund=fail_and_refund_order(order_id,'Vendor gagal menyiapkan nomor (web)')
                    return jsonify(error='Gagal mendapatkan nomor. Saldo sudah dikembalikan.',status='FAILED',balance=refund.get('balance')),409
                except Exception:
                    app.logger.exception('Refund otomatis gagal order=%s',order_id)
            # Nomor sudah didapat tapi gagal disimpan: tahan untuk admin (status terpisah agar tidak ikut di-refund otomatis).
            with conn() as db:db.execute("UPDATE orders SET status='PROVIDER_REVIEW' WHERE order_id=%s AND provider_order_id IS NULL",(order_id,))
            return err('Status pembelian perlu diperiksa admin. Jangan mengulangi order.',503)
    except psycopg.Error:app.logger.exception('Server order database failed');return err('Database sementara bermasalah',503)

@app.get('/api/server/order/<order_id>/status')
def server_order_status(order_id):
    if not uid():return err('Login diperlukan',401)
    if len(order_id)>80:return err('ID tidak valid')
    rows=query('SELECT order_id,provider,provider_order_id,status,otp_code,previous_otp_code,created_at,expired_at,refund_status FROM orders WHERE order_id=%s AND telegram_id=%s',(order_id,uid()))
    if not rows:return err('Order tidak ditemukan',404)
    o=rows[0]
    if str(o['status']).upper() in ('COMPLETED','CANCELLED','CANCELED','REFUNDED','FAILED') or o['otp_code'] or not o['provider_order_id'] or o['provider'] not in ('5sim','rumahotp','premotp','grizzly'):return jsonify(order=o)
    try:
        if o['provider']=='premotp':
            raw=prem_status(o['provider_order_id'])
            messages=raw.get('sms') or raw.get('messages') or []
            first=messages[0] if isinstance(messages,list) and messages else {}
            data={'otp':raw.get('otp_code') or raw.get('otp') or (first.get('code') if isinstance(first,dict) else None), 'text':raw.get('sms_text') or (first.get('text') if isinstance(first,dict) else None)}
        else:data=web_check_sms(o['provider'],o['provider_order_id'])
        if data.get('otp') and str(data['otp'])!=str(o.get('previous_otp_code') or ''):
            from database import save_otp_result, mark_order_success
            save_otp_result(order_id,data['otp'],data.get('text'))
            changed=bool(mark_order_success(order_id))
            with conn() as db:
                moved=db.execute("UPDATE orders SET status='SUCCESS' WHERE order_id=%s AND telegram_id=%s AND status='WAITING_OTP'",(order_id,uid()))
                changed=changed or moved.rowcount>0
            if changed:
                # Bot Telegram yang mengirim pesan OTP ke user + channel traffic (format sama dengan order bot).
                try:
                    with conn() as db:
                        db.execute('''CREATE TABLE IF NOT EXISTS web_otp_notify (order_id TEXT PRIMARY KEY,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),notified BOOLEAN NOT NULL DEFAULT FALSE,attempts INT NOT NULL DEFAULT 0)''')
                        db.execute('''INSERT INTO web_otp_notify(order_id) VALUES(%s)
                            ON CONFLICT (order_id) DO UPDATE SET notified=FALSE,attempts=0,created_at=NOW()''',(order_id,))
                except Exception:app.logger.exception('Antrean notifikasi OTP web gagal: %s',order_id)
            o['otp_code']=data['otp'];o['status']='SUCCESS'
        return jsonify(order=o)
    except Exception:app.logger.exception('OTP poll failed');return jsonify(order=o)

# Web cancellation is opt-in and never credits a user before provider confirmation.
@app.post('/api/order/cancel')
def web_cancel_order():
    if not uid():return err('Login diperlukan',401)
    if not ENABLE_ORDER:return err('Transaksi web belum diaktifkan',503)
    oid=str((request.get_json(silent=True) or {}).get('order_id',''))
    if not oid.startswith('OTP-') or len(oid)>80:return err('ID order tidak valid')
    try:
        # A session-level advisory lock prevents two web workers from cancelling
        # the same order concurrently. The existing refund helper locks DB rows.
        with conn() as lock_db:
            lock_db.execute('SELECT pg_advisory_lock(hashtext(%s))',(oid,))
            try:
                with conn() as db:
                    order=db.execute("SELECT order_id,provider,provider_order_id,status,otp_code, created_at,refund_status FROM orders WHERE order_id=%s AND telegram_id=%s",(oid,uid())).fetchone()
                if not order:return err('Order tidak ditemukan',404)
                if order['refund_status']=='REFUNDED':return jsonify(status='REFUNDED',message='Saldo sudah dikembalikan.')
                if order['otp_code'] or order['status'] in ('SUCCESS','COMPLETED'):
                    return err('OTP sudah diterima. Pembatalan tidak tersedia.',409)
                if order['status'] not in ('PENDING','WAITING_OTP'):
                    return err('Status order belum memungkinkan pembatalan.',409)
                pid=order['provider_order_id'];provider=str(order['provider'] or '').lower()
                started=order['created_at']
                # Server 4 (grizzly) tidak memakai batas 120 detik tetap: waktu proses batal mengikuti vendor.
                if started and provider!='grizzly':
                    if isinstance(started,str):started=datetime.fromisoformat(started)
                    now=datetime.now(started.tzinfo) if started.tzinfo else datetime.now()
                    seconds=(now-started).total_seconds()
                    if seconds<120:return err('Tunggu '+str(max(1,math.ceil((120-seconds)/60)))+' menit sebelum membatalkan.',409)
                if not pid:return err('ID provider belum tersedia; admin perlu memeriksa pesanan.',409)
                verified=False
                if provider=='5sim':
                    from azhura_web.web_servers import get5
                    try:
                        key=os.getenv('FIVESIM_API_KEY','').strip()
                        if not key:raise RuntimeError('Kunci 5SIM belum tersedia')
                        requests.get('https://5sim.net/v1/user/cancel/'+str(pid),headers={'Authorization':'Bearer '+key,'Accept':'application/json'},timeout=20).raise_for_status()
                    except (requests.RequestException,RuntimeError):app.logger.warning('5SIM cancel request failed order=%s',oid)
                    state=get5('user/check/'+str(pid),auth=True)
                    verified=str(state.get('status','')).lower() in ('canceled','cancelled','expired','timeout')
                elif provider=='rumahotp':
                    from azhura_web.web_servers import get2
                    state=get2('v1/orders/get_status',{'order_id':pid}) or {}
                    if str(state.get('status','')).lower() not in ('cancel','canceled','cancelled'):
                        try:get2('v1/orders/set_status',{'order_id':pid,'status':'cancel'})
                        except (requests.RequestException,RuntimeError):pass
                        state=get2('v1/orders/get_status',{'order_id':pid}) or {}
                    verified=str(state.get('status','')).lower() in ('cancel','canceled','cancelled')
                elif provider=='premotp':
                    from premotp import cancel_order as prem_cancel
                    try:prem_cancel(pid)
                    except (requests.RequestException,RuntimeError):pass
                    state=prem_status(pid)
                    verified=str(state.get('status','')).lower() in ('cancel','canceled','cancelled','expired','timeout')
                elif provider=='grizzly':
                    # Server 4: ikuti proses vendor secara realtime. Endpoint ini idempoten dan dipanggil
                    # berulang oleh web selama vendor belum membatalkan; refund hanya setelah vendor batal.
                    from grizzlysms import cancel_attempt as g4_attempt
                    with conn() as db:
                        db.execute('''CREATE TABLE IF NOT EXISTS web_cancel_requests (order_id TEXT PRIMARY KEY,requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),last_status TEXT,attempts INT NOT NULL DEFAULT 0)''')
                        db.execute('INSERT INTO web_cancel_requests(order_id) VALUES(%s) ON CONFLICT (order_id) DO NOTHING',(oid,))
                        req=db.execute('SELECT EXTRACT(EPOCH FROM (NOW()-requested_at))::int AS elapsed FROM web_cancel_requests WHERE order_id=%s',(oid,)).fetchone()
                    elapsed=int((req or {}).get('elapsed') or 0)
                    if elapsed>20*60:
                        with conn() as db:db.execute('DELETE FROM web_cancel_requests WHERE order_id=%s',(oid,))
                        return err('Vendor Server 4 belum mengonfirmasi pembatalan setelah 20 menit. Saldo belum dikembalikan; hubungi admin.',409)
                    attempt=g4_attempt(pid)
                    g4state=attempt.get('state');g4status=str(attempt.get('provider_status') or '')[:120]
                    with conn() as db:db.execute('UPDATE web_cancel_requests SET last_status=%s,attempts=attempts+1 WHERE order_id=%s',(g4status,oid))
                    if g4state=='otp_received':
                        # Kode OTP muncul di vendor saat proses batal: JANGAN batal/refund, teruskan OTP ke user.
                        with conn() as db:db.execute('DELETE FROM web_cancel_requests WHERE order_id=%s',(oid,))
                        snap=attempt.get('data') or {}
                        otp=snap.get('otp');otp_text=snap.get('text')
                        if otp:
                            try:
                                from database import save_otp_result, mark_order_success
                                save_otp_result(oid,otp,otp_text)
                                changed=bool(mark_order_success(oid))
                                with conn() as db:
                                    moved=db.execute("UPDATE orders SET status='SUCCESS' WHERE order_id=%s AND telegram_id=%s AND status='WAITING_OTP'",(oid,uid()))
                                    changed=changed or moved.rowcount>0
                                if changed:
                                    try:
                                        with conn() as db:
                                            db.execute('''CREATE TABLE IF NOT EXISTS web_otp_notify (order_id TEXT PRIMARY KEY,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),notified BOOLEAN NOT NULL DEFAULT FALSE,attempts INT NOT NULL DEFAULT 0)''')
                                            db.execute('''INSERT INTO web_otp_notify(order_id) VALUES(%s)
                                                ON CONFLICT (order_id) DO UPDATE SET notified=FALSE,attempts=0,created_at=NOW()''',(oid,))
                                    except Exception:app.logger.exception('Antrean notifikasi OTP web gagal: %s',oid)
                            except Exception:app.logger.exception('Simpan OTP saat batal Server 4 gagal: %s',oid)
                        return jsonify(status='OTP_RECEIVED',message='OTP sudah diterima dari vendor. Pesanan diteruskan, pembatalan tidak diperlukan.',otp_code=otp)
                    if g4state!='cancelled':
                        return jsonify(status='PENDING_VENDOR',message='Menunggu vendor Server 4 menyelesaikan pembatalan. Saldo otomatis kembali begitu vendor berhasil batal.',elapsed=elapsed,vendor_status=g4status or 'menunggu vendor',retry_after=5),202
                    verified=True
                else:return err('Provider order tidak dikenal. Hubungi admin.',409)
                if not verified:return err('Provider belum mengonfirmasi pembatalan. Saldo belum dikembalikan; coba lagi atau hubungi admin.',409)
                # Recheck OTP after network call; if another worker recorded an OTP,
                # do not issue a refund.
                with conn() as db:
                    latest=db.execute('SELECT otp_code,status FROM orders WHERE order_id=%s AND telegram_id=%s',(oid,uid())).fetchone()
                if not latest or latest['otp_code'] or latest['status'] in ('SUCCESS','COMPLETED'):
                    return err('OTP sudah diterima atau order selesai; refund ditahan untuk pemeriksaan.',409)
                refund=refund_order(oid,'Pembatalan web dikonfirmasi provider')
                if provider=='grizzly':
                    try:
                        with conn() as db:db.execute('DELETE FROM web_cancel_requests WHERE order_id=%s',(oid,))
                    except Exception:app.logger.warning('Cleanup web_cancel_requests gagal: %s',oid)
                return jsonify(status='REFUNDED',message='Pembatalan disetujui provider. Saldo sudah dikembalikan.',balance=refund.get('balance'))
            finally:lock_db.execute('SELECT pg_advisory_unlock(hashtext(%s))',(oid,))
    except (psycopg.Error,requests.RequestException,RuntimeError,ValueError) as ex:
        app.logger.exception('Web cancel failed %s',oid)
        return err('Pembatalan belum selesai. Saldo belum otomatis dikembalikan; hubungi admin bila berlanjut.',503)

@app.get('/api/deposit/<deposit_id>/status')
def web_deposit_status(deposit_id):
    if not uid():return err('Login diperlukan',401)
    if not deposit_id.startswith('DEP-') or len(deposit_id)>40:return err('ID deposit tidak valid')
    rows=query('SELECT deposit_id,status,amount,payment_method,created_at FROM deposits WHERE deposit_id=%s AND telegram_id=%s',(deposit_id,uid()))
    if not rows:return err('Deposit tidak ditemukan',404)
    # Bot's existing webhook/poller is the only authority that credits payment.
    return jsonify(deposit=rows[0])

@app.post('/api/order/resend')
def web_resend_order():
    if not uid():return err('Login diperlukan',401)
    if not ENABLE_ORDER:return err('Transaksi web belum diaktifkan',503)
    oid=str((request.get_json(silent=True) or {}).get('order_id',''))
    if len(oid)>80:return err('ID tidak valid')
    with conn() as lock_db:
        lock_db.execute('SELECT pg_advisory_lock(hashtext(%s))',(oid,))
        try:
            with conn() as db:
                o=db.execute('SELECT provider,provider_order_id,status,otp_code,expired_at FROM orders WHERE order_id=%s AND telegram_id=%s',(oid,uid())).fetchone()
            if not o:return err('Pesanan tidak ditemukan',404)
            if str(o['status']).upper()!='SUCCESS' or not o['otp_code']:return err('Resend hanya tersedia setelah OTP pertama diterima.',409)
            exp=o['expired_at']
            if exp:
                if isinstance(exp,str):exp=datetime.fromisoformat(exp)
                now=datetime.now(exp.tzinfo) if exp.tzinfo else datetime.now()
                if exp<=now:return err('Nomor sudah kedaluwarsa. Resend OTP tidak tersedia.',409)
            provider=str(o['provider'] or '').lower();pid=o['provider_order_id']
            if not pid:return err('ID provider belum tersedia.',409)
            if provider=='5sim':return err('Provider ini tidak dapat melakukan Kirim Ulang OTP.',409)
            try:
                if provider=='rumahotp':
                    from azhura_web.web_servers import get2
                    result=get2('v1/orders/set_status',{'order_id':pid,'status':'resend'})
                    result={'response':'OK'} if result is not None else {'response':'ERROR'}
                elif provider=='premotp':
                    from premotp import resend_order
                    result=resend_order(pid)
                elif provider=='grizzly':
                    from grizzlysms import resend_otp as g4_resend
                    result=g4_resend(pid)
                else:return err('Provider ini tidak dapat melakukan Kirim Ulang OTP.',409)
            except Exception:
                app.logger.exception('Resend failed for %s',oid)
                return err('Provider belum mengonfirmasi permintaan resend. Coba lagi nanti.',503)
            if not isinstance(result,dict) or str(result.get('response','OK')).upper() not in ('OK','SUCCESS') or result.get('success') is False or result.get('error'):
                return err('Provider ini tidak dapat melakukan Kirim Ulang OTP: '+str((result or {}).get('error','Permintaan ditolak'))[:120],409)
            from database import mark_order_waiting_for_otp
            if not mark_order_waiting_for_otp(oid):return err('Status pesanan berubah. Muat ulang riwayat.',409)
            return jsonify(ok=True,status='PENDING',message='Resend diterima. Menunggu Kode OTP baru.')
        finally:lock_db.execute('SELECT pg_advisory_unlock(hashtext(%s))',(oid,))

@app.post('/api/order/finish')
def web_finish_order():
    if not uid():return err('Login diperlukan',401)
    if not ENABLE_ORDER:return err('Transaksi web belum diaktifkan',503)
    oid=str((request.get_json(silent=True) or {}).get('order_id',''))
    if len(oid)>80:return err('ID tidak valid')
    with conn() as lock_db:
        lock_db.execute('SELECT pg_advisory_lock(hashtext(%s))',(oid,))
        try:
            with conn() as db:
                o=db.execute('SELECT provider,provider_order_id,status,otp_code FROM orders WHERE order_id=%s AND telegram_id=%s',(oid,uid())).fetchone()
            if not o:return err('Pesanan tidak ditemukan',404)
            if o['status']=='COMPLETED':return jsonify(ok=True,status='COMPLETED')
            if o['status']!='SUCCESS' or not o['otp_code']:return err('Pesanan belum siap diselesaikan.',409)
            provider=str(o['provider'] or '').lower();pid=o['provider_order_id']
            if not pid and provider!='premotp':return err('ID provider belum tersedia.',409)
            try:
                if provider=='5sim':
                    from azhura_web.web_servers import get5
                    result=get5('user/finish/'+str(pid),auth=True)
                    result={'response':'OK'} if str(result.get('status','')).lower() in ('finished','success','completed') else {'response':'ERROR'}
                elif provider=='rumahotp':
                    from azhura_web.web_servers import get2
                    result=get2('v1/orders/set_status',{'order_id':pid,'status':'done'})
                    result={'response':'OK'} if result is not None else {'response':'ERROR'}
                elif provider=='premotp':result={'response':'OK'}
                elif provider=='grizzly':
                    from grizzlysms import finish_number as g4_finish
                    result=g4_finish(pid)
                else:return err('Provider tidak dikenal.',409)
            except Exception:
                app.logger.exception('Finish failed for %s',oid)
                return err('Provider belum mengonfirmasi penyelesaian.',503)
            if not isinstance(result,dict) or result.get('response')!='OK':return err('Provider belum menyetujui penyelesaian. Coba lagi.',409)
            from database import mark_order_completed
            if not mark_order_completed(oid):return err('Status pesanan berubah. Muat ulang riwayat.',409)
            return jsonify(ok=True,status='COMPLETED',message='Pesanan Selesai.')
        finally:lock_db.execute('SELECT pg_advisory_unlock(hashtext(%s))',(oid,))


# --- AZHURA WEB ADMIN: isolated admin endpoints; bot handlers are untouched. ---
ADMIN_WEB_ID = int(os.getenv('ADMIN_ID', '0') or '0')
WEB_MAINT_KEYS = {'web': 'azhura_web_maintenance', '1': 'azhura_web_server_1_maintenance',
                  '2': 'azhura_web_server_2_maintenance', '3': 'azhura_web_server_3_maintenance', '4': 'azhura_web_server_4_maintenance',
                  'manual': 'azhura_web_payment_manual_maintenance', 'auto': 'azhura_web_payment_auto_maintenance'}

WEB_MAINT_KEYS['smm']='smm_maintenance'  # saklar SMM PANEL: dibagi dengan bot Telegram (smm.MAINT_KEY)

def web_is_admin():
    return bool(ADMIN_WEB_ID and uid() and int(uid()) == ADMIN_WEB_ID)

def web_admin_required():
    if not uid(): return err('Silakan login terlebih dahulu.', 401)
    if not web_is_admin(): return err('Akses khusus admin.', 403)
    return None

def web_maintenance_flags():
    keys=tuple(WEB_MAINT_KEYS.values())
    with conn() as db:
        rows=db.execute('SELECT setting_key,setting_value FROM bot_settings WHERE setting_key=ANY(%s)', (list(keys),)).fetchall()
    values={r['setting_key']:str(r['setting_value']).lower() in ('1','true','on','yes') for r in rows}
    return {k:values.get(v,False) for k,v in WEB_MAINT_KEYS.items()}

@app.before_request
def web_maintenance_guard():
    # Never block existing OTP polling, cancellation/refunds, login, payment status or callbacks.
    # Block only NEW web orders and NEW deposit invoices while web is in maintenance.
    path=request.path
    if request.method!='POST' or path not in ('/api/prem/buy','/api/deposit/manual/create',
        '/api/deposit/auto/create','/api/deposit/qris/create','/api/server/1/buy',
        '/api/server/2/buy','/api/server/3/buy','/api/server/4/buy'): return None
    if web_is_admin(): return None
    try: flags=web_maintenance_flags()
    except psycopg.Error:
        app.logger.exception('Maintenance flags unavailable; refusing new transaction')
        return err('Layanan sementara tidak tersedia. Coba beberapa saat lagi.',503)
    if flags['web']: return err('Website sedang dalam perbaikan. Silakan kembali beberapa saat lagi.',503)
    if path=='/api/deposit/manual/create' and flags['manual']: return err('QRIS Manual sedang tidak aktif. Silakan gunakan QRIS Otomatis.',503)
    if path=='/api/deposit/qris/create' and flags['auto']: return err('QRIS Otomatis sedang tidak aktif. Silakan gunakan QRIS Manual.',503)
    server=('3' if path=='/api/prem/buy' else path.split('/')[3] if path.startswith('/api/server/') else None)
    if server and flags.get(server): return err('Server '+server+' sedang maintenance. Silakan pilih server lain yang online.',503)

@app.before_request
def web_smm_maintenance_guard():
    # Tutup hanya ORDER SMM BARU saat maintenance. Riwayat/status pesanan tetap dapat dibuka.
    if request.method!='POST' or request.path!='/api/smm/order': return None
    if web_is_admin(): return None
    try: flags=web_maintenance_flags()
    except psycopg.Error:
        app.logger.exception('Maintenance flags unavailable; refusing new SMM order')
        return err('Layanan sementara tidak tersedia. Coba beberapa saat lagi.',503)
    if flags['web']: return err('Website sedang dalam perbaikan. Silakan kembali beberapa saat lagi.',503)
    if flags['smm']: return err('SMM PANEL sedang maintenance. Pemesanan baru ditutup sementara, silakan coba lagi nanti.',503)

@app.get('/api/web/status')
def web_public_status():
    try:return jsonify(maintenance=web_maintenance_flags(),admin=web_is_admin())
    except psycopg.Error:return err('Status layanan tidak tersedia.',503)

@app.get('/admin')
def web_admin_page():
    if not web_is_admin():return err('Halaman khusus admin. Login sebagai admin terlebih dahulu.',403)
    return send_from_directory('static','admin.html')

@app.get('/api/admin/overview')
def web_admin_overview():
    denied=web_admin_required()
    if denied:return denied
    day=request.args.get('date','')
    if not (len(day)==10 and day[4]=='-' and day[7]=='-' and day.replace('-','').isdigit()):
        day=datetime.now(timezone.utc).strftime('%Y-%m-%d')
    with conn() as db:
        users=db.execute('SELECT COUNT(*) n,COALESCE(SUM(balance),0) balance FROM users').fetchone()
        o=db.execute('''SELECT COUNT(*) n,COALESCE(SUM(sell_price),0) gross,
          COUNT(*) FILTER (WHERE UPPER(status) IN ('PENDING','WAITING','WAITING_OTP','PROCESSING')) pending,
          COUNT(*) FILTER (WHERE UPPER(status) IN ('SUCCESS','COMPLETED','FINISHED')) success
          FROM orders WHERE LEFT(created_at,10)=%s''',(day,)).fetchone()
        d=db.execute('''SELECT COUNT(*) n,COALESCE(SUM(amount) FILTER (WHERE UPPER(status) IN ('SUCCESS','COMPLETED','PAID')),0) paid,
          COUNT(*) FILTER (WHERE UPPER(status) IN ('PENDING','WAITING','WAITING_ADMIN')) pending FROM deposits
          WHERE LEFT(created_at,10)=%s''',(day,)).fetchone()
        all_orders=db.execute('SELECT COUNT(*) n FROM orders').fetchone()['n']
        all_paid=db.execute("SELECT COUNT(*) n,COALESCE(SUM(amount),0) amount FROM deposits WHERE UPPER(status) IN ('SUCCESS','COMPLETED','PAID')").fetchone()
        all_pending=db.execute("SELECT COUNT(*) n FROM deposits WHERE UPPER(status) IN ('PENDING','WAITING','WAITING_ADMIN')").fetchone()['n']
    return jsonify(date=day,users=users,orders=o,deposits=d,totals={'orders':all_orders,'paid_deposits':all_paid,'pending_deposits':all_pending},maintenance=web_maintenance_flags(),updated_at=datetime.now(timezone.utc).isoformat())

@app.get('/api/admin/orders')
def web_admin_orders():
    denied=web_admin_required()
    if denied:return denied
    day=request.args.get('date','')
    status=request.args.get('status','').upper().strip()
    search=request.args.get('q','').strip()[:70]
    where=['1=1']; args=[]
    if day:
        try:datetime.strptime(day,'%Y-%m-%d')
        except ValueError:return err('Tanggal tidak valid')
        where.append('LEFT(o.created_at,10)=%s');args.append(day)
    if status=='ACTIVE':where.append("UPPER(o.status) IN ('PENDING','WAITING','WAITING_OTP','PROCESSING')")
    elif status=='DONE':where.append("UPPER(o.status) IN ('SUCCESS','COMPLETED','FINISHED')")
    elif status and status!='ALL':where.append('UPPER(o.status)=%s');args.append(status)
    if search:where.append("(o.order_id ILIKE %s OR CAST(o.telegram_id AS TEXT) ILIKE %s OR COALESCE(o.service_name,o.service,'') ILIKE %s)");args.extend(['%'+search+'%']*3)
    sql='''SELECT o.order_id,o.telegram_id,u.username,COALESCE(o.service_name,o.service) service,
      COALESCE(o.country_name,o.country) country,o.provider,o.phone,o.sell_price,o.status,
      o.refund_status,o.created_at,o.completed_at FROM orders o LEFT JOIN users u ON u.telegram_id=o.telegram_id
      WHERE '''+' AND '.join(where)+' ORDER BY o.id DESC LIMIT 200'
    return jsonify(items=query(sql,tuple(args)))

@app.get('/api/admin/deposits')
def web_admin_deposits():
    denied=web_admin_required()
    if denied:return denied
    day=request.args.get('date',''); status=request.args.get('status','').upper().strip();search=request.args.get('q','').strip()[:70]
    where=['1=1'];args=[]
    if day:
        try:datetime.strptime(day,'%Y-%m-%d')
        except ValueError:return err('Tanggal tidak valid')
        where.append('LEFT(d.created_at,10)=%s');args.append(day)
    if status=='PAID_GROUP':where.append("UPPER(d.status) IN ('SUCCESS','COMPLETED','PAID')")
    elif status=='PENDING_GROUP':where.append("UPPER(d.status) IN ('PENDING','WAITING','WAITING_ADMIN')")
    elif status=='FAILED_GROUP':where.append("UPPER(d.status) IN ('FAILED','CANCELLED','CANCELED','EXPIRED','REJECTED')")
    elif status and status!='ALL':where.append('UPPER(d.status)=%s');args.append(status)
    if search:where.append("(d.deposit_id ILIKE %s OR CAST(d.telegram_id AS TEXT) ILIKE %s OR COALESCE(u.username,'') ILIKE %s)");args.extend(['%'+search+'%']*3)
    sql='''SELECT d.deposit_id,d.telegram_id,u.username,d.amount,d.payment_amount,d.payment_method,
      d.status,d.created_at,d.completed_at,d.confirmed_at FROM deposits d
      LEFT JOIN users u ON u.telegram_id=d.telegram_id WHERE '''+' AND '.join(where)+' ORDER BY d.id DESC LIMIT 200'
    return jsonify(items=query(sql,tuple(args)))

@app.get('/api/admin/users')
def web_admin_users():
    denied=web_admin_required()
    if denied:return denied
    search=request.args.get('q','').strip()[:70]
    if search:
        items=query('''SELECT telegram_id,username,first_name,balance,created_at FROM users
          WHERE CAST(telegram_id AS TEXT) ILIKE %s OR COALESCE(username,'') ILIKE %s OR
          COALESCE(first_name,'') ILIKE %s ORDER BY id DESC LIMIT 100''',tuple(['%'+search+'%']*3))
    else:items=query('SELECT telegram_id,username,first_name,balance,created_at FROM users ORDER BY id DESC LIMIT 100')
    return jsonify(items=items)

@app.get('/api/admin/resellers')
def web_admin_resellers():
    denied=web_admin_required()
    if denied:return denied
    search=request.args.get('q','').strip()[:80]
    archived=request.args.get('archived','0').lower() in ('1','true','yes','on')
    try:
        return jsonify(items=rc.db.admin_list_resellers(search, include_archived=archived, limit=500), archived=archived)
    except psycopg.Error:
        app.logger.exception('Gagal memuat reseller admin')
        return err('Data reseller tidak dapat dimuat.',503)

@app.get('/api/admin/resellers/<int:bot_id>')
def web_admin_reseller_detail(bot_id):
    denied=web_admin_required()
    if denied:return denied
    archived=request.args.get('archived','0').lower() in ('1','true','yes','on')
    try:
        data=rc.db.admin_get_reseller_detail(bot_id, archived=archived, limit=200)
    except psycopg.Error:
        app.logger.exception('Gagal memuat detail reseller %s',bot_id)
        return err('Detail reseller tidak dapat dimuat.',503)
    if not data:return err('Reseller tidak ditemukan.',404)
    return jsonify(**data)

@app.post('/api/admin/resellers/<int:bot_id>/block')
def web_admin_reseller_block(bot_id):
    denied=web_admin_required()
    if denied:return denied
    data=request.get_json(silent=True) or {}
    if type(data.get('blocked')) is not bool:return err('Status block tidak valid')
    try:
        bot=rc.db.admin_set_reseller_block(bot_id,data['blocked'])
    except ValueError as e:return err(str(e),404)
    except psycopg.Error:
        app.logger.exception('Gagal mengubah block reseller %s',bot_id)
        return err('Gagal mengubah status reseller.',503)
    return jsonify(ok=True,bot=bot)

@app.post('/api/admin/resellers/<int:bot_id>/delete')
def web_admin_reseller_delete(bot_id):
    denied=web_admin_required()
    if denied:return denied
    try:
        bot=rc.db.admin_delete_reseller(bot_id)
    except ValueError as e:return err(str(e),409)
    except psycopg.Error:
        app.logger.exception('Gagal menghapus reseller %s',bot_id)
        return err('Gagal menghapus bot reseller.',503)
    return jsonify(ok=True,deleted_id=int(bot['id']))


@app.post('/api/admin/maintenance')
def web_admin_maintenance():
    denied=web_admin_required()
    if denied:return denied
    data=request.get_json(silent=True) or {}
    target=str(data.get('target',''))
    if target not in WEB_MAINT_KEYS or type(data.get('enabled')) is not bool:return err('Pilihan maintenance tidak valid')
    key=WEB_MAINT_KEYS[target]
    with conn() as db:
        db.execute('''INSERT INTO bot_settings(setting_key,setting_value) VALUES (%s,%s)
          ON CONFLICT(setting_key) DO UPDATE SET setting_value=EXCLUDED.setting_value''',
          (key,'true' if data['enabled'] else 'false'))
    app.logger.warning('Web admin %s set maintenance %s=%s',uid(),target,data['enabled'])
    return jsonify(ok=True,maintenance=web_maintenance_flags())


# ---------------- SMM PANEL (SIMURU) - web ----------------
# Memakai modul smm.py yang sama dengan bot Telegram (saldo, harga, refund, maintenance = satu sistem).
import re as _re
import smm as smm_core

def _smm_json(row,*,admin=False):
    """Baris DB -> dict JSON aman (Decimal->int). User biasa tidak menerima harga modal/error internal."""
    if not row:return row
    out={}
    for k,v in dict(row).items():
        if not admin and k in ('provider_cost','cost_per_1000','sell_per_1000','error','idem_key','provider_status','telegram_id','updated_at'):continue
        out[k]=int(v) if hasattr(v,'as_tuple') else v
    return out

def _smm_fail(e):
    if isinstance(e,smm_core.SmmError):return err(str(e),getattr(e,'http_status',None) if getattr(e,'http_status',None) in (400,404,409,429) else 409 if isinstance(e,smm_core.SmmAmbiguous) else 400)
    app.logger.exception('SMM web error')
    return err('Layanan SMM sedang bermasalah. Coba lagi sebentar.',500)

@app.get('/api/smm/platforms')
def smm_platforms():
    if not uid():return err('Login diperlukan',401)
    try:
        items=[]
        for p in smm_core.get_platforms():
            cheapest=next((p[k] for k in ('min_price','cheapest_price','cheapest','price_min','min') if isinstance(p.get(k),(int,float,str)) and str(p.get(k)).replace('.','',1).isdigit()),None)
            items.append({'platform':p['platform'],'total':p.get('total') or p.get('count') or p.get('services') or 0,
                          'min_price':smm_core.sell_price_per_1000(cheapest) if cheapest is not None else 0})
        return jsonify(items=items)
    except Exception as e:return _smm_fail(e)

@app.get('/api/smm/kinds')
def smm_kinds():
    if not uid():return err('Login diperlukan',401)
    platform=request.args.get('platform','').strip()[:60]
    if not platform:return err('Platform wajib diisi')
    try:return jsonify(items=smm_core.get_kinds(platform))
    except Exception as e:return _smm_fail(e)

@app.get('/api/smm/services')
def smm_services():
    if not uid():return err('Login diperlukan',401)
    platform=request.args.get('platform','').strip()[:60];kind=request.args.get('kind','').strip()[:60];q=request.args.get('q','').strip()[:80]
    if not platform or not kind:return err('Platform dan jenis wajib diisi')
    try:
        fset=smm_core.featured_ids(platform,kind)
        raw=smm_core.get_services(platform,kind,q or None,200)
        raw=sorted(raw,key=lambda s:(0 if smm_core.is_featured(s,fset) else 1,s.get('price') or 0))
        items=[]
        for s in raw:
            pub=smm_core.public_service(s);pub['custom_comments']=smm_core.needs_custom_comments(s)
            pub['featured']=smm_core.is_featured(s,fset);pub['success_rate']=smm_core.service_rate(s)
            items.append(pub)
        return jsonify(items=items,notice=smm_core.delay_notice(platform,any(i['featured'] for i in items)))
    except Exception as e:return _smm_fail(e)

@app.post('/api/smm/order')
def smm_order():
    # Maintenance SMM sudah dijaga oleh web_smm_maintenance_guard (before_request).
    if not uid():return err('Login diperlukan',401)
    d=request.get_json(silent=True) or {}
    try:
        sid=int(d.get('service_id'));qty=int(d.get('quantity') or 0)
        expected=int(d['expected_price']) if d.get('expected_price') is not None else None
    except (TypeError,ValueError):return err('Data pesanan tidak valid')
    idem=_re.sub(r'[^a-zA-Z0-9]','',str(d.get('idem_key') or ''))[:40]
    if len(idem)<8:return err('Permintaan tidak valid. Muat ulang halaman.')
    target=str(d.get('target') or '').strip()
    try:
        s=smm_core.find_fresh_service(sid,str(d.get('platform') or '')[:60] or None,str(d.get('kind') or '')[:60] or None,str(d.get('title') or '')[:120] or None)
        if not s:return err('Layanan tidak ditemukan atau sudah berubah. Muat ulang daftar layanan.',409)
        order=smm_core.create_order(int(uid()),s,target,qty,d.get('custom_comments'),'WEB','web-%s-%s'%(uid(),idem),expected)
        bal=query('SELECT balance FROM users WHERE telegram_id=%s',(uid(),))
        return jsonify(ok=True,order=_smm_json(order),balance=int(bal[0]['balance']) if bal else None)
    except Exception as e:return _smm_fail(e)

@app.get('/api/smm/orders')
def smm_orders():
    if not uid():return err('Login diperlukan',401)
    try:return jsonify(orders=[_smm_json(o) for o in smm_core.list_user_orders(int(uid()),30,0)])
    except Exception as e:return _smm_fail(e)

@app.post('/api/smm/order/status')
def smm_order_status():
    if not uid():return err('Login diperlukan',401)
    local_id=str((request.get_json(silent=True) or {}).get('local_id') or '')[:40]
    try:
        if not smm_core.get_smm_order(local_id,int(uid())):return err('Pesanan tidak ditemukan.',404)
        return jsonify(order=_smm_json(smm_core.sync_order(local_id)))
    except Exception as e:return _smm_fail(e)

@app.get('/api/admin/smm/orders')
def web_admin_smm_orders():
    denied=web_admin_required()
    if denied:return denied
    day=request.args.get('date','');status=request.args.get('status','').upper().strip()[:20];search=request.args.get('q','').strip()[:70]
    if day:
        try:datetime.strptime(day,'%Y-%m-%d')
        except ValueError:return err('Tanggal tidak valid')
    try:
        items=smm_core.list_all_orders(200,0,day or None,status or None,search or None)
        return jsonify(configured=smm_core.is_configured(),summary=_smm_json(smm_core.admin_summary(),admin=True),items=[_smm_json(o,admin=True) for o in items])
    except Exception as e:return _smm_fail(e)

@app.post('/api/admin/smm/orders/sync')
def web_admin_smm_sync():
    denied=web_admin_required()
    if denied:return denied
    try:return jsonify(ok=True,synced=smm_core.sync_active_orders(25))
    except Exception as e:return _smm_fail(e)


# ---------------- RESELLER (panel web) ----------------
def reseller_payload():
    data=rc.panel_data(uid())
    if not data:return {'has_bot':False,'min_margin':rc.MIN_MARGIN,'max_margin':rc.MAX_MARGIN,'min_withdraw':rc.MIN_WITHDRAW}
    b,st=data['bot'],data['stats']
    label,active=rc.status_label(b,True)
    return {'has_bot':True,'min_margin':rc.MIN_MARGIN,'max_margin':rc.MAX_MARGIN,'min_withdraw':rc.MIN_WITHDRAW,
        'bot':{'name':b['bot_name'],'username':b['bot_username'],'status':label,'active':active,'enabled':b['enabled'],
        'margin':b['margin_percent'],'cs_url':b['cs_url'],'balance':int(b['margin_balance']),'total_earned':int(b['total_earned']),
        'buyers':st['buyers'],'success':st['success']}}
def reseller_call(fn,*a):
    try:return jsonify(ok=True,**{'reseller':(fn(*a),reseller_payload())[1]})
    except ValueError as e:return err(str(e))
    except psycopg.errors.UniqueViolation:return err('Token ini sudah dipakai bot reseller lain.')
@app.get('/api/reseller')
def reseller_info():
    if not uid():return err('Silakan login terlebih dahulu.',401)
    return jsonify(ok=True,reseller=reseller_payload())
@app.post('/api/reseller/create')
def reseller_create():
    if not uid():return err('Silakan login terlebih dahulu.',401)
    return reseller_call(rc.register_bot,uid(),str((request.get_json(silent=True) or {}).get('token','')).strip())
@app.post('/api/reseller/token')
def reseller_token():
    if not uid():return err('Silakan login terlebih dahulu.',401)
    return reseller_call(rc.change_token,uid(),str((request.get_json(silent=True) or {}).get('token','')).strip())
@app.post('/api/reseller/cs')
def reseller_cs():
    if not uid():return err('Silakan login terlebih dahulu.',401)
    return reseller_call(rc.set_cs,uid(),(request.get_json(silent=True) or {}).get('cs',''))
@app.post('/api/reseller/margin')
def reseller_margin():
    if not uid():return err('Silakan login terlebih dahulu.',401)
    return reseller_call(rc.set_margin,uid(),(request.get_json(silent=True) or {}).get('margin',''))
@app.post('/api/reseller/toggle')
def reseller_toggle():
    if not uid():return err('Silakan login terlebih dahulu.',401)
    return reseller_call(rc.set_enabled,uid(),bool((request.get_json(silent=True) or {}).get('enabled')))
@app.get('/api/reseller/withdrawals')
def reseller_withdrawals():
    if not uid():return err('Silakan login terlebih dahulu.',401)
    import database as _db
    rows=_db.list_reseller_withdrawals(uid(),20)
    return jsonify(ok=True,items=[{'id':r['id'],'amount':int(r['amount']),'provider':r['provider_name'],'number':r['account_number'],'name':r['account_name'],'status':r['status'],'created_at':r['created_at']} for r in rows])
@app.post('/api/reseller/withdraw')
def reseller_withdraw():
    if not uid():return err('Silakan login terlebih dahulu.',401)
    d=request.get_json(silent=True) or {}
    try:
        amount=rc.parse_amount(d.get('amount'))
        wd=rc.request_withdrawal(uid(),amount,d.get('provider',''),d.get('number',''),d.get('name',''))
    except ValueError as e:return err(str(e))
    admin=os.getenv('ADMIN_ID','').strip()
    if admin.isdigit():
        try:
            bot=rc.db.get_reseller_by_owner(uid()) or {}
            requests.post(f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',timeout=10,json={'chat_id':int(admin),'parse_mode':'HTML',
                'text':f"📤 <b>PERMINTAAN WITHDRAW RESELLER</b> (via web)\n\n🧾 ID: <code>WD-{wd['id']}</code>\n👤 Owner: <code>{uid()}</code>\n🤖 Bot: @{bot.get('bot_username') or '-'}\n💰 Nominal: <b>{rc.rp(wd['amount'])}</b>\n🏦 {wd['method']} • {wd['provider_name']}\n🔢 No: <code>{wd['account_number']}</code>\n📛 a.n. {wd['account_name']}\n\nTransfer manual lalu tekan <b>Sudah Ditransfer</b>.",
                'reply_markup':{'inline_keyboard':[[{'text':'✅ Sudah Ditransfer','callback_data':f"rs:adm_ok:{wd['id']}"},{'text':'❌ Tolak','callback_data':f"rs:adm_no:{wd['id']}"}]]}})
        except requests.RequestException:app.logger.exception('Gagal notifikasi admin WD')
    return jsonify(ok=True,id=wd['id'],reseller=reseller_payload())
@app.get('/api/health')
def health():return jsonify(ok=True,web_order_enabled=ENABLE_ORDER)
if __name__=='__main__':app.run(port=int(os.getenv('PORT','8080')),debug=os.getenv('WEB_DEV')=='1')

# --- AZHURA Monetag rewards (isolated web module; shared users/ledger) ---
from azhura_web.monetag_rewards import rewards_bp
app.register_blueprint(rewards_bp)
