"""Menu BELI GMAIL untuk bot Telegram — langsung buka Mini App (seperti SMM & APK).

Callback gmail_* disediakan sebagai fallback bila AZHURA_WEB_URL belum di-set.
Logika bisnis di gmail_premium.py.
"""
from __future__ import annotations

import asyncio
import logging
import os
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

import gmail_premium as gmail
from database import get_balance, is_maintenance_enabled
from reseller_ctx import current_reseller

logger = logging.getLogger("gmail_bot")

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


def _kb(rows):
    return InlineKeyboardMarkup(rows)


def _btn(text, data):
    return InlineKeyboardButton(text, callback_data=data)


def menu_button():
    """Tombol BELI GMAIL di menu utama: langsung buka mini app."""
    rs = current_reseller()
    url = gmail.miniapp_url(rs["id"] if rs else None)
    if url:
        return InlineKeyboardButton("📧 BELI GMAIL", web_app=WebAppInfo(url=url))
    return _btn("📧 BELI GMAIL", "gmail_home")


async def _show(query, text, markup):
    try:
        await query.edit_message_text(
            text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True
        )
    except Exception as exc:
        if "not modified" in str(exc).lower():
            return
        try:
            await query.message.reply_text(
                text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True
            )
        except Exception:
            logger.exception("gmail: gagal menampilkan pesan")


async def home(query, context):
    if not gmail.is_configured():
        await _show(
            query,
            "📧 <b>BELI GMAIL</b>\n\nLayanan belum tersedia. Hubungi admin.",
            _kb([[_btn("⬅️ Kembali", "user_home")]]),
        )
        return
    if gmail.is_maintenance() or is_maintenance_enabled():
        await _show(
            query,
            "📧 <b>BELI GMAIL</b>\n\n🛠 Sedang maintenance. Pemesanan baru ditutup sementara.",
            _kb([[_btn("📋 Riwayat", "gmail_h")], [_btn("⬅️ Kembali", "user_home")]]),
        )
        return
    try:
        stock = await asyncio.to_thread(gmail.get_stock)
    except gmail.GmailError as e:
        await _show(
            query,
            f"📧 <b>BELI GMAIL</b>\n\n⚠️ {escape(str(e))}",
            _kb([[_btn("🔄 Coba lagi", "gmail_home")], [_btn("⬅️ Kembali", "user_home")]]),
        )
        return
    bal = get_balance(query.from_user.id) or 0
    text = (
        "📧 <b>BELI GMAIL</b>\n\n"
        f"Harga: <b>{escape(stock['sell_formatted'])}</b>\n"
        f"Stok: <b>{stock['stock_available']}</b>\n"
        f"Saldo Anda: <b>{gmail.rupiah(bal)}</b>\n\n"
        "Akun Gmail siap pakai (email + password). "
        "Pembelian lebih nyaman lewat Mini App."
    )
    rows = [
        [_btn(f"🛒 Beli — {stock['sell_formatted']}", "gmail_buy")],
        [_btn("📋 Riwayat", "gmail_h")],
        [_btn("⬅️ Kembali", "user_home")],
    ]
    await _show(query, text, _kb(rows))


async def do_buy(query, context):
    uid = query.from_user.id
    if gmail.is_maintenance() or is_maintenance_enabled():
        await query.answer("Maintenance aktif.", show_alert=True)
        return
    try:
        order = await asyncio.to_thread(
            gmail.create_order, uid, "BOT", f"bot-{uid}-{query.id}"
        )
    except gmail.GmailError as e:
        await query.answer(str(e)[:180], show_alert=True)
        await home(query, context)
        return
    st = STATUS_LABEL.get(order["status"], order["status"])
    acc = ""
    if order.get("email_address"):
        acc = f"\n📧 <code>{escape(order['email_address'])}</code>"
        if order.get("password"):
            acc += f"\n🔑 <code>{escape(order['password'])}</code>"
    await _show(
        query,
        f"📧 <b>PESANAN GMAIL</b>\n\n"
        f"ID: <code>{escape(order['local_id'])}</code>\n"
        f"Status: {st}\n"
        f"Harga: {gmail.rupiah(order['sell_price'])}{acc}",
        _kb([[_btn("📋 Riwayat", "gmail_h")], [_btn("⬅️ Menu", "user_home")]]),
    )


