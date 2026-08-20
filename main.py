import base64
import json
import logging
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters,
)

from config import BOT_TOKEN, ADMIN_ID
from database import init_database, create_user, get_balance, add_balance, get_db, now


XENDIT_SECRET_KEY = os.getenv("XENDIT_SECRET_KEY", "").strip()
XENDIT_WEBHOOK_TOKEN = os.getenv("XENDIT_WEBHOOK_TOKEN", "").strip()
PORT = int(os.getenv("PORT", "8080"))

if not XENDIT_SECRET_KEY:
    raise RuntimeError("XENDIT_SECRET_KEY belum diatur di Railway.")

if not XENDIT_WEBHOOK_TOKEN:
    raise RuntimeError("XENDIT_WEBHOOK_TOKEN belum diatur di Railway.")


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

XENDIT_API_URL = "https://" + "api.xendit.co/v2/invoices"


# =========================================================
# HELPER
# =========================================================

def is_admin(user_id):
    return user_id == ADMIN_ID


def format_rupiah(amount):
    return f"Rp{amount:,}".replace(",", ".")


# =========================================================
# USER MENU
# =========================================================

def user_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "💰 Saldo",
                callback_data="user_balance"
            ),
            InlineKeyboardButton(
                "💳 Deposit",
                callback_data="user_deposit"
            ),
        ],
        [
            InlineKeyboardButton(
                "📱 Layanan",
                callback_data="user_services"
            ),
            InlineKeyboardButton(
                "📜 Riwayat",
                callback_data="user_history"
            ),
        ],
    ])


# =========================================================
# ADMIN MENU
# =========================================================

def admin_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "👥 Users",
                callback_data="admin_users"
            ),
            InlineKeyboardButton(
                "💳 Deposit",
                callback_data="admin_deposits"
            ),
        ],
        [
            InlineKeyboardButton(
                "📦 Orders",
                callback_data="admin_orders"
            ),
            InlineKeyboardButton(
                "💰 Provider",
                callback_data="admin_provider"
            ),
        ],
        [
            InlineKeyboardButton(
                "📊 Statistik",
                callback_data="admin_stats"
            )
        ],
    ])


# =========================================================
# XENDIT CREATE INVOICE
# =========================================================

def create_xendit_invoice(amount, deposit_id):

    auth = base64.b64encode(
        f"{XENDIT_SECRET_KEY}:".encode()
    ).decode()

    payload = {
        "external_id": deposit_id,
        "amount": amount,
        "description": f"Deposit saldo {deposit_id}",
        "invoice_duration": 86400,
        "currency": "IDR",

        "items": [
            {
                "name": "Deposit Saldo",
                "quantity": 1,
                "price": amount,
                "category": "Digital Service",
            }
        ],

        "metadata": {
            "deposit_id": deposit_id,
        },
    }

    request = Request(
        XENDIT_API_URL,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:

        with urlopen(request, timeout=30) as response:

            return json.loads(
                response.read().decode()
            )

    except HTTPError as error:

        body = error.read().decode(
            errors="replace"
        )

        logger.error(
            "Xendit HTTP %s: %s",
            error.code,
            body
        )

        raise RuntimeError(
            f"Xendit menolak invoice (HTTP {error.code})."
        ) from error

    except (URLError, TimeoutError) as error:

        logger.error(
            "Xendit connection error: %s",
            error
        )

        raise RuntimeError(
            "Tidak bisa terhubung ke Xendit."
        ) from error


# =========================================================
# TELEGRAM NOTIFICATION
# =========================================================

def send_telegram_message(chat_id, text):

    url = (
        "https://"
        + "api.telegram.org/bot"
        + BOT_TOKEN
        + "/sendMessage"
    )

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }

    request = Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json"
        },
        method="POST",
    )

    try:

        with urlopen(request, timeout=20) as response:
            response.read()

    except Exception as error:

        logger.error(
            "Notifikasi Telegram gagal: %s",
            error
        )


# =========================================================
# COMPLETE DEPOSIT
# =========================================================

