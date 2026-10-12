"""Menu APK PREMIUM AZHURA untuk bot Telegram (callback `apk_*`) + panel admin `admin_apk`.

Alur: katalog kategori -> pilih paket -> (input email bila perlu) -> konfirmasi -> order ->
cek status / riwayat. Logika bisnis di apk_premium.py (dipakai juga oleh web).

Nama provider TIDAK ditampilkan. Tombol Komplain/CS mengarah ke admin AZHURA.
"""
from __future__ import annotations

import os
import uuid
import asyncio
import logging
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

import apk_premium as apk
from database import get_balance, is_maintenance_enabled
from reseller_ctx import current_reseller

logger = logging.getLogger("apk_bot")

PAGE = 8
HIST_PAGE = 5
SYNC_INTERVAL = 90

STATUS_LABEL = {
    "CREATING": "⏳ Membuat",
    "PENDING": "🕐 Menunggu",
    "PROCESSING": "🔄 Diproses",
    "COMPLETED": "✅ Selesai",
    "CANCELED": "❌ Dibatalkan",
    "REFUNDED": "↩️ Refund",
    "ERROR": "❌ Gagal",
    "FAILED": "❌ Gagal",
}


def _admin_id():
    try:
        return int(os.getenv("ADMIN_ID", "0"))
    except ValueError:
        return 0


def _kb(rows):
    return InlineKeyboardMarkup(rows)


def _btn(text, data):
    return InlineKeyboardButton(text, callback_data=data)


def _n(v):
    try:
        return f"{int(v):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "-"


def _cs_url():
    """URL CS AZHURA (bukan provider)."""
    rs = current_reseller()
    if rs and rs.get("cs_url"):
        return str(rs["cs_url"]).strip()
    username = (os.getenv("ADMIN_USERNAME") or "").strip().lstrip("@")
    if username:
        return f"https://t.me/{username}"
    return None


def menu_button():
    """Tombol 'APK PREMIUM' di menu utama: langsung buka mini app (seperti SMM PANEL).
    Bila AZHURA_WEB_URL belum di-set / APK_MINIAPP=0 -> menu di dalam bot."""
    rs = current_reseller()
    url = apk.miniapp_url(rs["id"] if rs else None)
    if url:
        return InlineKeyboardButton("💎 APK PREMIUM", web_app=WebAppInfo(url=url))
    return _btn("💎 APK PREMIUM", "apk_home")


async def _show(query, text, markup):
    try:
        await query.edit_message_text(
            text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)
    except Exception as exc:
        if "not modified" in str(exc).lower():
            return
        try:
            await query.message.reply_text(
                text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)
        except Exception:
            logger.exception("apk: gagal menampilkan pesan")


# =========================================================
# MENU UTAMA & KATALOG
# =========================================================

async def home(query, context):
    if not apk.is_configured():
        await _show(query,
                    "💎 <b>APK PREMIUM AZHURA</b>\n\n"
                    "Layanan belum tersedia. Hubungi admin.",
                    _kb([[_btn("⬅️ Kembali", "home")]]))
        return
    if apk.is_maintenance() or is_maintenance_enabled():
        await _show(query,
                    "💎 <b>APK PREMIUM AZHURA</b>\n\n"
                    "🛠 Sedang maintenance. Pemesanan baru ditutup sementara.",
                    _kb([[_btn("📋 Riwayat", "apk_h:0")], [_btn("⬅️ Kembali", "home")]]))
        return

    try:
        cats = await asyncio.to_thread(apk.get_categories)
    except apk.ApkError as e:
        await _show(query, f"💎 <b>APK PREMIUM AZHURA</b>\n\n⚠️ {escape(str(e))}",
                    _kb([[_btn("🔄 Coba lagi", "apk_home")], [_btn("⬅️ Kembali", "home")]]))
        return
    except Exception:
        logger.exception("apk home catalog")
        await _show(query, "💎 <b>APK PREMIUM AZHURA</b>\n\n⚠️ Gagal memuat katalog. Coba lagi.",
                    _kb([[_btn("🔄 Coba lagi", "apk_home")], [_btn("⬅️ Kembali", "home")]]))
        return

    bal = get_balance(query.from_user.id)
    lines = [
        "💎 <b>APK PREMIUM AZHURA</b>\n",
        "Langganan aplikasi premium (Netflix, Canva, Gemini, dll).\n",
        f"💰 Saldo Anda: <b>{apk.rupiah(bal)}</b>\n",
        "Pilih aplikasi:",
    ]
    rows = []
    for i, c in enumerate(cats):
        pub = apk.public_category(c)
        label = f"{pub['app']} · dari {pub['mulai_formatted']}"
        if pub["varian"]:
            label += f" ({pub['varian']} paket)"
        rows.append([_btn(label[:60], f"apk_c:{c['slug']}")])
        if i >= 40:
            break
    rows.append([_btn("📋 Riwayat Pesanan", "apk_h:0")])
    cs = _cs_url()
    if cs:
        rows.append([InlineKeyboardButton("💬 Komplain / CS", url=cs)])
    rows.append([_btn("⬅️ Kembali", "home")])
    await _show(query, "\n".join(lines), _kb(rows))


