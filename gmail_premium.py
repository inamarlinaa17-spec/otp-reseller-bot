"""BELI GMAIL AZHURA — modul inti (bot Telegram + Azhura Web).

Integrasi API SIMURU Gmail (https://simuru.com/openapi.json):
  GET /gmail/stock, /gmail/buy, /gmail/order/detail, /gmail/orders

Nama provider (SIMURU) TIDAK pernah ditampilkan ke pengguna. Branding: "BELI GMAIL".

Margin khusus: 10% (harga jual = harga modal * 1.10, dibulatkan ke atas) — sama seperti APK PREMIUM.
Margin reseller tambahan mengikuti pengaturan bot reseller (reseller_ctx).

Environment:
  SIMURU_API_KEY          wajib agar fitur aktif
  SIMURU_BASE_URL         opsional, default https://simuru.com/api
  AZHURA_WEB_URL          URL Mini App (https)
  GMAIL_MINIAPP=0         paksa menu di bot (default: buka Mini App)
"""
from __future__ import annotations

import os
import time
import uuid
import logging
import threading
from decimal import Decimal, ROUND_CEILING

import requests

from database import get_db, now, add_balance, subtract_balance, get_bot_setting, set_bot_setting

log = logging.getLogger("gmail_premium")

SIMURU_BASE_URL = os.getenv("SIMURU_BASE_URL", "https://simuru.com/api").strip().rstrip("/")
HTTP_TIMEOUT = 30

GMAIL_MARGIN_PERCENT = Decimal("10")

FINAL_STATUSES = {"COMPLETED", "CANCELED", "REFUNDED", "ERROR", "FAILED"}
ACTIVE_STATUSES = {"PENDING", "PROCESSING", "CREATING"}

MAINT_KEY = "gmail_premium_maintenance"
_TRUE = ("1", "true", "yes", "on")


class GmailError(Exception):
    """Error aman ditampilkan ke user."""
    http_status = 400


class GmailAmbiguous(GmailError):
    """Hasil create tidak pasti (timeout/jaringan) — jangan refund otomatis."""
    http_status = 409


# =========================================================
# CONFIG
# =========================================================

def api_key():
    return os.getenv("SIMURU_API_KEY", "").strip()


def is_configured():
    return bool(api_key())


def traffic_channel():
    return (os.getenv("TRAFIC_LIVE_CHANNEL_ID", "").strip()
            or os.getenv("TRAFIC_LIVE_CHANNEL", "").strip()
            or os.getenv("TRAFFIC_CHANNEL", "").strip())


def rupiah(n):
    return "Rp" + f"{int(n or 0):,}".replace(",", ".")


def is_maintenance():
    return str(get_bot_setting(MAINT_KEY) or "").strip().lower() in _TRUE


def set_maintenance(enabled):
    set_bot_setting(MAINT_KEY, "1" if enabled else "0")


# =========================================================
# PRICING — margin 10% khusus BELI GMAIL
# =========================================================

def _base_sell(cost):
    try:
        c = Decimal(str(cost))
        if c <= 0:
            return 0
        sell = c * (Decimal("1") + GMAIL_MARGIN_PERCENT / Decimal("100"))
        return int(sell.to_integral_value(rounding=ROUND_CEILING))
    except Exception:
        return 0


def current_reseller_row():
    try:
        from reseller_ctx import current_reseller
        return current_reseller()
    except Exception:
        return None


def sell_price(cost):
    base = _base_sell(cost)
    row = current_reseller_row()
    if base and row:
        try:
            from reseller_ctx import add_margin
            return add_margin(base, row.get("margin_percent") or 0)
        except Exception:
            return base
    return base


def calc_prices(cost):
    cost = int(cost or 0)
    sp = sell_price(cost)
    return cost, int(max(sp, cost, 1))


# =========================================================
# API CLIENT
# =========================================================