def complete_deposit_payment(
    deposit_id,
    payment_reference,
    paid_amount
):

    with get_db() as db:

        deposit = db.execute(
            """
            SELECT
                deposit_id,
                telegram_id,
                amount,
                status
            FROM deposits
            WHERE deposit_id = %s
            FOR UPDATE
            """,
            (deposit_id,),
        ).fetchone()

        if not deposit:

            raise ValueError(
                "Deposit tidak ditemukan."
            )

        # Mencegah saldo masuk dua kali
        if deposit["status"] == "SUCCESS":

            return {
                "completed": False,
                "already_completed": True,
            }

        if deposit["status"] != "PENDING":

            raise ValueError(
                f"Deposit berstatus "
                f"{deposit['status']}, bukan PENDING."
            )

        # Pastikan nominal webhook sama
        # dengan nominal deposit
        if int(paid_amount) != int(
            deposit["amount"]
        ):

            raise ValueError(
                "Nominal pembayaran Xendit "
                "tidak sama dengan nominal deposit."
            )

        user = db.execute(
            """
            SELECT balance
            FROM users
            WHERE telegram_id = %s
            FOR UPDATE
            """,
            (deposit["telegram_id"],),
        ).fetchone()

        if not user:

            raise ValueError(
                "User deposit tidak ditemukan."
            )

        before = user["balance"]

        after = (
            before
            + deposit["amount"]
        )

        # Update saldo
        db.execute(
            """
            UPDATE users
            SET balance = %s
            WHERE telegram_id = %s
            """,
            (
                after,
                deposit["telegram_id"],
            ),
        )

        # Catat ledger
        db.execute(
            """
            INSERT INTO ledger
            (
                telegram_id,
                amount,
                balance_before,
                balance_after,
                transaction_type,
                reference,
                description,
                created_at
            )
            VALUES
            (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s
            )
            """,
            (
                deposit["telegram_id"],
                deposit["amount"],
                before,
                after,
                "DEPOSIT",
                payment_reference
                or deposit_id,
                f"Deposit Xendit {deposit_id}",
                now(),
            ),
        )

        # Tandai deposit sukses
        db.execute(
            """
            UPDATE deposits
            SET
                status = 'SUCCESS',
                payment_reference =
                    COALESCE(
                        %s,
                        payment_reference
                    ),
                completed_at = %s
            WHERE deposit_id = %s
            """,
            (
                payment_reference,
                now(),
                deposit_id,
            ),
        )

        return {
            "completed": True,
            "already_completed": False,
            "telegram_id": deposit["telegram_id"],
            "amount": deposit["amount"],
            "new_balance": after,
        }


# =========================================================
# PROCESS XENDIT WEBHOOK
# =========================================================

def process_xendit_webhook(payload):

    status = str(
        payload.get("status", "")
    ).upper()

    deposit_id = payload.get(
        "external_id"
    )

    if not deposit_id:

        raise ValueError(
            "Webhook tidak memiliki external_id."
        )

    # -----------------------------------------------------
    # PAID
    # -----------------------------------------------------

    if status == "PAID":

        paid_amount = payload.get(
            "paid_amount",
            payload.get("amount")
        )

        try:

            paid_amount = int(
                paid_amount
            )

        except (
            TypeError,
            ValueError
        ):

            raise ValueError(
                "Nominal PAID dari Xendit tidak valid."
            )

        result = complete_deposit_payment(
            deposit_id,
            payload.get("id")
            or payload.get("payment_id"),
            paid_amount,
        )

        if result["completed"]:

            send_telegram_message(

                result["telegram_id"],

                "✅ <b>Deposit berhasil!</b>\n\n"

                f"💰 Deposit: "
                f"<b>{format_rupiah(result['amount'])}</b>\n"

                "💳 Status: <b>PAID</b>\n"

                f"🧾 ID: "
                f"<code>{deposit_id}</code>\n\n"

                f"💰 Saldo sekarang: "
                f"<b>{format_rupiah(result['new_balance'])}</b>",
            )

        else:

            logger.info(
                "Webhook duplicate diabaikan: %s",
                deposit_id
            )

    # -----------------------------------------------------
    # EXPIRED
    # -----------------------------------------------------

    elif status == "EXPIRED":

        with get_db() as db:

            db.execute(
                """
                UPDATE deposits
                SET status = 'EXPIRED'
                WHERE deposit_id = %s
                  AND status = 'PENDING'
                """,
                (deposit_id,),
            )

        logger.info(
            "Deposit expired: %s",
            deposit_id
        )

    else:

        logger.info(
            "Webhook Xendit diabaikan: "
            "deposit=%s status=%s",
            deposit_id,
            status,
        )