async def show_variants(query, context, slug):
    try:
        items = await asyncio.to_thread(apk.get_variants, slug)
    except apk.ApkError as e:
        await _show(query, f"⚠️ {escape(str(e))}",
                    _kb([[_btn("⬅️ Katalog", "apk_home")]]))
        return
    if not items:
        await _show(query, "Paket tidak tersedia / stok habis.",
                    _kb([[_btn("⬅️ Katalog", "apk_home")]]))
        return

    app_name = items[0].get("app") or slug
    lines = [f"💎 <b>{escape(app_name)}</b>\n", "Pilih paket:"]
    rows = []
    for it in items:
        pub = apk.public_item(it)
        label = f"{pub['nama'] or pub['nama_penuh']} · {pub['harga_formatted']}"
        if pub.get("stok") is not None:
            label += f" (stok {pub['stok']})"
        rows.append([_btn(label[:64], f"apk_i:{it['id']}")])
    rows.append([_btn("⬅️ Katalog", "apk_home")])
    await _show(query, "\n".join(lines), _kb(rows))


async def show_item(query, context, app_id):
    try:
        item, cat = await asyncio.to_thread(apk.find_item, app_id)
    except Exception:
        item, cat = None, None
    if not item:
        await _show(query, "Paket tidak ditemukan atau stok habis.",
                    _kb([[_btn("⬅️ Katalog", "apk_home")]]))
        return

    pub = apk.public_item(item, cat)
    bal = get_balance(query.from_user.id)
    lines = [
        "💎 <b>DETAIL PAKET</b>\n",
        f"📱 <b>{escape(pub['app'])}</b>",
        f"📦 {escape(pub['nama_penuh'] or pub['nama'])}",
    ]
    if pub.get("keterangan"):
        lines.append(f"📝 {escape(str(pub['keterangan'])[:300])}")
    if pub.get("durasi_hari"):
        lines.append(f"⏱ Durasi: {pub['durasi_hari']} hari")
    if pub.get("garansi_hari"):
        lines.append(f"🛡 Garansi: {pub['garansi_hari']} hari")
    lines.append(f"💰 Harga: <b>{pub['harga_formatted']}</b>")
    lines.append(f"💳 Saldo Anda: <b>{apk.rupiah(bal)}</b>")
    if pub.get("butuh_email"):
        lines.append("\n⚠️ Paket ini membutuhkan <b>email tujuan</b>.")
    if pub.get("stok") is not None:
        lines.append(f"📦 Stok tersisa: {pub['stok']}")

    rows = []
    if pub.get("butuh_email"):
        context.user_data["apk_state"] = {
            "step": "email",
            "app_id": int(app_id),
            "expected_price": pub["harga"],
            "nama": pub["nama_penuh"] or pub["nama"],
        }
        rows.append([_btn("✉️ Kirim Email Tujuan", "apk_ask_email")])
    else:
        rows.append([_btn("✅ Beli Sekarang", f"apk_buy:{app_id}:{pub['harga']}")])
    rows.append([_btn("⬅️ Kembali", f"apk_c:{pub['slug'] or (cat or {}).get('slug', '')}")])
    rows.append([_btn("🏠 Katalog", "apk_home")])
    await _show(query, "\n".join(lines), _kb(rows))


