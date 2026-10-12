"""APK PREMIUM AZHURA - modul inti, dipakai bersama oleh bot Telegram dan Azhura Web.

Integrasi API SIMURU Aplikasi Premium (https://simuru.com/openapi.json):
  GET /apps/catalog, /apps/categories, /apps/variants, /apps/order/create,
  /apps/orders, /apps/order/status

Nama provider (SIMURU) TIDAK pernah ditampilkan ke pengguna. Branding:
  "APK PREMIUM AZHURA".

Margin khusus APK PREMIUM: 10% (harga jual = harga modal * 1.10, dibulatkan ke atas).
Margin layanan lain (OTP, SMM) tidak diubah.

Environment variable:
  SIMURU_API_KEY          API key (wajib agar fitur aktif)
  SIMURU_BASE_URL         (opsional) default https://simuru.com/api
  TRAFIC_LIVE_CHANNEL_ID  / TRAFFIC_CHANNEL untuk notifikasi traffic
  TRAFFIC_BOT_TOKEN       (opsional)
  ADMIN_ID / ADMIN_USERNAME untuk CS
"""
from __future__ import annotations

import os
import time
import uuid
import html
import logging
import threading
from decimal import Decimal, ROUND_CEILING

import requests

from database import get_db, now, add_balance, subtract_balance, get_bot_setting, set_bot_setting

log = logging.getLogger("apk_premium")

SIMURU_BASE_URL = os.getenv("SIMURU_BASE_URL", "https://simuru.com/api").strip().rstrip("/")
HTTP_TIMEOUT = 30

# Margin tetap 10% khusus APK PREMIUM (bukan PROFIT_PERCENT)
APK_MARGIN_PERCENT = Decimal("10")

FINAL_STATUSES = {"COMPLETED", "CANCELED", "REFUNDED", "ERROR", "FAILED"}
ACTIVE_STATUSES = {"PENDING", "PROCESSING", "CREATING"}

MAINT_KEY = "apk_premium_maintenance"
_TRUE = ("1", "true", "yes", "on")


class ApkError(Exception):
    """Error yang aman ditampilkan ke user."""
    http_status = 400


class ApkAmbiguous(ApkError):
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
    return str(get_bot_setting(MAINT_KEY, "0")).strip().lower() in _TRUE


def set_maintenance(enabled):
    set_bot_setting(MAINT_KEY, "1" if enabled else "0")


# =========================================================
# PRICING — margin 10% khusus APK PREMIUM
# =========================================================

def _base_sell(cost):
    """Harga jual bot utama (tanpa margin reseller)."""
    try:
        c = Decimal(str(cost))
        if c <= 0:
            return 0
        sell = c * (Decimal("1") + APK_MARGIN_PERCENT / Decimal("100"))
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
    """Harga jual final (termasuk margin reseller bila ada)."""
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
    """Return (provider_cost, sell_price)."""
    cost = int(cost or 0)
    sp = sell_price(cost)
    return cost, int(max(sp, cost, 1))


# =========================================================
# API CLIENT
# =========================================================

def _call(path, params=None):
    key = api_key()
    if not key:
        raise ApkError("APK PREMIUM belum dikonfigurasi. Hubungi admin.")
    q = dict(params or {})
    q["apikey"] = key
    url = f"{SIMURU_BASE_URL}{path}"
    try:
        r = requests.get(url, params=q, timeout=HTTP_TIMEOUT)
    except requests.Timeout:
        raise ApkAmbiguous("Koneksi ke server timeout. Cek riwayat pesanan sebelum mencoba lagi.")
    except requests.RequestException as exc:
        log.warning("APK API network error: %s", type(exc).__name__)
        raise ApkAmbiguous("Koneksi ke server gagal. Cek riwayat pesanan sebelum mencoba lagi.")

    try:
        body = r.json()
    except Exception:
        log.error("APK API non-JSON HTTP %s: %s", r.status_code, r.text[:300])
        raise ApkError("Respons server tidak valid. Coba lagi nanti.")

    if r.status_code in (401, 403):
        raise ApkError("API key tidak valid. Hubungi admin.")
    if r.status_code == 429:
        raise ApkError("Terlalu banyak permintaan. Tunggu sebentar lalu coba lagi.")
    if r.status_code == 404:
        raise ApkError(body.get("message") or "Produk/pesanan tidak ditemukan.")
    if r.status_code == 422:
        raise ApkError(body.get("message") or "Data pesanan tidak valid.")
    if r.status_code >= 500:
        raise ApkAmbiguous(body.get("message") or "Server sedang bermasalah. Cek riwayat pesanan.")

    if not body.get("success", True) and r.status_code not in (200, 201):
        msg = body.get("message") or "Permintaan gagal."
        raise ApkError(str(msg)[:300])

    return body.get("data")


