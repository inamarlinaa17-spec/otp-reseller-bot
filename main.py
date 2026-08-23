import base64
import json
import logging
import os
import uuid
import threading
import hashlib
import hmac
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import asyncio
import pytz

from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import (
    BOT_TOKEN,
    ADMIN_ID,
    MIDTRANS_SERVER_KEY,
    MIDTRANS_CLIENT_KEY,
    MIDTRANS_API_URL,
    MIDTRANS_SNAP_URL,
    KURS_DOLAR,
    PROFIT_PERCENT
)

from database import (
    init_database,
    create_user,
    get_balance,
    add_balance,
    subtract_balance,
    get_db,
    now,
    get_total_users,
    get_deposit_history,
    get_order_history,
    add_order,
    update_order_otp,
    update_order_status,
    get_order
)

import midtransclient
import provider # <-- TAMBAHAN BARU

# =========================================================
# VALIDASI MIDTRANS
# =========================================================
if not MIDTRANS_SERVER_KEY:
    raise RuntimeError("MIDTRANS_SERVER_KEY belum diatur di Railway.")

# =========================================================
# LOGGING
# =========================================================
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# =========================================================
# FLASK
# =========================================================
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

# =========================================================
# MIDTRANS SNAP
# =========================================================
snap = midtransclient.Snap(is_production=True, server_key=MIDTRANS_SERVER_KEY)

# =========================================================
# UTILS
# =========================================================
def format_rupiah(amount):
    try:
        return f"Rp{int(amount):,}".replace(",", ".")
    except:
        return "Rp0"

def is_admin(user_id):
    return str(user_id) == str(ADMIN_ID)

def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Users", callback_data="admin_users")],
        [InlineKeyboardButton("💳 Deposit", callback_data="admin_deposits")],
        [InlineKeyboardButton("📦 Orders", callback_data="admin_orders")],
        [InlineKeyboardButton("💰 Saldo 5SIM", callback_data="admin_provider")],
        [InlineKeyboardButton("📊 Stats", callback_data="admin_stats")]
    ])

# =========================================================
# FUNGSI BARU: CEK OTP OTOMATIS
# =========================================================
async def cek_otp_task(application, order_id, telegram_id):
    """Jalan di background, cek OTP tiap 3 detik"""
    while True:
        await asyncio.sleep(3)
        res = provider.get_sms(order_id)

        if res.get("sms"):
            otp = res["sms"][0]["code"]
            update_order_otp(order_id, otp)
            await application.bot.send_message(
                chat_id=telegram_id,
                text=f"✅ <b>Kode OTP Masuk!</b>\n\n"
                     f"📱 Nomor: <code>{get_order(order_id)['phone']}</code>\n"
                     f"🔑 Kode: <code>{otp}</code>\n\n"
                     f"Order: <code>{order_id}</code>",
                parse_mode="HTML"
            )
            break

        if res.get("status") == "CANCELED":
            update_order_status(order_id, "CANCELED")
            await application.bot.send_message(chat_id=telegram_id, text=f"❌ Order {order_id} dibatalkan provider")
            break