async def ask_email(query, context):
    st = context.user_data.get("apk_state") or {}
    if st.get("step") != "email":
        await home(query, context)
        return
    await _show(query,
                "✉️ <b>Kirim alamat email tujuan</b>\n\n"
                f"Paket: <b>{escape(str(st.get('nama') or ''))}</b>\n"
                "Ketik email Anda di chat ini (contoh: nama@gmail.com).",
                _kb([[_btn("❌ Batal", "apk_home")]]))


async def confirm_buy(query, context, app_id, expected_price):
    try:
        item, cat = await asyncio.to_thread(apk.find_item, app_id)
    except Exception:
        item = None
    if not item:
        await _show(query, "Paket tidak ditemukan.",
                    _kb([[_btn("⬅️ Katalog", "apk_home")]]))
        return
    pub = apk.public_item(item, cat)
    bal = get_balance(query.from_user.id)
    if bal < pub["harga"]:
        await _show(query,
                    f"💰 Saldo tidak cukup.\nDibutuhkan {pub['harga_formatted']}, "
                    f"saldo Anda {apk.rupiah(bal)}.\nSilakan deposit terlebih dahulu.",
                    _kb([[_btn("⬅️ Katalog", "apk_home")]]))
        return

    lines = [
        "💎 <b>KONFIRMASI PESANAN</b>\n",
        f"📱 {escape(pub['app'])}",
        f"📦 {escape(pub['nama_penuh'] or pub['nama'])}",
        f"💰 Harga: <b>{pub['harga_formatted']}</b>",
        f"💳 Saldo setelah: <b>{apk.rupiah(bal - pub['harga'])}</b>\n",
        "Pesanan tidak dapat dibatalkan setelah diproses.",
    ]
    await _show(query, "\n".join(lines), _kb([
        [_btn("✅ Ya, Pesan Sekarang", f"apk_do:{app_id}:{pub['harga']}")],
        [_btn("❌ Batal", "apk_home")],
    ]))


async def do_order(query, context, app_id, expected_price, email=None):
    if apk.is_maintenance() or is_maintenance_enabled():
        await _show(query, "🛠 Sedang maintenance. Coba lagi nanti.",
                    _kb([[_btn("⬅️ Katalog", "apk_home")]]))
        return

    await _show(query, "⏳ Memproses pesanan…", None)
    idem = f"apk-{query.from_user.id}-{app_id}-{uuid.uuid4().hex[:8]}"
    try:
        order = await asyncio.to_thread(
            apk.create_order,
            query.from_user.id,
            int(app_id),
            email=email,
            source="BOT",
            idem_key=idem,
            expected_price=int(expected_price) if expected_price else None,
        )
    except apk.ApkError as e:
        await _show(query, f"❌ {escape(str(e))}",
                    _kb([[_btn("⬅️ Katalog", "apk_home")], [_btn("📋 Riwayat", "apk_h:0")]]))
        return
    except Exception:
        logger.exception("apk do_order")
        await _show(query, "❌ Gagal membuat pesanan. Cek riwayat / saldo Anda.",
                    _kb([[_btn("📋 Riwayat", "apk_h:0")], [_btn("⬅️ Katalog", "apk_home")]]))
        return

    context.user_data.pop("apk_state", None)
    await show_order(query, context, order["local_id"])