_cache = {}
_cache_lock = threading.Lock()
CACHE_TTL = 60  # katalog di-cache 60 detik sesuai docs


def _cached(key, fn):
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_TTL:
            return hit[1]
    value = fn()
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)
    return value


def get_catalog():
    """Seluruh katalog: list of {app, slug, logo, items: [PremiumAppItem]}."""
    data = _cached(("catalog",), lambda: _call("/apps/catalog"))
    out = []
    for cat in data or []:
        if not isinstance(cat, dict):
            continue
        items = [i for i in (cat.get("items") or []) if isinstance(i, dict) and i.get("id") is not None]
        if items:
            out.append({
                "app": cat.get("app") or "",
                "slug": cat.get("slug") or "",
                "logo": cat.get("logo"),
                "varian": len(items),
                "mulai": min(int(i.get("harga") or 0) for i in items) if items else 0,
                "items": items,
            })
    return out


def get_categories():
    data = _cached(("categories",), lambda: _call("/apps/categories"))
    return [c for c in (data or []) if isinstance(c, dict) and c.get("slug")]


def get_variants(slug):
    data = _cached(("variants", slug), lambda: _call("/apps/variants", {"slug": slug}))
    return [i for i in (data or []) if isinstance(i, dict) and i.get("id") is not None]


def find_item(app_id):
    """Cari paket by id di katalog (cache)."""
    app_id = int(app_id)
    for cat in get_catalog():
        for it in cat.get("items") or []:
            if int(it.get("id") or -1) == app_id:
                return it, cat
    # fallback: coba categories + variants
    for cat in get_categories():
        for it in get_variants(cat["slug"]):
            if int(it.get("id") or -1) == app_id:
                return it, cat
    return None, None


def public_item(item, cat=None):
    """Bentuk aman untuk user: harga sudah + margin; modal disembunyikan."""
    cost = int(item.get("harga") or 0)
    sp = sell_price(cost)
    return {
        "id": item.get("id"),
        "app": item.get("app") or (cat or {}).get("app") or "",
        "slug": item.get("slug") or (cat or {}).get("slug") or "",
        "nama": item.get("nama") or item.get("nama_penuh") or "",
        "nama_penuh": item.get("nama_penuh") or item.get("nama") or "",
        "keterangan": item.get("keterangan"),
        "durasi_hari": item.get("durasi_hari"),
        "garansi_hari": item.get("garansi_hari"),
        "butuh_email": bool(item.get("butuh_email")),
        "harga": sp,
        "harga_formatted": rupiah(sp),
        "stok": item.get("stok"),
        "logo": item.get("logo") or (cat or {}).get("logo"),
    }


def public_category(cat):
    mulai_cost = int(cat.get("mulai") or 0)
    return {
        "app": cat.get("app") or "",
        "slug": cat.get("slug") or "",
        "varian": int(cat.get("varian") or len(cat.get("items") or []) or 0),
        "mulai": sell_price(mulai_cost),
        "mulai_formatted": rupiah(sell_price(mulai_cost)),
        "logo": cat.get("logo"),
    }


# =========================================================
# DATABASE
# =========================================================

