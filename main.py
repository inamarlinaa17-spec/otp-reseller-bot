import base64
import json
import logging
import os
import uuid
import threading # TAMBAH INI
import hashlib # TAMBAH INI
import hmac # TAMBAH INI
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import asyncio
import pytz # TAMBAH INI

from flask import Flask, request, jsonify # TAMBAH INI
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters,
)

from config import (
    BOT_TOKEN, ADMIN_ID, MIDTRANS_SERVER_KEY, MIDTRANS_CLIENT_KEY,
    MIDTRANS_API_URL, MIDTRANS_SNAP_URL
)
from database import init_database, create_user, get_balance, add_balance, get_db, now, get_total_users, get_deposit_history, get_order_history # UBAH INI TAMBAH 3
import midtransclient

if not MIDTRANS_SERVER_KEY:
    raise RuntimeError("MIDTRANS_SERVER_KEY belum diatur di Railway.")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# TAMBAH INI UNTUK FLASK
app = Flask(__name__)

# Init Midtrans Snap
snap = midtransclient.Snap(
    is_production=os.getenv("MIDTRANS_IS_PRODUCTION", "false").lower() == "true",
    server_key=MIDTRANS_SERVER_KEY
)

# =========================================================
# HELPER
# =========================================================

def is_admin(user_id):
    return user_id == ADMIN_ID

def format_rupiah(amount):
    return f"Rp{amount:,}".replace(",", ".")

def get_wib_time(): # TAMBAH INI
    wib = pytz.timezone('Asia/Jakarta')
    return datetime.now(wib).strftime("%d %B %Y pukul %H:%M:%S WIB")

# =========================================================
# USER MENU - UDAH GUE UBAH JADI KAYA MOCHI
# =========================================================

def user_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 Cara Penggunaan", callback_data="cara")],
        [
            InlineKeyboardButton("📱 Order OTP", callback_data="order"),
            InlineKeyboardButton("💳 Deposit", callback_data="user_deposit")
        ],
        [
            InlineKeyboardButton("📋 Histori Order", callback_data="user_history_order"),
            InlineKeyboardButton("📜 Histori Deposit", callback_data="user_history_depo")
        ],
        [
            InlineKeyboardButton("👥 Referral", callback_data="referral"),
            InlineKeyboardButton("💬 Contact CS", callback_data="cs")
        ]
    ])

# =========================================================
# ADMIN MENU
# =========================================================

def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Users", callback_data="admin_users"), InlineKeyboardButton("💳 Deposit", callback_data="admin_deposits")],
        [InlineKeyboardButton("📦 Orders", callback_data="admin_orders"), InlineKeyboardButton("💰 Provider", callback_data="admin_provider")],
        [InlineKeyboardButton("📊 Statistik", callback_data="admin_stats")],
    ])

# =========================================================
# MIDTRANS CREATE SNAP TOKEN
# =========================================================

def create_midtrans_snap(amount, deposit_id):
    param = {
        "transaction_details": {"order_id": deposit_id, "gross_amount": amount},
        "item_details": [{"id": "DEPOSIT", "price": amount, "quantity": 1, "name": "Deposit Saldo Bot"}],
        "customer_details": {"first_name": f"User {deposit_id}"},
        "expiry": {"start_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S +07:00"), "unit": "hours", "duration": 24}
    }
    try:
        transaction = snap.create_transaction(param)
        return transaction
    except Exception as error:
        logger.error("Midtrans Error: %s", error)
        raise RuntimeError("Gagal membuat Snap Token Midtrans.") from error

# ====== TAMBAH INI 1: FUNGSI CEK STATUS ======
def cek_status_midtrans(order_id):
    url = f"{MIDTRANS_API_URL}/{order_id}/status"
    req = Request(url, headers={
        "Authorization": "Basic " + base64.b64encode(f"{MIDTRANS_SERVER_KEY}:".encode()).decode()
    }, method="GET")
    try:
        with urlopen(req, timeout=20) as response:
            data = json.loads(response.read().decode())
            return data
    except HTTPError as e:
        logger.error("Cek status gagal: %s", e.read().decode())
        return None
# =============================================

# =========================================================
# TELEGRAM NOTIFICATION
# =========================================================

def send_telegram_message(chat_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    request = Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=20) as response:
            response.read()
    except Exception as error:
        logger.error("Notifikasi Telegram gagal: %s", error)

