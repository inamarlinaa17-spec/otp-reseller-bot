"""SMM PANEL (SIMURU) - modul inti, dipakai bersama oleh bot Telegram dan Azhura Web.

Endpoint yang dipakai (semua sesuai https://simuru.com/openapi.json, GET + query `apikey`):
  /smm/platforms, /smm/kinds, /smm/services, /smm/order/create, /smm/orders, /smm/order/status

Modul ini sengaja TIDAK mengimpor config.py (agar aman diimpor service web) -
hanya environment variable + database.py.

Environment variable:
  SIMURU_API_KEY          API key SIMURU (wajib agar SMM aktif)
  TRAFIC_LIVE_CHANNEL_ID  ID/@username channel Telegram TRAFIC LIVE (fallback: TRAFFIC_CHANNEL)
  TRAFFIC_BOT_TOKEN       (opsional) token bot khusus notifikasi; default BOT_TOKEN
  PROFIT_PERCENT          margin lama (default 7) - dipakai juga untuk SMM
  SIMURU_BASE_URL         (opsional) default https://simuru.com/api
"""
import os
import time
import uuid
import html
import logging
import threading
from decimal import Decimal, ROUND_CEILING

import requests

from database import get_db, now, add_balance, subtract_balance

log = logging.getLogger("smm")

SIMURU_BASE_URL = os.getenv("SIMURU_BASE_URL", "https://simuru.com/api").strip().rstrip("/")
HTTP_TIMEOUT = 25

FINAL_STATUSES = {"COMPLETED", "CANCELED", "REFUNDED", "FAILED", "PARTIAL"}
ACTIVE_STATUSES = {"PENDING", "PROCESSING"}


class SmmError(Exception):
    """Error yang aman ditampilkan ke user."""


class SmmAmbiguous(SmmError):
    """Hasil pembuatan order tidak pasti (timeout/jaringan) - jangan refund otomatis."""


# =========================================================
# CONFIG HELPERS
# =========================================================

def api_key():
    return os.getenv("SIMURU_API_KEY", "").strip()


def is_configured():
    return bool(api_key())


def profit_percent():
    try:
        return Decimal(str(os.getenv("PROFIT_PERCENT", "7")))
    except Exception:
        return Decimal("7")


def traffic_channel():
    return (os.getenv("TRAFIC_LIVE_CHANNEL_ID", "").strip()
            or os.getenv("TRAFIC_LIVE_CHANNEL", "").strip()
            or os.getenv("TRAFFIC_CHANNEL", "").strip())


def rupiah(n):
    return "Rp" + f"{int(n or 0):,}".replace(",", ".")


# =========================================================
# PRICING (rumus margin lama: modal * (1 + PROFIT_PERCENT/100), dibulatkan ke atas)
# =========================================================

def sell_price_per_1000(cost_per_1000):
    try:
        cost = Decimal(str(cost_per_1000))
        if cost <= 0:
            return 0
        sell = cost * (Decimal("1") + profit_percent() / Decimal("100"))
        return int(sell.to_integral_value(rounding=ROUND_CEILING))
    except Exception:
        return 0


def calc_prices(cost_per_1000, quantity):
    """Return (provider_cost, sell_price) untuk quantity tertentu, dalam rupiah bulat."""
    q = Decimal(int(quantity))
    cost_total = (Decimal(str(cost_per_1000)) * q / Decimal(1000)).to_integral_value(rounding=ROUND_CEILING)
    sell_pk = Decimal(sell_price_per_1000(cost_per_1000))
    sell_total = (sell_pk * q / Decimal(1000)).to_integral_value(rounding=ROUND_CEILING)
    # harga jual tidak boleh lebih rendah dari modal total
    return int(cost_total), int(max(sell_total, cost_total, 1))


# =========================================================
# SIMURU API CLIENT
# =========================================================

_session = requests.Session()