def ensure_tables():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS apk_orders (
                id              BIGSERIAL PRIMARY KEY,
                local_id        TEXT UNIQUE NOT NULL,
                idem_key        TEXT,
                telegram_id     BIGINT NOT NULL,
                source          TEXT NOT NULL DEFAULT 'BOT',
                app_id          INTEGER NOT NULL,
                app_name        TEXT,
                package_name    TEXT,
                slug            TEXT,
                email           TEXT,
                provider_cost   BIGINT NOT NULL DEFAULT 0,
                sell_price      BIGINT NOT NULL DEFAULT 0,
                provider_order_id TEXT,
                status          TEXT NOT NULL DEFAULT 'CREATING',
                provider_status TEXT,
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
        db.execute("CREATE INDEX IF NOT EXISTS apk_orders_user_idx ON apk_orders (telegram_id, id DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS apk_orders_status_idx ON apk_orders (status)")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS apk_orders_idem_idx ON apk_orders (idem_key) WHERE idem_key IS NOT NULL")
        db.execute("ALTER TABLE apk_orders ADD COLUMN IF NOT EXISTS reseller_bot_id BIGINT")
        db.execute("ALTER TABLE apk_orders ADD COLUMN IF NOT EXISTS reseller_margin BIGINT NOT NULL DEFAULT 0")
        db.execute("ALTER TABLE apk_orders ADD COLUMN IF NOT EXISTS reseller_margin_status TEXT NOT NULL DEFAULT 'NONE'")


def get_apk_order(local_id, telegram_id=None):
    ensure_tables()
    with get_db() as db:
        if telegram_id is None:
            return db.execute("SELECT * FROM apk_orders WHERE local_id=%s", (local_id,)).fetchone()
        return db.execute("SELECT * FROM apk_orders WHERE local_id=%s AND telegram_id=%s",
                          (local_id, telegram_id)).fetchone()


def list_user_orders(telegram_id, limit=20, offset=0):
    ensure_tables()
    with get_db() as db:
        return db.execute(
            "SELECT * FROM apk_orders WHERE telegram_id=%s AND status<>'CREATING' "
            "ORDER BY id DESC LIMIT %s OFFSET %s",
            (telegram_id, limit, offset)).fetchall()


def count_user_orders(telegram_id):
    ensure_tables()
    with get_db() as db:
        return db.execute(
            "SELECT COUNT(*) n FROM apk_orders WHERE telegram_id=%s AND status<>'CREATING'",
            (telegram_id,)).fetchone()["n"]


def list_all_orders(limit=20, offset=0, q=None):
    ensure_tables()
    where = ["1=1"]
    args = []
    if q:
        like = f"%{q}%"
        where.append(
            "(CAST(o.telegram_id AS TEXT) ILIKE %s OR COALESCE(o.local_id,'') ILIKE %s "
            "OR COALESCE(o.app_name,'') ILIKE %s OR COALESCE(o.package_name,'') ILIKE %s "
            "OR COALESCE(o.provider_order_id,'') ILIKE %s)")
        args.extend([like] * 5)
    sql = ("SELECT o.*, u.username FROM apk_orders o LEFT JOIN users u ON u.telegram_id=o.telegram_id WHERE "
           + " AND ".join(where) + " ORDER BY o.id DESC LIMIT %s OFFSET %s")
    args.extend([limit, offset])
    with get_db() as db:
        return db.execute(sql, tuple(args)).fetchall()


def admin_summary():
    ensure_tables()
    with get_db() as db:
        return db.execute("""
            SELECT COUNT(*) total,
              COUNT(*) FILTER (WHERE status IN ('PENDING','PROCESSING','CREATING')) active,
              COUNT(*) FILTER (WHERE status='COMPLETED') completed,
              COUNT(*) FILTER (WHERE status IN ('CANCELED','REFUNDED','ERROR','FAILED')) failed,
              COALESCE(SUM(sell_price) FILTER (WHERE status NOT IN ('CREATING','FAILED','CANCELED','REFUNDED','ERROR')),0) gross,
              COALESCE(SUM((sell_price-COALESCE(reseller_margin,0)-provider_cost)
                * (sell_price-refunded_amount)/GREATEST(sell_price,1))
                FILTER (WHERE status NOT IN ('CREATING','FAILED','CANCELED','REFUNDED','ERROR')),0)::BIGINT profit
            FROM apk_orders""").fetchone()


# =========================================================
# NOTIFIKASI TRAFFIC
# =========================================================

def _mask(value, ks=3, ke=3):
    raw = str(value or "-").strip()
    if raw in ("", "-"):
        return "-"
    if len(raw) <= ks + ke:
        return "*" * len(raw)
    return raw[:ks] + "*" * (len(raw) - ks - ke) + raw[-ke:]


def _send_channel(text):
    channel = traffic_channel()
    token = os.getenv("TRAFFIC_BOT_TOKEN", "").strip() or os.getenv("BOT_TOKEN", "").strip()
    if not channel or not token:
        log.error("[APK-TRAFFIC] channel/token belum diatur")
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": channel, "text": text, "parse_mode": "HTML",
                                "disable_web_page_preview": True}, timeout=15)
        if r.status_code == 200 and r.json().get("ok"):
            return True
        log.error("[APK-TRAFFIC] sendMessage gagal HTTP %s: %s", r.status_code, r.text[:200])
    except requests.RequestException as exc:
        log.error("[APK-TRAFFIC] error: %s", type(exc).__name__)
    return False