async def show_order(query, context, local_id):
    try:
        order = await asyncio.to_thread(apk.sync_order, local_id)
    except apk.ApkError:
        order = await asyncio.to_thread(apk.get_apk_order, local_id, query.from_user.id)
    if not order or int(order["telegram_id"]) != int(query.from_user.id):
        await _show(query, "Pesanan tidak ditemukan.",
                    _kb([[_btn("📋 Riwayat", "apk_h:0")]]))
        return

    st = STATUS_LABEL.get(order["status"], order["status"])
    lines = [
        "💎 <b>DETAIL PESANAN APK PREMIUM</b>\n",
        f"🆔 <code>{escape(order['local_id'])}</code>",
        f"📱 {escape(str(order.get('app_name') or '-'))}",
        f"📦 {escape(str(order.get('package_name') or '-'))}",
        f"💰 {apk.rupiah(order.get('sell_price'))}",
        f"📊 Status: <b>{st}</b>",
    ]
    if order.get("email"):
        lines.append(f"✉️ Email: {escape(order['email'])}")
    if order.get("status") == "COMPLETED" and order.get("account_data"):
        lines.append("\n🔑 <b>DATA AKUN:</b>")
        lines.append(f"<code>{escape(str(order['account_data']))}</code>")
        lines.append("\n⚠️ Simpan data di atas. Salin sekarang.")
    if int(order.get("refunded_amount") or 0) > 0:
        lines.append(f"\n↩️ Dikembalikan: {apk.rupiah(order['refunded_amount'])}")
    if order.get("created_at"):
        lines.append(f"\n🕐 {escape(str(order['created_at']))}")

    rows = []
    if order["status"] in ("PENDING", "PROCESSING", "CREATING"):
        rows.append([_btn("🔄 Cek Status", f"apk_st:{local_id}")])
    cs = _cs_url()
    if cs:
        rows.append([InlineKeyboardButton("💬 Komplain / CS", url=cs)])
    rows.append([_btn("📋 Riwayat", "apk_h:0"), _btn("🏠 Katalog", "apk_home")])
    await _show(query, "\n".join(lines), _kb(rows))