def _call(path, params=None, ambiguous_on_network_error=False):
    key = api_key()
    if not key:
        raise SmmError("Layanan SMM belum dikonfigurasi (SIMURU_API_KEY kosong).")
    q = dict(params or {})
    q["apikey"] = key
    try:
        r = _session.get(SIMURU_BASE_URL + path, params=q, timeout=HTTP_TIMEOUT,
                         headers={"Accept": "application/json"})
    except requests.RequestException as exc:
        # Jangan log URL (mengandung apikey).
        log.warning("SIMURU %s network error: %s", path, type(exc).__name__)
        if ambiguous_on_network_error:
            raise SmmAmbiguous("Koneksi ke provider terputus saat membuat pesanan.")
        raise SmmError("Provider SMM tidak dapat dihubungi. Coba lagi sebentar.")
    try:
        body = r.json()
    except ValueError:
        body = None
    if r.status_code >= 500 and ambiguous_on_network_error:
        raise SmmAmbiguous(f"Provider error HTTP {r.status_code} saat membuat pesanan.")
    if not isinstance(body, dict):
        raise SmmError(f"Respon provider tidak valid (HTTP {r.status_code}).")
    if r.status_code in (401, 403):
        log.error("SIMURU menolak API key (HTTP %s)", r.status_code)
        raise SmmError("Layanan SMM sedang tidak tersedia (otorisasi provider). Hubungi admin.")
    if r.status_code == 429:
        raise SmmError("Terlalu banyak permintaan ke provider. Coba lagi sebentar.")
    if r.status_code >= 400 or body.get("success") is False:
        msg = str(body.get("message") or "Permintaan ditolak provider.")[:300]
        err = SmmError(msg)
        err.http_status = r.status_code
        raise err
    return body.get("data")


# ---------- katalog (cache singkat; /smm/* dibatasi 1200 req/menit) ----------
_cache = {}
_cache_lock = threading.Lock()
CACHE_TTL = 300


def _cached(key, fn):
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_TTL:
            return hit[1]
    value = fn()
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)
    return value


def get_platforms():
    data = _cached(("platforms",), lambda: _call("/smm/platforms"))
    return [p for p in (data or []) if isinstance(p, dict) and p.get("platform")]


def get_kinds(platform):
    """Daftar jenis (Followers/Likes/Views, dst). Bentuk SmmKind tidak terdokumentasi
    di bagian spec yang tersedia, jadi diparse defensif; fallback: turunkan dari /smm/services."""
    def load():
        try:
            data = _call("/smm/kinds", {"platform": platform}) or []
            out = []
            for k in data:
                if isinstance(k, str):
                    out.append(k)
                elif isinstance(k, dict):
                    v = k.get("kind") or k.get("name") or k.get("title")
                    if v:
                        out.append(str(v))
            if out:
                return out
        except SmmError:
            pass
        kinds = []
        for s in _call("/smm/services", {"platform": platform, "limit": 200}) or []:
            kd = s.get("kind")
            if kd and kd not in kinds:
                kinds.append(kd)
        return kinds
    return _cached(("kinds", platform), load)


def get_services(platform=None, kind=None, q=None, limit=200):
    params = {"limit": max(1, min(int(limit), 200))}
    if platform:
        params["platform"] = platform
    if kind:
        params["kind"] = kind
    if q:
        params["q"] = q
    data = _cached(("services", tuple(sorted(params.items()))), lambda: _call("/smm/services", params))
    out = []
    for s in data or []:
        if isinstance(s, dict) and s.get("id") is not None:
            out.append(s)
    return out


def service_title(s):
    return str(s.get("title") or s.get("name") or f"Layanan {s.get('id')}")


def public_service(s):
    """Bentuk aman untuk user: harga sudah + margin; harga modal tidak ikut dikirim."""
    cost = s.get("price") or 0
    return {
        "id": s.get("id"),
        "title": service_title(s),
        "name": s.get("name"),
        "platform": s.get("platform"),
        "kind": s.get("kind"),
        "target_type": s.get("target_type"),
        "type": s.get("type"),
        "min": s.get("min"),
        "max": s.get("max"),
        "refill": bool(s.get("refill")),
        "start_minutes": s.get("start_minutes"),
        "speed_per_day": s.get("speed_per_day"),
        "note": s.get("note"),
        "quality": s.get("quality"),
        "price_per": s.get("price_per") or 1000,
        "price": sell_price_per_1000(cost),
    }


def needs_custom_comments(s):
    return "comment" in str(s.get("type") or "").lower() and "custom" in str(s.get("type") or "").lower()


def find_fresh_service(service_id, platform=None, kind=None, title=None):
    """Ambil data layanan terbaru (hindari cache) untuk validasi harga saat konfirmasi."""
    params = {"limit": 200}
    if platform:
        params["platform"] = platform
    if kind:
        params["kind"] = kind
    if title:
        params["q"] = title
    try:
        for s in _call("/smm/services", params) or []:
            if isinstance(s, dict) and str(s.get("id")) == str(service_id):
                return s
    except SmmError:
        return None
    return None