def _notification_text(order, completed):
    with get_db() as db:
        u = db.execute("SELECT username, first_name FROM users WHERE telegram_id=%s",
                       (order["telegram_id"],)).fetchone()
    name = (u or {}).get("username") or (u or {}).get("first_name") or ""
    e = lambda v: html.escape(str(v if v not in (None, "") else "-"))
    head = "✅ <b>APK PREMIUM SELESAI</b>" if completed else "💎 <b>APK PREMIUM DIBUAT</b>"
    status = "COMPLETED" if completed else "DIPROSES"
    return (
        f"{head}\n\n"
        f"• <b>ID:</b> <code>{e(order.get('provider_order_id') or order['local_id'])}</code>\n"
        f"• <b>User:</b> {e(_mask(name))}\n"
        f"• <b>Aplikasi:</b> {e(order.get('app_name'))}\n"
        f"• <b>Paket:</b> {e(order.get('package_name'))}\n"
        f"• <b>Harga:</b> {e(rupiah(order.get('sell_price')))}\n"
        f"• <b>Status:</b> {status}\n"
        f"• <b>Via:</b> {e(order.get('source'))}"
    )


def notify_traffic(local_id, completed=False):
    ensure_tables()
    col = "notified_completed" if completed else "notified_created"
    with get_db() as db:
        row = db.execute(
            f"UPDATE apk_orders SET {col}=TRUE WHERE local_id=%s AND {col}=FALSE RETURNING *",
            (local_id,)).fetchone()
    if not row:
        return False
    ok = False
    try:
        ok = _send_channel(_notification_text(row, completed))
    except Exception:
        log.exception("[APK-TRAFFIC] gagal notif %s", local_id)
    if not ok:
        with get_db() as db:
            db.execute(f"UPDATE apk_orders SET {col}=FALSE WHERE local_id=%s", (local_id,))
    return ok


# =========================================================
# ORDER FLOW
# =========================================================

def normalize_status(raw):
    s = str(raw or "").strip().upper()
    mapping = {
        "PENDING": "PENDING",
        "PROCESSING": "PROCESSING",
        "COMPLETED": "COMPLETED",
        "ERROR": "ERROR",
        "FAILED": "FAILED",
        "CANCELED": "CANCELED",
        "CANCELLED": "CANCELED",
        "REFUNDED": "REFUNDED",
    }
    return mapping.get(s, s or "PENDING")


def _set(local_id, **fields):
    fields["updated_at"] = now()
    cols = ", ".join(f"{k}=%s" for k in fields)
    with get_db() as db:
        db.execute(f"UPDATE apk_orders SET {cols} WHERE local_id=%s", (*fields.values(), local_id))


