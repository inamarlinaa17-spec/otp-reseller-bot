# Verified Monetag TMA rewarded interstitial postbacks.
import os, hmac, hashlib, json, secrets, time, uuid
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from urllib.parse import parse_qsl
import psycopg
from psycopg.rows import dict_row
from flask import Blueprint, request, jsonify, session, send_from_directory

rewards_bp = Blueprint('monetag_rewards', __name__)

def settings():
    return {'zone': os.getenv('MONETAG_ZONE_ID', '').strip(),
            'secret': os.getenv('MONETAG_POSTBACK_SECRET', '').strip(),
            'share': os.getenv('MONETAG_REWARD_SHARE', '0.2'),
            'rate': os.getenv('MONETAG_USD_IDR', '16000')}

def connection():
    return psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row)

def ensure_tables(db):
    db.execute('''CREATE TABLE IF NOT EXISTS azhura_monetag_attempts (
        ymid TEXT PRIMARY KEY, telegram_id BIGINT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        reward_idr BIGINT NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'PENDING')''')
    db.execute('''CREATE TABLE IF NOT EXISTS azhura_monetag_events (
        ymid TEXT PRIMARY KEY, event_type TEXT NOT NULL, estimated_price TEXT,
        received_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''')

def telegram_init_user(init_data, bot_token):
    # Official Telegram WebApp initData HMAC: untrusted user ID is never taken from the client body alone.
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    signature = pairs.pop('hash', '')
    auth_date = int(pairs.get('auth_date', '0'))
    if not signature or abs(time.time() - auth_date) > 300:
        raise ValueError('Sesi Telegram kedaluwarsa')
    check = '\n'.join(f'{k}={v}' for k,v in sorted(pairs.items()))
    secret = hmac.new(b'WebAppData', bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError('Tanda tangan Telegram tidak valid')
    return int(json.loads(pairs['user'])['id'])

@rewards_bp.get('/rewards')
def rewards_page():
    return send_from_directory('static', 'rewards.html')

@rewards_bp.post('/api/rewards/login')
def rewards_login():
    try:
        data = request.get_json(silent=True) or {}
        telegram_id = telegram_init_user(str(data.get('init_data', '')), os.environ['BOT_TOKEN'])
        with connection() as db:
            if not db.execute('SELECT 1 FROM users WHERE telegram_id=%s', (telegram_id,)).fetchone():
                return jsonify(error='Mulai bot Telegram dengan /start terlebih dahulu'), 403
        session.clear(); session.permanent=True
        session['uid']=telegram_id;session['csrf']=secrets.token_urlsafe(32)
        return jsonify(ok=True, csrf=session['csrf'])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return jsonify(error='Buka menu iklan melalui bot Telegram'), 401

@rewards_bp.get('/api/rewards/config')
def rewards_config():
    cfg=settings()
    if not session.get('uid'):
        return jsonify(error='Login melalui bot Telegram'),401
    if not cfg['zone'] or len(cfg['secret']) < 32:
        return jsonify(enabled=False, message='Monetag belum dikonfigurasi oleh admin')
    return jsonify(enabled=True, zone_id=cfg['zone'])

@rewards_bp.post('/api/rewards/start')
def rewards_start():
    if not session.get('uid'):return jsonify(error='Login diperlukan'),401
    if request.headers.get('X-CSRF-Token') != session.get('csrf'):
        return jsonify(error='Sesi tidak valid'),403
    cfg=settings()
    if not cfg['zone'] or len(cfg['secret']) < 32:
        return jsonify(error='Monetag belum diaktifkan'),503
    ymid=uuid.uuid4().hex
    with connection() as db:
        ensure_tables(db)
        db.execute('INSERT INTO azhura_monetag_attempts(ymid,telegram_id) VALUES (%s,%s)',(ymid,session['uid']))
    return jsonify(ymid=ymid)

@rewards_bp.get('/api/rewards/status/<ymid>')
def rewards_status(ymid):
    if not session.get('uid'):return jsonify(error='Login diperlukan'),401
    with connection() as db:
        ensure_tables(db)
        row=db.execute('SELECT status,reward_idr FROM azhura_monetag_attempts WHERE ymid=%s AND telegram_id=%s',
                       (ymid,session['uid'])).fetchone()
    return jsonify(row) if row else (jsonify(error='Tayangan tidak ditemukan'),404)

@rewards_bp.get('/api/rewards/postback')
def monetag_postback():
    cfg=settings()
    # A long unguessable shared secret is required; do not accept unsigned internet requests.
    if not cfg['secret'] or len(cfg['secret']) < 32 or not hmac.compare_digest(
            request.args.get('token',''), cfg['secret']):
        return jsonify(error='Unauthorized'),403
    ymid=request.args.get('ymid','')
    if len(ymid)!=32 or any(c not in '0123456789abcdef' for c in ymid):
        return jsonify(error='Invalid ymid'),400
    zone=request.args.get('zone','')
    if not cfg['zone'] or zone!=cfg['zone']:
        return jsonify(error='Wrong zone'),400
    event=request.args.get('event','')
    value=request.args.get('value','')
    # Monetag Rewarded Interstitial can send both impression and click postbacks.
    # Only a monetized/valued event may create a reward. The attempt's PENDING ->
    # CREDITED transition is locked below, so impression + click cannot pay twice
    # for the same ymid. Accept both Monetag's documented `valued` value and the
    # `yes` value shown by some dashboard configurations.
    if event not in ('impression', 'click') or value not in ('yes', 'valued'):
        return jsonify(ok=True, credited=False)
    try:
        price=Decimal(request.args.get('price','0'))
        share=Decimal(cfg['share']);rate=Decimal(cfg['rate'])
        if not price.is_finite() or not 0 < price < 10 or not 0 < share <= 1 or not 0 < rate < 100000:
            raise ValueError()
        amount=int((price*share*rate).to_integral_value(rounding=ROUND_DOWN))
    except (InvalidOperation, ValueError):
        return jsonify(error='Invalid reward parameters'),400
    # DB row locks and unique ymid guarantee at most one credit even on simultaneous retries.
    with connection() as db:
        ensure_tables(db)
        db.execute('INSERT INTO azhura_monetag_events(ymid,event_type,estimated_price) VALUES (%s,%s,%s) ON CONFLICT (ymid) DO NOTHING', (ymid, event, str(price)))
        attempt=db.execute('SELECT * FROM azhura_monetag_attempts WHERE ymid=%s FOR UPDATE',(ymid,)).fetchone()
        if not attempt:return jsonify(error='Unknown event'),404
        if attempt['status']!='PENDING':return jsonify(ok=True,credited=False,duplicate=True)
        if amount < 1:
            db.execute("UPDATE azhura_monetag_attempts SET status='NO_REWARD' WHERE ymid=%s",(ymid,))
            return jsonify(ok=True,credited=False)
        user=db.execute('SELECT balance FROM users WHERE telegram_id=%s FOR UPDATE',(attempt['telegram_id'],)).fetchone()
        if not user:return jsonify(error='Unknown user'),404
        before=int(user['balance']);after=before+amount
        db.execute('UPDATE users SET balance=%s WHERE telegram_id=%s',(after,attempt['telegram_id']))
        db.execute('''INSERT INTO ledger(telegram_id,amount,balance_before,balance_after,transaction_type,reference,description,created_at)
            VALUES(%s,%s,%s,%s,'MONETAG_REWARD',%s,%s,%s)''',
            (attempt['telegram_id'],amount,before,after,ymid,'Reward Monetag verified ad event',time.strftime('%Y-%m-%d %H:%M:%S')))
        db.execute("UPDATE azhura_monetag_attempts SET status='CREDITED',reward_idr=%s WHERE ymid=%s",(amount,ymid))
    return jsonify(ok=True,credited=True)