# =========================================================
# STATUS NORMALIZATION
# =========================================================

def normalize_status(raw):
    s = str(raw or "").strip().lower()
    if s in ("completed", "complete", "selesai", "success", "done", "finished"):
        return "COMPLETED"
    if s in ("canceled", "cancelled", "dibatalkan", "batal"):
        return "CANCELED"
    if s in ("refunded", "refund"):
        return "REFUNDED"
    if s in ("partial", "partially completed"):
        return "PARTIAL"
    if s in ("error", "failed", "fail", "gagal", "rejected"):
        return "FAILED"
    if s in ("pending", "queued", "menunggu"):
        return "PENDING"
    return "PROCESSING"


# =========================================================
# DATABASE
# =========================================================

_tables_ready = False
_tables_lock = threading.Lock()


def ensure_tables():
    global _tables_ready
    if _tables_ready:
        return
    with _tables_lock:
        if _tables_ready:
            return
        with get_db() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS smm_orders (
                    id BIGSERIAL PRIMARY KEY,
                    local_id TEXT UNIQUE NOT NULL,
                    idem_key TEXT UNIQUE,
                    telegram_id BIGINT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'BOT',
                    service_id BIGINT NOT NULL,
                    service_name TEXT,
                    platform TEXT,
                    kind TEXT,
                    target TEXT NOT NULL,
                    quantity BIGINT NOT NULL,
                    cost_per_1000 BIGINT NOT NULL DEFAULT 0,
                    sell_per_1000 BIGINT NOT NULL DEFAULT 0,
                    provider_cost BIGINT NOT NULL DEFAULT 0,
                    sell_price BIGINT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'CREATING',
                    provider_status TEXT,
                    provider_order_id TEXT,
                    start_count BIGINT,
                    remains BIGINT,
                    error TEXT,
                    refunded_amount BIGINT NOT NULL DEFAULT 0,
                    notified_created BOOLEAN NOT NULL DEFAULT FALSE,
                    notified_completed BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    completed_at TEXT
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS smm_orders_user_idx ON smm_orders (telegram_id, id DESC)")
            db.execute("CREATE INDEX IF NOT EXISTS smm_orders_status_idx ON smm_orders (status)")
        _tables_ready = True


def get_smm_order(local_id, telegram_id=None):
    ensure_tables()
    with get_db() as db:
        if telegram_id is None:
            return db.execute("SELECT * FROM smm_orders WHERE local_id=%s", (local_id,)).fetchone()
        return db.execute("SELECT * FROM smm_orders WHERE local_id=%s AND telegram_id=%s",
                          (local_id, telegram_id)).fetchone()


def list_user_orders(telegram_id, limit=20, offset=0):
    ensure_tables()
    with get_db() as db:
        return db.execute(
            "SELECT * FROM smm_orders WHERE telegram_id=%s AND status<>'CREATING' "
            "ORDER BY id DESC LIMIT %s OFFSET %s", (telegram_id, limit, offset)).fetchall()


def count_user_orders(telegram_id):
    ensure_tables()
    with get_db() as db:
        return db.execute("SELECT COUNT(*) n FROM smm_orders WHERE telegram_id=%s AND status<>'CREATING'",
                          (telegram_id,)).fetchone()["n"]


def list_all_orders(limit=200, offset=0, day=None, status=None, search=None):
    ensure_tables()
    where, args = ["1=1"], []
    if day:
        where.append("LEFT(o.created_at,10)=%s"); args.append(day)
    st = (status or "").upper().strip()
    if st == "ACTIVE":
        where.append("o.status IN ('CREATING','PENDING','PROCESSING')")
    elif st and st != "ALL":
        where.append("o.status=%s"); args.append(st)
    if search:
        like = "%" + search + "%"
        where.append("(o.local_id ILIKE %s OR o.provider_order_id ILIKE %s OR CAST(o.telegram_id AS TEXT) ILIKE %s "
                     "OR COALESCE(o.service_name,'') ILIKE %s OR COALESCE(o.target,'') ILIKE %s)")
        args.extend([like] * 5)
    sql = ("SELECT o.*, u.username FROM smm_orders o LEFT JOIN users u ON u.telegram_id=o.telegram_id WHERE "
           + " AND ".join(where) + " ORDER BY o.id DESC LIMIT %s OFFSET %s")
    args.extend([limit, offset])
    with get_db() as db:
        return db.execute(sql, tuple(args)).fetchall()


def admin_summary():
    ensure_tables()
    with get_db() as db:
        return db.execute("""
            SELECT COUNT(*) total,
              COUNT(*) FILTER (WHERE status IN ('PENDING','PROCESSING')) active,
              COUNT(*) FILTER (WHERE status='COMPLETED') completed,
              COUNT(*) FILTER (WHERE status IN ('CANCELED','REFUNDED','FAILED')) failed,
              COUNT(*) FILTER (WHERE status='REVIEW') review,
              COALESCE(SUM(sell_price) FILTER (WHERE status NOT IN ('CREATING','FAILED','CANCELED','REFUNDED')),0) gross,
              COALESCE(SUM((sell_price-provider_cost)*(sell_price-refunded_amount)/GREATEST(sell_price,1)) FILTER (WHERE status NOT IN ('CREATING','FAILED','CANCELED','REFUNDED')),0)::BIGINT profit
            FROM smm_orders""").fetchone()


# =========================================================
# TELEGRAM NOTIFICATION (TRAFIC LIVE) - anti ganda via klaim atomik di DB
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
        log.error("[SMM-TRAFFIC] channel/token belum diatur; notifikasi dilewati")
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": channel, "text": text, "parse_mode": "HTML",
                                "disable_web_page_preview": True}, timeout=15)
        if r.status_code == 200 and r.json().get("ok"):
            return True
        log.error("[SMM-TRAFFIC] sendMessage gagal HTTP %s: %s", r.status_code, r.text[:200])
    except requests.RequestException as exc:
        log.error("[SMM-TRAFFIC] sendMessage error: %s", type(exc).__name__)
    return False