def _refund(local_id, telegram_id, amount, why):
    """Refund sekali per order (klaim atomik lewat refunded_amount)."""
    amount = int(amount)
    if amount <= 0:
        return 0
    with get_db() as db:
        row = db.execute(
            "UPDATE apk_orders SET refunded_amount=refunded_amount+%s WHERE local_id=%s "
            "AND refunded_amount=0 RETURNING local_id", (amount, local_id)).fetchone()
    if not row:
        return 0
    try:
        add_balance(telegram_id, amount, "APK_REFUND", reference=local_id, description=why)
    except Exception:
        with get_db() as db:
            db.execute(
                "UPDATE apk_orders SET refunded_amount=refunded_amount-%s WHERE local_id=%s",
                (amount, local_id))
        log.exception("Refund APK gagal %s", local_id)
        raise
    return amount


def _alert_admin(text):
    token = os.getenv("BOT_TOKEN", "").strip()
    admin = os.getenv("ADMIN_ID", "").strip()
    if not token or not admin:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": admin, "text": text}, timeout=10)
    except requests.RequestException:
        pass


def create_order(telegram_id, app_id, email=None, source="BOT", idem_key=None, expected_price=None):
    """Buat pesanan APK PREMIUM.
    Return dict order (baris apk_orders). Raise ApkError bila gagal (saldo dikembalikan bila sudah dipotong).
    """
    ensure_tables()
    item, cat = find_item(app_id)
    if not item:
        raise ApkError("Paket tidak ditemukan atau stok habis. Muat ulang katalog.")

    cost = int(item.get("harga") or 0)
    if cost <= 0:
        raise ApkError("Harga paket tidak valid.")
    provider_cost, sp = calc_prices(cost)

    if item.get("butuh_email"):
        email = (email or "").strip()
        if not email or "@" not in email or len(email) > 200:
            raise ApkError("Paket ini membutuhkan alamat email yang valid.")
    else:
        email = (email or "").strip() or None

    rs_row = current_reseller_row()
    rs_id = int(rs_row["id"]) if rs_row and rs_row.get("id") else None
    base = _base_sell(cost)
    rs_margin = max(0, sp - base) if rs_id else 0

    if expected_price is not None and int(expected_price) != sp:
        raise ApkError(f"Harga berubah menjadi {rupiah(sp)}. Silakan ulangi pemesanan.")

    local_id = "APK-" + uuid.uuid4().hex[:12].upper()
    app_name = item.get("app") or (cat or {}).get("app") or ""
    package_name = item.get("nama_penuh") or item.get("nama") or ""
    slug = item.get("slug") or (cat or {}).get("slug") or ""

    with get_db() as db:
        if idem_key:
            existing = db.execute(
                "SELECT * FROM apk_orders WHERE idem_key=%s AND telegram_id=%s",
                (idem_key, telegram_id)).fetchone()
            if existing:
                return existing
        try:
            db.execute("""INSERT INTO apk_orders
                (local_id, idem_key, telegram_id, source, app_id, app_name, package_name, slug, email,
                 provider_cost, sell_price, reseller_bot_id, reseller_margin, status, created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'CREATING',%s,%s)""",
                       (local_id, idem_key, telegram_id, source, int(app_id), app_name, package_name, slug,
                        email, provider_cost, sp, rs_id, rs_margin, now(), now()))
        except Exception as exc:
            if "idem_key" in str(exc).lower() or "unique" in str(exc).lower():
                raise ApkError("Permintaan ganda terdeteksi. Cek riwayat pesanan.")
            raise

    # 1) potong saldo
    try:
        subtract_balance(telegram_id, sp, "APK_ORDER", reference=local_id,
                         description=f"APK PREMIUM {package_name or app_name}")
    except ValueError as exc:
        _set(local_id, status="FAILED", error=str(exc)[:200])
        raise ApkError(str(exc))

    # 2) kirim ke API
    params = {"app_id": int(app_id), "request_id": local_id}
    if email:
        params["email"] = email

    data = None
    try:
        data = _call("/apps/order/create", params)
    except ApkAmbiguous:
        # Timeout / network — jangan refund; mungkin order sudah terbuat di provider
        _set(local_id, status="PENDING", error="Ambiguous create — cek status manual")
        _alert_admin(f"⚠️ APK {local_id}: create ambiguous (timeout/network). Cek manual.")
        return get_apk_order(local_id)
    except ApkError as exc:
        _refund(local_id, telegram_id, sp, f"Gagal buat order APK: {exc}")
        _set(local_id, status="FAILED", error=str(exc)[:200])
        raise
    except Exception as exc:
        log.exception("APK create unexpected %s", local_id)
        _refund(local_id, telegram_id, sp, "Error sistem APK")
        _set(local_id, status="FAILED", error="unexpected error")
        raise ApkError("Pesanan gagal karena kesalahan sistem. Saldo sudah dikembalikan.")

    d = data if isinstance(data, dict) else {}
    provider_oid = d.get("order_id") or d.get("id")
    raw_status = d.get("status") or "processing"
    st = normalize_status(raw_status)
    account = d.get("account")

    if st in ("FAILED", "CANCELED", "REFUNDED", "ERROR"):
        _refund(local_id, telegram_id, sp, f"Order APK ditolak ({st})")
        account = None

    _set(local_id,
         status=st,
         provider_status=str(raw_status)[:50],
         provider_order_id=str(provider_oid) if provider_oid is not None else None,
         account_data=account,
         completed_at=now() if st in FINAL_STATUSES else None)

    if provider_oid is None and st not in FINAL_STATUSES:
        _alert_admin(f"⚠️ APK {local_id}: respon create tanpa order_id provider.")

    order = get_apk_order(local_id)
    if order["status"] not in ("FAILED", "CANCELED", "REFUNDED", "ERROR"):
        notify_traffic(local_id, completed=False)
        if order["status"] == "COMPLETED":
            notify_traffic(local_id, completed=True)
            try:
                credit_reseller_margin(local_id)
            except Exception:
                log.exception("kredit margin reseller APK gagal %s", local_id)
    return get_apk_order(local_id)