# =========================================================
# WEBHOOK SERVER
# =========================================================

class XenditWebhookHandler(
    BaseHTTPRequestHandler
):

    def log_message(
        self,
        fmt,
        *args
    ):

        logger.info(
            "Webhook HTTP: " + fmt,
            *args
        )

    def send_json(
        self,
        code,
        data
    ):

        body = json.dumps(
            data
        ).encode()

        self.send_response(code)

        self.send_header(
            "Content-Type",
            "application/json"
        )

        self.send_header(
            "Content-Length",
            str(len(body))
        )

        self.end_headers()

        self.wfile.write(body)

    def do_GET(self):

        if self.path == "/health":

            self.send_json(
                200,
                {"ok": True}
            )

        else:

            self.send_json(
                404,
                {
                    "ok": False,
                    "error": "Not found"
                }
            )

    def do_POST(self):

        if self.path != "/xendit/webhook":

            self.send_json(
                404,
                {
                    "ok": False,
                    "error": "Not found"
                }
            )

            return

        token = self.headers.get(
            "x-callback-token",
            ""
        )

        if token != XENDIT_WEBHOOK_TOKEN:

            logger.warning(
                "Webhook Xendit ditolak: "
                "token tidak cocok."
            )

            self.send_json(
                403,
                {
                    "ok": False,
                    "error": "Forbidden"
                }
            )

            return

        try:

            length = int(
                self.headers.get(
                    "Content-Length",
                    "0"
                )
            )

            raw_body = self.rfile.read(
                length
            )

            payload = json.loads(
                raw_body.decode("utf-8")
            )

            process_xendit_webhook(
                payload
            )

            self.send_json(
                200,
                {"ok": True}
            )

        except json.JSONDecodeError:

            self.send_json(
                400,
                {
                    "ok": False,
                    "error": "Invalid JSON"
                }
            )

        except Exception:

            logger.exception(
                "Gagal memproses webhook Xendit."
            )

            self.send_json(
                500,
                {
                    "ok": False,
                    "error": (
                        "Webhook processing failed"
                    )
                }
            )


def start_webhook_server():

    server = ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        XenditWebhookHandler,
    )

    thread = threading.Thread(
        target=server.serve_forever,
        daemon=True,
    )

    thread.start()

    logger.info(
        "Webhook server aktif di port %s",
        PORT
    )

    return server


# =========================================================
# USER START
# =========================================================

async def user_start(update):

    user = update.effective_user

    create_user(
        user.id,
        user.username,
        user.first_name
    )

    balance = get_balance(
        user.id
    )

    await update.message.reply_text(

        "👋 <b>Selamat datang!</b>\n\n"

        "Bot layanan digital kamu sudah aktif.\n\n"

        f"💰 Saldo: "
        f"<b>{format_rupiah(balance)}</b>\n\n"

        "Silakan pilih menu:",

        parse_mode="HTML",

        reply_markup=user_menu(),
    )


# =========================================================
# ADMIN START
# =========================================================

async def admin_start(update):

    await update.message.reply_text(

        "👑 <b>ADMIN PANEL</b>\n\n"

        "Selamat datang, Admin.\n\n"

        "Pilih menu:",

        parse_mode="HTML",

        reply_markup=admin_menu(),
    )


# =========================================================
# /START
# =========================================================