def _notification_text(order, completed):
    with get_db() as db:
        u = db.execute("SELECT username, first_name FROM users WHERE telegram_id=%s",
                       (order["telegram_id"],)).fetchone()
    name = (u or {}).get("username") or (u or {}).get("first_name") or ""
    e = lambda v: html.escape(str(v if v not in (None, "") else "-"))
    qty_text = f"{int(order.get('quantity') or 0):,}".replace(",", ".")
    head = "✅ <b>SMM ORDER COMPLETED</b>" if completed else "🚀 <b>SMM ORDER DIBUAT</b>"
    status = "COMPLETED" if completed else "DIBUAT / DIPROSES"
    return (
        f"{head}\n\n"
        f"• <b>ID:</b> <code>{e(order.get('provider_order_id') or order['local_id'])}</code>\n"
        f"• <b>Users:</b> {e(_mask(name))}\n"
        f"• <b>Layanan:</b> {e(order.get('service_name'))}\n"
        f"• <b>Platform:</b> {e(order.get('platform'))} - {e(order.get('kind'))}\n"
        f"• <b>Jumlah:</b> {qty_text}\n"
        f"• <b>Target:</b> <code>{e(_mask(order.get('target'), 4, 3))}</code>\n"
        f"• <b>Price:</b> {e(rupiah(order.get('sell_price')))}\n"
        f"• <b>Status:</b> {status}\n"
        f"• <b>Via:</b> {e(order.get('source'))}"
    )


def notify_traffic(local_id, completed=False):
    """Kirim notifikasi sekali saja per jenis (dibuat / completed).
    Klaim atomik (UPDATE ... WHERE flag=FALSE RETURNING) mencegah ganda walau bot+web
    atau beberapa proses memanggil bersamaan. Jika kirim gagal, klaim dilepas untuk dicoba lagi."""
    ensure_tables()
    col = "notified_completed" if completed else "notified_created"
    with get_db() as db:
        row = db.execute(f"UPDATE smm_orders SET {col}=TRUE WHERE local_id=%s AND {col}=FALSE RETURNING *",
                         (local_id,)).fetchone()
    if not row:
        return False
    ok = False
    try:
        ok = _send_channel(_notification_text(row, completed))
    except Exception:
        log.exception("[SMM-TRAFFIC] gagal menyusun/mengirim notifikasi %s", local_id)
    if not ok:
        with get_db() as db:
            db.execute(f"UPDATE smm_orders SET {col}=FALSE WHERE local_id=%s", (local_id,))
    return ok


