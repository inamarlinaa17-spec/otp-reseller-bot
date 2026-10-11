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

def _base_sell_per_1000(cost_per_1000):
    """Harga jual bot UTAMA per 1.000 (tanpa margin reseller)."""
    try:
        cost = Decimal(str(cost_per_1000))
        if cost <= 0:
            return 0
        sell = cost * (Decimal("1") + profit_percent() / Decimal("100"))
        return int(sell.to_integral_value(rounding=ROUND_CEILING))
    except Exception:
        return 0


def current_reseller_row():
    """Reseller yang sedang aktif pada konteks ini (bot reseller / sesi mini app reseller), atau None."""
    try:
        from reseller_ctx import current_reseller
        return current_reseller()
    except Exception:
        return None


def sell_price_per_1000(cost_per_1000):
    """Harga jual per 1.000. Di bot/mini app reseller ditambah margin reseller (SAMA dengan margin OTP)."""
    base = _base_sell_per_1000(cost_per_1000)
    row = current_reseller_row()
    if base and row:
        try:
            from reseller_ctx import add_margin
            return add_margin(base, row.get("margin_percent") or 0)
        except Exception:
            return base
    return base


def base_sell_total(cost_per_1000, quantity):
    """Total harga bot utama (tanpa margin reseller) -> dasar menghitung margin reseller."""
    q = Decimal(int(quantity))
    cost_total = (Decimal(str(cost_per_1000)) * q / Decimal(1000)).to_integral_value(rounding=ROUND_CEILING)
    sell_total = (Decimal(_base_sell_per_1000(cost_per_1000)) * q / Decimal(1000)).to_integral_value(rounding=ROUND_CEILING)
    return int(max(sell_total, cost_total, 1))


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
            db.execute("ALTER TABLE smm_orders ADD COLUMN IF NOT EXISTS reseller_bot_id BIGINT")
            db.execute("ALTER TABLE smm_orders ADD COLUMN IF NOT EXISTS reseller_margin BIGINT NOT NULL DEFAULT 0")
            db.execute("ALTER TABLE smm_orders ADD COLUMN IF NOT EXISTS reseller_margin_status TEXT NOT NULL DEFAULT 'NONE'")
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
              COALESCE(SUM((sell_price-COALESCE(reseller_margin,0)-provider_cost)*(sell_price-refunded_amount)/GREATEST(sell_price,1)) FILTER (WHERE status NOT IN ('CREATING','FAILED','CANCELED','REFUNDED')),0)::BIGINT profit
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
    rs_row = current_reseller_row()
    rs_id = int(rs_row["id"]) if rs_row and rs_row.get("id") else None
    rs_margin = max(0, sell_price - base_sell_total(cost_pk, quantity)) if rs_id else 0
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
                reseller_bot_id, reseller_margin, status, created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'CREATING',%s,%s)""",
                       (local_id, idem_key, telegram_id, source, int(service["id"]), service_title(service),
                        service.get("platform"), service.get("kind"), target, quantity, cost_pk,
                        sell_price_per_1000(cost_pk), provider_cost, sell_price, rs_id, rs_margin, now(), now()))
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
            try:
                credit_reseller_margin(local_id)
            except Exception:
                log.exception("kredit margin reseller gagal %s", local_id)
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
    if (order.get("reseller_bot_id") and order.get("reseller_margin_status") == "NONE"
            and order["status"] in ("COMPLETED", "PARTIAL")):
        try:  # pemulihan: margin yang belum sempat dikreditkan
            credit_reseller_margin(local_id)
        except Exception:
            log.exception("kredit margin reseller gagal %s", local_id)
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
        if st in ("COMPLETED", "PARTIAL"):
            credit_reseller_margin(local_id)
    except Exception:
        log.exception("post-sync action gagal %s", local_id)
    return get_smm_order(local_id)


def sync_active_orders(max_orders=25):
    """Dipanggil berkala oleh bot: sinkronkan order aktif + kirim notif completed."""
    ensure_tables()
    with get_db() as db:
        rows = db.execute(
            "SELECT local_id FROM smm_orders WHERE provider_order_id IS NOT NULL AND "
            "(status IN ('PENDING','PROCESSING') OR (status='COMPLETED' AND notified_completed=FALSE) "
            "OR (reseller_bot_id IS NOT NULL AND reseller_margin_status='NONE' AND status IN ('COMPLETED','PARTIAL'))) "
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


# =========================================================
# TAMBAHAN V3: KETERANGAN LENGKAP KATEGORI & LAYANAN (bot + web)
# Field resmi SIMURU: completion_rate, repeat_rate, recommended, speed_label,
# quality_tags, target_type, start_minutes, min_price, total.
# =========================================================

KIND_INFO = {
    "followers": ("👥", "Menambah jumlah pengikut/subscriber pada AKUN — bikin profil terlihat lebih terpercaya."),
    "subscribers": ("👥", "Menambah jumlah subscriber pada CHANNEL/AKUN — bikin channel terlihat lebih terpercaya."),
    "members": ("👥", "Menambah jumlah anggota pada grup/channel/komunitas."),
    "likes": ("❤️", "Menambah jumlah suka pada SATU postingan/video — sinyal engagement ke algoritma."),
    "views": ("👁", "Menambah tayangan/play pada video atau postingan — dorong jangkauan (reach)."),
    "plays": ("▶️", "Menambah jumlah putar/play pada konten — dorong jangkauan (reach)."),
    "comments": ("💬", "Menambah komentar pada postingan — bikin postingan terlihat ramai dan hidup."),
    "shares": ("🔁", "Menambah jumlah bagikan/share pada postingan — memperluas penyebaran konten."),
    "saves": ("🔖", "Menambah jumlah simpan/save pada postingan — sinyal konten bernilai ke algoritma."),
    "reposts": ("🔁", "Menambah jumlah repost pada postingan — memperluas penyebaran konten."),
    "retweets": ("🔁", "Menambah jumlah retweet/repost pada postingan — memperluas penyebaran konten."),
    "reactions": ("😍", "Menambah reaksi (emoji) pada postingan — bikin postingan terlihat aktif."),
    "impressions": ("📊", "Menambah impresi/tayangan konten — memperbesar jangkauan postingan."),
    "reach": ("📡", "Menambah jangkauan (reach) konten ke lebih banyak akun."),
    "watch time": ("⏱", "Menambah jam tayang/durasi tonton video — membantu performa channel."),
    "watchtime": ("⏱", "Menambah jam tayang/durasi tonton video — membantu performa channel."),
    "live": ("🔴", "Menambah penonton/interaksi pada siaran langsung (live)."),
    "traffic": ("🌐", "Mengarahkan kunjungan ke link/website — menambah jumlah pengunjung."),
    "votes": ("🗳", "Menambah suara/vote pada polling atau kontes."),
    "clicks": ("🖱", "Menambah jumlah klik pada link."),
    "downloads": ("⬇️", "Menambah jumlah unduhan/install aplikasi atau file."),
    "lainnya": ("✨", "Layanan tambahan lainnya untuk platform ini."),
    "other": ("✨", "Layanan tambahan lainnya untuk platform ini."),
}

# target_type -> (apa yang harus dikirim user, tampilkan peringatan akun publik?)
TARGET_INFO = {
    "profile": ("LINK PROFIL atau USERNAME akun — bukan link postingan.", True),
    "post": ("LINK POSTINGAN/VIDEO — bukan link profil.", True),
    "comment": ("LINK KOMENTAR yang ingin dituju.", True),
    "story": ("LINK STORY.", True),
    "live": ("LINK SIARAN LANGSUNG (live) yang sedang berlangsung.", False),
    "track": ("LINK LAGU/TRACK.", False),
    "app": ("LINK APLIKASI.", False),
    "keyword": ("KATA KUNCI yang dituju.", False),
    "website": ("LINK WEBSITE.", False),
    "tg_channel": ("LINK CHANNEL/GRUP TELEGRAM (t.me/namachannel).", False),
    "tg_post": ("LINK POSTINGAN TELEGRAM (t.me/namachannel/123).", False),
}

# platform -> (contoh profil, contoh postingan)
PLATFORM_EXAMPLES = {
    "instagram": ("instagram.com/username", "instagram.com/p/xxxxxxxx"),
    "tiktok": ("tiktok.com/@username", "tiktok.com/@username/video/123456"),
    "youtube": ("youtube.com/@namachannel", "youtube.com/watch?v=xxxxxxxx"),
    "facebook": ("facebook.com/username", "facebook.com/username/posts/123456"),
    "twitter": ("x.com/username", "x.com/username/status/123456"),
    "x": ("x.com/username", "x.com/username/status/123456"),
    "twitter / x": ("x.com/username", "x.com/username/status/123456"),
    "threads": ("threads.net/@username", "threads.net/@username/post/xxxxxxxx"),
    "telegram": ("t.me/namachannel", "t.me/namachannel/123"),
    "spotify": ("open.spotify.com/artist/xxxx", "open.spotify.com/track/xxxx"),
    "soundcloud": ("soundcloud.com/username", "soundcloud.com/username/nama-lagu"),
    "linkedin": ("linkedin.com/in/username", "linkedin.com/posts/xxxx"),
}


def kind_info(kind, platform=None):
    k = str(kind or "").strip().lower()
    icon, desc = KIND_INFO.get(k, ("📦", ""))
    if not desc:
        for key, val in KIND_INFO.items():
            if key in k:
                icon, desc = val
                break
    if not desc:
        desc = f"Layanan {kind} untuk {platform}." if platform else f"Layanan {kind}."
    return icon, desc


def target_guide(platform, target_type):
    """(teks 'yang harus dimasukkan', contoh, peringatan) berdasarkan target_type dari provider."""
    tt = str(target_type or "").strip().lower()
    what, warn = TARGET_INFO.get(tt, ("LINK / USERNAME target sesuai layanan.", False))
    ex = PLATFORM_EXAMPLES.get(str(platform or "").strip().lower())
    example = None
    if ex and tt in ("profile", "post", "comment", "story"):
        example = ex[0] if tt == "profile" else ex[1]
    elif tt == "tg_channel":
        example = "t.me/namachannel"
    elif tt == "tg_post":
        example = "t.me/namachannel/123"
    warning = ("Akun wajib PUBLIK selama proses. Kalau digembok/private → pesanan tidak masuk & bisa hangus."
               if warn else None)
    return what, example, warning


def _pct(s, keys):
    for k in keys:
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


def format_eta(minutes):
    try:
        m = float(minutes)
    except (TypeError, ValueError):
        return ""
    if m <= 0:
        return ""
    if m < 60:
        return f"~{int(round(m))} menit"
    if m < 1440:
        return f"~{int(round(m / 60))} jam"
    return f"~{int(round(m / 1440))} hari"


def max_short(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    if n >= 1_000_000:
        v = n / 1_000_000
        return f"{v:g}M"
    if n >= 1000:
        v = n / 1000
        return f"{v:g}K"
    return str(n)


def rich_service(s, featured_set=None):
    """public_service + keterangan lengkap (rekomendasi, % tuntas, % order ulang, estimasi, tag, panduan target)."""
    pub = public_service(s)
    tags = s.get("quality_tags")
    tags = [str(t) for t in tags if t] if isinstance(tags, (list, tuple)) else []
    speed = s.get("speed_label")
    eta = (str(speed).strip() if isinstance(speed, str) and speed.strip()
           else ((("⚡ " if (s.get("start_minutes") or 999) <= 180 else "") + format_eta(s.get("start_minutes")))
                 if format_eta(s.get("start_minutes")) else ""))
    what, example, warning = target_guide(s.get("platform"), s.get("target_type"))
    sub = list(tags)
    if s.get("max"):
        sub.append(f"MAX {max_short(s.get('max'))}")
    pub.update({
        "custom_comments": needs_custom_comments(s),
        "featured": is_featured(s, featured_set),
        "completion": _pct(s, ("completion_rate", "success_rate", "completion_percent")),
        "repeat_rate": _pct(s, ("repeat_rate",)),
        "refund_rate": _pct(s, ("refund_rate",)),
        "eta": eta,
        "tags": tags,
        "subtitle": " · ".join(sub),
        "target_what": what, "target_example": example, "target_warning": warning,
    })
    return pub


def _kind_raw(platform):
    def load():
        try:
            return [k for k in (_call("/smm/kinds", {"platform": platform}) or []) if isinstance(k, (dict, str))]
        except SmmError:
            return []
    return _cached(("kinds_raw", platform), load)


def _kind_overview_raw(platform):
    """Daftar kategori (Followers/Likes/...) lengkap: ikon, deskripsi, jumlah layanan, harga mulai (sudah +margin),
    estimasi proses, % pesanan tuntas. Memakai field /smm/kinds bila ada; sisanya dihitung dari /smm/services."""
    def load():
        raw = _kind_raw(platform)
        names, meta = [], {}
        for k in raw:
            if isinstance(k, str):
                names.append(k)
            else:
                nm = k.get("kind") or k.get("name") or k.get("title")
                if nm:
                    names.append(str(nm)); meta[str(nm)] = k
        if not names:
            names = list(get_kinds(platform))
        from concurrent.futures import ThreadPoolExecutor

        def one(nm):
            m = meta.get(nm, {})
            total = m.get("total") or m.get("count") or m.get("services")
            cheapest = None
            for key in ("min_price", "cheapest_price", "cheapest", "price_min"):
                if m.get(key) not in (None, ""):
                    try:
                        cheapest = float(m[key]); break
                    except (TypeError, ValueError):
                        pass
            comp = _pct(m, ("completion_rate", "success_rate", "completion_percent", "completion"))
            eta_min = None
            for key in ("start_minutes", "avg_minutes", "eta_minutes", "est_minutes"):
                if m.get(key) not in (None, ""):
                    try:
                        eta_min = float(m[key]); break
                    except (TypeError, ValueError):
                        pass
            if total is None or cheapest is None or comp is None or eta_min is None:
                try:
                    svcs = get_services(platform, nm, None, 200)
                except Exception:
                    svcs = []
                prices = [float(x.get("price")) for x in svcs if x.get("price")]
                if cheapest is None and prices:
                    cheapest = min(prices)
                if total is None:
                    total = len(svcs)
                if comp is None:
                    vals = [v for v in (_pct(x, ("completion_rate",)) for x in svcs) if v is not None]
                    comp = int(round(sum(vals) / len(vals))) if vals else None
                if eta_min is None:
                    mins = sorted(float(x["start_minutes"]) for x in svcs
                                  if isinstance(x.get("start_minutes"), (int, float)) and x["start_minutes"] > 0)
                    eta_min = mins[len(mins) // 2] if mins else None
            icon, desc = kind_info(nm, platform)
            return {"kind": nm, "icon": icon, "desc": desc, "total": int(total or 0),
                    "min_cost": cheapest or 0,
                    "eta": format_eta(eta_min), "completion": comp}
        with ThreadPoolExecutor(max_workers=6) as ex:
            return list(ex.map(one, names))
    try:
        return _cached(("kind_overview", platform), load)
    except Exception:
        return [{"kind": k, "icon": kind_info(k, platform)[0], "desc": kind_info(k, platform)[1], "total": 0,
                 "min_cost": 0, "eta": "", "completion": None} for k in get_kinds(platform)]


def kind_overview(platform):
    """Kategori + harga mulai (sudah +margin; untuk reseller +margin reseller). Cache hanya menyimpan modal."""
    return [dict(o, min_price=sell_price_per_1000(o.get("min_cost")) if o.get("min_cost") else 0)
            for o in _kind_overview_raw(platform)]


def kind_guide(platform, kind, services):
    """Kartu keterangan di atas daftar layanan: deskripsi kategori, yang harus dimasukkan, contoh, peringatan, estimasi."""
    icon, desc = kind_info(kind, platform)
    tt = next((s.get("target_type") for s in services if s.get("target_type")), None)
    what, example, warning = target_guide(platform, tt)
    mins = sorted(float(s["start_minutes"]) for s in services
                  if isinstance(s.get("start_minutes"), (int, float)) and s["start_minutes"] > 0)
    vals = [v for v in (_pct(s, ("completion_rate",)) for s in services) if v is not None]
    return {"icon": icon, "title": f"{kind}", "desc": desc, "target_what": what, "target_example": example,
            "target_warning": warning, "eta": format_eta(mins[len(mins) // 2]) if mins else "",
            "completion": int(round(sum(vals) / len(vals))) if vals else None}


# =========================================================
# TAMBAHAN V4: INFO PENTING & KETENTUAN SMM (teks bot Telegram, HTML)
# =========================================================

INFO_TEXT = (
    "⚠️ <b>Info penting sebelum memesan</b>\n\n"
    "📸 <b>Instagram</b>\n"
    "Sebagian akun perlu <b>menonaktifkan</b> pengaturan \"Laporkan untuk ditinjau\" agar followers masuk "
    "dan pesanan tidak error/partial.\n"
    "<i>Pengaturan dan aktivitas → Ikut dan undang teman → Laporkan untuk ditinjau (nonaktifkan)</i>\n\n"
    "🎵 <b>TikTok</b>\n"
    "Pastikan akun <b>publik</b> (bukan privat) dan video tidak disembunyikan, agar followers / likes / views "
    "bisa masuk dan tidak error.\n\n"
    "✈️ <b>Telegram (Subscriber Channel)</b>\n"
    "Channel harus <b>publik</b> dan punya <b>username</b>. Jangan ubah ke privat atau ganti username selama "
    "pesanan berjalan agar subscriber masuk.\n\n"
    "<i>Pesanan dipantau otomatis untuk estimasi kecepatan pengerjaan tiap layanan.</i>"
)

TERMS_TEXT = (
    "📋 <b>Ketentuan (Order · Speed Up · Refill) — wajib baca</b>\n\n"
    "<b>Order</b>\n"
    "• Input link <b>sesuai format</b> di keterangan — pesanan <b>tidak bisa dibatalkan</b>.\n"
    "• Jangan pakai 2 layanan sekaligus untuk link yang sama (tunggu <b>completed</b> dulu).\n"
    "• Link salah / di-private / dihapus setelah order → otomatis completed, <b>tanpa refund</b>.\n"
    "• Status <b>partial / canceled</b> → saldo otomatis di-refund, bisa order ulang.\n"
    "• Jumlah <b>maks = kapasitas total per akun/link</b>, bukan per sekali order.\n"
    "• Speed &amp; drop rate = estimasi, bisa berubah tergantung server. Report setelah 24 jam.\n\n"
    "<b>Speed Up</b>\n"
    "• Untuk order <b>stuck &gt;24 jam</b> (kecuali layanan bertanda SLOW).\n"
    "• Lapor <b>Order ID</b> ke CS; diproses 1×24 jam; maks 1×/hari per order.\n\n"
    "<b>Refill (garansi isi ulang drop)</b>\n"
    "• Untuk drop <b>&gt;30%</b> setelah status SELESAI (partial tidak bisa refill).\n"
    "• Hanya layanan bergaransi refill (lihat keterangan). Ajukan ke CS dengan <b>Order ID</b>.\n"
    "• Akun harus <b>PUBLIK</b> &amp; <b>tidak ganti username</b> — kalau tidak, refill gagal.\n"
    "• <b>Jangan order ulang</b> ke link sama selama proses (garansi hangus). Proses 3–5 hari.\n"
    "• Klaim tidak berlaku bila masa garansi sudah habis."
)


# =========================================================
# TAMBAHAN V8: MINI APP (Telegram WebApp) + MARGIN RESELLER UNTUK SMM
# - Bot utama & bot reseller: tombol SMM PANEL langsung membuka mini app (judul jendela = nama bot).
# - Login mini app dari initData. Bot utama: HMAC dengan BOT_TOKEN. Bot reseller: tanda tangan Ed25519
#   milik Telegram (tanpa token) -> pemilik bot reseller TIDAK bisa memalsukan user lain.
# Env: AZHURA_WEB_URL (https, wajib), SMM_MINIAPP=0 untuk mematikan (kembali ke menu di bot).
# =========================================================

TELEGRAM_PUBKEY_PROD = bytes.fromhex("e7bf03a2fa4602af4580703d88dda5bb59f32ed8b02a56c187fe7d34caed242d")
INITDATA_MAX_AGE = 24 * 3600


def miniapp_enabled():
    return os.getenv("SMM_MINIAPP", "1").strip().lower() not in {"0", "false", "off", "no"}


def web_base_url():
    url = os.getenv("AZHURA_WEB_URL", "").strip().rstrip("/")
    return url if url.startswith("https://") else ""


def miniapp_url(reseller_id=None):
    """URL mini app SMM. Untuk bot reseller ditambah ?rb=<id reseller>. Kosong bila AZHURA_WEB_URL belum
    https atau mini app dimatikan (SMM_MINIAPP=0)."""
    base = web_base_url()
    if not base or not miniapp_enabled():
        return ""
    url = base + "/?tab=smm"
    if reseller_id:
        url += f"&rb={int(reseller_id)}"
    return url


# ---- Ed25519 (RFC 8032), hanya verifikasi; dipakai agar web tidak butuh library tambahan ----
_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _recover_x(y, sign):
    if y >= _P:
        return None
    x2 = ((y * y - 1) * pow(_D * y * y + 1, _P - 2, _P)) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = (x * _SQRT_M1) % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


def _pt(x, y):
    return (x, y, 1, (x * y) % _P)


def _pt_add(p, q):
    a = ((p[1] - p[0]) * (q[1] - q[0])) % _P
    b = ((p[1] + p[0]) * (q[1] + q[0])) % _P
    c = (2 * p[3] * q[3] * _D) % _P
    d = (2 * p[2] * q[2]) % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return ((e * f) % _P, (g * h) % _P, (f * g) % _P, (e * h) % _P)


def _pt_mul(s, p):
    q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            q = _pt_add(q, p)
        p = _pt_add(p, p)
        s >>= 1
    return q


def _pt_eq(p, q):
    return ((p[0] * q[2] - q[0] * p[2]) % _P == 0) and ((p[1] * q[2] - q[1] * p[2]) % _P == 0)


def _pt_decode(b):
    y = int.from_bytes(b, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else _pt(x, y)


_G = _pt(_recover_x(4 * pow(5, _P - 2, _P) % _P, 0), 4 * pow(5, _P - 2, _P) % _P)


def ed25519_verify(public_key, message, signature):
    import hashlib
    try:
        if len(public_key) != 32 or len(signature) != 64:
            return False
        a = _pt_decode(public_key)
        r = _pt_decode(signature[:32])
        s = int.from_bytes(signature[32:], "little")
        if a is None or r is None or s >= _L:
            return False
        h = int.from_bytes(hashlib.sha512(signature[:32] + public_key + message).digest(), "little") % _L
        return _pt_eq(_pt_mul(s, _G), _pt_add(r, _pt_mul(h, a)))
    except Exception:
        return False


def verify_init_data(init_data, bot_token=None, bot_id=None, now_ts=None, public_key=None):
    """Validasi initData Telegram Mini App -> kembalikan telegram_id; raise ValueError bila tidak sah.
    bot_token  : validasi HMAC (hanya untuk bot milik server sendiri / bot utama).
    bot_id     : validasi tanda tangan Ed25519 Telegram (untuk bot reseller; tidak butuh token bot)."""
    import hmac, hashlib, json, base64
    from urllib.parse import parse_qsl
    try:
        pairs = dict(parse_qsl(str(init_data), keep_blank_values=True))
        auth_date = int(pairs.get("auth_date", "0"))
        if abs(int(now_ts if now_ts is not None else time.time()) - auth_date) > INITDATA_MAX_AGE:
            raise ValueError("kedaluwarsa")
        user_id = int(json.loads(pairs["user"])["id"])
        if bot_token:
            given = pairs.pop("hash", "")
            check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
            secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
            if not given or not hmac.compare_digest(given, hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()):
                raise ValueError("hash")
        elif bot_id:
            sig_b64 = pairs.pop("signature", "")
            pairs.pop("hash", None)
            if not sig_b64:
                raise ValueError("tanpa signature")
            sig = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))
            check = f"{int(bot_id)}:WebAppData\n" + "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
            if not ed25519_verify(public_key or TELEGRAM_PUBKEY_PROD, check.encode(), sig):
                raise ValueError("signature")
        else:
            raise ValueError("tanpa kunci")
        return user_id
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, base64.binascii.Error):
        raise ValueError("initData tidak valid")


# ---- Margin reseller untuk SMM (sama dengan margin OTP; masuk saldo margin reseller) ----

def credit_reseller_margin(local_id):
    """Kreditkan margin reseller SEKALI (atomik). COMPLETED -> penuh; PARTIAL -> proporsional bagian
    yang terkirim. Order gagal/batal/refund tidak pernah dikreditkan. Return jumlah yang dikreditkan."""
    ensure_tables()
    with get_db() as db:
        o = db.execute(
            "SELECT reseller_bot_id, reseller_margin, reseller_margin_status, status, quantity, remains "
            "FROM smm_orders WHERE local_id=%s FOR UPDATE", (local_id,)).fetchone()
        if not o or not o["reseller_bot_id"] or o["reseller_margin_status"] != "NONE":
            return 0
        margin = int(o["reseller_margin"] or 0)
        if o["status"] == "PARTIAL":
            qty, rem = int(o["quantity"] or 0), int(o["remains"] or 0)
            margin = margin * (qty - rem) // qty if qty > 0 and 0 <= rem <= qty else 0
        elif o["status"] != "COMPLETED":
            return 0
        if margin <= 0:
            db.execute("UPDATE smm_orders SET reseller_margin_status='SKIPPED' WHERE local_id=%s", (local_id,))
            return 0
        bot = db.execute(
            "UPDATE reseller_bots SET margin_balance = margin_balance + %s, total_earned = total_earned + %s "
            "WHERE id = %s RETURNING margin_balance", (margin, margin, o["reseller_bot_id"])).fetchone()
        if not bot:
            return 0  # bot reseller sudah dihapus -> tidak ada yang dikreditkan
        db.execute("UPDATE smm_orders SET reseller_margin_status='CREDITED', reseller_margin=%s WHERE local_id=%s",
                   (margin, local_id))
        db.execute("INSERT INTO reseller_ledger (bot_id, amount, balance_after, entry_type, reference, created_at) "
                   "VALUES (%s,%s,%s,%s,%s,%s)",
                   (o["reseller_bot_id"], margin, int(bot["margin_balance"]), "MARGIN", local_id, now()))
        return margin