# =========================================================
# COMPLETE DEPOSIT
# =========================================================

def complete_deposit_payment(deposit_id, payment_reference, paid_amount):
    with get_db() as db:
        deposit = db.execute("SELECT deposit_id, telegram_id, amount, status FROM deposits WHERE deposit_id = %s FOR UPDATE", (deposit_id,)).fetchone()

        if not deposit:
            raise ValueError("Deposit tidak ditemukan.")

        if deposit["status"] == "SUCCESS":
            return {"completed": False, "already_completed": True}

        if deposit["status"] != "PENDING":
            raise ValueError(f"Deposit berstatus {deposit['status']}, bukan PENDING.")

        if int(float(paid_amount)) != int(deposit["amount"]):
            raise ValueError("Nominal pembayaran Midtrans tidak sama dengan nominal deposit.")

        user = db.execute(
            "SELECT balance FROM users WHERE telegram_id = %s FOR UPDATE",
            (deposit["telegram_id"],)
        ).fetchone()

        if not user:
            raise ValueError("User deposit tidak ditemukan.")

        before = user["balance"]
        after = before + deposit["amount"]

        db.execute(
            "UPDATE users SET balance = %s WHERE telegram_id = %s",
            (after, deposit["telegram_id"])
        )

        db.execute(
            """INSERT INTO ledger
            (telegram_id, amount, balance_before, balance_after,
             transaction_type, reference, description, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                deposit["telegram_id"],
                deposit["amount"],
                before,
                after,
                "DEPOSIT",
                payment_reference or deposit_id,
                f"Deposit Midtrans {deposit_id}",
                now()
            )
        )

        db.execute(
            """UPDATE deposits
            SET status = 'SUCCESS',
                payment_reference = COALESCE(%s, payment_reference),
                completed_at = %s
            WHERE deposit_id = %s""",
            (payment_reference, now(), deposit_id)
        )

        return {
            "completed": True,
            "already_completed": False,
            "telegram_id": deposit["telegram_id"],
            "amount": deposit["amount"],
            "new_balance": after
        }

# =========================================================
# MIDTRANS WEBHOOK
# =========================================================

@app.route('/midtrans/webhook', methods=['POST'])
def midtrans_webhook():
    try:
        data = request.get_json(silent=True)

        if not data:
            logger.error("Webhook Midtrans menerima body kosong.")
            return jsonify({
                "status": "error",
                "message": "Invalid JSON"
            }), 400

        order_id = data.get('order_id')
        status = data.get('transaction_status')
        fraud = data.get('fraud_status')
        gross_amount = str(data.get('gross_amount', ''))
        status_code = str(data.get('status_code', ''))
        signature_key = data.get('signature_key')
        transaction_id = data.get('transaction_id')
        payment_type = data.get('payment_type')

        logger.info(
            "Webhook Midtrans masuk | order_id=%s | status=%s | payment_type=%s | fraud=%s | amount=%s",
            order_id,
            status,
            payment_type,
            fraud,
            gross_amount
        )

        # -------------------------------------------------
        # VALIDASI DATA DASAR
        # -------------------------------------------------

        if not order_id:
            logger.error("Webhook tidak memiliki order_id.")
            return jsonify({
                "status": "error",
                "message": "Missing order_id"
            }), 400

        if not status_code:
            logger.error("Webhook %s tidak memiliki status_code.", order_id)
            return jsonify({
                "status": "error",
                "message": "Missing status_code"
            }), 400

        if not gross_amount:
            logger.error("Webhook %s tidak memiliki gross_amount.", order_id)
            return jsonify({
                "status": "error",
                "message": "Missing gross_amount"
            }), 400

        if not signature_key:
            logger.error("Webhook %s tidak memiliki signature_key.", order_id)
            return jsonify({
                "status": "error",
                "message": "Missing signature_key"
            }), 403

        # -------------------------------------------------
        # VERIFIKASI SIGNATURE MIDTRANS
        #
        # SHA512(order_id + status_code + gross_amount + ServerKey)
        # -------------------------------------------------

        signature_string = (
            str(order_id)
            + str(status_code)
            + str(gross_amount)
            + str(MIDTRANS_SERVER_KEY)
        )

        expected_signature = hashlib.sha512(
            signature_string.encode("utf-8")
        ).hexdigest()

        if not hmac.compare_digest(
            expected_signature.lower(),
            str(signature_key).lower()
        ):
            logger.error(
                "Signature Midtrans tidak valid untuk order %s.",
                order_id
            )

            return jsonify({
                "status": "error",
                "message": "Invalid signature"
            }), 403

        logger.info(
            "Signature Midtrans valid untuk order %s.",
            order_id
        )

        # -------------------------------------------------
        # CEK STATUS PEMBAYARAN
        # -------------------------------------------------
        #
        # settlement = sukses
        # capture = sukses untuk kartu
        #
        # fraud_status tidak selalu tersedia pada semua
        # metode pembayaran. Kalau ada, harus accept.
        # -------------------------------------------------

        is_success = False

        if status == "settlement":
            is_success = True

        elif status == "capture" and payment_type == "credit_card":
            is_success = True

        # Kalau fraud_status ada, harus accept.
        if is_success and fraud is not None:
            if str(fraud).lower() != "accept":
                logger.warning(
                    "Pembayaran %s ditolak karena fraud_status=%s",
                    order_id,
                    fraud
                )

                return jsonify({
                    "status": "ok",
                    "message": "Fraud status not accepted"
                }), 200

        # status_code untuk transaksi sukses harus 200
        if is_success and status_code != "200":
            logger.warning(
                "Pembayaran %s memiliki status sukses tetapi status_code=%s",
                order_id,
                status_code
            )

            return jsonify({
                "status": "ok",
                "message": "Invalid success status code"
            }), 200

        # -------------------------------------------------
        # PROSES PEMBAYARAN
        # -------------------------------------------------

        if is_success:
            try:
                result = complete_deposit_payment(
                    order_id,
                    transaction_id,
                    gross_amount
                )

                if result["completed"]:
                    send_telegram_message(
                        result["telegram_id"],
                        f"✅ <b>Deposit berhasil!</b>\n\n"
                        f"💰 Deposit: <b>{format_rupiah(result['amount'])}</b>\n"
                        f"💳 Status: <b>PAID</b>\n"
                        f"🧾 ID: <code>{order_id}</code>\n\n"
                        f"💰 Saldo sekarang: "
                        f"<b>{format_rupiah(result['new_balance'])}</b>"
                    )

                    logger.info(
                        "SALDO BERHASIL MASUK | user=%s | amount=%s | order=%s",
                        result["telegram_id"],
                        result["amount"],
                        order_id
                    )

                elif result["already_completed"]:
                    logger.info(
                        "Webhook duplikat diabaikan | order=%s",
                        order_id
                    )

            except Exception as e:
                logger.exception(
                    "Gagal proses pembayaran order %s: %s",
                    order_id,
                    e
                )

                # Return 500 supaya Midtrans dapat mencoba
                # mengirim notification kembali.
                return jsonify({
                    "status": "error",
                    "message": "Failed to process payment"
                }), 500

        elif status in ["expire", "cancel"]:
            logger.info(
                "Transaksi %s berstatus %s.",
                order_id,
                status
            )

            # Jangan mengubah SUCCESS menjadi EXPIRED/CANCEL.
            try:
                with get_db() as db:
                    db.execute(
                        """UPDATE deposits
                        SET status = 'EXPIRED'
                        WHERE deposit_id = %s
                        AND status = 'PENDING'""",
                        (order_id,)
                    )
            except Exception as e:
                logger.error(
                    "Gagal update expired deposit %s: %s",
                    order_id,
                    e
                )

        else:
            logger.info(
                "Webhook %s diterima tetapi belum sukses. status=%s",
                order_id,
                status
            )

        return jsonify({
            "status": "ok"
        }), 200

    except Exception as e:
        logger.exception(
            "Error tidak terduga pada webhook Midtrans: %s",
            e
        )

        return jsonify({
            "status": "error",
            "message": "Internal server error"
        }), 500

# =========================================================
# HEALTH CHECK RAILWAY
# =========================================================

@app.route('/', methods=['GET'])
def health_check():
    return jsonify({
        "status": "online",
        "service": "otp-reseller-bot"
    }), 200

# =========================================================
# TELEGRAM HANDLERS - UDAH GUE UBAH TOTAL
# =========================================================

async def user_start(update_or_query): # FIX FINAL: PAKE from_user BUAT CALLBACK
    if hasattr(update_or_query, 'message'): # kalau dari /start
        user = update_or_query.effective_user
        send = update_or_query.message.reply_text
    else: # kalau dari tombol Kembali
        user = update_or_query.from_user # INI YG DIBENERIN
        send = update_or_query.edit_message_text

    create_user(user.id, user.username, user.first_name)
    saldo = get_balance(user.id)
    total_user = get_total_users()
    waktu = get_wib_time()

    text = f"""
👋 <b>{user.first_name.upper()}</b>
{waktu}

<b>User Info :</b>
├ ID : <code>{user.id}</code>
├ Username : @{user.username or '-'}

<b>Balance Info :</b>
├ Balance : <b>{format_rupiah(saldo)}</b>

<b>Bot Stats :</b>
├ Total User : {total_user}

<b>Info Promo :</b>
├ Channel : @ChannelLu

<b>Shortcut :</b>
├ /start - Mulai Bot
"""
    await send(text, parse_mode="HTML", reply_markup=user_menu())

async def admin_start(update):
    await update.message.reply_text("👑 <b>ADMIN PANEL</b>\n\nSelamat datang, Admin.\n\nPilih menu:", parse_mode="HTML", reply_markup=admin_menu())

async def start(update, context):
    user = update.effective_user
    create_user(user.id, user.username, user.first_name)
    if is_admin(user.id): await admin_start(update)
    else: await user_start(update)

async def user_callback(query, user_id, context): # FIX: TAMBAH context
    if query.data == "cara":
        text = f"""📁 <b>PANDUAN PENGGUNAAN BOT</b>

1️⃣ <b>Deposit</b>
Isi saldo terlebih dahulu melalui menu <b>Deposit</b>.

2️⃣ <b>Order Nomor</b>
Pilih layanan yang ingin digunakan:
- <b>Order OTP</b> → untuk 1 aplikasi saja.
- <b>Multiservice</b> → 1 nomor dapat digunakan untuk beberapa aplikasi sekaligus.

3️⃣ <b>Gunakan Nomor</b>
Setelah order berhasil, saldo akan otomatis terpotong dan nomor akan diberikan oleh bot.

4️⃣ <b>Menunggu SMS</b>
Masukkan nomor tersebut ke aplikasi tujuan dan tunggu kode OTP masuk ke bot.

5️⃣ <b>Refund</b>
Jika kode OTP tidak masuk, tekan tombol <b>Batal / Refund</b>.
Saldo akan dikembalikan secara otomatis.

⚠️ <b>Catatan:</b>
Gunakan nomor segera setelah order untuk meningkatkan kemungkinan OTP masuk."""
        keyboard = [[InlineKeyboardButton("🗑️ Kembali", callback_data="user_home")]]
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == "order":
        await query.edit_message_text("📱 <b>Order OTP</b>\n\nFitur masih tahap development bos", parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data="user_home")]]))

    elif query.data == "user_deposit":
        context.chat_data["waiting_deposit"] = True # INI BIKIN DEPOSIT JALAN
        await query.edit_message_text("💳 <b>Deposit Saldo</b>\n\nMasukkan nominal deposit.\n\nMinimum: <b>Rp1.000</b>\nKelipatan: <b>Rp1.000</b>\n\nContoh:\n1000\n5000\n10000\n25000\nKetik nominal sekarang.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Batal", callback_data="user_home")]]))

    elif query.data == "user_history_order":
        orders = get_order_history(user_id)
        if not orders: text = "📋 <b>Histori Order</b>\n\nBelum ada histori order."
        else: text = "📋 <b>5 Histori Order Terakhir</b>\n\n" + "\n".join([f"├ {o['order_id']} - {o['status']}" for o in orders])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data="user_home")]]))

    elif query.data == "user_history_depo":
        deposits = get_deposit_history(user_id)
        if not deposits: text = "📜 <b>Histori Deposit</b>\n\nBelum ada histori deposit."
        else: text = "📜 <b>5 Histori Deposit Terakhir</b>\n\n" + "\n".join([f"├ {d['deposit_id']} - {format_rupiah(d['amount'])} - {d['status']}" for d in deposits])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data="user_home")]]))

    elif query.data == "referral":
        ref_link = f"https://t.me/{query.from_user.username}?start=ref{user_id}"
        await query.edit_message_text(f"👥 <b>Referral</b>\n\nLink kamu:\n<code>{ref_link}</code>\n\nDapet 10% dari deposit teman", parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data="user_home")]]))

    elif query.data == "cs":
        await query.edit_message_text("💬 <b>Contact CS</b>\n\nHubungi: @AdminLu", parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data="user_home")]]))

    # ====== TAMBAH INI 2: HANDLER CEK DEPOSIT ======
    elif query.data == "cek_deposit":
        with get_db() as db:
            deposits = db.execute("SELECT deposit_id, amount FROM deposits WHERE telegram_id = %s AND status = 'PENDING' ORDER BY created_at DESC LIMIT 1", (user_id,)).fetchone()

        if not deposits:
            await query.edit_message_text("❌ Kamu tidak punya deposit pending.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data="user_home")]]))
            return

        await query.edit_message_text("⏳ Mengecek pembayaran ke Midtrans...", parse_mode="HTML")

        status_data = await asyncio.to_thread(
            cek_status_midtrans,
            deposits["deposit_id"]
        )

        if not status_data:
            await query.edit_message_text("❌ Gagal cek ke Midtrans. Coba lagi 5 detik.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Cek Lagi", callback_data="cek_deposit")]]))
            return

        if status_data["transaction_status"] == "settlement":
            result = complete_deposit_payment(
                deposits["deposit_id"],
                status_data["transaction_id"],
                status_data["gross_amount"]
            )

            if result["completed"]:
                await query.edit_message_text(
                    f"✅ <b>Deposit Berhasil!</b>\n\n"
                    f"💰 Masuk: <b>{format_rupiah(result['amount'])}</b>\n"
                    f"💳 Saldo sekarang: <b>{format_rupiah(result['new_balance'])}</b>",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🏠 Menu Utama", callback_data="user_home")]
                    ])
                )

                send_telegram_message(
                    result["telegram_id"],
                    f"✅ <b>Deposit berhasil!</b>\n\n"
                    f"💰 Deposit: <b>{format_rupiah(result['amount'])}</b>\n"
                    f"💳 Status: <b>PAID</b>\n"
                    f"🧾 ID: <code>{deposits['deposit_id']}</code>\n\n"
                    f"💰 Saldo sekarang: "
                    f"<b>{format_rupiah(result['new_balance'])}</b>"
                )

            elif result["already_completed"]:
                await query.edit_message_text(
                    f"✅ <b>Deposit sudah berhasil diproses.</b>\n\n"
                    f"💰 Saldo sekarang: "
                    f"<b>{format_rupiah(get_balance(user_id))}</b>",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🏠 Menu Utama", callback_data="user_home")]
                    ])
                )

        elif status_data["transaction_status"] in ["expire", "cancel"]:
            with get_db() as db:
                db.execute(
                    "UPDATE deposits SET status = 'EXPIRED' WHERE deposit_id = %s",
                    (deposits["deposit_id"],)
                )

            await query.edit_message_text(
                "❌ <b>Deposit Expired</b>\n\nSilakan buat invoice baru.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Deposit Lagi", callback_data="user_deposit")]
                ])
            )

        else:
            await query.edit_message_text(
                f"⏳ <b>Status: {status_data['transaction_status'].upper()}</b>\n\n"
                f"Belum dibayar. Klik cek lagi setelah bayar.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Cek Lagi", callback_data="cek_deposit")],
                    [InlineKeyboardButton("⬅️ Kembali", callback_data="user_home")]
                ])
            )
    # ===============================================

    elif query.data == "user_home":
        await user_start(query) # reset udah dipindah ke button_handler

async def admin_callback(query):
    back = [[InlineKeyboardButton("⬅️ Admin Panel", callback_data="admin_home")]]

    if query.data == "admin_users":
        with get_db() as db:
            total = db.execute(
                "SELECT COUNT(*) AS total FROM users"
            ).fetchone()["total"]

        await query.edit_message_text(
            f"👥 <b>USERS</b>\n\nTotal user: <b>{total}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(back)
        )

    elif query.data == "admin_deposits":
        with get_db() as db:
            total = db.execute(
                "SELECT COUNT(*) AS total FROM deposits"
            ).fetchone()["total"]

            pending = db.execute(
                "SELECT COUNT(*) AS total FROM deposits WHERE status = 'PENDING'"
            ).fetchone()["total"]

            success = db.execute(
                "SELECT COUNT(*) AS total FROM deposits WHERE status = 'SUCCESS'"
            ).fetchone()["total"]

        await query.edit_message_text(
            f"💳 <b>DEPOSIT</b>\n\n"
            f"Total transaksi: <b>{total}</b>\n"
            f"Pending: <b>{pending}</b>\n"
            f"Success: <b>{success}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(back)
        )

    elif query.data == "admin_orders":
        with get_db() as db:
            total = db.execute(
                "SELECT COUNT(*) AS total FROM orders"
            ).fetchone()["total"]

            pending = db.execute(
                "SELECT COUNT(*) AS total FROM orders WHERE status = 'PENDING'"
            ).fetchone()["total"]

            success = db.execute(
                "SELECT COUNT(*) AS total FROM orders WHERE status = 'SUCCESS'"
            ).fetchone()["total"]

        await query.edit_message_text(
            f"📦 <b>ORDERS</b>\n\n"
            f"Total order: <b>{total}</b>\n"
            f"Pending: <b>{pending}</b>\n"
            f"Success: <b>{success}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(back)
        )

    elif query.data == "admin_provider":
        await query.edit_message_text(
            "💰 <b>PROVIDER</b>\n\nProvider API belum terhubung.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(back)
        )

    elif query.data == "admin_stats":
        with get_db() as db:
            users = db.execute(
                "SELECT COUNT(*) AS total FROM users"
            ).fetchone()["total"]

            deposits = db.execute(
                "SELECT COUNT(*) AS total FROM deposits"
            ).fetchone()["total"]

            orders = db.execute(
                "SELECT COUNT(*) AS total FROM orders"
            ).fetchone()["total"]

            balance = db.execute(
                "SELECT COALESCE(SUM(balance), 0) AS total FROM users"
            ).fetchone()["total"]

        await query.edit_message_text(
            f"📊 <b>STATISTIK</b>\n\n"
            f"👥 Users: <b>{users}</b>\n"
            f"💳 Deposits: <b>{deposits}</b>\n"
            f"📦 Orders: <b>{orders}</b>\n"
            f"💰 Total saldo user: <b>{format_rupiah(balance)}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(back)
        )

    elif query.data == "admin_home":
        await query.edit_message_text(
            "👑 <b>ADMIN PANEL</b>\n\nPilih menu:",
            parse_mode="HTML",
            reply_markup=admin_menu()
        )

async def button_handler(update, context): # FIX FINAL: RESET DISINI
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    admin_callbacks = {
        "admin_users",
        "admin_deposits",
        "admin_orders",
        "admin_provider",
        "admin_stats",
        "admin_home"
    }

    if query.data in admin_callbacks:
        if not is_admin(user_id):
            await query.answer(
                "❌ Kamu bukan admin.",
                show_alert=True
            )
            return

        await admin_callback(query)
        return

    if query.data == "user_home": # INI YG BIKIN BATAL/KEMBALI JALAN
        context.chat_data["waiting_deposit"] = False

    await user_callback(query, user_id, context)

async def text_handler(update, context):
    if not update.message:
        return

    if not context.chat_data.get("waiting_deposit"):
        return

    user = update.effective_user

    context.chat_data["waiting_deposit"] = False

    text = update.message.text.strip().replace(".", "").replace(",", "")

    if not text.isdigit():
        await update.message.reply_text(
            "❌ Nominal harus berupa angka.\n\n"
            "Contoh: <code>10000</code>",
            parse_mode="HTML"
        )
        return

    amount = int(text)

    if amount < 1000:
        await update.message.reply_text(
            "❌ <b>Deposit terlalu kecil.</b>\n\n"
            "Minimum deposit adalah <b>Rp1.000</b>.",
            parse_mode="HTML"
        )
        return

    if amount % 1000 != 0:
        await update.message.reply_text(
            "❌ <b>Nominal tidak valid.</b>\n\n"
            "Deposit harus kelipatan <b>Rp1.000</b>.",
            parse_mode="HTML"
        )
        return

    create_user(
        user.id,
        user.username,
        user.first_name
    )

    deposit_id = "DEP-" + uuid.uuid4().hex[:12].upper()

    with get_db() as db:
        db.execute(
            """INSERT INTO deposits
            (deposit_id, telegram_id, amount, status, created_at)
            VALUES (%s,%s,%s,%s,%s)""",
            (
                deposit_id,
                user.id,
                amount,
                "PENDING",
                now()
            )
        )

    try:
        snap_data = await asyncio.to_thread(
            create_midtrans_snap,
            amount,
            deposit_id
        )

        snap_url = snap_data.get("redirect_url")
        snap_token = snap_data.get("token")

        if not snap_url or not snap_token:
            raise RuntimeError(
                "Respons Midtrans tidak berisi redirect_url/token."
            )

        with get_db() as db:
            db.execute(
                "UPDATE deposits SET payment_reference = %s WHERE deposit_id = %s",
                (snap_token, deposit_id)
            )

        # ====== UBAH INI 3: TAMBAH TOMBOL CEK ======
        keyboard = [
            [
                InlineKeyboardButton(
                    "💳 Bayar Sekarang",
                    url=snap_url
                )
            ],
            [
                InlineKeyboardButton(
                    "✅ Cek Pembayaran",
                    callback_data="cek_deposit"
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Menu Utama",
                    callback_data="user_home"
                )
            ]
        ]
        # ===========================================

        await update.message.reply_text(
            f"💳 <b>Invoice Deposit Dibuat</b>\n\n"
            f"🧾 ID: <code>{deposit_id}</code>\n"
            f"💰 Nominal: <b>{format_rupiah(amount)}</b>\n"
            f"📌 Status: <b>PENDING</b>\n\n"
            f"Klik tombol di bawah untuk melakukan pembayaran.\n\n"
            f"Setelah bayar, klik 'Cek Pembayaran' untuk konfirmasi.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception:
        logger.exception(
            "Gagal membuat invoice Midtrans."
        )

        with get_db() as db:
            db.execute(
                """UPDATE deposits
                SET status = 'FAILED'
                WHERE deposit_id = %s
                AND status = 'PENDING'""",
                (deposit_id,)
            )

        await update.message.reply_text(
            "❌ <b>Gagal membuat invoice pembayaran.</b>\n\n"
            "Silakan coba lagi beberapa saat kemudian.",
            parse_mode="HTML"
        )

async def admin_add_balance(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "❌ Kamu bukan admin."
        )
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "Format:\n\n"
            "/addbalance TELEGRAM_ID NOMINAL\n"
            "Contoh:\n"
            "/addbalance 123456789 10000"
        )
        return

    try:
        telegram_id = int(context.args[0])
        amount = int(context.args[1])
    except ValueError:
        await update.message.reply_text(
            "❌ Telegram ID dan nominal harus angka."
        )
        return

    if amount <= 0:
        await update.message.reply_text(
            "❌ Nominal harus lebih dari 0."
        )
        return

    try:
        new_balance = add_balance(
            telegram_id=telegram_id,
            amount=amount,
            transaction_type="ADMIN_TOPUP",
            reference=(
                "ADMIN-" +
                uuid.uuid4().hex[:8].upper()
            ),
            description=(
                "Saldo ditambahkan oleh admin"
            )
        )

    except Exception as error:
        await update.message.reply_text(
            f"❌ Gagal:\n{error}"
        )
        return

    await update.message.reply_text(
        f"✅ <b>Saldo berhasil ditambahkan.</b>\n\n"
        f"👤 User: <code>{telegram_id}</code>\n"
        f"💰 Saldo baru: <b>{format_rupiah(new_balance)}</b>",
        parse_mode="HTML"
    )

async def error_handler(update, context):
    logger.error(
        "Exception while handling update:",
        exc_info=context.error
    )

# =========================================================
# FLASK
# =========================================================

def run_flask(): # TAMBAH INI
    port = int(os.getenv("PORT", "5000"))
    logger.info("Flask webhook berjalan di port %s", port)
    app.run(
        host='0.0.0.0',
        port=port
    )

def run():
    init_database()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("addbalance", admin_add_balance)
    )

    application.add_handler(
        CallbackQueryHandler(button_handler)
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler
        )
    )

    application.add_error_handler(
        error_handler
    )

    logger.info("Bot berhasil dijalankan.")

    # JALANIN FLASK + BOT BARENG
    threading.Thread(
        target=run_flask,
        daemon=True
    ).start()

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )

if __name__ == "__main__":
    run()