# =========================================================
# ORDER FLOW: saldo -> API -> (refund jika ditolak) -> notifikasi
# =========================================================

def validate_input(service, target, quantity, custom_comments=None):
    target = str(target or "").strip()
    if not target:
        raise SmmError("Link/username target wajib diisi.")
    if len(target) > 1024:
        raise SmmError("Target terlalu panjang (maks. 1024 karakter).")
    comments = None
    if needs_custom_comments(service):
        lines = [l.strip() for l in str(custom_comments or "").replace("\r", "").split("\n") if l.strip()]
        if not lines:
            raise SmmError("Layanan ini membutuhkan komentar (satu komentar per baris).")
        if len("\n".join(lines)) > 10000:
            raise SmmError("Komentar terlalu panjang (maks. 10.000 karakter).")
        comments = "\n".join(lines)
        quantity = len(lines)
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise SmmError("Jumlah harus berupa angka.")
    mn, mx = int(service.get("min") or 1), int(service.get("max") or 10**9)
    if quantity < max(mn, 1) or quantity > mx:
        raise SmmError(f"Jumlah harus antara {mn:,} dan {mx:,}.".replace(",", "."))
    return target, quantity, comments


def create_order(telegram_id, service, target, quantity, custom_comments=None,
                 source="BOT", idem_key=None, expected_price=None):
    """Buat order SMM. `service` = dict mentah dari /smm/services.
    Return dict order (baris smm_orders). Raise SmmError bila gagal (saldo sudah dikembalikan)."""
    ensure_tables()
    target, quantity, comments = validate_input(service, target, quantity, custom_comments)
    cost_pk = int(service.get("price") or 0)
    if cost_pk <= 0:
        raise SmmError("Harga layanan tidak valid.")
    provider_cost, sell_price = calc_prices(cost_pk, quantity)
    if expected_price is not None and int(expected_price) != sell_price:
        raise SmmError(f"Harga berubah menjadi {rupiah(sell_price)}. Silakan ulangi pemesanan.")

    local_id = "SMM-" + uuid.uuid4().hex[:12].upper()
    with get_db() as db:
        if idem_key:
            existing = db.execute("SELECT * FROM smm_orders WHERE idem_key=%s AND telegram_id=%s",
                                  (idem_key, telegram_id)).fetchone()
            if existing:
                return existing  # permintaan ganda (klik dobel) -> kembalikan order yang sama
        try:
            db.execute("""INSERT INTO smm_orders (local_id, idem_key, telegram_id, source, service_id, service_name,
                platform, kind, target, quantity, cost_per_1000, sell_per_1000, provider_cost, sell_price,
                status, created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'CREATING',%s,%s)""",
                       (local_id, idem_key, telegram_id, source, int(service["id"]), service_title(service),
                        service.get("platform"), service.get("kind"), target, quantity, cost_pk,
                        sell_price_per_1000(cost_pk), provider_cost, sell_price, now(), now()))
        except Exception as exc:
            if "idem_key" in str(exc):
                raise SmmError("Permintaan ganda terdeteksi. Cek riwayat pesanan.")
            raise

    # 1) potong saldo (atomik, memakai sistem saldo lama)
    try:
        subtract_balance(telegram_id, sell_price, "SMM_ORDER", reference=local_id,
                         description=f"SMM {service_title(service)} x{quantity}")
    except ValueError as exc:
        _set(local_id, status="FAILED", error=str(exc)[:200])
        raise SmmError(str(exc))

    # 2) kirim ke SIMURU
    params = {"service_id": int(service["id"]), "target": target, "quantity": quantity}
    if comments:
        params["custom_comments"] = comments
    try:
        data = _call("/smm/order/create", params, ambiguous_on_network_error=True)
    except SmmAmbiguous as exc:
        # Server tidak merespons: cek dulu apakah order sempat terbuat di SIMURU.
        found = _find_remote_order(service, target, quantity)
        if found:
            data = found
        else:
            _refund(local_id, telegram_id, sell_price, "Order SMM gagal (server provider tidak merespons)")
            _set(local_id, status="FAILED", error=str(exc)[:200])
            raise SmmError("Pesanan gagal: server provider tidak merespons. Saldo sudah dikembalikan.")
    except SmmError as exc:
        # penolakan jelas dari provider -> tidak ada order dibuat -> refund penuh
        _refund(local_id, telegram_id, sell_price, "Order SMM ditolak provider")
        _set(local_id, status="FAILED", error=str(exc)[:200])
        raise SmmError(f"Pesanan ditolak provider: {exc}. Saldo sudah dikembalikan.")
    except Exception:
        log.exception("create_order error tak terduga %s", local_id)
        _refund(local_id, telegram_id, sell_price, "Order SMM gagal (kesalahan sistem)")
        _set(local_id, status="FAILED", error="unexpected error")
        raise SmmError("Pesanan gagal karena kesalahan sistem. Saldo sudah dikembalikan.")

    d = data if isinstance(data, dict) else {}
    provider_oid = d.get("id") or d.get("order_id")
    raw_status = d.get("status") or "processing"
    st = normalize_status(raw_status)
    if st in ("FAILED", "CANCELED", "REFUNDED"):
        # provider langsung menolak (balance sudah dikembalikan oleh SIMURU sesuai dokumen)
        _refund(local_id, telegram_id, sell_price, "Order SMM ditolak provider")
    _set(local_id, status=st if st != "FAILED" else "FAILED", provider_status=str(raw_status)[:50],
         provider_order_id=str(provider_oid) if provider_oid is not None else None,
         start_count=_int(d.get("start_count")), remains=_int(d.get("remains")),
         completed_at=now() if st in FINAL_STATUSES else None)
    if provider_oid is None:
        _alert_admin(f"⚠️ SMM {local_id}: respon create tanpa ID provider. Cek manual di SIMURU.")

    order = get_smm_order(local_id)
    if order["status"] not in ("FAILED", "CANCELED", "REFUNDED"):
        notify_traffic(local_id, completed=False)
        if order["status"] == "COMPLETED":
            notify_traffic(local_id, completed=True)
    return get_smm_order(local_id)