def _call(path, params=None, accept_pending=False):
    key = api_key()
    if not key:
        raise GmailError("BELI GMAIL belum dikonfigurasi. Hubungi admin.")
    q = dict(params or {})
    q["apikey"] = key
    url = f"{SIMURU_BASE_URL}{path}"
    try:
        r = requests.get(url, params=q, timeout=HTTP_TIMEOUT)
    except requests.Timeout:
        raise GmailAmbiguous("Koneksi ke server timeout. Cek riwayat pesanan sebelum mencoba lagi.")
    except requests.RequestException as exc:
        log.warning("Gmail API network error: %s", type(exc).__name__)
        raise GmailAmbiguous("Koneksi ke server gagal. Cek riwayat pesanan sebelum mencoba lagi.")

    try:
        body = r.json()
    except Exception:
        log.error("Gmail API non-JSON HTTP %s: %s", r.status_code, r.text[:300])
        raise GmailError("Respons server tidak valid. Coba lagi nanti.")

    if r.status_code in (401, 403):
        raise GmailError("API key tidak valid. Hubungi admin.")
    if r.status_code == 429:
        raise GmailError("Terlalu banyak permintaan. Tunggu sebentar lalu coba lagi.")
    if r.status_code == 404:
        raise GmailError(body.get("message") or "Pesanan tidak ditemukan.")
    if r.status_code == 422:
        raise GmailError(body.get("message") or "Permintaan tidak valid.")
    if r.status_code == 503:
        raise GmailError(body.get("message") or "Layanan sedang sibuk. Coba lagi sebentar.")
    if r.status_code >= 500:
        raise GmailAmbiguous(body.get("message") or "Server sedang bermasalah. Cek riwayat pesanan.")

    # 201 = created, 202 = pending (accepted)
    if r.status_code == 202 and accept_pending:
        return body.get("data"), True
    if r.status_code not in (200, 201) and not body.get("success", True):
        msg = body.get("message") or "Permintaan gagal."
        raise GmailError(str(msg)[:300])

    return body.get("data"), False


_cache = {}
_cache_lock = threading.Lock()
CACHE_TTL = 30


def _cached(key, fn):
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_TTL:
            return hit[1]
    value = fn()
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)
    return value


def get_stock():
    """Return dict: price, price_formatted, stock_available, sell_price, sell_formatted."""
    def fetch():
        data, _ = _call("/gmail/stock")
        data = data if isinstance(data, dict) else {}
        cost = int(data.get("price") or 0)
        _, sp = calc_prices(cost)
        return {
            "price": cost,
            "price_formatted": data.get("price_formatted") or rupiah(cost),
            "stock_available": int(data.get("stock_available") or 0),
            "sell_price": sp,
            "sell_formatted": rupiah(sp),
        }
    return _cached(("gmail_stock",), fetch)


def normalize_status(raw):
    s = str(raw or "").strip().lower()
    mapping = {
        "active": "COMPLETED",
        "completed": "COMPLETED",
        "success": "COMPLETED",
        "pending": "PENDING",
        "processing": "PROCESSING",
        "creating": "CREATING",
        "refund_pending": "PENDING",
        "refunded": "REFUNDED",
        "rejected": "FAILED",
        "failed": "FAILED",
        "error": "ERROR",
        "canceled": "CANCELED",
        "cancelled": "CANCELED",
    }
    return mapping.get(s, str(raw or "PENDING").upper()[:30])


# =========================================================
# DATABASE
# =========================================================