async def start(
    update,
    context
):

    user = update.effective_user

    create_user(
        user.id,
        user.username,
        user.first_name
    )

    if is_admin(user.id):

        await admin_start(update)

    else:

        await user_start(update)


# =========================================================
# USER CALLBACK
# =========================================================

async def user_callback(
    query,
    user_id
):

    if query.data == "user_balance":

        keyboard = [
            [
                InlineKeyboardButton(
                    "💳 Deposit",
                    callback_data="user_deposit"
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Kembali",
                    callback_data="user_home"
                )
            ],
        ]

        await query.edit_message_text(

            "💰 <b>Saldo Kamu</b>\n\n"

            f"Saldo: "
            f"<b>{format_rupiah(get_balance(user_id))}</b>",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
        )

    elif query.data == "user_deposit":

        return "WAIT_DEPOSIT"

    elif query.data == "user_services":

        await query.edit_message_text(

            "📱 <b>Layanan</b>\n\n"

            "Modul layanan belum diaktifkan.\n\n"

            "Nanti menu ini akan terhubung "
            "ke provider API.",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "⬅️ Kembali",
                        callback_data="user_home"
                    )
                ]]
            ),
        )

    elif query.data == "user_history":

        await query.edit_message_text(

            "📜 <b>Riwayat Transaksi</b>\n\n"

            "Riwayat deposit akan kita tampilkan "
            "di tahap berikutnya.",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "⬅️ Kembali",
                        callback_data="user_home"
                    )
                ]]
            ),
        )

    elif query.data == "user_home":

        await query.edit_message_text(

            "🏠 <b>Menu Utama</b>\n\n"

            f"💰 Saldo: "
            f"<b>{format_rupiah(get_balance(user_id))}</b>\n\n"

            "Pilih menu:",

            parse_mode="HTML",

            reply_markup=user_menu(),
        )


# =========================================================
# ADMIN CALLBACK
# =========================================================

async def admin_callback(query):

    back = [[
        InlineKeyboardButton(
            "⬅️ Admin Panel",
            callback_data="admin_home"
        )
    ]]

    if query.data == "admin_users":

        with get_db() as db:

            total = db.execute(
                """
                SELECT COUNT(*) AS total
                FROM users
                """
            ).fetchone()["total"]

        await query.edit_message_text(

            "👥 <b>USERS</b>\n\n"

            f"Total user: <b>{total}</b>",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup(
                back
            ),
        )

    elif query.data == "admin_deposits":

        with get_db() as db:

            total = db.execute(
                """
                SELECT COUNT(*) AS total
                FROM deposits
                """
            ).fetchone()["total"]

            pending = db.execute(
                """
                SELECT COUNT(*) AS total
                FROM deposits
                WHERE status = 'PENDING'
                """
            ).fetchone()["total"]

            success = db.execute(
                """
                SELECT COUNT(*) AS total
                FROM deposits
                WHERE status = 'SUCCESS'
                """
            ).fetchone()["total"]

        await query.edit_message_text(

            "💳 <b>DEPOSIT</b>\n\n"

            f"Total transaksi: <b>{total}</b>\n"

            f"Pending: <b>{pending}</b>\n"

            f"Success: <b>{success}</b>",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup(
                back
            ),
        )

    elif query.data == "admin_orders":

        with get_db() as db:

            total = db.execute(
                """
                SELECT COUNT(*) AS total
                FROM orders
                """
            ).fetchone()["total"]

            pending = db.execute(
                """
                SELECT COUNT(*) AS total
                FROM orders
                WHERE status = 'PENDING'
                """
            ).fetchone()["total"]

            success = db.execute(
                """
                SELECT COUNT(*) AS total
                FROM orders
                WHERE status = 'SUCCESS'
                """
            ).fetchone()["total"]

        await query.edit_message_text(

            "📦 <b>ORDERS</b>\n\n"

            f"Total order: <b>{total}</b>\n"

            f"Pending: <b>{pending}</b>\n"

            f"Success: <b>{success}</b>",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup(
                back
            ),
        )

    elif query.data == "admin_provider":

        await query.edit_message_text(

            "💰 <b>PROVIDER</b>\n\n"

            "Provider API belum terhubung.",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup(
                back
            ),
        )

    elif query.data == "admin_stats":

        with get_db() as db:

            users = db.execute(
                """
                SELECT COUNT(*) AS total
                FROM users
                """
            ).fetchone()["total"]

            deposits = db.execute(
                """
                SELECT COUNT(*) AS total
                FROM deposits
                """
            ).fetchone()["total"]

            orders = db.execute(
                """
                SELECT COUNT(*) AS total
                FROM orders
                """
            ).fetchone()["total"]

            balance = db.execute(
                """
                SELECT COALESCE(
                    SUM(balance),
                    0
                ) AS total
                FROM users
                """
            ).fetchone()["total"]

        await query.edit_message_text(

            "📊 <b>STATISTIK</b>\n\n"

            f"👥 Users: <b>{users}</b>\n"

            f"💳 Deposits: <b>{deposits}</b>\n"

            f"📦 Orders: <b>{orders}</b>\n"

            f"💰 Total saldo user: "
            f"<b>{format_rupiah(balance)}</b>",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup(
                back
            ),
        )

    elif query.data == "admin_home":

        await query.edit_message_text(

            "👑 <b>ADMIN PANEL</b>\n\n"
            "Pilih menu:",

            parse_mode="HTML",

            reply_markup=admin_menu(),
        )