def _find_remote_order(service, target, quantity):
    """Cari order yang mungkin sempat terbuat di SIMURU (belum tercatat di DB kita)."""
    try:
        remote = _call("/smm/orders", {"limit": 20}) or []
        with get_db() as db:
            known = {str(r["provider_order_id"]) for r in db.execute(
                "SELECT provider_order_id FROM smm_orders WHERE provider_order_id IS NOT NULL").fetchall()}
        for o in remote:
            if (isinstance(o, dict) and str(o.get("id")) not in known
                    and str(o.get("target")) == str(target) and int(o.get("quantity") or -1) == int(quantity)
                    and str(o.get("service_name") or "") in (service_title(service), str(service.get("name") or ""))):
                return o
    except Exception:
        log.warning("reconcile /smm/orders gagal")
    return None


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _set(local_id, **fields):
    fields["updated_at"] = now()
    cols = ", ".join(f"{k}=%s" for k in fields)
    with get_db() as db:
        db.execute(f"UPDATE smm_orders SET {cols} WHERE local_id=%s", (*fields.values(), local_id))


def _refund(local_id, telegram_id, amount, why):
    """Refund sekali per order (klaim atomik lewat refunded_amount)."""
    amount = int(amount)
    if amount <= 0:
        return 0
    with get_db() as db:
        row = db.execute(
            "UPDATE smm_orders SET refunded_amount=refunded_amount+%s WHERE local_id=%s "
            "AND refunded_amount=0 RETURNING local_id", (amount, local_id)).fetchone()
    if not row:
        return 0
    try:
        add_balance(telegram_id, amount, "SMM_REFUND", reference=local_id, description=why)
    except Exception:
        with get_db() as db:
            db.execute("UPDATE smm_orders SET refunded_amount=refunded_amount-%s WHERE local_id=%s", (amount, local_id))
        log.exception("Refund SMM gagal %s", local_id)
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


# =========================================================
# STATUS SYNC
# =========================================================