def ensure_tables():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS gmail_orders (
                id              BIGSERIAL PRIMARY KEY,
                local_id        TEXT UNIQUE NOT NULL,
                idem_key        TEXT,
                telegram_id     BIGINT NOT NULL,
                source          TEXT NOT NULL DEFAULT 'BOT',
                provider_cost   BIGINT NOT NULL DEFAULT 0,
                sell_price      BIGINT NOT NULL DEFAULT 0,
                provider_order_id TEXT,
                status          TEXT NOT NULL DEFAULT 'CREATING',
                provider_status TEXT,
                email_address   TEXT,
                password        TEXT,
                account_data    TEXT,
                error           TEXT,
                refunded_amount BIGINT NOT NULL DEFAULT 0,
                reseller_bot_id BIGINT,
                reseller_margin BIGINT NOT NULL DEFAULT 0,
                reseller_margin_status TEXT NOT NULL DEFAULT 'NONE',
                notified_created  BOOLEAN NOT NULL DEFAULT FALSE,
                notified_completed BOOLEAN NOT NULL DEFAULT FALSE,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                completed_at    TEXT
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS gmail_orders_user_idx ON gmail_orders (telegram_id, id DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS gmail_orders_status_idx ON gmail_orders (status)")
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS gmail_orders_idem_idx "
            "ON gmail_orders (idem_key) WHERE idem_key IS NOT NULL"
        )


def get_gmail_order(local_id, telegram_id=None):
    ensure_tables()
    with get_db() as db:
        if telegram_id is None:
            return db.execute("SELECT * FROM gmail_orders WHERE local_id=%s", (local_id,)).fetchone()
        return db.execute(
            "SELECT * FROM gmail_orders WHERE local_id=%s AND telegram_id=%s",
            (local_id, telegram_id),
        ).fetchone()


def list_user_orders(telegram_id, limit=20, offset=0):
    ensure_tables()
    with get_db() as db:
        return db.execute(
            "SELECT * FROM gmail_orders WHERE telegram_id=%s ORDER BY id DESC LIMIT %s OFFSET %s",
            (telegram_id, int(limit), int(offset)),
        ).fetchall()


def count_user_orders(telegram_id):
    ensure_tables()
    with get_db() as db:
        row = db.execute(
            "SELECT COUNT(*) AS c FROM gmail_orders WHERE telegram_id=%s", (telegram_id,)
        ).fetchone()
        return int(row["c"] if row else 0)


def list_all_orders(limit=20, offset=0, q=None):
    ensure_tables()
    with get_db() as db:
        if q:
            like = f"%{q.strip()}%"
            return db.execute(
                """SELECT * FROM gmail_orders
                   WHERE local_id ILIKE %s OR CAST(telegram_id AS TEXT) ILIKE %s
                      OR COALESCE(email_address,'') ILIKE %s
                      OR COALESCE(provider_order_id,'') ILIKE %s
                   ORDER BY id DESC LIMIT %s OFFSET %s""",
                (like, like, like, like, int(limit), int(offset)),
            ).fetchall()
        return db.execute(
            "SELECT * FROM gmail_orders ORDER BY id DESC LIMIT %s OFFSET %s",
            (int(limit), int(offset)),
        ).fetchall()


def admin_summary():
    ensure_tables()
    with get_db() as db:
        row = db.execute("""
            SELECT
              COUNT(*) AS total,
              COUNT(*) FILTER (WHERE status IN ('PENDING','PROCESSING','CREATING')) AS active,
              COUNT(*) FILTER (WHERE status = 'COMPLETED') AS completed,
              COUNT(*) FILTER (WHERE status IN ('FAILED','CANCELED','REFUNDED','ERROR')) AS failed,
              COALESCE(SUM(sell_price) FILTER (WHERE status = 'COMPLETED'), 0) AS gross,
              COALESCE(SUM(sell_price - provider_cost) FILTER (WHERE status = 'COMPLETED'), 0) AS profit
            FROM gmail_orders
        """).fetchone()
        return dict(row) if row else {}


def _set(local_id, **fields):
    if not fields:
        return
    fields["updated_at"] = now()
    cols = ", ".join(f"{k}=%s" for k in fields)
    vals = list(fields.values()) + [local_id]
    with get_db() as db:
        db.execute(f"UPDATE gmail_orders SET {cols} WHERE local_id=%s", vals)


def _refund(local_id, telegram_id, amount, why):
    amount = int(amount or 0)
    if amount <= 0:
        return
    with get_db() as db:
        o = db.execute(
            "SELECT refunded_amount, status FROM gmail_orders WHERE local_id=%s FOR UPDATE",
            (local_id,),
        ).fetchone()
        if not o:
            return
        already = int(o.get("refunded_amount") or 0)
        if already > 0:
            return
    try:
        add_balance(
            telegram_id,
            amount,
            transaction_type="REFUND_GMAIL",
            reference=local_id,
            description=str(why)[:200],
        )
        _set(local_id, refunded_amount=amount, status="REFUNDED")
    except Exception:
        log.exception("Gmail refund failed %s", local_id)


def _alert_admin(text):
    token = os.getenv("BOT_TOKEN", "").strip()
    admin = os.getenv("ADMIN_ID", "").strip()
    if not token or not admin:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": admin, "text": text},
            timeout=10,
        )
    except Exception:
        pass