# =========================================================
# USER CALLBACK
# =========================================================
async def user_callback(query, user_id, context):
    application = context.application

    if query.data == "user_home":
        keyboard = [[InlineKeyboardButton("📦 Order Nomor WA", callback_data="order")]]
        if is_admin(user_id): keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_home")])
        await query.edit_message_text(f"🏠 <b>MENU UTAMA</b>\nSaldo: {format_rupiah(get_balance(user_id))}", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == "order":
        saldo = get_balance(user_id)
        prices = provider.get_prices("0")

        if not prices or "whatsapp" not in prices:
            await query.edit_message_text("❌ Layanan WhatsApp kosong di 5sim", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data="user_home")]]))
            return

        harga_dolar = min([op['cost'] for op in prices['whatsapp'].values()])
        harga_jual = provider.hitung_harga_jual(harga_dolar)

        if saldo < harga_jual:
            await query.edit_message_text(f"❌ Saldo tidak cukup\nHarga WA: {format_rupiah(harga_jual)}\nSaldo: {format_rupiah(saldo)}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💳 Deposit", callback_data="user_deposit"), InlineKeyboardButton("⬅️ Kembali", callback_data="user_home")]]))
            return

        await query.edit_message_text("⏳ Sedang beli nomor...", parse_mode="HTML")
        buy_res = provider.buy_number(country="0", product="whatsapp")

        if buy_res.get("response") == "ERROR":
            await query.edit_message_text(f"❌ Gagal beli: {buy_res.get('message')}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data="user_home")]]))
            return

        order_id = str(buy_res["id"])
        phone = buy_res["phone"]
        provider_cost = int(harga_dolar * 100)

        subtract_balance(user_id, harga_jual, "ORDER", order_id, f"Beli nomor {phone}")
        add_order(order_id, user_id, phone, "0", "whatsapp", harga_jual, provider_cost, order_id)

        await query.edit_message_text(
            f"✅ <b>Order Berhasil!</b>\n\n"
            f"📱 Nomor: <code>{phone}</code>\n"
            f"💰 Harga: {format_rupiah(harga_jual)}\n"
            f"⏳ Status: Menunggu OTP...\n\n"
            f"Masukan nomor ini ke WhatsApp. Kode akan dikirim otomatis kesini.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Batal & Refund", callback_data=f"cancel_{order_id}"), InlineKeyboardButton("🏠 Menu", callback_data="user_home")]])
        )
        asyncio.create_task(cek_otp_task(application, order_id, user_id))

    elif query.data.startswith("cancel_"):
        order_id = query.data.split("_")[1]
        order = get_order(order_id)
        if order and order['status'] == 'WAITING':
            provider.cancel_number(order_id)
            update_order_status(order_id, "CANCELED")
            add_balance(user_id, order['sell_price'], "REFUND", order_id, "Refund order gagal")
            await query.edit_message_text(f"✅ Order {order_id} dibatalkan. Saldo dikembalikan {format_rupiah(order['sell_price'])}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="user_home")]]))
        else:
            await query.answer("Order sudah tidak bisa dibatalkan", show_alert=True)

    elif query.data == "cara":
        await query.edit_message_text("Cara pakai: Klik Order > Beli nomor > Masukkan ke WA", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data="user_home")]]))

    elif query.data == "user_deposit":
        await query.edit_message_text("Fitur deposit Midtrans", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data="user_home")]]))

# =========================================================
# ADMIN CALLBACK
# =========================================================
async def admin_callback(query):
    back = [[InlineKeyboardButton("⬅️ Admin Panel", callback_data="admin_home")]]
    if query.data == "admin_home":
        await query.edit_message_text("👑 <b>ADMIN PANEL</b>\n\nPilih menu:", parse_mode="HTML", reply_markup=admin_menu())
    elif query.data == "admin_provider":
        balance = provider.get_balance()
        await query.edit_message_text(f"💰 <b>5SIM BALANCE</b>\n\nSaldo: <b>${balance}</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(back))
    elif query.data == "admin_users":
        total = get_total_users()
        await query.edit_message_text(f"👥 Total Users: {total}", reply_markup=InlineKeyboardMarkup(back))
    else:
        await query.edit_message_text("Menu admin lain", reply_markup=InlineKeyboardMarkup(back))

# =========================================================
# HANDLERS UTAMA
# =========================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    create_user(user_id)
    keyboard = [[InlineKeyboardButton("📦 Order Nomor WA", callback_data="order")]]
    if is_admin(user_id): keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_home")])
    await update.message.reply_text(f"Halo! Saldo: {format_rupiah(get_balance(user_id))}", reply_markup=InlineKeyboardMarkup(keyboard))

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Pake tombol aja ya bos /start")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

async def admin_add_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await update.message.reply_text("Gunakan panel admin untuk add balance")

async def button_handler(update, context):
    query = update.callback_query
    if not query: return
    try: await query.answer()
    except Exception: pass
    user_id = query.from_user.id

    admin_callbacks = {"admin_users", "admin_deposits", "admin_orders", "admin_provider", "admin_stats", "admin_home"}
    if query.data in admin_callbacks:
        if not is_admin(user_id):
            await query.answer("❌ Kamu bukan admin.", show_alert=True)
            return
        await admin_callback(query)
        return

    if query.data in ["user_home", "cancel_deposit"]:
        context.chat_data["waiting_deposit"] = False
    await user_callback(query, user_id, context)

# =========================================================
# RUN BOT
# =========================================================
def run():
    init_database()
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("addbalance", admin_add_balance))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    application.add_error_handler(error_handler)

    logger.info("Bot berhasil dijalankan.")

    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling(allowed_updates=Update.ALL_TYPES)

# =========================================================
# MAIN
# =========================================================
if __name__ == "__main__":
    run()