def sync_order(local_id):
    """Sinkronkan status dari SIMURU (/smm/order/status), kirim notifikasi completed sekali,
    dan refund otomatis bila dibatalkan/gagal/parsial. Return baris order terbaru."""
    order = get_smm_order(local_id)
    if not order:
        raise SmmError("Pesanan tidak ditemukan.")
    if not order.get("provider_order_id") or order["status"] in ("CREATING", "REVIEW"):
        return order
    needs_refund = (order["status"] in ("CANCELED", "REFUNDED", "FAILED")
                    and int(order.get("refunded_amount") or 0) == 0)
    if (order["status"] in FINAL_STATUSES and not needs_refund
            and (order["status"] != "COMPLETED" or order["notified_completed"])):
        return order
    try:
        data = _call("/smm/order/status", {"order_id": order["provider_order_id"]})
    except SmmError:
        return order
    d = data if isinstance(data, dict) else {}
    raw = d.get("status")
    st = normalize_status(raw)
    _set(local_id, status=st, provider_status=str(raw)[:50] if raw is not None else order.get("provider_status"),
         start_count=_int(d.get("start_count")) if d.get("start_count") is not None else order.get("start_count"),
         remains=_int(d.get("remains")) if d.get("remains") is not None else order.get("remains"),
         completed_at=order.get("completed_at") or (now() if st in FINAL_STATUSES else None))
    fresh = get_smm_order(local_id)
    try:
        if st in ("CANCELED", "REFUNDED", "FAILED"):
            _refund(local_id, fresh["telegram_id"], fresh["sell_price"], f"Order SMM {st.lower()}")
        elif st == "PARTIAL":
            qty, rem = int(fresh["quantity"] or 0), int(fresh["remains"] or 0)
            if qty > 0 and 0 < rem <= qty:
                part = (Decimal(fresh["sell_price"]) * Decimal(rem) / Decimal(qty)).to_integral_value(rounding=ROUND_CEILING)
                _refund(local_id, fresh["telegram_id"], min(int(part), int(fresh["sell_price"])), "Refund parsial SMM")
        elif st == "COMPLETED":
            notify_traffic(local_id, completed=True)
    except Exception:
        log.exception("post-sync action gagal %s", local_id)
    return get_smm_order(local_id)


def sync_active_orders(max_orders=25):
    """Dipanggil berkala oleh bot: sinkronkan order aktif + kirim notif completed."""
    ensure_tables()
    with get_db() as db:
        rows = db.execute(
            "SELECT local_id FROM smm_orders WHERE provider_order_id IS NOT NULL AND "
            "(status IN ('PENDING','PROCESSING') OR (status='COMPLETED' AND notified_completed=FALSE)) "
            "ORDER BY updated_at ASC LIMIT %s", (max_orders,)).fetchall()
        # order yang notif 'dibuat'-nya gagal terkirim sebelumnya
        unsent = db.execute(
            "SELECT local_id FROM smm_orders WHERE provider_order_id IS NOT NULL AND notified_created=FALSE "
            "AND status IN ('PENDING','PROCESSING','COMPLETED') LIMIT 10").fetchall()
    for r in unsent:
        notify_traffic(r["local_id"], completed=False)
    n = 0
    for r in rows:
        try:
            sync_order(r["local_id"])
            n += 1
        except Exception:
            log.exception("sync %s gagal", r["local_id"])
        time.sleep(0.3)
    return n


# =========================================================
# TAMBAHAN: MAINTENANCE SMM, NOTICE PLATFORM, LABEL LAYANAN
# (aditif - tidak mengubah fungsi di atas)
# =========================================================

MAINT_KEY = "smm_maintenance"          # dipakai bersama oleh bot Telegram & dashboard azhura_web
_TRUE = {"1", "true", "on", "yes"}


def is_maintenance():
    """True bila SMM PANEL sedang maintenance (flag bersama bot + web, tabel bot_settings)."""
    from database import get_bot_setting
    return str(get_bot_setting(MAINT_KEY, "0")).strip().lower() in _TRUE


def set_maintenance(enabled):
    from database import set_bot_setting
    set_bot_setting(MAINT_KEY, "1" if enabled else "0")
    return bool(enabled)


MAINTENANCE_TEXT = (
    "🛠 <b>SMM PANEL SEDANG MAINTENANCE</b>\n\n"
    "Pemesanan layanan SMM sementara ditutup agar proses perbaikan berjalan aman.\n\n"
    "✅ Saldo dan pesanan kamu yang sudah berjalan tetap aman.\n"
    "🧾 Kamu masih bisa mengecek progres lewat \"Pesanan SMM Saya\".\n\n"
    "Silakan kembali beberapa saat lagi. Terima kasih 🙏"
)

