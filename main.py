import base64
import json
import logging
import os
import threading
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters,
)

from config import (
    BOT_TOKEN, ADMIN_ID, MIDTRANS_SERVER_KEY, MIDTRANS_CLIENT_KEY,
    MIDTRANS_API_URL, MIDTRANS_SNAP_URL
)
from database import init_database, create_user, get_balance, add_balance, get_db, now
import midtransclient

PORT = int(os.getenv("PORT", "8080"))

if not MIDTRANS_SERVER_KEY:
    raise RuntimeError("MIDTRANS_SERVER_KEY belum diatur di Railway.")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

# Init Midtrans Snap
snap = midtransclient.Snap(
    is_production=os.getenv("MIDTRANS_IS_PRODUCTION", "false").lower() == "true",
    server_key=MIDTRANS_SERVER_KEY
)

def is_admin(user_id):
    return user_id == ADMIN_ID

def format_rupiah(amount):
    return f"Rp{amount:,}".replace(",", ".")

def user_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💰 Saldo", callback_data="user_balance"),
            InlineKeyboardButton("💳 Deposit", callback_data="user_deposit"),
        ],
        [
            InlineKeyboardButton("📱 Layanan", callback_data="user_services"),
            InlineKeyboardButton("📜 Riwayat", callback_data="user_history"),
        ],
    ])

def admin_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👥 Users", callback_data="admin_users"),
            InlineKeyboardButton("💳 Deposit", callback_data="admin_deposits"),
        ],
        [
            InlineKeyboardButton("📦 Orders", callback_data="admin_orders"),
            InlineKeyboardButton("💰 Provider", callback_data="admin_provider"),
        ],
        [
            InlineKeyboardButton("📊 Statistik", callback_data="admin_stats")
        ],
    ])

def create_midtrans_snap(amount, deposit_id):
    param = {
        "transaction_details": {
            "order_id": deposit_id,
            "gross_amount": amount,
        },
        "item_details": [{
            "id": "DEPOSIT",
            "price": amount,
            "quantity": 1,
            "name": "Deposit Saldo Bot"
        }],
        "customer_details": {
            "first_name": f"User {deposit_id}"
        },
        "expiry": {
            "start_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S +07:00"),
            "unit": "hours",
            "duration": 24
        }
    }
    try:
        transaction = snap.create_transaction(param)
        return transaction
    except Exception as error:
        logger.error("Midtrans Error: %s", error)
        raise RuntimeError("Gagal membuat Snap Token Midtrans.") from error

def send_telegram_message(chat_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    request = Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=20) as response:
            response.read()
    except Exception as error:
        logger.error("Notifikasi Telegram gagal: %s", error)