def sync_order(local_id):
    """Sinkronkan status dari /apps/order/status, refund bila gagal, kirim notif completed."""
    order = get_apk_order(local_id)
    if not order:
        raise ApkError("Pesanan tidak ditemukan.")
    if (order.get("reseller_bot_id") and order.get("reseller_margin_status") == "NONE"
            and order["status"] == "COMPLETED"):
        try:
            credit_reseller_margin(local_id)
        except Exception:
            log.exception("recover margin APK %s", local_id)

    if not order.get("provider_order_id"):
        return order

    needs_refund = (order["status"] in ("CANCELED", "REFUNDED", "ERROR", "FAILED")
                    and int(order.get("refunded_amount") or 0) == 0)
    if (order["status"] in FINAL_STATUSES and not needs_refund
            and (order["status"] != "COMPLETED" or order["notified_completed"])):
        return order

    try:
        data = _call("/apps/order/status", {"order_id": order["provider_order_id"]})
    except ApkError:
        return order

    d = data if isinstance(data, dict) else {}
    raw = d.get("status")
    st = normalize_status(raw)
    account = d.get("account") if st == "COMPLETED" else order.get("account_data")

    _set(local_id,
         status=st,
         provider_status=str(raw)[:50] if raw is not None else order.get("provider_status"),
         account_data=account,
         completed_at=order.get("completed_at") or (now() if st in FINAL_STATUSES else None))

    fresh = get_apk_order(local_id)
    try:
        if st in ("CANCELED", "REFUNDED", "ERROR", "FAILED"):
            _refund(local_id, fresh["telegram_id"], fresh["sell_price"], f"Order APK {st.lower()}")
        elif st == "COMPLETED":
            notify_traffic(local_id, completed=True)
            credit_reseller_margin(local_id)
    except Exception:
        log.exception("post-sync APK gagal %s", local_id)
    return get_apk_order(local_id)


