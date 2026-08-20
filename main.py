import base64
import hmac
import json
import logging
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import asyncio

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import BOT_TOKEN, ADMIN_ID

from database import (
    init_database,
    create_user,
    get_balance,
    add_balance,
    complete_deposit,
    get_db,
    now,
)

XENDIT_SECRET_KEY = os.getenv("XENDIT_SECRET_KEY", "").strip()
XENDIT_WEBHOOK_TOKEN = os.getenv("XENDIT_WEBHOOK_TOKEN", "").strip()
PORT = int(os.getenv("PORT", "8080"))

if not XENDIT_SECRET_KEY:
    raise RuntimeError("XENDIT_SECRET_KEY belum diatur di Railway.")

if not XENDIT_WEBHOOK_TOKEN:
    raise RuntimeError("XENDIT_WEBHOOK_TOKEN belum diatur di Railway.")

XENDIT_API_URL = "https://api.xendit.co/v2/invoices"


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# HELPER
# =========================================================

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def format_rupiah(amount: int) -> str:
    return f"Rp{amount:,}".replace(",", ".")


# =========================================================
# XENDIT
# =========================================================

def create_xendit_invoice(amount: int, deposit_id: str):
    """Create a Xendit Payment Link / Invoice."""

    auth = base64.b64encode(
        f"{XENDIT_SECRET_KEY}:".encode("utf-8")
    ).decode("ascii")

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
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        logger.error("Xendit HTTP %s: %s", error.code, body)
        raise RuntimeError(
            f"Xendit menolak invoice (HTTP {error.code})."
        ) from error

    except (URLError, TimeoutError) as error:
        logger.error("Xendit connection error: %s", error)
        raise RuntimeError(
            "Tidak bisa terhubung ke Xendit."
        ) from error


def send_telegram_message(chat_id: int, text: str):
    """Send a Telegram message from the webhook thread."""

    url = (
        "https://api.telegram.org/bot"
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
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=20) as response:
            response.read()
    except Exception as error:
        logger.error("Notifikasi Telegram gagal: %s", error)


def process_xendit_webhook(payload: dict):
    """
    Process the legacy Xendit Invoice webhook.

    Test & Save Xendit boleh mengirim external_id
    yang tidak ada di database kita. Itu harus tetap
    mendapat HTTP 200 supaya webhook bisa disimpan.
    """

    status = str(payload.get("status", "")).upper()
    deposit_id = payload.get("external_id")

    if not deposit_id:
        logger.warning(
            "Webhook Xendit tanpa external_id: %s",
            payload
        )
        return {
            "ok": True,
            "ignored": "missing_external_id"
        }

    if status == "PAID":

        paid_amount = payload.get(
            "paid_amount",
            payload.get("amount"),
        )

        try:
            paid_amount = int(paid_amount)

        except (TypeError, ValueError):

            logger.warning(
                "Nominal PAID tidak valid untuk %s: %r",
                deposit_id,
                paid_amount,
            )

            return {
                "ok": True,
                "ignored": "invalid_amount"
            }

        # Cari deposit asli di database.
        with get_db() as db:

            deposit = db.execute(
                """
                SELECT telegram_id, amount, status
                FROM deposits
                WHERE deposit_id = %s
                """,
                (deposit_id,),
            ).fetchone()

        # Test & Save bisa menggunakan external_id palsu.
        if not deposit:

            logger.info(
                "Webhook PAID diabaikan karena deposit tidak ditemukan: %s",
                deposit_id,
            )

            return {
                "ok": True,
                "ignored": "deposit_not_found"
            }

        # Pastikan nominal pembayaran sama dengan nominal deposit.
        if int(deposit["amount"]) != paid_amount:

            logger.error(
                "Nominal webhook tidak cocok: deposit=%s expected=%s paid=%s",
                deposit_id,
                deposit["amount"],
                paid_amount,
            )

            return {
                "ok": True,
                "ignored": "amount_mismatch"
            }

        # Hindari saldo masuk dua kali.
        if deposit["status"] == "SUCCESS":

            logger.info(
                "Webhook duplicate diabaikan: %s",
                deposit_id
            )

            return {
                "ok": True,
                "duplicate": True
            }

        if deposit["status"] != "PENDING":

            logger.info(
                "Deposit %s berstatus %s; webhook diabaikan.",
                deposit_id,
                deposit["status"],
            )

            return {
                "ok": True,
                "ignored": "not_pending"
            }

        payment_reference = (
            payload.get("payment_id")
            or payload.get("id")
            or deposit_id
        )

        completed, telegram_id, amount, new_balance = complete_deposit(
            deposit_id,
            payment_reference=payment_reference,
        )

        if completed:

            send_telegram_message(
                telegram_id,
                "✅ <b>Deposit berhasil!</b>\n\n"
                f"💰 Deposit: <b>{format_rupiah(amount)}</b>\n"
                "💳 Status: <b>PAID</b>\n"
                f"🧾 ID: <code>{deposit_id}</code>\n\n"
                f"💰 Saldo sekarang: <b>{format_rupiah(new_balance)}</b>",
            )

        return {
            "ok": True,
            "completed": completed
        }

    if status == "EXPIRED":

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

        return {
            "ok": True,
            "expired": True
        }

    logger.info(
        "Webhook Xendit diabaikan: deposit=%s status=%s",
        deposit_id,
        status,
    )

    return {
        "ok": True,
        "ignored": status or "empty_status"
    }


class XenditWebhookHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        logger.info(
            "Webhook HTTP: " + fmt,
            *args
        )

    def send_json(self, code: int, data: dict):

        body = json.dumps(data).encode("utf-8")

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

        path = self.path.split("?", 1)[0]

        if path == "/health":

            self.send_json(
                200,
                {"ok": True}
            )

            return

        if path == "/xendit/webhook":

            self.send_json(
                200,
                {
                    "ok": True,
                    "method": "POST required"
                }
            )

            return

        self.send_json(
            404,
            {
                "ok": False,
                "error": "Not found"
            }
        )

    def do_POST(self):

        path = self.path.split("?", 1)[0]

        if path != "/xendit/webhook":

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
        ).strip()

        if (
            not token
            or not hmac.compare_digest(
                token,
                XENDIT_WEBHOOK_TOKEN
            )
        ):

            logger.warning(
                "Webhook Xendit ditolak: token tidak cocok."
            )

            self.send_json(
                403,
                {
                    "ok": False,
                    "error": "Forbidden"
                }
            )

            return

        webhook_id = self.headers.get(
            "webhook-id",
            ""
        )

        if webhook_id:

            logger.info(
                "Menerima webhook-id: %s",
                webhook_id
            )

        try:

            length = int(
                self.headers.get(
                    "Content-Length",
                    "0"
                )
            )

            if (
                length <= 0
                or length > 1024 * 1024
            ):

                self.send_json(
                    400,
                    {
                        "ok": False,
                        "error": "Invalid body"
                    }
                )

                return

            raw_body = self.rfile.read(length)

            payload = json.loads(
                raw_body.decode("utf-8")
            )

            result = process_xendit_webhook(
                payload
            )

            # Selalu balas 200 untuk webhook yang
            # token-nya valid.
            self.send_json(
                200,
                result
            )

        except json.JSONDecodeError:

            self.send_json(
                400,
                {
                    "ok": False,
                    "error": "Invalid JSON"
                }
            )

        except Exception as error:

            logger.exception(
                "Gagal memproses webhook Xendit: %s",
                error
            )

            self.send_json(
                500,
                {
                    "ok": False,
                    "error": "Webhook processing failed"
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
        "Webhook Xendit aktif di port %s",
        PORT
    )

    return server
    # =========================================================
# CALLBACK HANDLER
# =========================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    # -----------------------------------------------------
    # SECURITY
    # -----------------------------------------------------

    admin_callbacks = [
        "admin_users",
        "admin_deposits",
        "admin_orders",
        "admin_provider",
        "admin_stats",
        "admin_home",
    ]

    if query.data in admin_callbacks:

        if not is_admin(user_id):
            await query.answer(
                "❌ Kamu bukan admin.",
                show_alert=True,
            )
            return

        await admin_callback(query)
        return

    # -----------------------------------------------------
    # USER CALLBACKS
    # -----------------------------------------------------

    result = await user_callback(
        query,
        user_id,
    )

    if result == "WAIT_DEPOSIT":

        context.chat_data["waiting_deposit"] = True

        keyboard = [
            [
                InlineKeyboardButton(
                    "❌ Batal",
                    callback_data="user_home",
                )
            ]
        ]

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
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


# =========================================================
# DEPOSIT INPUT
# =========================================================

async def text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:
        return

    if not context.chat_data.get("waiting_deposit"):
        return

    user = update.effective_user

    text = update.message.text.strip()

    context.chat_data["waiting_deposit"] = False

    text = text.replace(".", "").replace(",", "")

    if not text.isdigit():

        await update.message.reply_text(
            "❌ Nominal harus berupa angka.\n\n"
            "Contoh:\n"
            "<code>10000</code>",
            parse_mode="HTML",
        )

        return

    amount = int(text)

    # -----------------------------------------------------
    # MINIMUM
    # -----------------------------------------------------

    if amount < 1000:

        await update.message.reply_text(
            "❌ <b>Deposit terlalu kecil.</b>\n\n"
            "Minimum deposit adalah <b>Rp1.000</b>.",
            parse_mode="HTML",
        )

        return

    # -----------------------------------------------------
    # MULTIPLE
    # -----------------------------------------------------

    if amount % 1000 != 0:

        await update.message.reply_text(
            "❌ <b>Nominal tidak valid.</b>\n\n"
            "Deposit harus kelipatan <b>Rp1.000</b>.\n\n"
            "Contoh benar:\n"
            "Rp1.000\n"
            "Rp5.000\n"
            "Rp10.000\n"
            "Rp25.000",
            parse_mode="HTML",
        )

        return

    create_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )

    deposit_id = (
        "DEP-"
        + uuid.uuid4().hex[:12].upper()
    )

    # -----------------------------------------------------
    # SIMPAN DEPOSIT
    # -----------------------------------------------------

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
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                deposit_id,
                user.id,
                amount,
                "PENDING",
                now(),
            ),
        )

    # -----------------------------------------------------
    # CREATE XENDIT INVOICE
    # -----------------------------------------------------

    try:

        invoice = await asyncio.to_thread(
            create_xendit_invoice,
            amount,
            deposit_id,
        )

        invoice_id = invoice.get("id")
        invoice_url = invoice.get("invoice_url")

        if not invoice_id or not invoice_url:

            raise RuntimeError(
                "Respons Xendit tidak berisi "
                "invoice ID atau invoice URL."
            )

        # -------------------------------------------------
        # SIMPAN ID INVOICE
        # -------------------------------------------------

        with get_db() as db:

            db.execute(
                """
                UPDATE deposits
                SET payment_reference = %s
                WHERE deposit_id = %s
                """,
                (
                    invoice_id,
                    deposit_id,
                ),
            )

        # -------------------------------------------------
        # PAYMENT BUTTON
        # -------------------------------------------------

        keyboard = [
            [
                InlineKeyboardButton(
                    "💳 Bayar Sekarang",
                    url=invoice_url,
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Menu Utama",
                    callback_data="user_home",
                )
            ],
        ]

        await update.message.reply_text(
            "💳 <b>Invoice Deposit Dibuat</b>\n\n"
            f"🧾 ID: <code>{deposit_id}</code>\n"
            f"💰 Nominal: <b>{format_rupiah(amount)}</b>\n"
            "📌 Status: <b>PENDING</b>\n\n"
            "Klik tombol di bawah untuk melakukan pembayaran.\n\n"
            "Setelah pembayaran berhasil, saldo akan otomatis masuk.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as error:

        logger.exception(
            "Gagal membuat invoice Xendit: %s",
            error,
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
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not is_admin(update.effective_user.id):

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
            description="Saldo ditambahkan oleh admin",
        )

    except Exception as error:

        await update.message.reply_text(
            f"❌ Gagal:\n{error}"
        )

        return

    await update.message.reply_text(
        "✅ <b>Saldo berhasil ditambahkan.</b>\n\n"
        f"👤 User: <code>{telegram_id}</code>\n"
        f"💰 Saldo baru: "
        f"<b>{format_rupiah(new_balance)}</b>",
        parse_mode="HTML",
    )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):

    logger.error(
        "Exception while handling update:",
        exc_info=context.error,
    )


# =========================================================
# RUN BOT
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

    # -----------------------------------------------------
    # START
    # -----------------------------------------------------

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    # -----------------------------------------------------
    # ADMIN ADD BALANCE
    # -----------------------------------------------------

    application.add_handler(
        CommandHandler(
            "addbalance",
            admin_add_balance,
        )
    )

    # -----------------------------------------------------
    # CALLBACK
    # -----------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    # -----------------------------------------------------
    # TEXT
    # -----------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler,
        )
    )

    # -----------------------------------------------------
    # ERROR HANDLER
    # -----------------------------------------------------

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "Bot berhasil dijalankan."
    )

    logger.info(
        "Webhook Xendit: /xendit/webhook"
    )

    # -----------------------------------------------------
    # START POLLING
    # -----------------------------------------------------

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":
    run()