def complete_deposit_payment(deposit_id, payment_reference, paid_amount):
    with get_db() as db:
        deposit = db.execute("SELECT deposit_id, telegram_id, amount, status FROM deposits WHERE deposit_id = %s FOR UPDATE", (deposit_id,)).fetchone()
        if not deposit: raise ValueError("Deposit tidak ditemukan.")
        if deposit["status"] == "SUCCESS": return {"completed": False, "already_completed": True}
        if deposit["status"]!= "PENDING": raise ValueError(f"Deposit berstatus {deposit['status']}, bukan PENDING.")
        if int(paid_amount)!= int(deposit["amount"]): raise ValueError("Nominal pembayaran Midtrans tidak sama dengan nominal deposit.")
        user = db.execute("SELECT balance FROM users WHERE telegram_id = %s FOR UPDATE", (deposit["telegram_id"],)).fetchone()
        if not user: raise ValueError("User deposit tidak ditemukan.")
        before = user["balance"]
        after = before + deposit["amount"]
        db.execute("UPDATE users SET balance = %s WHERE telegram_id = %s", (after, deposit["telegram_id"]))
        db.execute("""INSERT INTO ledger (telegram_id, amount, balance_before, balance_after, transaction_type, reference, description, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                   (deposit["telegram_id"], deposit["amount"], before, after, "DEPOSIT", payment_reference or deposit_id, f"Deposit Midtrans {deposit_id}", now()))
        db.execute("UPDATE deposits SET status = 'SUCCESS', payment_reference = COALESCE(%s, payment_reference), completed_at = %s WHERE deposit_id = %s",
                   (payment_reference, now(), deposit_id))
        return {"completed": True, "already_completed": False, "telegram_id": deposit["telegram_id"], "amount": deposit["amount"], "new_balance": after}

def process_midtrans_webhook(payload):
    status = payload.get("transaction_status")
    deposit_id = payload.get("order_id")
    payment_reference = payload.get("transaction_id")
    paid_amount = payload.get("gross_amount")
    if not deposit_id: raise ValueError("Webhook tidak memiliki order_id.")
    if status == "settlement":
        result = complete_deposit_payment(deposit_id, payment_reference, int(float(paid_amount)))
        if result["completed"]:
            send_telegram_message(result["telegram_id"], f"✅ <b>Deposit berhasil!</b>\n\n💰 Deposit: <b>{format_rupiah(result['amount'])}</b>\n💳 Status: <b>PAID</b>\n🧾 ID: <code>{deposit_id}</code>\n\n💰 Saldo sekarang: <b>{format_rupiah(result['new_balance'])}</b>")
    elif status in ["expire", "cancel"]:
        with get_db() as db:
            db.execute("UPDATE deposits SET status = 'EXPIRED' WHERE deposit_id = %s AND status = 'PENDING'", (deposit_id,))
        logger.info("Deposit expired: %s", deposit_id)
    else:
        logger.info("Webhook Midtrans diabaikan: deposit=%s status=%s", deposit_id, status)

class MidtransWebhookHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): logger.info("Webhook HTTP: " + fmt, *args)
    def send_json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        if self.path == "/health": self.send_json(200, {"ok": True})
        else: self.send_json(404, {"ok": False, "error": "Not found"})
    def do_POST(self):
        if self.path!= "/midtrans/webhook":
            self.send_json(404, {"ok": False, "error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length)
            payload = json.loads(raw_body.decode("utf-8"))
            process_midtrans_webhook(payload)
            self.send_json(200, {"ok": True})
        except json.JSONDecodeError:
            self.send_json(400, {"ok": False, "error": "Invalid JSON"})
            logger.exception("Gagal memproses webhook Midtrans.")
            self.send_json(500, {"ok": False, "error": "Webhook processing failed"})

def start_webhook_server():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), MidtransWebhookHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Webhook server aktif di port %s", PORT)
    return server

async def user_start(update):
    user = update.effective_user
    create_user(user.id, user.username, user.first_name)
    balance = get_balance(user.id)
    await update.message.reply_text(f"👋 <b>Selamat datang!</b>\n\nBot layanan digital kamu sudah aktif.\n\n💰 Saldo: <b>{format_rupiah(balance)}</b>\n\nSilakan pilih menu:", parse_mode="HTML", reply_markup=user_menu())

async def admin_start(update):
    await update.message.reply_text("👑 <b>ADMIN PANEL</b>\n\nSelamat datang, Admin.\n\nPilih menu:", parse_mode="HTML", reply_markup=admin_menu())

async def start(update, context):
    user = update.effective_user
    create_user(user.id, user.username, user.first_name)
    if is_admin(user.id): await admin_start(update)
    else: await user_start(update)

async def user_callback(query, user_id):
    if query.data == "user_balance":
        keyboard = [[InlineKeyboardButton("💳 Deposit", callback_data="user_deposit")], [InlineKeyboardButton("⬅️ Kembali", callback_data="user_home")]]
        await query.edit_message_text(f"💰 <b>Saldo Kamu</b>\n\nSaldo: <b>{format_rupiah(get_balance(user_id))}</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    elif query.data == "user_deposit": return "WAIT_DEPOSIT"
    elif query.data == "user_services":
        await query.edit_message_text("📱 <b>Layanan</b>\n\nModul layanan belum diaktifkan.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data="user_home")]]))
    elif query.data == "user_history":
        await query.edit_message_text("📜 <b>Riwayat Transaksi</b>\n\nRiwayat deposit akan kita tampilkan di tahap berikutnya.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data="user_home")]]))
    elif query.data == "user_home":
        await query.edit_message_text(f"🏠 <b>Menu Utama</b>\n\n💰 Saldo: <b>{format_rupiah(get_balance(user_id))}</b>\n\nPilih menu:", parse_mode="HTML", reply_markup=user_menu())

async def admin_callback(query):
    back = [[InlineKeyboardButton("⬅️ Admin Panel", callback_data="admin_home")]]
    if query.data == "admin_users":
        with get_db() as db: total = db.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"]
        await query.edit_message_text(f"👥 <b>USERS</b>\n\nTotal user: <b>{total}</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(back))
    elif query.data == "admin_deposits":
        with get_db() as db: total = db.execute("SELECT COUNT(*) AS total FROM deposits").fetchone()["total"]; pending = db.execute("SELECT COUNT(*) AS total FROM deposits WHERE status = 'PENDING'").fetchone()["total"]; success = db.execute("SELECT COUNT(*) AS total FROM deposits WHERE status = 'SUCCESS'").fetchone()["total"]
        await query.edit_message_text(f"💳 <b>DEPOSIT</b>\n\nTotal transaksi: <b>{total}</b>\nPending: <b>{pending}</b>\nSuccess: <b>{success}</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(back))
    elif query.data == "admin_orders":
        with get_db() as db: total = db.execute("SELECT COUNT(*) AS total FROM orders").fetchone()["total"]; pending = db.execute("SELECT COUNT(*) AS total FROM orders WHERE status = 'PENDING'").fetchone()["total"]; success = db.execute("SELECT COUNT(*) AS total FROM orders WHERE status = 'SUCCESS'").fetchone()["total"]
        await query.edit_message_text(f"📦 <b>ORDERS</b>\n\nTotal order: <b>{total}</b>\nPending: <b>{pending}</b>\nSuccess: <b>{success}</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(back))
    elif query.data == "admin_provider": await query.edit_message_text("💰 <b>PROVIDER</b>\n\nProvider API belum terhubung.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(back))
    elif query.data == "admin_stats":
        with get_db() as db: users = db.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"]; deposits = db.execute("SELECT COUNT(*) AS total FROM deposits").fetchone()["total"]; orders = db.execute("SELECT COUNT(*) AS total FROM orders").fetchone()["total"]; balance = db.execute("SELECT COALESCE(SUM(balance), 0) AS total FROM users").fetchone()["total"]
        await query.edit_message_text(f"📊 <b>STATISTIK</b>\n\n👥 Users: <b>{users}</b>\n💳 Deposits: <b>{deposits}</b>\n📦 Orders: <b>{orders}</b>\n💰 Total saldo user: <b>{format_rupiah(balance)}</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(back))
    elif query.data == "admin_home": await query.edit_message_text("👑 <b>ADMIN PANEL</b>\n\nPilih menu:", parse_mode="HTML", reply_markup=admin_menu())

async def button_handler(update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    admin_callbacks = {"admin_users", "admin_deposits", "admin_orders", "admin_provider", "admin_stats", "admin_home"}
    if query.data in admin_callbacks:
        if not is_admin(user_id): await query.answer("❌ Kamu bukan admin.", show_alert=True); return
        await admin_callback(query); return
    if query.data == "user_home": context.chat_data["waiting_deposit"] = False
    result = await user_callback(query, user_id)
    if result == "WAIT_DEPOSIT":
        context.chat_data["waiting_deposit"] = True
        await query.edit_message_text("💳 <b>Deposit Saldo</b>\n\nMasukkan nominal deposit.\n\nMinimum: <b>Rp1.000</b>\nKelipatan: <b>Rp1.000</b>\n\nContoh:\n1000\n5000\n10000\n25000\nKetik nominal sekarang.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Batal", callback_data="user_home")]]))

async def text_handler(update, context):
    if not update.message: return
    if not context.chat_data.get("waiting_deposit"): return
    user = update.effective_user
    context.chat_data["waiting_deposit"] = False
    text = update.message.text.strip().replace(".", "").replace(",", "")
    if not text.isdigit(): await update.message.reply_text("❌ Nominal harus berupa angka.\n\nContoh: <code>10000</code>", parse_mode="HTML"); return
    amount = int(text)
    if amount < 1000: await update.message.reply_text("❌ <b>Deposit terlalu kecil.</b>\n\nMinimum deposit adalah <b>Rp1.000</b>.", parse_mode="HTML"); return
    if amount % 1000!= 0: await update.message.reply_text("❌ <b>Nominal tidak valid.</b>\n\nDeposit harus kelipatan <b>Rp1.000</b>.", parse_mode="HTML"); return
    create_user(user.id, user.username, user.first_name)
    deposit_id = "DEP-" + uuid.uuid4().hex[:12].upper()
    with get_db() as db: db.execute("INSERT INTO deposits (deposit_id, telegram_id, amount, status, created_at) VALUES (%s,%s,%s,%s,%s)", (deposit_id, user.id, amount, "PENDING", now()))
    try:
        snap_data = await asyncio.to_thread(create_midtrans_snap, amount, deposit_id)
        snap_url = snap_data.get("redirect_url")
        snap_token = snap_data.get("token")
        if not snap_url or not snap_token: raise RuntimeError("Respons Midtrans tidak berisi redirect_url/token.")
        with get_db() as db: db.execute("UPDATE deposits SET payment_reference = %s WHERE deposit_id = %s", (snap_token, deposit_id))
        keyboard = [[InlineKeyboardButton("💳 Bayar Sekarang", url=snap_url)], [InlineKeyboardButton("⬅️ Menu Utama", callback_data="user_home")]]
        await update.message.reply_text(f"💳 <b>Invoice Deposit Dibuat</b>\n\n🧾 ID: <code>{deposit_id}</code>\n💰 Nominal: <b>{format_rupiah(amount)}</b>\n📌 Status: <b>PENDING</b>\n\nKlik tombol di bawah untuk melakukan pembayaran.\n\nSetelah pembayaran berhasil, saldo akan otomatis masuk.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception:
        logger.exception("Gagal membuat invoice Midtrans.")
        with get_db() as db: db.execute("UPDATE deposits SET status = 'FAILED' WHERE deposit_id = %s AND status = 'PENDING'", (deposit_id,))
        await update.message.reply_text("❌ <b>Gagal membuat invoice pembayaran.</b>\n\nSilakan coba lagi beberapa saat kemudian.", parse_mode="HTML")

async def admin_add_balance(update, context):
    if not is_admin(update.effective_user.id): await update.message.reply_text("❌ Kamu bukan admin."); return
    if len(context.args)!= 2: await update.message.reply_text("Format:\n\n/addbalance TELEGRAM_ID NOMINAL\nContoh:\n/addbalance 123456789 10000"); return
    try: telegram_id = int(context.args[0]); amount = int(context.args[1])
    except ValueError: await update.message.reply_text("❌ Telegram ID dan nominal harus angka."); return
    if amount <= 0: await update.message.reply_text("❌ Nominal harus lebih dari 0."); return
    try: new_balance = add_balance(telegram_id=telegram_id, amount=amount, transaction_type="ADMIN_TOPUP", reference=("ADMIN-" + uuid.uuid4().hex[:8].upper()), description=("Saldo ditambahkan oleh admin"))
    except Exception as error: await update.message.reply_text(f"❌ Gagal:\n{error}"); return
    await update.message.reply_text(f"✅ <b>Saldo berhasil ditambahkan.</b>\n\n👤 User: <code>{telegram_id}</code>\n💰 Saldo baru: <b>{format_rupiah(new_balance)}</b>", parse_mode="HTML")

async def error_handler(update, context): logger.error("Exception while handling update:", exc_info=context.error)

def main():
    init_database()
    start_webhook_server()
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("addbalance", admin_add_balance))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    application.add_error_handler(error_handler)
    logger.info("Bot berhasil dijalankan.")
    logger.info("Webhook Midtrans aktif di /midtrans/webhook")
    
    port = int(os.environ.get('PORT', 8080))
    application.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path="telegram",
        webhook_url=f"https://otp-reseller-bot-production.up.railway.app/telegram"
    )

if __name__ == '__main__':
    main()