HELP_TEXT = (
    "❓ <b>Tentang Pesanan SMM</b>\n\n"
    "⚡ <b>Diproses otomatis</b>\n"
    "Setiap pesanan langsung diproses otomatis begitu pembayaran berhasil. "
    "Kamu tidak perlu melakukan apa pun — cukup tunggu.\n\n"
    "🛡 <b>Gagal? Saldo kembali</b>\n"
    "Jika pesanan gagal diproses, saldo otomatis dikembalikan penuh. "
    "Kamu tidak dirugikan sedikit pun.\n\n"
    "⏳ <b>Beberapa platform perlu waktu</b>\n"
    "Sebagian platform menerapkan keamanan ekstra ketat, sehingga pemrosesannya bisa lebih lama — "
    "kadang sampai beberapa hari. Itu normal dan pesanan tetap aman dalam antrean.\n\n"
    "😊 Tenang saja, pesanan kamu aman dan pasti kami proses. "
    "Cek perkembangannya kapan saja di \"Pesanan SMM Saya\"."
)


def delay_notice(platform, has_featured=True):
    """Teks pemberitahuan di atas daftar layanan untuk platform yang sedang delay.
    Env SMM_DELAY_NOTICE_PLATFORMS (pisah koma, default 'TikTok'; kosongkan / '-' untuk mematikan).
    Env SMM_DELAY_NOTICE_TEXT (opsional) boleh memakai {platform}."""
    raw = os.getenv("SMM_DELAY_NOTICE_PLATFORMS", "TikTok")
    names = {p.strip().lower() for p in raw.split(",") if p.strip() and p.strip() != "-"}
    if not platform or str(platform).strip().lower() not in names:
        return ""
    custom = os.getenv("SMM_DELAY_NOTICE_TEXT", "").strip()
    if custom:
        return custom.replace("{platform}", html.escape(str(platform)))
    msg = f"⚠️ Layanan {html.escape(str(platform))} sedang dominan mengalami delay proses."
    if has_featured:
        msg += " Disarankan memilih layanan berlabel ⭐ Rekomendasi."
    return msg


def featured_ids(platform, kind):
    """Set id layanan 'rekomendasi' (filter resmi `featured=true`). Gagal -> set kosong."""
    def load():
        params = {"featured": "true", "limit": 200}
        if platform:
            params["platform"] = platform
        if kind:
            params["kind"] = kind
        try:
            data = _call("/smm/services", params) or []
        except SmmError:
            return frozenset()
        return frozenset(str(s.get("id")) for s in data if isinstance(s, dict) and s.get("id") is not None)
    try:
        return _cached(("featured", platform, kind), load)
    except Exception:
        return frozenset()


_FEATURED_KEYS = ("featured", "recommended", "is_featured", "is_recommended")
_RATE_KEYS = ("success_rate", "completion_rate", "completion_percent", "completion_pct",
              "complete_rate", "completion", "success_percent", "finish_rate", "done_rate")


def is_featured(s, featured_set=None):
    if featured_set and str(s.get("id")) in featured_set:
        return True
    return any(s.get(k) is True for k in _FEATURED_KEYS)


def service_rate(s):
    """Persentase penyelesaian (0-100) bila disediakan provider; selain itu None."""
    for k in _RATE_KEYS:
        v = s.get(k)
        if isinstance(v, bool) or v is None:
            continue
        try:
            f = float(str(v).replace("%", "").strip())
        except ValueError:
            continue
        if f < 0:
            continue
        if f <= 1:
            f *= 100
        return int(round(min(f, 100)))
    return None


def format_start(minutes):
    """Estimasi mulai: 52m / 2j / 3h (menit / jam / hari)."""
    try:
        m = float(minutes)
    except (TypeError, ValueError):
        return ""
    if m < 0:
        return ""
    if m < 60:
        return f"{int(round(m))}m"
    if m < 60 * 24:
        return f"{int(round(m / 60))}j"
    return f"{int(round(m / 1440))}h"


def service_button_label(s, featured_set=None, max_len=46):
    """Teks tombol daftar layanan: ⭐ Judul · Rp900/1K · 1j · ↻56%."""
    star = "⭐ " if is_featured(s, featured_set) else ""
    tail = [rupiah(sell_price_per_1000(s.get("price") or 0)) + "/1K"]
    st = format_start(s.get("start_minutes"))
    if st:
        tail.append(st)
    rate = service_rate(s)
    if rate is not None:
        tail.append(f"↻{rate}%")
    tail_txt = " · ".join(tail)
    room = max(12, max_len - len(star) - len(tail_txt) - 3)
    title = service_title(s)
    if len(title) > room:
        title = title[:room - 1].rstrip() + "…"
    return f"{star}{title} · {tail_txt}"