def sync_active_orders(max_orders=25):
    ensure_tables()
    with get_db() as db:
        rows = db.execute(
            "SELECT local_id FROM apk_orders WHERE provider_order_id IS NOT NULL AND "
            "(status IN ('PENDING','PROCESSING') OR (status='COMPLETED' AND notified_completed=FALSE) "
            "OR (reseller_bot_id IS NOT NULL AND reseller_margin_status='NONE' AND status='COMPLETED')) "
            "ORDER BY updated_at ASC LIMIT %s", (max_orders,)).fetchall()
        unsent = db.execute(
            "SELECT local_id FROM apk_orders WHERE notified_created=FALSE AND status NOT IN ('CREATING','FAILED') "
            "ORDER BY created_at ASC LIMIT 10").fetchall()
    for r in rows:
        try:
            sync_order(r["local_id"])
        except Exception:
            log.exception("sync APK %s", r["local_id"])
    for r in unsent:
        try:
            notify_traffic(r["local_id"], completed=False)
        except Exception:
            pass


def credit_reseller_margin(local_id):
    ensure_tables()
    with get_db() as db:
        o = db.execute(
            "SELECT reseller_bot_id, reseller_margin, reseller_margin_status, status "
            "FROM apk_orders WHERE local_id=%s FOR UPDATE", (local_id,)).fetchone()
        if not o or not o["reseller_bot_id"] or o["reseller_margin_status"] != "NONE":
            return 0
        if o["status"] != "COMPLETED":
            return 0
        margin = int(o["reseller_margin"] or 0)
        if margin <= 0:
            db.execute("UPDATE apk_orders SET reseller_margin_status='SKIPPED' WHERE local_id=%s", (local_id,))
            return 0
        bot = db.execute(
            "UPDATE reseller_bots SET margin_balance = margin_balance + %s, total_earned = total_earned + %s "
            "WHERE id = %s RETURNING margin_balance", (margin, margin, o["reseller_bot_id"])).fetchone()
        if not bot:
            return 0
        db.execute(
            "UPDATE apk_orders SET reseller_margin_status='CREDITED', reseller_margin=%s WHERE local_id=%s",
            (margin, local_id))
        db.execute(
            "INSERT INTO reseller_ledger (bot_id, amount, balance_after, entry_type, reference, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (o["reseller_bot_id"], margin, int(bot["margin_balance"]), "MARGIN", local_id, now()))
        return margin


def order_public(o):
    """Bentuk aman untuk API web / tampilan user."""
    return {
        "local_id": o["local_id"],
        "app_name": o.get("app_name") or "",
        "package_name": o.get("package_name") or "",
        "email": o.get("email"),
        "sell_price": int(o.get("sell_price") or 0),
        "status": o.get("status"),
        "account": o.get("account_data") if o.get("status") == "COMPLETED" else None,
        "refunded_amount": int(o.get("refunded_amount") or 0),
        "created_at": o.get("created_at"),
        "completed_at": o.get("completed_at"),
        "provider_order_id": o.get("provider_order_id"),
    }


# =========================================================
# MINI APP (sama pola SMM PANEL)
# Env: AZHURA_WEB_URL (https), APK_MINIAPP=0 untuk paksa menu di bot
# =========================================================

def miniapp_enabled():
    return os.getenv("APK_MINIAPP", "1").strip().lower() not in {"0", "false", "off", "no"}


def web_base_url():
    url = os.getenv("AZHURA_WEB_URL", "").strip().rstrip("/")
    return url if url.startswith("https://") else ""


def miniapp_url(reseller_id=None):
    """URL mini app APK PREMIUM. Bot reseller: ?rb=<id>. Kosong bila AZHURA_WEB_URL belum https
    atau APK_MINIAPP=0 (fallback menu di bot)."""
    base = web_base_url()
    if not base or not miniapp_enabled():
        return ""
    url = base + "/?tab=apk"
    if reseller_id:
        url += f"&rb={int(reseller_id)}"
    return url