# =========================================================
# BUTTON HANDLER
# =========================================================

async def button_handler(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    admin_callbacks = {
        "admin_users",
        "admin_deposits",
        "admin_orders",
        "admin_provider",
        "admin_stats",
        "admin_home",
    }

    if query.data in admin_callbacks:

        if not is_admin(user_id):

            await query.answer(
                "❌ Kamu bukan admin.",
                show_alert=True
            )

            return

        await admin_callback(
            query
        )

        return

    if query.data == "user_home":

        context.chat_data[
            "waiting_deposit"
        ] = False

    result = await user_callback(
        query,
        user_id
    )

    if result == "WAIT_DEPOSIT":

        context.chat_data[
            "waiting_deposit"
        ] = True

        await query.edit_message_text(

            "💳 <b>Deposit Saldo</b>\n\n"

            "Masukkan nominal deposit.\n\n"

            "Minimum: <b>Rp1.000</b>\n"

            "Kelipatan: <b>Rp1.000</b>\n\n"

            "Contoh:\n"
            "1000\n"
            "5000\n"
            "10000\n"
            "25000\n\n"

            "Ketik nominal sekarang.",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "❌ Batal",
                        callback_data="user_home"
                    )
                ]]
            ),
        )


# =========================================================
# DEPOSIT INPUT
# =========================================================