async def history(query, context, page=0):
    tid = query.from_user.id
    total = await asyncio.to_thread(apk.count_user_orders, tid)
    rows_data = await asyncio.to_thread(apk.list_user_orders, tid, HIST_PAGE, page * HIST_PAGE)
    pages = max(1, (total + HIST_PAGE - 1) // HIST_PAGE)
    lines = [f"📋 <b>RIWAYAT APK PREMIUM</b> ({total})\n"]
    for o in rows_data:
        st = STATUS_LABEL.get(o["status"], o["status"])
        lines.append(
            f"• <code>{escape(o['local_id'])}</code>\n"
            f"  {escape(str(o.get('app_name') or '')[:30])} · {apk.rupiah(o['sell_price'])} · <b>{st}</b>")
    if not rows_data:
        lines.append("Belum ada pesanan.")
    nav = []
    if page > 0:
        nav.append(_btn("◀️", f"apk_h:{page - 1}"))
    if page < pages - 1:
        nav.append(_btn("▶️", f"apk_h:{page + 1}"))
    kb = []
    for o in rows_data:
        kb.append([_btn(f"📄 {o['local_id']}", f"apk_st:{o['local_id']}")])
    if nav:
        kb.append(nav)
    kb.append([_btn("🏠 Katalog", "apk_home"), _btn("⬅️ Menu", "home")])
    await _show(query, "\n".join(lines), _kb(kb))


# =========================================================
# CALLBACK & TEXT HANDLERS
# =========================================================

async def handle_callback(query, context):
    data = query.data or ""
    if not data.startswith("apk_"):
        return False

    try:
        await query.answer()
    except Exception:
        pass

    if data == "apk_home":
        await home(query, context)
    elif data.startswith("apk_c:"):
        await show_variants(query, context, data.split(":", 1)[1])
    elif data.startswith("apk_i:"):
        await show_item(query, context, data.split(":", 1)[1])
    elif data == "apk_ask_email":
        await ask_email(query, context)
    elif data.startswith("apk_buy:"):
        parts = data.split(":")
        await confirm_buy(query, context, parts[1], parts[2] if len(parts) > 2 else None)
    elif data.startswith("apk_do:"):
        parts = data.split(":")
        st = context.user_data.get("apk_state") or {}
        email = st.get("email") if st.get("app_id") == int(parts[1]) else None
        await do_order(query, context, parts[1], parts[2] if len(parts) > 2 else None, email=email)
    elif data.startswith("apk_st:"):
        await show_order(query, context, data.split(":", 1)[1])
    elif data.startswith("apk_h:"):
        try:
            page = int(data.split(":", 1)[1])
        except ValueError:
            page = 0
        await history(query, context, page)
    else:
        await home(query, context)
    return True


async def handle_text(update, context):
    """Tangani input email untuk paket butuh_email."""
    st = context.user_data.get("apk_state")
    if not st or st.get("step") != "email":
        return False
    text = (update.message.text or "").strip()
    if not text or "@" not in text or len(text) > 200:
        await update.message.reply_text(
            "❌ Email tidak valid. Kirim alamat email yang benar (contoh: nama@gmail.com).",
            reply_markup=_kb([[_btn("❌ Batal", "apk_home")]]))
        return True

    st["email"] = text
    st["step"] = "confirm"
    context.user_data["apk_state"] = st
    app_id = st["app_id"]
    expected = st.get("expected_price")

    # Konfirmasi lewat pesan baru
    bal = get_balance(update.effective_user.id)
    try:
        item, cat = await asyncio.to_thread(apk.find_item, app_id)
        pub = apk.public_item(item, cat) if item else None
    except Exception:
        pub = None
    if not pub:
        await update.message.reply_text("Paket tidak ditemukan.", reply_markup=_kb([[_btn("⬅️ Katalog", "apk_home")]]))
        context.user_data.pop("apk_state", None)
        return True

    lines = [
        "💎 <b>KONFIRMASI PESANAN</b>\n",
        f"📱 {escape(pub['app'])}",
        f"📦 {escape(pub['nama_penuh'] or pub['nama'])}",
        f"✉️ Email: <code>{escape(text)}</code>",
        f"💰 Harga: <b>{pub['harga_formatted']}</b>",
        f"💳 Saldo setelah: <b>{apk.rupiah(bal - pub['harga'])}</b>\n",
        "Pesanan tidak dapat dibatalkan setelah diproses.",
    ]
    await update.message.reply_text(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=_kb([
            [_btn("✅ Ya, Pesan Sekarang", f"apk_do:{app_id}:{pub['harga']}")],
            [_btn("❌ Batal", "apk_home")],
        ]))
    return True


# =========================================================
# ADMIN
# =========================================================

async def admin_view(query, page=0):
    per = 8
    summ = await asyncio.to_thread(apk.admin_summary)
    rows = await asyncio.to_thread(apk.list_all_orders, per, page * per)
    pages = max(1, (int(summ["total"]) + per - 1) // per)
    lines = [
        "💎 <b>APK PREMIUM ORDERS (ADMIN)</b>\n",
        f"Total {summ['total']} • Aktif {summ['active']} • Selesai {summ['completed']} • "
        f"Gagal {summ['failed']}",
        f"💰 Omzet: <b>{apk.rupiah(summ['gross'])}</b> • Profit ± <b>{apk.rupiah(summ['profit'])}</b>\n",
    ]
    for o in rows:
        lines.append(
            f"• <code>{escape(o['local_id'])}</code> | {escape(str(o.get('username') or o['telegram_id']))} "
            f"[{escape(str(o['source']))}]\n  {escape(str(o.get('app_name') or '')[:30])} · "
            f"{apk.rupiah(o['sell_price'])} · <b>{STATUS_LABEL.get(o['status'], o['status'])}</b>")
    if not rows:
        lines.append("Belum ada pesanan APK PREMIUM.")
    nav = []
    if page > 0:
        nav.append(_btn("◀️", f"admin_apk_page:{page - 1}"))
    if page < pages - 1:
        nav.append(_btn("▶️", f"admin_apk_page:{page + 1}"))
    maint = await asyncio.to_thread(apk.is_maintenance)
    lines.insert(1, "🛠 Maintenance APK: <b>" + ("AKTIF" if maint else "NONAKTIF") + "</b>")
    kb = ([nav] if nav else []) + [
        [_btn("🛠 Maintenance APK: " + ("ON → matikan" if maint else "OFF → aktifkan"), "admin_apk_maintenance")],
        [_btn("⬅️ Admin Panel", "admin_home")],
    ]
    await _show(query, "\n".join(lines), _kb(kb))


async def admin_toggle_maintenance(query):
    current = await asyncio.to_thread(apk.is_maintenance)
    await asyncio.to_thread(apk.set_maintenance, not current)
    await admin_view(query, 0)


# =========================================================
# BACKGROUND SYNC
# =========================================================

async def sync_loop():
    await asyncio.sleep(40)
    while True:
        try:
            if apk.is_configured():
                await asyncio.to_thread(apk.sync_active_orders)
        except Exception:
            logger.exception("apk sync_loop error")
        await asyncio.sleep(SYNC_INTERVAL)