async def history(query, context):
    uid = query.from_user.id
    try:
        rows = await asyncio.to_thread(gmail.list_user_orders, uid, 10, 0)
    except Exception as e:
        await _show(query, f"⚠️ {escape(str(e))}", _kb([[_btn("⬅️ Kembali", "gmail_home")]]))
        return
    if not rows:
        await _show(
            query,
            "📧 <b>RIWAYAT BELI GMAIL</b>\n\nBelum ada pesanan.",
            _kb([[_btn("⬅️ Kembali", "gmail_home")]]),
        )
        return
    lines = ["📧 <b>RIWAYAT BELI GMAIL</b>\n"]
    for o in rows:
        st = STATUS_LABEL.get(o["status"], o["status"])
        line = f"• <code>{escape(o['local_id'])}</code> · {st} · {gmail.rupiah(o['sell_price'])}"
        if o.get("email_address") and o["status"] == "COMPLETED":
            line += f"\n  📧 <code>{escape(o['email_address'])}</code>"
        lines.append(line)
    await _show(
        query,
        "\n".join(lines),
        _kb([[_btn("🔄 Refresh", "gmail_h")], [_btn("⬅️ Kembali", "gmail_home")]]),
    )


async def handle_callback(query, context):
    data = query.data or ""
    if data == "gmail_home":
        await home(query, context)
    elif data == "gmail_buy":
        await do_buy(query, context)
    elif data == "gmail_h":
        await history(query, context)
    else:
        await home(query, context)


async def admin_view(query, page=0):
    try:
        summary = await asyncio.to_thread(gmail.admin_summary)
        items = await asyncio.to_thread(gmail.list_all_orders, 15, page * 15)
    except Exception as e:
        await _show(query, f"⚠️ {escape(str(e))}", _kb([[_btn("⬅️ Admin", "admin")]]))
        return
    lines = [
        "📧 <b>HISTORI BELI GMAIL</b>\n",
        f"Total: {summary.get('total', 0)} · Selesai: {summary.get('completed', 0)} · "
        f"Aktif: {summary.get('active', 0)}",
        f"Omzet: {gmail.rupiah(summary.get('gross', 0))} · "
        f"Profit ± {gmail.rupiah(summary.get('profit', 0))}\n",
    ]
    for o in items:
        st = STATUS_LABEL.get(o["status"], o["status"])
        lines.append(
            f"• <code>{escape(o['local_id'])}</code> · {st}\n"
            f"  👤 {o['telegram_id']} · {gmail.rupiah(o['sell_price'])} "
            f"(modal {gmail.rupiah(o['provider_cost'])})"
        )
    rows = []
    nav = []
    if page > 0:
        nav.append(_btn("⬅️", f"admin_gmail_page:{page - 1}"))
    if len(items) >= 15:
        nav.append(_btn("➡️", f"admin_gmail_page:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([_btn("⬅️ Admin", "admin")])
    await _show(query, "\n".join(lines) or "Belum ada data.", _kb(rows))


async def admin_toggle_maintenance(query):
    cur = gmail.is_maintenance()
    gmail.set_maintenance(not cur)
    state = "ON" if not cur else "OFF"
    await query.answer(f"Gmail maintenance {state}")
    await _show(
        query,
        f"📧 Maintenance BELI GMAIL: <b>{state}</b>",
        _kb([[_btn("⬅️ Admin", "admin")]]),
    )


async def sync_loop():
    while True:
        try:
            await asyncio.to_thread(gmail.sync_active_orders, 25)
        except Exception:
            logger.exception("gmail sync_loop error")
        await asyncio.sleep(90)