async def text_handler(
    update,
    context
):

    if not update.message:

        return

    if not context.chat_data.get(
        "waiting_deposit"
    ):

        return

    user = update.effective_user

    context.chat_data[
        "waiting_deposit"
    ] = False

    text = (
        update.message.text
        .strip()
        .replace(".", "")
        .replace(",", "")
    )

    if not text.isdigit():

        await update.message.reply_text(

            "❌ Nominal harus berupa angka.\n\n"

            "Contoh: "
            "<code>10000</code>",

            parse_mode="HTML",
        )

        return

    amount = int(text)

    if amount < 1000:

        await update.message.reply_text(

            "❌ <b>Deposit terlalu kecil.</b>\n\n"

            "Minimum deposit adalah "
            "<b>Rp1.000</b>.",

            parse_mode="HTML",
        )

        return

    if amount % 1000 != 0:

        await update.message.reply_text(

            "❌ <b>Nominal tidak valid.</b>\n\n"

            "Deposit harus kelipatan "
            "<b>Rp1.000</b>.",

            parse_mode="HTML",
        )

        return

    create_user(
        user.id,
        user.username,
        user.first_name
    )

    deposit_id = (
        "DEP-"
        + uuid.uuid4().hex[:12].upper()
    )

    # Simpan deposit PENDING
    with get_db() as db:

        db.execute(
            """
            INSERT INTO deposits
            (
                deposit_id,
                telegram_id,
                amount,
                status,
                created_at
            )
            VALUES
            (
                %s,
                %s,
                %s,
                %s,
                %s
            )
            """,
            (
                deposit_id,
                user.id,
                amount,
                "PENDING",
                now(),
            ),
        )

    try:

        invoice = await asyncio.to_thread(
            create_xendit_invoice,
            amount,
            deposit_id
        )

        invoice_id = invoice.get(
            "id"
        )

        invoice_url = invoice.get(
            "invoice_url"
        )

        if not invoice_id or not invoice_url:

            raise RuntimeError(
                "Respons Xendit tidak berisi "
                "invoice_id/invoice_url."
            )

        # Simpan invoice ID
        with get_db() as db:

            db.execute(
                """
                UPDATE deposits
                SET payment_reference = %s
                WHERE deposit_id = %s
                """,
                (
                    invoice_id,
                    deposit_id
                ),
            )

        keyboard = [

            [
                InlineKeyboardButton(
                    "💳 Bayar Sekarang",
                    url=invoice_url
                )
            ],

            [
                InlineKeyboardButton(
                    "⬅️ Menu Utama",
                    callback_data="user_home"
                )
            ],

        ]

        await update.message.reply_text(

            "💳 <b>Invoice Deposit Dibuat</b>\n\n"

            f"🧾 ID: "
            f"<code>{deposit_id}</code>\n"

            f"💰 Nominal: "
            f"<b>{format_rupiah(amount)}</b>\n"

            "📌 Status: <b>PENDING</b>\n\n"

            "Klik tombol di bawah "
            "untuk melakukan pembayaran.\n\n"

            "Setelah pembayaran berhasil, "
            "saldo akan otomatis masuk.",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
        )

    except Exception:

        logger.exception(
            "Gagal membuat invoice Xendit."
        )

        with get_db() as db:

            db.execute(
                """
                UPDATE deposits
                SET status = 'FAILED'
                WHERE deposit_id = %s
                  AND status = 'PENDING'
                """,
                (deposit_id,),
            )

        await update.message.reply_text(

            "❌ <b>Gagal membuat invoice pembayaran.</b>\n\n"

            "Silakan coba lagi beberapa saat kemudian.",

            parse_mode="HTML",
        )


# =========================================================
# ADMIN ADD BALANCE
# =========================================================

async def admin_add_balance(
    update,
    context
):

    if not is_admin(
        update.effective_user.id
    ):

        await update.message.reply_text(
            "❌ Kamu bukan admin."
        )

        return

    if len(context.args) != 2:

        await update.message.reply_text(

            "Format:\n\n"

            "/addbalance TELEGRAM_ID NOMINAL\n\n"

            "Contoh:\n"

            "/addbalance 123456789 10000"
        )

        return

    try:

        telegram_id = int(
            context.args[0]
        )

        amount = int(
            context.args[1]
        )

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
                "ADMIN-"
                + uuid.uuid4().hex[:8].upper()
            ),

            description=(
                "Saldo ditambahkan oleh admin"
            ),
        )

    except Exception as error:

        await update.message.reply_text(
            f"❌ Gagal:\n{error}"
        )

        return

    await update.message.reply_text(

        "✅ <b>Saldo berhasil ditambahkan.</b>\n\n"

        f"👤 User: "
        f"<code>{telegram_id}</code>\n"

        f"💰 Saldo baru: "
        f"<b>{format_rupiah(new_balance)}</b>",

        parse_mode="HTML",
    )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update,
    context
):

    logger.error(
        "Exception while handling update:",
        exc_info=context.error,
    )


# =========================================================
# RUN
# =========================================================

def run():

    init_database()

    start_webhook_server()

    application = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CommandHandler(
            "addbalance",
            admin_add_balance
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            text_handler
        )
    )

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "Bot berhasil dijalankan."
    )

    logger.info(
        "Webhook Xendit aktif di "
        "/xendit/webhook"
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    run()