def _send_channel(text):
    ch = traffic_channel()
    token = (os.getenv("TRAFFIC_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
    if not ch or not token:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": ch, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10,
        )
    except Exception:
        pass


def notify_traffic(local_id, completed=False):
    ensure_tables()
    with get_db() as db:
        o = db.execute("SELECT * FROM gmail_orders WHERE local_id=%s", (local_id,)).fetchone()
        if not o:
            return
        flag = "notified_completed" if completed else "notified_created"
        if o.get(flag):
            return
        db.execute(
            f"UPDATE gmail_orders SET {flag}=TRUE, updated_at=%s WHERE local_id=%s",
            (now(), local_id),
        )
    email = o.get("email_address") or "—"
    if completed:
        text = (
            f"✅ <b>BELI GMAIL SELESAI</b>\n"
            f"🧾 <code>{o['local_id']}</code>\n"
            f"📧 {email}\n"
            f"💰 {rupiah(o['sell_price'])}"
        )
    else:
        text = (
            f"📧 <b>ORDER BELI GMAIL</b>\n"
            f"🧾 <code>{o['local_id']}</code>\n"
            f"👤 <code>{o['telegram_id']}</code>\n"
            f"💰 {rupiah(o['sell_price'])}\n"
            f"📊 {o['status']}"
        )
    _send_channel(text)


# =========================================================
# ORDER FLOW
# =========================================================

def create_order(telegram_id, source="BOT", idem_key=None, expected_price=None):
    """Beli 1 akun Gmail. Return baris gmail_orders."""
    ensure_tables()
    stock = get_stock()
    cost = int(stock.get("price") or 0)
    if cost <= 0:
        raise GmailError("Harga Gmail tidak tersedia saat ini.")
    stock_n = int(stock.get("stock_available") or 0)
    if stock_n <= 0:
        raise GmailError("Stok Gmail habis. Coba lagi nanti.")

    provider_cost, sp = calc_prices(cost)
    if expected_price is not None and int(expected_price) != sp:
        raise GmailError(f"Harga berubah menjadi {rupiah(sp)}. Silakan ulangi pemesanan.")

    rs_row = current_reseller_row()
    rs_id = int(rs_row["id"]) if rs_row and rs_row.get("id") else None
    base = _base_sell(cost)
    rs_margin = max(0, sp - base) if rs_id else 0

    local_id = "GML-" + uuid.uuid4().hex[:12].upper()

    with get_db() as db:
        if idem_key:
            existing = db.execute(
                "SELECT * FROM gmail_orders WHERE idem_key=%s AND telegram_id=%s",
                (idem_key, telegram_id),
            ).fetchone()
            if existing:
                return existing
        try:
            db.execute(
                """INSERT INTO gmail_orders
                   (local_id, idem_key, telegram_id, source, provider_cost, sell_price,
                    reseller_bot_id, reseller_margin, status, created_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'CREATING',%s,%s)""",
                (local_id, idem_key, telegram_id, source, provider_cost, sp,
                 rs_id, rs_margin, now(), now()),
            )
        except Exception as exc:
            if "idem_key" in str(exc).lower() or "unique" in str(exc).lower():
                raise GmailError("Permintaan ganda terdeteksi. Cek riwayat pesanan.")
            raise

    # 1) potong saldo
    try:
        subtract_balance(
            telegram_id,
            sp,
            transaction_type="ORDER_GMAIL",
            reference=local_id,
            description="BELI GMAIL",
        )
    except ValueError as exc:
        _set(local_id, status="FAILED", error=str(exc)[:200])
        raise GmailError(str(exc))

    # 2) API buy
    data = None
    pending = False
    try:
        data, pending = _call("/gmail/buy", accept_pending=True)
    except GmailAmbiguous:
        _set(local_id, status="PENDING", error="Ambiguous create — cek status manual")
        _alert_admin(f"⚠️ Gmail {local_id}: create ambiguous (timeout/network). Cek manual.")
        return get_gmail_order(local_id)
    except GmailError as exc:
        _refund(local_id, telegram_id, sp, f"Gagal beli Gmail: {exc}")
        _set(local_id, status="FAILED", error=str(exc)[:200])
        raise
    except Exception:
        log.exception("Gmail create unexpected %s", local_id)
        _refund(local_id, telegram_id, sp, "Error sistem BELI GMAIL")
        _set(local_id, status="FAILED", error="unexpected error")
        raise GmailError("Pesanan gagal karena kesalahan sistem. Saldo sudah dikembalikan.")

    d = data if isinstance(data, dict) else {}
    provider_oid = d.get("id") or d.get("order_id")
    raw_status = d.get("status") or ("pending" if pending else "active")
    st = normalize_status(raw_status)
    if pending and st not in FINAL_STATUSES:
        st = "PENDING"

    email_addr = d.get("email_address") or d.get("email")
    password = d.get("password")
    account_bits = []
    if email_addr:
        account_bits.append(str(email_addr))
    if password:
        account_bits.append(str(password))
    account = " | ".join(account_bits) if account_bits else None

    if st in ("FAILED", "CANCELED", "REFUNDED", "ERROR"):
        _refund(local_id, telegram_id, sp, f"Order Gmail ditolak ({st})")
        email_addr = None
        password = None
        account = None

    _set(
        local_id,
        status=st,
        provider_status=str(raw_status)[:50],
        provider_order_id=str(provider_oid) if provider_oid is not None else None,
        email_address=str(email_addr)[:200] if email_addr else None,
        password=str(password)[:200] if password else None,
        account_data=account,
        completed_at=now() if st in FINAL_STATUSES else None,
    )

    if provider_oid is None and st not in FINAL_STATUSES:
        _alert_admin(f"⚠️ Gmail {local_id}: respon create tanpa order_id provider.")

    order = get_gmail_order(local_id)
    if order["status"] not in ("FAILED", "CANCELED", "REFUNDED", "ERROR"):
        notify_traffic(local_id, completed=False)
        if order["status"] == "COMPLETED":
            notify_traffic(local_id, completed=True)
            try:
                credit_reseller_margin(local_id)
            except Exception:
                log.exception("kredit margin reseller Gmail gagal %s", local_id)
    # invalidate stock cache
    with _cache_lock:
        _cache.pop(("gmail_stock",), None)
    return get_gmail_order(local_id)


def sync_order(local_id):
    ensure_tables()
    o = get_gmail_order(local_id)
    if not o:
        raise GmailError("Pesanan tidak ditemukan.")
    if o["status"] in FINAL_STATUSES and o.get("email_address"):
        return o
    pid = o.get("provider_order_id")
    if not pid:
        return o
    try:
        data, _ = _call("/gmail/order/detail", {"order_id": int(pid)})
    except GmailError:
        return o
    except Exception:
        log.exception("sync Gmail %s", local_id)
        return o

    d = data if isinstance(data, dict) else {}
    raw_status = d.get("status") or o.get("provider_status")
    st = normalize_status(raw_status)
    email_addr = d.get("email_address") or d.get("email") or o.get("email_address")
    password = d.get("password") or o.get("password")
    account_bits = []
    if email_addr:
        account_bits.append(str(email_addr))
    if password:
        account_bits.append(str(password))
    account = " | ".join(account_bits) if account_bits else o.get("account_data")

    prev = o["status"]
    fields = {
        "status": st,
        "provider_status": str(raw_status)[:50] if raw_status else o.get("provider_status"),
        "email_address": str(email_addr)[:200] if email_addr else o.get("email_address"),
        "password": str(password)[:200] if password else o.get("password"),
        "account_data": account,
    }
    if st in FINAL_STATUSES and not o.get("completed_at"):
        fields["completed_at"] = now()

    if st in ("FAILED", "CANCELED", "REFUNDED", "ERROR") and prev not in FINAL_STATUSES:
        if int(o.get("refunded_amount") or 0) <= 0:
            _refund(local_id, o["telegram_id"], o["sell_price"], f"Gmail status {st}")
            fields["status"] = "REFUNDED"

    _set(local_id, **fields)
    order = get_gmail_order(local_id)
    if order["status"] == "COMPLETED" and prev != "COMPLETED":
        notify_traffic(local_id, completed=True)
        try:
            credit_reseller_margin(local_id)
        except Exception:
            log.exception("kredit margin reseller Gmail gagal %s", local_id)
    return order


def sync_active_orders(max_orders=25):
    ensure_tables()
    with get_db() as db:
        rows = db.execute(
            """SELECT local_id FROM gmail_orders
               WHERE status IN ('PENDING','PROCESSING','CREATING')
               ORDER BY id ASC LIMIT %s""",
            (int(max_orders),),
        ).fetchall()
    n = 0
    for r in rows:
        try:
            sync_order(r["local_id"])
            n += 1
        except Exception:
            log.exception("sync Gmail %s", r["local_id"])
    return n


def credit_reseller_margin(local_id):
    ensure_tables()
    with get_db() as db:
        o = db.execute(
            "SELECT reseller_bot_id, reseller_margin, reseller_margin_status, status "
            "FROM gmail_orders WHERE local_id=%s FOR UPDATE",
            (local_id,),
        ).fetchone()
        if not o or not o["reseller_bot_id"] or o["reseller_margin_status"] != "NONE":
            return 0
        if o["status"] != "COMPLETED":
            return 0
        margin = int(o["reseller_margin"] or 0)
        if margin <= 0:
            db.execute(
                "UPDATE gmail_orders SET reseller_margin_status='SKIPPED' WHERE local_id=%s",
                (local_id,),
            )
            return 0
        bot = db.execute(
            "UPDATE reseller_bots SET margin_balance = margin_balance + %s, total_earned = total_earned + %s "
            "WHERE id = %s RETURNING margin_balance",
            (margin, margin, o["reseller_bot_id"]),
        ).fetchone()
        if not bot:
            return 0
        db.execute(
            "UPDATE gmail_orders SET reseller_margin_status='CREDITED', reseller_margin=%s WHERE local_id=%s",
            (margin, local_id),
        )
        db.execute(
            "INSERT INTO reseller_ledger (bot_id, amount, balance_after, entry_type, reference, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (o["reseller_bot_id"], margin, int(bot["margin_balance"]), "MARGIN", local_id, now()),
        )
        return margin


def order_public(o):
    if not o:
        return {}
    return {
        "local_id": o.get("local_id"),
        "status": o.get("status"),
        "sell_price": int(o.get("sell_price") or 0),
        "email_address": o.get("email_address") if o.get("status") == "COMPLETED" else None,
        "password": o.get("password") if o.get("status") == "COMPLETED" else None,
        "account": o.get("account_data") if o.get("status") == "COMPLETED" else None,
        "refunded_amount": int(o.get("refunded_amount") or 0),
        "created_at": o.get("created_at"),
        "completed_at": o.get("completed_at"),
        "provider_order_id": o.get("provider_order_id"),
        "source": o.get("source"),
    }


# =========================================================
# MINI APP
# =========================================================

def miniapp_enabled():
    return os.getenv("GMAIL_MINIAPP", "1").strip().lower() not in {"0", "false", "off", "no"}


def web_base_url():
    url = os.getenv("AZHURA_WEB_URL", "").strip().rstrip("/")
    return url if url.startswith("https://") else ""


def miniapp_url(reseller_id=None):
    base = web_base_url()
    if not base or not miniapp_enabled():
        return ""
    url = base + "/?tab=gmail"
    if reseller_id:
        url += f"&rb={int(reseller_id)}"
    return url
