import base64
import json
import logging
import os
import uuid
import threading
import hashlib
import hmac
import asyncio
import pytz
import random
from html import escape

try:
    import pycountry
except Exception:
    pycountry = None
from datetime import timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

from datetime import datetime
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from flask import Flask, request, jsonify

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)

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
    PROMO_CHANNEL,
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
    create_pending_order,
    get_checkin_status,
    claim_checkin,
    save_provider_order,
    get_order,
    mark_order_success,
    refund_order,
    request_order_cancel,
    clear_order_cancel_request,
    get_cancel_queue,
    save_otp_quote,
    get_otp_quote
)

import midtransclient

from provider import (
    check_api as check_5sim_api,
    get_balance as get_5sim_balance,
    get_products,
    get_all_products,
    get_prices,
    get_all_countries,
    get_cheapest_operator,
    get_price_options,
    get_price_options_for_operator,
    hitung_harga_jual,
    buy_number,
    buy_number_any_operator,
    get_sms,
    cancel_number
)



from rumahotp import (
    check_api as check_rumahotp_api,
    get_balance as get_rumahotp_balance,
    get_services as get_rumahotp_services,
    find_service as find_rumahotp_service,
    get_all_quotes as get_rumahotp_all_quotes,
    get_quotes_for_country as get_rumahotp_quotes_for_country,
    get_operator_quotes as get_rumahotp_operator_quotes,
    get_cheapest_quote as get_rumahotp_cheapest_quote,
    buy_number as buy_rumahotp_number,
    get_sms as get_rumahotp_sms,
    cancel_number as cancel_rumahotp_number,
    resend_otp as resend_rumahotp_otp
)


# =========================================================
# KONFIGURASI
# =========================================================

if not MIDTRANS_SERVER_KEY:

    raise RuntimeError(
        "MIDTRANS_SERVER_KEY belum diatur di Railway."
    )


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    format=(
        "%(asctime)s - "
        "%(name)s - "
        "%(levelname)s - "
        "%(message)s"
    ),
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

# Runtime fallback for a rare database stall immediately after a provider purchase.
# This does not change order logic; it only prevents the Telegram callback from
# remaining stuck when the provider has already issued the number.
_RUNTIME_PROVIDER_CACHE = {}


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)


# =========================================================
# MIDTRANS SNAP
# =========================================================

snap = midtransclient.Snap(
    is_production=True,
    server_key=MIDTRANS_SERVER_KEY
)


# =========================================================
# KONFIGURASI MENU OTP
# =========================================================

COUNTRIES_PER_PAGE = 12
OPERATORS_PER_PAGE = 12
PRICE_TIERS_PER_PAGE = 12
PRODUCTS_PER_PAGE = 12
SERVICES_PER_PAGE = 16
COUNTRY_QUOTES_PER_PAGE = 8


# =========================================================
# SERVER OTP
# =========================================================

OTP_SERVERS = {
    "5sim": "Server 1",
    "rumahotp": "Server 2",
}

# RumahOTP-style user-facing cancellation window. The provider UI shown by the
# user uses a short wait before cancellation becomes available. Keep provider
# names completely hidden from customers.
CANCEL_COOLDOWN_SECONDS = 60
USER_TIMEZONE = timezone(timedelta(hours=7))

def _parse_created_timestamp(value):
    if value is None or value == "":
        return None
    raw = str(value).strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).timestamp()
    except Exception:
        try:
            number = float(raw)
            return number / 1000.0 if number > 10_000_000_000 else number
        except Exception:
            return None

def _format_countdown(seconds):
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"

def _order_timing(order):
    now_ts = datetime.now(timezone.utc).timestamp()
    created_ts = _parse_created_timestamp(order.get("created_at"))
    expiry_ts = _parse_expiry_timestamp(order.get("expired_at")) if "_parse_expiry_timestamp" in globals() else None
    cancel_at = (created_ts + CANCEL_COOLDOWN_SECONDS) if created_ts is not None else None
    return {
        "cancel_wait": max(0, int(cancel_at - now_ts)) if cancel_at is not None else 0,
        "active_left": max(0, int(expiry_ts - now_ts)) if expiry_ts is not None else None,
        "expired": expiry_ts is not None and now_ts >= expiry_ts,
        "expired_at": expiry_ts,
    }

def _user_timing_text(order, include_cancel=True):
    t = _order_timing(order)
    lines = []
    if t["active_left"] is not None:
        lines.append(f"⏳ <b>Masa aktif tersisa:</b> <code>{_format_countdown(t['active_left'])}</code>")
    if t["expired_at"]:
        dt = datetime.fromtimestamp(t["expired_at"], timezone.utc).astimezone(USER_TIMEZONE)
        lines.append(f"🕒 <b>Expired:</b> <code>{dt.strftime('%d-%m-%Y %H:%M:%S')} WIB</code>")
    lines.append("⚠️ Gunakan nomor sebelum waktu expired habis.")
    if include_cancel and t["cancel_wait"] > 0:
        lines.append(f"Tunggu {_format_countdown(t['cancel_wait'])} sebelum klik batal.")
    return "\n".join(lines)


# =========================================================
# DAFTAR LAYANAN OTP
# =========================================================

OTP_SERVICES = [

    (
        "whatsapp",
        "📱 WhatsApp"
    ),

    (
        "telegram",
        "✈️ Telegram"
    ),

    (
        "shopee",
        "🛒 Shopee"
    ),

    (
        "tiktok",
        "🎵 TikTok"
    ),

    (
        "facebook",
        "📘 Facebook"
    ),

    (
        "instagram",
        "📸 Instagram"
    ),

    (
        "google",
        "🔎 Google / Gmail / YouTube"
    ),

    (
        "vercel",
        "▲ Vercel"
    ),

    (
        "uangme",
        "💰 UangMe"
    ),

    (
        "grab",
        "🚕 Grab"
    ),

    (
        "dana",
        "💳 DANA"
    ),

    (
        "gojek",
        "🟢 Gojek"
    ),

    (
        "any",
        "🌐 Any Other"
    ),

    (
        "ovo",
        "💜 OVO"
    ),

    (
        "kopikenangan",
        "☕ Kopi Kenangan"
    ),

    (
        "tokopedia",
        "🛍 Tokopedia"
    )

]


# =========================================================
# HELPER
# =========================================================

def is_admin(user_id):

    return user_id == ADMIN_ID


def format_rupiah(amount):

    return (
        f"Rp{int(amount):,}"
    ).replace(
        ",",
        "."
    )


def get_wib_time():

    wib = pytz.timezone(
        "Asia/Jakarta"
    )

    return datetime.now(
        wib
    ).strftime(
        "%d %B %Y pukul %H:%M:%S WIB"
    )


# =========================================================
# COUNTRY / SERVICE DISPLAY HELPERS
# =========================================================

_COUNTRY_ALIASES = {
    "uk": "GB", "united kingdom": "GB", "usa": "US",
    "united states": "US", "united states of america": "US",
    "uae": "AE", "united arab emirates": "AE",
    "south korea": "KR", "korea": "KR", "russia": "RU",
    "vietnam": "VN", "viet nam": "VN", "laos": "LA",
    "czech republic": "CZ", "ivory coast": "CI",
    "bolivia": "BO", "venezuela": "VE", "tanzania": "TZ",
}

def country_flag(name_or_code):
    value = str(name_or_code or "").strip().replace("_", " ")
    if len(value) == 2 and value.isalpha():
        code = value.upper()
    else:
        code = _COUNTRY_ALIASES.get(value.lower())
        if not code and pycountry is not None:
            try:
                code = pycountry.countries.lookup(value).alpha_2
            except Exception:
                code = None
    if not code or len(code) != 2:
        return "🏳️"
    return "".join(chr(127397 + ord(ch)) for ch in code.upper())


def rumah_service_label(service):
    found = find_rumahotp_service(service)
    if found:
        return str(found.get("name") or service).strip()
    return str(service).strip()


def canonical_5sim_service(service):
    """Map a Server 2 service code/name to the canonical 5SIM service name when possible."""
    raw = str(service or "").strip()
    known = {str(code).lower(): str(code) for code, _ in OTP_SERVICES}
    if raw.lower() in known:
        return known[raw.lower()]

    found = find_rumahotp_service(raw)
    name = str(found.get("name") or "") if found else raw
    target = name.lower().strip()
    aliases = {
        "whatsapp": "whatsapp",
        "telegram": "telegram",
        "facebook": "facebook",
        "instagram": "instagram",
        "tiktok": "tiktok",
        "shopee": "shopee",
        "google": "google",
        "gmail": "google",
        "youtube": "google",
    }
    if target in aliases:
        return aliases[target]
    compact = target.replace(",", " ").replace("/", " ")
    if "google" in compact and ("gmail" in compact or "youtube" in compact):
        return "google"
    for code, label in OTP_SERVICES:
        if target == str(label).lower().replace("📱 ", "").replace("✈️ ", "").strip():
            return code
    return raw


# =========================================================
# USER MENU
# =========================================================

def user_menu():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "📖 Cara Penggunaan",
                callback_data="cara"
            )
        ],

        [
            InlineKeyboardButton(
                "📱 Order OTP",
                callback_data="order"
            ),

            InlineKeyboardButton(
                "💳 Deposit",
                callback_data="user_deposit"
            )
        ],

        [
            InlineKeyboardButton(
                "📋 Histori Order",
                callback_data="user_history_order"
            ),

            InlineKeyboardButton(
                "📜 Histori Deposit",
                callback_data="user_history_depo"
            )
        ],

        [
            InlineKeyboardButton(
                "👥 Referral",
                callback_data="referral"
            ),

            InlineKeyboardButton(
                "🎁 Saldo Gratis",
                callback_data="checkin"
            )
        ],

        [
            InlineKeyboardButton(
                "💬 Contact CS",
                callback_data="cs"
            )
        ]

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
            )
        ],

        [
            InlineKeyboardButton(
                "📦 Orders",
                callback_data="admin_orders"
            ),

            InlineKeyboardButton(
                "💰 Provider",
                callback_data="admin_provider"
            )
        ],

        [
            InlineKeyboardButton(
                "📊 Statistik",
                callback_data="admin_stats"
            )
        ]

    ])


# =========================================================
# MIDTRANS CREATE SNAP TOKEN
# =========================================================

def create_midtrans_snap(
    amount,
    deposit_id
):

    param = {

        "transaction_details": {

            "order_id": deposit_id,

            "gross_amount": amount

        },

        "item_details": [

            {
                "id": "DEPOSIT",

                "price": amount,

                "quantity": 1,

                "name": "Deposit Saldo Bot"
            }

        ],

        "customer_details": {

            "first_name":
                f"User {deposit_id}"

        },

        "expiry": {

            "start_time":
                datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S +07:00"
                ),

            "unit": "hours",

            "duration": 24

        }

    }

    try:

        transaction = snap.create_transaction(
            param
        )

        return transaction

    except Exception as error:

        logger.error(
            "Midtrans Error: %s",
            error
        )

        raise RuntimeError(
            "Gagal membuat Snap Token Midtrans."
        ) from error


# =========================================================
# CEK STATUS MIDTRANS
# =========================================================

def cek_status_midtrans(
    order_id
):

    url = (
        f"{MIDTRANS_API_URL}/"
        f"{order_id}/status"
    )

    req = Request(

        url,

        headers={

            "Authorization":
                "Basic " +
                base64.b64encode(
                    f"{MIDTRANS_SERVER_KEY}:".encode()
                ).decode()

        },

        method="GET"

    )

    try:

        with urlopen(
            req,
            timeout=20
        ) as response:

            return json.loads(
                response.read().decode()
            )

    except HTTPError as error:

        logger.error(
            "Cek status gagal: %s",
            error.read().decode()
        )

        return None

    except Exception as error:

        logger.error(
            "Cek status Midtrans error: %s",
            error
        )

        return None


# =========================================================
# TELEGRAM NOTIFICATION
# =========================================================

def send_telegram_message(
    chat_id,
    text
):

    url = (
        "https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    payload = {

        "chat_id": chat_id,

        "text": text,

        "parse_mode": "HTML"

    }

    telegram_request = Request(

        url,

        data=json.dumps(
            payload
        ).encode(),

        headers={

            "Content-Type":
                "application/json"

        },

        method="POST"

    )

    try:

        with urlopen(
            telegram_request,
            timeout=20
        ) as response:

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
            (deposit_id,)
        ).fetchone()

        if not deposit:

            raise ValueError(
                "Deposit tidak ditemukan."
            )

        if deposit["status"] == "SUCCESS":

            return {
                "completed": False,
                "already_completed": True
            }

        if deposit["status"] != "PENDING":

            raise ValueError(
                f"Deposit berstatus "
                f"{deposit['status']}, bukan PENDING."
            )

        if int(float(paid_amount)) != int(
            deposit["amount"]
        ):

            raise ValueError(
                "Nominal pembayaran Midtrans "
                "tidak sama dengan nominal deposit."
            )

        user = db.execute(
            """
            SELECT balance
            FROM users
            WHERE telegram_id = %s
            FOR UPDATE
            """,
            (deposit["telegram_id"],)
        ).fetchone()

        if not user:

            raise ValueError(
                "User deposit tidak ditemukan."
            )

        before = int(user["balance"] or 0)
        deposit_amount = int(deposit["amount"])
        bonus = int(deposit_amount * 0.10) if deposit_amount >= 100000 else 0
        after_deposit = before + deposit_amount
        after = after_deposit + bonus

        db.execute(
            """
            UPDATE users
            SET balance = %s
            WHERE telegram_id = %s
            """,
            (after, deposit["telegram_id"])
        )

        db.execute(
            """
            INSERT INTO ledger
            (telegram_id, amount, balance_before, balance_after,
             transaction_type, reference, description, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                deposit["telegram_id"], deposit_amount, before, after_deposit,
                "DEPOSIT", payment_reference or deposit_id,
                f"Deposit Midtrans {deposit_id}", now()
            )
        )

        if bonus > 0:
            db.execute(
                """
                INSERT INTO ledger
                (telegram_id, amount, balance_before, balance_after,
                 transaction_type, reference, description, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    deposit["telegram_id"], bonus, after_deposit, after,
                    "DEPOSIT_BONUS", deposit_id,
                    f"Bonus 10% deposit {deposit_id}", now()
                )
            )

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
                deposit_id
            )
        )

        return {

            "completed": True,

            "already_completed": False,

            "telegram_id":
                deposit["telegram_id"],

            "amount":
                deposit["amount"],

            "bonus":
                bonus,

            "credited":
                deposit["amount"] + bonus,

            "new_balance":
                after

        }


# =========================================================
# MIDTRANS WEBHOOK
# =========================================================

@app.route(
    "/midtrans/webhook",
    methods=["POST"]
)
def midtrans_webhook():

    try:

        data = request.get_json(
            silent=True
        )

        if not data:

            return jsonify({
                "status": "error",
                "message": "Invalid JSON"
            }), 400

        order_id = data.get(
            "order_id"
        )

        status = data.get(
            "transaction_status"
        )

        fraud = data.get(
            "fraud_status"
        )

        gross_amount = str(
            data.get(
                "gross_amount",
                ""
            )
        )

        status_code = str(
            data.get(
                "status_code",
                ""
            )
        )

        signature_key = data.get(
            "signature_key"
        )

        transaction_id = data.get(
            "transaction_id"
        )

        payment_type = data.get(
            "payment_type"
        )

        logger.info(
            "Webhook Midtrans | "
            "order=%s status=%s type=%s",
            order_id,
            status,
            payment_type
        )

        if not order_id:

            return jsonify({
                "status": "error",
                "message": "Missing order_id"
            }), 400

        if not status_code:

            return jsonify({
                "status": "error",
                "message": "Missing status_code"
            }), 400

        if not gross_amount:

            return jsonify({
                "status": "error",
                "message": "Missing gross_amount"
            }), 400

        if not signature_key:

            return jsonify({
                "status": "error",
                "message": "Missing signature_key"
            }), 403

        signature_string = (
            str(order_id)
            +
            str(status_code)
            +
            str(gross_amount)
            +
            str(MIDTRANS_SERVER_KEY)
        )

        expected_signature = hashlib.sha512(
            signature_string.encode(
                "utf-8"
            )
        ).hexdigest()

        if not hmac.compare_digest(
            expected_signature.lower(),
            str(
                signature_key
            ).lower()
        ):

            return jsonify({
                "status": "error",
                "message": "Invalid signature"
            }), 403

        is_success = False

        if status == "settlement":

            is_success = True

        elif (
            status == "capture"
            and
            payment_type == "credit_card"
        ):

            is_success = True

        if is_success and fraud is not None:

            if str(
                fraud
            ).lower() != "accept":

                return jsonify({
                    "status": "ok",
                    "message":
                        "Fraud status not accepted"
                }), 200

        if (
            is_success
            and
            status_code != "200"
        ):

            return jsonify({
                "status": "ok",
                "message":
                    "Invalid success status code"
            }), 200

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
                        f"💰 Deposit: "
                        f"<b>{format_rupiah(result['amount'])}</b>\n"
                        f"💳 Status: <b>PAID</b>\n"
                        f"🧾 ID: "
                        f"<code>{order_id}</code>\n\n"
                        f"💰 Saldo sekarang: "
                        f"<b>{format_rupiah(result['new_balance'])}</b>"

                    )

            except Exception:

                logger.exception(
                    "Gagal proses payment %s",
                    order_id
                )

                return jsonify({
                    "status": "error"
                }), 500

        elif status in [
            "expire",
            "cancel"
        ]:

            with get_db() as db:

                db.execute(
                    """
                    UPDATE deposits
                    SET status = 'EXPIRED'
                    WHERE deposit_id = %s
                    AND status = 'PENDING'
                    """,
                    (order_id,)
                )

        return jsonify({
            "status": "ok"
        }), 200

    except Exception:

        logger.exception(
            "Webhook error"
        )

        return jsonify({
            "status": "error"
        }), 500


# =========================================================
# HEALTH CHECK
# =========================================================

@app.route(
    "/",
    methods=["GET"]
)
def health_check():

    return jsonify({

        "status": "online",

        "service":
            "otp-reseller-bot"

    }), 200


# =========================================================
# USER START
# =========================================================

async def user_start(
    update_or_query
):

    if isinstance(
        update_or_query,
        Update
    ):

        user = (
            update_or_query
            .effective_user
        )

        send = (
            update_or_query
            .message
            .reply_text
        )

    else:

        user = (
            update_or_query
            .from_user
        )

        send = (
            update_or_query
            .edit_message_text
        )

    create_user(

        user.id,

        user.username,

        user.first_name

    )

    saldo = get_balance(
        user.id
    )

    total_user = get_total_users()

    waktu = get_wib_time()

    # PROMO_CHANNEL can be configured in Railway as either:
    #   @YourChannel
    #   YourChannel
    #   https://t.me/YourChannel
    # Keep the public channel configurable; never hard-code it in source.
    raw_channel = (PROMO_CHANNEL or "").strip()
    if raw_channel:
        channel_handle = raw_channel.rstrip("/")
        if channel_handle.startswith("https://t.me/"):
            channel_url = channel_handle
            channel_label = "@" + channel_handle.rsplit("/", 1)[-1].lstrip("@")
        elif channel_handle.startswith("http://t.me/"):
            channel_url = "https://" + channel_handle.split("://", 1)[1]
            channel_label = "@" + channel_handle.rsplit("/", 1)[-1].lstrip("@")
        else:
            channel_label = "@" + channel_handle.lstrip("@")
            channel_url = "https://t.me/" + channel_handle.lstrip("@")
        promo_channel_display = f'<a href="{channel_url}">{channel_label}</a>'
    else:
        promo_channel_display = "Belum diatur"

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
├ Channel : {promo_channel_display}

<b>Shortcut :</b>
├ /start - Mulai Bot
"""

    await send(
        text,
        parse_mode="HTML",
        reply_markup=user_menu()
    )


# =========================================================
# ADMIN START
# =========================================================

async def admin_start(
    update
):

    await update.message.reply_text(

        "👑 <b>ADMIN PANEL</b>\n\n"
        "Selamat datang, Admin.\n\n"
        "Pilih menu:",

        parse_mode="HTML",

        reply_markup=admin_menu()

    )


# =========================================================
# START
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

    if is_admin(
        user.id
    ):

        await admin_start(
            update
        )

    else:

        context.chat_data[
            "waiting_deposit"
        ] = False

        await user_start(
            update
        )


# =========================================================
# PILIH SERVER OTP
# =========================================================

async def show_server_page(
    query
):

    keyboard = [
        [InlineKeyboardButton("Server 1", callback_data="otp_server:5sim")],
        [InlineKeyboardButton("Server 2", callback_data="otp_server:rumahotp")],
        [InlineKeyboardButton("↩️ Kembali", callback_data="user_home")],
    ]

    await query.edit_message_text(
        "🌟 <b>PILIH SERVER OTP</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ <b>SERVER 1 — HIGH STOCK</b>\n"
        "Server utama dengan stok nomor dalam jumlah besar dan performa stabil.\n\n"
        "⚡ <b>SERVER 2 — FULL TEXT</b>\n"
        "Server khusus yang menampilkan isi pesan SMS secara utuh tanpa filter kode.⚡\n\n"
        "Silahkan pilih server melalui tombol di bawah ini",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================================================
# PILIH LAYANAN OTP
# =========================================================


def _merge_service_catalog(catalog, seen, data, code_keys, label_keys):
    """Tambahkan layanan provider ke katalog tanpa duplikasi."""
    if isinstance(data, dict):
        iterable = data.items()
    elif isinstance(data, list):
        iterable = enumerate(data)
    else:
        iterable = []

    for key, value in iterable:
        if isinstance(value, dict):
            code = ""
            for field in code_keys:
                if value.get(field) is not None:
                    code = str(value.get(field)).strip()
                    if code:
                        break
            code = code or str(key).strip()

            label = ""
            for field in label_keys:
                if value.get(field) is not None:
                    label = str(value.get(field)).strip()
                    if label:
                        break
            label = label or code
        else:
            code = str(value).strip()
            label = code

        if code and code.lower() not in seen:
            catalog.append((code, label))
            seen.add(code.lower())


def get_service_catalog(server):
    """Katalog layanan dari provider Server 1 (5SIM) atau Server 2 (RumahOTP)."""
    if server == "5sim":
        catalog = list(OTP_SERVICES)
        seen = {code.lower() for code, _ in catalog}

        data = get_all_products()
        if isinstance(data, dict):
            for product, info in data.items():
                if isinstance(info, dict):
                    category = str(
                        info.get("Category", "")
                    ).lower()
                    if category and category != "activation":
                        continue
                code = str(product).strip()
                if code and code.lower() not in seen:
                    catalog.append((code, code.replace("_", " ").title()))
                    seen.add(code.lower())

        return catalog

    if server == "rumahotp":
        # Server 2 must follow the live RumahOTP catalog. The old static
        # OTP_SERVICES list caused services to be missing/duplicated and could
        # send users into a service code that did not match RumahOTP.
        catalog = []
        seen = set()

        for item in get_rumahotp_services() or []:
            if not isinstance(item, dict):
                continue

            code = str(
                item.get("service_code")
                or item.get("id")
                or ""
            ).strip()
            label = str(
                item.get("service_name")
                or item.get("name")
                or code
            ).strip()

            if not code:
                continue

            key = code.lower()
            if key not in seen:
                catalog.append((code, label))
                seen.add(key)

        # If the live catalog is temporarily unavailable, keep the bot
        # navigable; the country page will still re-check the live API.
        if not catalog:
            return list(OTP_SERVICES)

        return catalog

    return list(OTP_SERVICES)


async def show_service_page(
    query,
    server,
    page=0
):
    """Tampilan layanan 2 kolom seperti menu referensi."""

    try:
        services = await asyncio.wait_for(
            asyncio.to_thread(get_service_catalog, server),
            timeout=15
        )
    except asyncio.TimeoutError:
        await query.edit_message_text(
            "⚠️ <b>Data layanan sedang dimuat.</b>\n\n"
            "Silakan tekan kembali lalu coba lagi sebentar.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Coba Lagi", callback_data=f"otp_server:{server}"),
                InlineKeyboardButton("⬅️ Kembali", callback_data="order"),
            ]])
        )
        return

    total_pages = (
        len(services) + SERVICES_PER_PAGE - 1
    ) // SERVICES_PER_PAGE
    total_pages = max(total_pages, 1)
    page = max(0, min(page, total_pages - 1))

    start_index = page * SERVICES_PER_PAGE
    page_items = services[
        start_index:start_index + SERVICES_PER_PAGE
    ]

    keyboard = []

    for i in range(0, len(page_items), 2):
        row = []
        for service_code, service_name in page_items[i:i + 2]:
            row.append(
                InlineKeyboardButton(
                    service_name,
                    callback_data=(
                        f"otp_service:{server}:{service_code}"
                    )
                )
            )
        keyboard.append(row)

    navigation = [
        InlineKeyboardButton(
            "◀️",
            callback_data=(
                f"otp_services:{server}:"
                f"{max(page - 1, 0)}"
            )
        ),
        InlineKeyboardButton(
            f"{page + 1}/{total_pages}",
            callback_data="otp_noop"
        ),
        InlineKeyboardButton(
            "▶️",
            callback_data=(
                f"otp_services:{server}:"
                f"{min(page + 1, total_pages - 1)}"
            )
        )
    ]

    keyboard.append(navigation)

    keyboard.append([
        InlineKeyboardButton(
            "⌕ Search",
            callback_data=f"otp_search:{server}"
        ),
        InlineKeyboardButton(
            "▧ Kembali",
            callback_data="order"
        )
    ])

    await query.edit_message_text(
        "🖥 <b>PILIH LAYANAN OTP</b>\n\n"
        "Pilih layanan OTP dari katalog.\n\nGoogle / Gmail / YouTube ditampilkan sebagai satu layanan bila tersedia.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


def _country_items_5sim(service):
    """Build one row per country from the live 5SIM product price matrix.

    5SIM's product-filtered endpoint returns product -> country -> operator,
    while the country+product form returns country -> product -> operator.
    provider.get_prices() normalizes these shapes, and this function keeps a
    defensive fallback so a provider response change cannot silently remove
    every country from the bot.
    """
    data = get_prices(product=service)

    if not isinstance(data, dict):
        return []

    # Defensive normalization in case a raw product -> country response ever
    # reaches this layer.
    target = str(service).strip().lower()
    for key in list(data.keys()):
        if str(key).strip().lower() == target and isinstance(data.get(key), dict):
            product_data = data.get(key)
            normalized = {}
            for country_name, country_data in product_data.items():
                if isinstance(country_data, dict):
                    normalized[str(country_name)] = {str(key): country_data}
            data = normalized
            break

    items = []
    for country, country_data in data.items():
        if not isinstance(country_data, dict):
            continue
        product_data = country_data.get(service)
        if not isinstance(product_data, dict):
            target = str(service).strip().lower()
            for key, value in country_data.items():
                if str(key).strip().lower() == target:
                    product_data = value
                    break
        if not isinstance(product_data, dict):
            continue

        groups = {}
        total_stock = 0
        for operator, info in product_data.items():
            if not isinstance(info, dict):
                continue
            try:
                cost = float(info.get("cost") or 0)
                count = int(info.get("count") or 0)
            except Exception:
                continue
            if cost <= 0:
                continue
            count = max(count, 0)
            key = round(cost, 6)
            group = groups.setdefault(key, {"cost": cost, "stock": 0})
            group["stock"] += count
            total_stock += count

        if groups:
            # IMPORTANT: a country must be marked available when *any*
            # price/operator has stock. Previously we used the cheapest
            # price tier even when that tier had count=0, so the country
            # showed ❌ although clicking it revealed valid stock at a
            # different price.
            available_groups = [g for g in groups.values() if g["stock"] > 0]
            cheapest_available = min(
                available_groups,
                key=lambda x: x["cost"]
            ) if available_groups else min(groups.values(), key=lambda x: x["cost"])

            country_name = str(country).replace("_", " ").title()
            items.append({
                "country": str(country),
                "name": country_name,
                "iso_code": str(country).strip().lower(),
                "cost": float(cheapest_available["cost"]),
                "stock": int(total_stock),
            })

    return items


def _country_items_rumahotp(service):
    """One country row per country, using the cheapest listed price even when stock is zero."""
    quotes = get_rumahotp_all_quotes(service) or []
    grouped = {}
    for q in quotes:
        if not isinstance(q, dict):
            continue
        try:
            cost = float(q.get("cost_idr") or q.get("price_idr") or 0)
            stock = int(float(q.get("stock") or 0))
        except Exception:
            continue
        if cost <= 0:
            continue
        country_id = str(q.get("country") or q.get("country_name") or "").strip()
        name = str(q.get("country_name") or country_id).strip()
        iso = str(q.get("iso_code") or "").strip().lower()
        if not country_id:
            continue
        key = country_id.lower()
        if key not in grouped:
            grouped[key] = {
                "country": country_id,
                "name": name,
                "iso_code": iso,
                "cost_idr": cost,
                "cost": float(q.get("cost_usd") or 0),
                "stock": max(stock, 0),
            }
        else:
            grouped[key]["stock"] += max(stock, 0)
            if cost < grouped[key]["cost_idr"]:
                grouped[key]["cost_idr"] = cost
                grouped[key]["cost"] = float(q.get("cost_usd") or 0)
                grouped[key]["name"] = name
                grouped[key]["iso_code"] = iso
    return list(grouped.values())

def get_service_countries(server, service):
    if server == "5sim":
        return _country_items_5sim(service)
    if server == "rumahotp":
        return _country_items_rumahotp(service)
    return []


async def show_service_country_page(
    query,
    server,
    service,
    page=0
):
    """Show countries for the selected service; Server 2 keeps price tiers for the next step."""
    service_label = (
        rumah_service_label(service) if server == "rumahotp"
        else dict(OTP_SERVICES).get(service, service.title())
    )

    try:
        items = await asyncio.wait_for(
            asyncio.to_thread(get_service_countries, server, service),
            timeout=20
        )
    except asyncio.TimeoutError:
        logger.warning("OTP stock timeout: server=%s service=%s", server, service)
        await query.edit_message_text(
            "⚠️ <b>Server terlalu lama merespons.</b>\n\n"
            f"📱 Layanan: <b>{service_label}</b>\n\n"
            "Silakan tekan Refresh dan coba lagi.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh", callback_data=f"otp_service:{server}:{service}"),
                InlineKeyboardButton("⬅️ Pilih Layanan", callback_data=f"otp_server:{server}")
            ]])
        )
        return
    except Exception:
        logger.exception("OTP stock error: server=%s service=%s", server, service)
        await query.edit_message_text(
            "⚠️ <b>Gagal mengambil stok server.</b>\n\n"
            f"📱 Layanan: <b>{service_label}</b>\n\n"
            "Silakan tekan Refresh dan coba lagi.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh", callback_data=f"otp_service:{server}:{service}"),
                InlineKeyboardButton("⬅️ Pilih Layanan", callback_data=f"otp_server:{server}")
            ]])
        )
        return

    # Hanya negara yang benar-benar memiliki stok yang ditampilkan.
    items = [x for x in items if int(x.get("stock") or 0) > 0]
    items.sort(key=lambda x: (
        0 if str(x.get("name", "")).lower() == "indonesia" else 1,
        str(x.get("name", "")).lower()
    ))

    if not items:
        await query.edit_message_text(
            "❌ <b>Produk tidak tersedia</b>\n\n"
            f"📱 Layanan: <b>{service_label}</b>\n\n"
            "Belum ada negara/harga yang dikembalikan server.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Refresh", callback_data=f"otp_service:{server}:{service}")],
                [InlineKeyboardButton("⬅️ Pilih Layanan", callback_data=f"otp_server:{server}")],
                [InlineKeyboardButton("🏠 Menu Utama", callback_data="user_home")]
            ])
        )
        return

    total_pages = max(1, (len(items) + COUNTRIES_PER_PAGE - 1) // COUNTRIES_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    page_items = items[page * COUNTRIES_PER_PAGE:(page + 1) * COUNTRIES_PER_PAGE]
    keyboard = []

    # Referensi UI: negara ditampilkan 2 kolom. Harga/stok sengaja tidak
    # ditaruh di halaman negara; user melihat operator lalu tabel harga/stok.
    for i in range(0, len(page_items), 2):
        row = []
        for item in page_items[i:i + 2]:
            row.append(InlineKeyboardButton(
                f"{country_flag(item.get('iso_code') or item.get('name'))} {item['name']}",
                callback_data=(
                    f"otp_choose_server:{server}:{service}:{item['country']}"
                )
            ))
        keyboard.append(row)

    if total_pages > 1:
        keyboard.append([
            InlineKeyboardButton("◀️", callback_data=f"otp_service_countries:{server}:{service}:{max(0, page-1)}"),
            InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="otp_noop"),
            InlineKeyboardButton("▶️", callback_data=f"otp_service_countries:{server}:{service}:{min(total_pages-1, page+1)}")
        ])
    keyboard.append([InlineKeyboardButton("🔎 Cari Negara", callback_data=f"otp_country_search:{server}:{service}")])
    keyboard.append([
        InlineKeyboardButton("↩️ Kembali", callback_data=f"otp_server:{server}"),
        InlineKeyboardButton("🏠 Menu Utama", callback_data="user_home")
    ])

    await query.edit_message_text(
        "🗺️ <b>PILIH NEGARA</b>\n\n"
        f"📱 Layanan: <b>{service_label}</b>\n\n"
        "Pilih negara tujuan Anda:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def _get_otp_operator_names(server, country, service):
    """Return real operators for the selected server/country/service."""
    if server == "5sim":
        product = canonical_5sim_service(service)
        try:
            data = await asyncio.to_thread(get_prices, country=country, product=product)
        except Exception:
            logger.exception("5SIM operator lookup failed")
            return []
        country_data = data.get(country) if isinstance(data, dict) else None
        if not isinstance(country_data, dict):
            target = str(country).strip().lower()
            for key, value in (data or {}).items():
                if str(key).strip().lower() == target:
                    country_data = value
                    break
        if not isinstance(country_data, dict):
            return []
        product_data = country_data.get(product)
        if not isinstance(product_data, dict):
            target = str(product).strip().lower()
            for key, value in country_data.items():
                if str(key).strip().lower() == target:
                    product_data = value
                    break
        names = {}
        for name, info in (product_data or {}).items():
            try:
                stock = int(info.get("count") or 0)
            except Exception:
                stock = 0
            if isinstance(info, dict) and stock > 0:
                text = str(name).strip()
                if not text or text.lower() in {"any", "all", "auto", "automatic", "-"}:
                    continue
                key = text.lower().replace("_", " ").strip()
                # Merge common aliases/casing so AXIS/Axis and 3/Three are
                # shown as one operator button.
                if key in {"3", "three", "3 (three)"}:
                    display = "3 (Three)"
                    key = "three"
                elif key in {"tsel", "telkom", "telkomsel", "telkomsel indonesia"}:
                    display = "Telkomsel"
                    key = "telkomsel"
                elif key in {"xl", "xl axiata"}:
                    display = "XL"
                    key = "xl"
                elif key in {"im3", "im3 ooredoo", "indosat", "indosat ooredoo"}:
                    display = "Indosat"
                    key = "indosat"
                elif key in {"axis"}:
                    display = "Axis"
                    key = "axis"
                elif key in {"smartfren"}:
                    display = "Smartfren"
                    key = "smartfren"
                elif key in {"byu", "by.u"}:
                    display = "By.U"
                    key = "byu"
                else:
                    display = text
                names.setdefault(key, display)
        return sorted(names.values(), key=str.lower)

    if server == "rumahotp":
        try:
            rows = await asyncio.wait_for(
                asyncio.to_thread(get_rumahotp_operator_quotes, country, service),
                timeout=25,
            )
        except Exception:
            logger.exception("RumahOTP operator lookup failed")
            rows = []
        names = {}
        for item in rows or []:
            try:
                stock = int(item.get("stock") or 0)
            except Exception:
                stock = 0
            name = str(item.get("provider_operator") or item.get("operator") or "").strip()
            if stock <= 0 or not name or name.lower() in {"any", "all", "auto", "automatic", "-"}:
                continue
            key = name.lower().replace("_", " ").strip()
            if key in {"3", "three", "3 (three)"}:
                display, key = "3 (Three)", "three"
            elif key in {"tsel", "telkom", "telkomsel", "telkomsel indonesia"}:
                display, key = "Telkomsel", "telkomsel"
            elif key in {"xl", "xl axiata"}:
                display, key = "XL", "xl"
            elif key in {"im3", "im3 ooredoo", "indosat", "indosat ooredoo"}:
                display, key = "Indosat", "indosat"
            elif key == "axis":
                display = "Axis"
            elif key == "smartfren":
                display = "Smartfren"
            elif key in {"byu", "by.u"}:
                display, key = "By.U", "byu"
            else:
                display = name
            names.setdefault(key, display)
        return sorted(names.values(), key=str.lower)

    return []


async def show_otp_operator_page(query, server, service, country, page=0):
    """Operator selection shown after country, matching the reference UI."""
    service_label = rumah_service_label(service) if server == "rumahotp" else dict(OTP_SERVICES).get(service, str(service).title())
    display_country = str(country)
    names = await _get_otp_operator_names(server, country, service)

    # Always offer the provider's random/any route first.
    operators = ["any"] + names
    total_pages = max(1, (len(operators) + OPERATORS_PER_PAGE - 1) // OPERATORS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    page_items = operators[page * OPERATORS_PER_PAGE:(page + 1) * OPERATORS_PER_PAGE]

    keyboard = []
    if page == 0:
        keyboard.append([InlineKeyboardButton(
            "🌐 Semua Operator (Acak)",
            callback_data=f"otp_operator:{server}:{service}:{country}:any",
        )])
        page_items = page_items[1:]

    for i in range(0, len(page_items), 2):
        row = []
        for op in page_items[i:i + 2]:
            label = str(op).replace("_", " ").title()
            row.append(InlineKeyboardButton(
                f"📡 {label}",
                callback_data=f"otp_operator:{server}:{service}:{country}:{op}",
            ))
        if row:
            keyboard.append(row)

    if total_pages > 1:
        keyboard.append([
            InlineKeyboardButton("◀️", callback_data=f"otp_operators:{server}:{service}:{country}:{max(0, page-1)}"),
            InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="otp_noop"),
            InlineKeyboardButton("▶️", callback_data=f"otp_operators:{server}:{service}:{country}:{min(total_pages-1, page+1)}"),
        ])

    keyboard.append([InlineKeyboardButton(
        "↩️ Kembali",
        callback_data=f"otp_service_countries:{server}:{service}:0",
    )])

    await query.edit_message_text(
        "✨ <b>PILIH OPERATOR</b>\n\n"
        f"Negara: {country_flag(display_country)} <b>{escape(display_country)}</b>\n"
        f"Layanan: <b>{escape(str(service_label))}</b>\n\n"
        "Silakan pilih operator yang diinginkan:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def show_otp_price_page(query, user_id, server, service, country, operator="any", page=0):
    """Show price/stock tiers, 2 columns, always cheapest first."""
    service_label = rumah_service_label(service) if server == "rumahotp" else dict(OTP_SERVICES).get(service, str(service).title())
    display_country = str(country)
    operator = str(operator or "any")
    rows = []

    if server == "5sim":
        product = canonical_5sim_service(service)
        if operator.lower() == "any":
            rows = await asyncio.to_thread(get_price_options, country, product)
        else:
            rows = await asyncio.to_thread(get_price_options_for_operator, country, product, operator)
        rows = [r for r in (rows or []) if float(r.get("cost") or 0) > 0 and int(r.get("stock") or 0) > 0]
        rows.sort(key=lambda r: float(r.get("cost") or 0))

        if operator.lower() == "any":
            # get_price_options merges equal provider prices and keeps all
            # operator routes for checkout.
            prepared = rows
        else:
            prepared = rows

        for option in prepared:
            cost_usd = float(option.get("cost") or 0)
            stock = int(option.get("stock") or 0)
            quote_id = "5Q-" + uuid.uuid4().hex[:12].upper()
            ops = option.get("operators") or [operator]
            save_otp_quote(
                quote_id=quote_id,
                telegram_id=user_id,
                provider="5sim",
                country=country,
                country_name=display_country,
                service=product,
                operator=str(ops[0] if ops else operator),
                pool=json.dumps({"operators": ops}, separators=(",", ":")),
                cost_usd=cost_usd,
                stock=stock,
            )
            rows_label = (format_rupiah(hitung_harga_jual(cost_usd)), stock, quote_id)
            # Replace in-place with a normalized tuple for rendering below.
            option["_display"] = rows_label

    elif server == "rumahotp":
        try:
            live = await asyncio.wait_for(
                asyncio.to_thread(get_rumahotp_operator_quotes, country, service),
                timeout=25,
            )
        except Exception:
            logger.exception("RumahOTP price lookup failed")
            live = []

        if operator.lower() == "any":
            grouped = {}
            for q in live or []:
                try:
                    cost_idr = float(q.get("cost_idr") or q.get("price_idr") or 0)
                    stock = int(q.get("stock") or 0)
                except Exception:
                    continue
                if cost_idr <= 0 or stock <= 0:
                    continue
                key = round(cost_idr, 6)
                group = grouped.setdefault(key, {"cost_idr": cost_idr, "stock": 0, "routes": []})
                group["stock"] += stock
                try:
                    group["routes"].append(json.loads(q.get("pool") or "{}"))
                except Exception:
                    pass
            rows = sorted(grouped.values(), key=lambda x: x["cost_idr"])
            for group in rows:
                sell = int(round(group["cost_idr"] * (1 + PROFIT_PERCENT / 100) / 100) * 100)
                quote_id = "2Q-" + uuid.uuid4().hex[:12].upper()
                save_otp_quote(
                    quote_id=quote_id, telegram_id=user_id, provider="rumahotp",
                    country=country, country_name=display_country, service=str(find_rumahotp_service(service).get("id") if find_rumahotp_service(service) else service),
                    operator="any", pool=json.dumps({"routes": group["routes"]}, separators=(",", ":")),
                    cost_usd=group["cost_idr"] / float(KURS_DOLAR), stock=group["stock"],
                )
                group["_display"] = (format_rupiah(sell), int(group["stock"]), quote_id)
        else:
            filtered = []
            for q in live or []:
                name = str(q.get("provider_operator") or q.get("operator") or "").strip()
                try:
                    stock = int(q.get("stock") or 0)
                    cost_idr = float(q.get("cost_idr") or q.get("price_idr") or 0)
                except Exception:
                    continue
                if name.lower() != operator.lower() or stock <= 0 or cost_idr <= 0:
                    continue
                filtered.append(q)
            filtered.sort(key=lambda q: float(q.get("cost_idr") or q.get("price_idr") or 0))
            rows = []
            for q in filtered:
                cost_idr = float(q.get("cost_idr") or q.get("price_idr") or 0)
                stock = int(q.get("stock") or 0)
                sell = int(round(cost_idr * (1 + PROFIT_PERCENT / 100) / 100) * 100)
                quote_id = "2Q-" + uuid.uuid4().hex[:12].upper()
                save_otp_quote(
                    quote_id=quote_id, telegram_id=user_id, provider="rumahotp",
                    country=str(q.get("country") or country), country_name=str(q.get("country_name") or display_country),
                    service=str(q.get("service") or service), operator=operator, pool=str(q.get("pool") or ""),
                    cost_usd=cost_idr / float(KURS_DOLAR), stock=stock,
                )
                rows.append({"_display": (format_rupiah(sell), stock, quote_id)})

    if not rows:
        await query.edit_message_text(
            "❌ <b>Stok tidak tersedia.</b>\n\n"
            f"{country_flag(display_country)} Negara: <b>{escape(display_country)}</b>\n"
            f"📡 Operator: <b>{escape('Semua Operator' if operator == 'any' else operator)}</b>\n\n"
            "Coba operator lain atau refresh.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ Pilih Operator", callback_data=f"otp_choose_server:{server}:{service}:{country}")],
            ]),
        )
        return

    total_pages = max(1, (len(rows) + PRICE_TIERS_PER_PAGE - 1) // PRICE_TIERS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    page_items = rows[page * PRICE_TIERS_PER_PAGE:(page + 1) * PRICE_TIERS_PER_PAGE]
    keyboard = []
    for i in range(0, len(page_items), 2):
        row = []
        for item in page_items[i:i + 2]:
            price, stock, quote_id = item["_display"]
            row.append(InlineKeyboardButton(
                f"{price} | Stok {stock}",
                callback_data=f"otp_quote:{quote_id}",
            ))
        keyboard.append(row)

    if total_pages > 1:
        keyboard.append([
            InlineKeyboardButton("◀️", callback_data=f"otp_prices:{server}:{service}:{country}:{operator}:{max(0,page-1)}"),
            InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="otp_noop"),
            InlineKeyboardButton("▶️", callback_data=f"otp_prices:{server}:{service}:{country}:{operator}:{min(total_pages-1,page+1)}"),
        ])
    keyboard.append([InlineKeyboardButton("↩️ Kembali", callback_data=f"otp_choose_server:{server}:{service}:{country}")])

    await query.edit_message_text(
        "💰 <b>PILIH HARGA / STOCK</b>\n\n"
        f"{country_flag(display_country)} Negara: <b>{escape(display_country)}</b>\n"
        f"📡 Operator: <b>{escape('Semua Operator (Acak)' if operator == 'any' else operator)}</b>\n"
        f"📱 Layanan: <b>{escape(str(service_label))}</b>\n\n"
        "Harga diurutkan dari yang paling rendah.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def show_server_choice_page(query, user_id, service, country, source_server="5sim"):
    """Show price/server choices only from the server the user selected.

    Server 1 is isolated to 5SIM. Server 2 is isolated to RumahOTP.
    We never mix provider offers here; this keeps the selected server context
    intact while still sorting its own offers from cheapest to most expensive.
    """
    if source_server not in OTP_SERVERS:
        source_server = "5sim"

    service_label = (
        rumah_service_label(service)
        if source_server == "rumahotp"
        else dict(OTP_SERVICES).get(service, str(service).title())
    )
    display_country = str(country)

    keyboard = []
    available = 0

    if source_server == "5sim":
        server_service = canonical_5sim_service(service)
        try:
            offers = await asyncio.to_thread(get_price_options, country, server_service)
        except Exception:
            logger.exception("5SIM offer lookup failed")
            offers = []

        # get_price_options already merges equal provider prices across all
        # 5SIM operators and retains the operator list for checkout.
        offers = sorted(offers or [], key=lambda item: float(item.get("cost") or 0))

        for option in offers:
            stock = int(option.get("stock") or 0)
            if stock <= 0:
                continue
            cost_usd = float(option.get("cost") or 0)
            sell_price = hitung_harga_jual(cost_usd)
            operators = [str(x).strip() for x in (option.get("operators") or []) if str(x).strip()]
            quote_id = "5Q-" + uuid.uuid4().hex[:12].upper()
            save_otp_quote(
                quote_id=quote_id,
                telegram_id=user_id,
                provider="5sim",
                country=country,
                country_name=display_country,
                service=server_service,
                operator=operators[0] if operators else "any",
                pool=json.dumps({"operators": operators}, separators=(",", ":")),
                cost_usd=cost_usd,
                stock=stock,
            )
            operator_label = ""
            if len(operators) == 1:
                operator_label = f" • 👤 {operators[0]}"
            elif operators:
                operator_label = f" • 👤 {len(operators)} operator"
            label = (
                f"💰 {format_rupiah(sell_price)} • 📦 {stock}{operator_label}\n"
                "⚡ Server 1"
            )
            keyboard.append([InlineKeyboardButton(label, callback_data=f"otp_quote:{quote_id}")])
            available += 1

    elif source_server == "rumahotp":
        try:
            quotes = await asyncio.to_thread(get_rumahotp_quotes_for_country, country, service)
        except Exception:
            logger.exception("RumahOTP offer lookup failed")
            quotes = []

        # Group by the displayed selling price. Stock from equal selling-price
        # tiers is merged, while all underlying routes are retained in the
        # quote pool for checkout.
        grouped = {}
        for q in quotes or []:
            try:
                cost_idr = float(q.get("cost_idr") or q.get("price_idr") or 0)
                stock = int(q.get("stock") or 0)
            except Exception:
                continue
            if cost_idr <= 0 or stock <= 0:
                continue
            sell_price = int(round(cost_idr * (1 + PROFIT_PERCENT / 100) / 100) * 100)
            group = grouped.setdefault(sell_price, {"cost_idr": cost_idr, "stock": 0, "quotes": []})
            group["stock"] += stock
            group["quotes"].append(q)
            group["cost_idr"] = min(group["cost_idr"], cost_idr)

        for sell_price in sorted(grouped):
            group = grouped[sell_price]
            quotes_for_price = group["quotes"]
            q = min(quotes_for_price, key=lambda item: float(item.get("cost_idr") or item.get("price_idr") or 0))
            stock = int(group["stock"])
            cost_idr = float(group["cost_idr"])
            routes = []
            operators = []
            for route in quotes_for_price:
                try:
                    meta = json.loads(route.get("pool") or "{}")
                except Exception:
                    meta = {}
                if meta:
                    routes.append(meta)
                op = str(route.get("provider_operator") or route.get("operator") or "").strip()
                if op and op.lower() not in {"any", "-"} and op not in operators:
                    operators.append(op)

            quote_id = "2Q-" + uuid.uuid4().hex[:12].upper()
            save_otp_quote(
                quote_id=quote_id,
                telegram_id=user_id,
                provider="rumahotp",
                country=str(q.get("country") or country),
                country_name=str(q.get("country_name") or display_country),
                service=str(q.get("service") or service),
                operator=operators[0] if operators else "any",
                pool=json.dumps({"routes": routes}, separators=(",", ":")),
                cost_usd=cost_idr / float(KURS_DOLAR),
                stock=stock,
            )
            operator_label = ""
            if operators:
                operator_label = " • 👤 " + "/".join(operators[:5])
                if len(operators) > 5:
                    operator_label += f" +{len(operators)-5}"
            label = (
                f"💰 {format_rupiah(sell_price)} • 📦 {stock}{operator_label}\n"
                "⚡ Server 2"
            )
            keyboard.append([InlineKeyboardButton(label, callback_data=f"otp_quote:{quote_id}")])
            available += 1

    if not available:
        keyboard.append([InlineKeyboardButton(
            "🔄 Refresh",
            callback_data=f"otp_choose_server:{source_server}:{service}:{country}"
        )])

    keyboard.append([
        InlineKeyboardButton(
            "⬅️ Pilih Negara",
            callback_data=f"otp_service_countries:{source_server}:{service}:0"
        ),
        InlineKeyboardButton("🏠 Menu Utama", callback_data="user_home"),
    ])

    await query.edit_message_text(
        "💰 <b>PILIH HARGA / SERVER</b>\n\n"
        f"{country_flag(display_country)} Negara: <b>{display_country}</b>\n"
        f"📱 Layanan: <b>{service_label}</b>\n\n"
        "Harga diurutkan dari yang termurah. Pilih untuk melanjutkan pembelian.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def show_rumahotp_operator_page(query, user_id, quote):
    """Show real RumahOTP mobile operators for the selected price tier."""
    service = str(quote.get("service") or "")
    country = str(quote.get("country") or "")
    country_name = str(quote.get("country_name") or country)
    base_cost_idr = float(quote.get("cost_usd") or 0) * float(KURS_DOLAR)
    target_sell = int(round(base_cost_idr * (1 + PROFIT_PERCENT / 100) / 100) * 100)

    try:
        operator_quotes = await asyncio.wait_for(
            asyncio.to_thread(get_rumahotp_operator_quotes, country, service),
            timeout=20,
        )
    except Exception:
        logger.exception("RumahOTP operator lookup failed: service=%s country=%s", service, country)
        operator_quotes = []

    # Keep only operator routes belonging to the selected displayed price.
    matching = []
    seen = set()
    for item in operator_quotes or []:
        try:
            cost_idr = float(item.get("cost_idr") or item.get("price_idr") or 0)
        except Exception:
            continue
        if cost_idr <= 0:
            continue
        sell = int(round(cost_idr * (1 + PROFIT_PERCENT / 100) / 100) * 100)
        name = str(item.get("provider_operator") or item.get("operator") or "").strip()
        if sell != target_sell or not name or name.lower() in {"any", "all", "auto", "automatic", "-"}:
            continue
        key = (name.lower(), str(item.get("provider_id") or ""), str(item.get("pool") or ""))
        if key in seen:
            continue
        seen.add(key)
        matching.append(item)

    matching.sort(key=lambda x: (str(x.get("provider_operator") or x.get("operator") or "").lower(), float(x.get("cost_idr") or 0)))

    keyboard = []
    for idx, item in enumerate(matching):
        name = str(item.get("provider_operator") or item.get("operator") or "Operator").strip()
        keyboard.append([InlineKeyboardButton(
            f"👤 {name}",
            callback_data=f"otp_roperator:{quote['quote_id']}:{idx}",
        )])

    if not matching:
        # If the operator endpoint is unavailable, keep the original quote
        # usable instead of blocking checkout completely.
        keyboard.append([InlineKeyboardButton(
            "🎲 Semua Operator / Acak",
            callback_data=f"otp_quote:{quote['quote_id']}",
        )])

    keyboard.append([
        InlineKeyboardButton("⬅️ Pilih Harga", callback_data=f"otp_quote_back:{quote['quote_id']}"),
        InlineKeyboardButton("🏠 Menu Utama", callback_data="user_home"),
    ])

    await query.edit_message_text(
        "📡 <b>PILIH OPERATOR</b>\n\n"
        f"{country_flag(country_name)} Negara: <b>{escape(country_name)}</b>\n"
        f"📱 Layanan: <b>{escape(rumah_service_label(service))}</b>\n"
        f"💰 Harga: <b>{format_rupiah(target_sell)}</b>\n\n"
        "Pilih operator yang ingin digunakan:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def show_rumahotp_quote_page(query, service, country, page=0):
    service_label = rumah_service_label(service)
    try:
        quotes = await asyncio.wait_for(
            asyncio.to_thread(get_rumahotp_quotes_for_country, country, service),
            timeout=20
        )
    except Exception:
        logger.exception("RumahOTP quote error: service=%s country=%s", service, country)
        quotes = []

    quotes = [q for q in quotes if float(q.get("cost_idr") or 0) > 0]
    # Always sort by the actual displayed selling price, cheapest first.
    quotes.sort(key=lambda q: (
        float(q.get("cost_idr") or 0) * (1 + PROFIT_PERCENT / 100),
        str(q.get("provider_id") or ""),
        str(q.get("server_id") or ""),
    ))

    if not quotes:
        await query.edit_message_text(
            "❌ <b>Harga tidak tersedia.</b>\n\n"
            f"📱 Layanan: <b>{service_label}</b>\n"
            f"🇮🇩 Negara: <b>{country}</b>\n\n"
            "Belum ada daftar harga untuk pilihan ini.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Refresh", callback_data=f"otp_rquotes:rumahotp:{service}:{country}:0")],
                [InlineKeyboardButton("⬅️ Pilih Negara", callback_data=f"otp_service:{'rumahotp'}:{service}")]
            ])
        )
        return

    total_pages = max(1, (len(quotes) + COUNTRY_QUOTES_PER_PAGE - 1) // COUNTRY_QUOTES_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    page_items = quotes[page * COUNTRY_QUOTES_PER_PAGE:(page + 1) * COUNTRY_QUOTES_PER_PAGE]
    keyboard = []

    for q in page_items:
        quote_id = "ROQ-" + uuid.uuid4().hex[:12].upper()
        save_otp_quote(
            quote_id=quote_id,
            telegram_id=query.from_user.id,
            provider="rumahotp",
            country=str(q.get("country") or country),
            country_name=str(q.get("country_name") or country),
            service=str(q.get("service") or service),
            operator=str(q.get("provider_operator") or "any"),
            pool=str(q.get("pool") or ""),
            cost_usd=float(q.get("cost_usd") or 0),
            stock=int(q.get("stock") or 0)
        )
        cost_idr = float(q.get("cost_idr") or q.get("price_idr") or 0)
        sell_price = int(round(cost_idr * (1 + PROFIT_PERCENT / 100) / 100) * 100)
        stock = int(q.get("stock") or 0)
        server_id = str(q.get("server_id") or "2")
        provider_id = str(q.get("provider_id") or "-")
        if stock > 0:
            status = f"📦 Stock: {stock}"
        else:
            status = "❌ Produk/stock di harga ini sedang tidak ada"
        # Keep the server/ID information together, then put the live stock
        # directly underneath it as requested.
        label = (
            f"🖥 Server {server_id}.0 • ID {provider_id}\n"
            f"{status}\n"
            f"💰 {format_rupiah(sell_price)}"
        )
        keyboard.append([InlineKeyboardButton(label, callback_data=f"otp_quote:{quote_id}")])

    if total_pages > 1:
        keyboard.append([
            InlineKeyboardButton("◀️", callback_data=f"otp_rquotes:rumahotp:{service}:{country}:{max(0,page-1)}"),
            InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="otp_noop"),
            InlineKeyboardButton("▶️", callback_data=f"otp_rquotes:rumahotp:{service}:{country}:{min(total_pages-1,page+1)}")
        ])
    keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data=f"otp_rquotes:rumahotp:{service}:{country}:0")])
    keyboard.append([
        InlineKeyboardButton("⬅️ Pilih Negara", callback_data=f"otp_service:rumahotp:{service}"),
        InlineKeyboardButton("🏠 Menu Utama", callback_data="user_home")
    ])

    country_name = str(quotes[0].get("country_name") or country)
    flag = country_flag(quotes[0].get("iso_code") or country_name)
    await query.edit_message_text(
        "💰 <b>PILIH HARGA / SERVER</b>\n\n"
        f"{flag} Negara: <b>{country_name}</b>\n"
        f"📱 Layanan: <b>{service_label}</b>\n\n"
        "Semua pilihan harga yang tersedia ditampilkan. Harga bot sudah termasuk margin reseller.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_country_page(
    query,
    page=0
):

    countries = await asyncio.to_thread(
        get_all_countries
    )

    if not countries:

        await query.edit_message_text(

            "❌ <b>Server 1 tidak dapat "
            "dihubungi.</b>\n\n"
            "Silakan coba lagi.",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup([

                [
                    InlineKeyboardButton(
                        "🏠 Menu Utama",
                        callback_data="user_home"
                    )
                ]

            ])

        )

        return

    items = []

    for country_code, country_data in (
        countries.items()
    ):

        if isinstance(
            country_data,
            dict
        ):

            name = country_data.get(
                "text_en",
                country_code
            )

        else:

            name = str(
                country_data
            )

        items.append(
            (
                country_code,
                name
            )
        )

    # -----------------------------------------------------
    # PRIORITASKAN INDONESIA
    # -----------------------------------------------------

    indonesia = []

    others = []

    for item in items:

        code = item[0].lower()

        name = item[1].lower()

        if (
            code == "indonesia"
            or
            name == "indonesia"
        ):

            indonesia.append(
                item
            )

        else:

            others.append(
                item
            )

    others.sort(
        key=lambda x:
            x[1].lower()
    )

    items = indonesia + others

    total_pages = (
        len(items)
        +
        COUNTRIES_PER_PAGE
        - 1
    ) // COUNTRIES_PER_PAGE

    if page < 0:

        page = 0

    if page >= total_pages:

        page = total_pages - 1

    start_index = (
        page *
        COUNTRIES_PER_PAGE
    )

    end_index = (
        start_index +
        COUNTRIES_PER_PAGE
    )

    page_items = items[
        start_index:end_index
    ]

    keyboard = []

    for country_code, name in page_items:

        keyboard.append([

            InlineKeyboardButton(

                f"{country_flag(name)} {name}",

                callback_data=(
                    f"otp_country:"
                    f"{country_code}"
                )

            )

        ])

    navigation = []

    if page > 0:

        navigation.append(

            InlineKeyboardButton(
                "⬅️ Sebelumnya",
                callback_data=(
                    f"order_page:"
                    f"{page - 1}"
                )
            )

        )

    if page < total_pages - 1:

        navigation.append(

            InlineKeyboardButton(
                "Berikutnya ➡️",
                callback_data=(
                    f"order_page:"
                    f"{page + 1}"
                )
            )

        )

    if navigation:

        keyboard.append(
            navigation
        )

    keyboard.append([
        InlineKeyboardButton("🔎 Cari Negara", callback_data="otp_country_search:legacy:")
    ])

    keyboard.append([

        InlineKeyboardButton(
            "🏠 Menu Utama",
            callback_data="user_home"
        )

    ])

    await query.edit_message_text(

        f"📱 <b>ORDER OTP</b>\n\n"
        f"🌎 Pilih negara nomor.\n\n"
        f"Halaman <b>{page + 1}</b> "
        f"dari <b>{total_pages}</b>\n"
        f"Total negara: <b>{len(items)}</b>\n\n"
        f"🇮🇩 Indonesia diprioritaskan "
        f"di halaman pertama.",

        parse_mode="HTML",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        )

    )


# =========================================================
# TAMPILKAN PRODUCT / OTP
# =========================================================

async def show_product_page(
    query,
    country,
    page=0
):

    products = await asyncio.to_thread(
        get_products,
        country,
        "any"
    )

    if not products:

        await query.edit_message_text(

            "❌ <b>Tidak ada layanan.</b>\n\n"
            "Server tidak mengembalikan "
            "produk untuk negara ini.",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup([

                [
                    InlineKeyboardButton(
                        "⬅️ Pilih Negara",
                        callback_data="order"
                    )
                ]

            ])

        )

        return

    items = []

    for product, info in products.items():

        if not isinstance(
            info,
            dict
        ):

            continue

        category = str(
            info.get(
                "Category",
                ""
            )
        ).lower()

        # -------------------------------------------------
        # HANYA OTP / ACTIVATION
        # -------------------------------------------------

        if category != "activation":

            continue

        try:

            qty = int(
                info.get(
                    "Qty",
                    0
                ) or 0
            )

            cost = float(
                info.get(
                    "Price",
                    0
                ) or 0
            )

        except Exception:

            continue

        # -------------------------------------------------
        # STOCK 0 JANGAN DITAMPILKAN
        # -------------------------------------------------

        if qty <= 0:

            continue

        if cost <= 0:

            continue

        sell_price = hitung_harga_jual(
            cost
        )

        if sell_price <= 0:

            continue

        items.append({

            "product":
                str(product),

            "qty":
                qty,

            "cost":
                cost,

            "sell_price":
                sell_price

        })

    # -----------------------------------------------------
    # SORT STOCK TERBANYAK
    # -----------------------------------------------------

    items.sort(

        key=lambda item: (
            -item["qty"],
            item["product"].lower()
        )

    )

    if not items:

        await query.edit_message_text(

            f"{country_flag(country)} <b>{country}</b>\n\n"
            "❌ Tidak ada stok OTP "
            "yang tersedia saat ini.",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup([

                [
                    InlineKeyboardButton(
                        "🔄 Refresh",
                        callback_data=(
                            f"otp_country:{country}"
                        )
                    )
                ],

                [
                    InlineKeyboardButton(
                        "⬅️ Pilih Negara",
                        callback_data="order"
                    )
                ]

            ])

        )

        return

    total_pages = (

        len(items)
        +
        PRODUCTS_PER_PAGE
        - 1

    ) // PRODUCTS_PER_PAGE

    if page < 0:

        page = 0

    if page >= total_pages:

        page = total_pages - 1

    start_index = (
        page *
        PRODUCTS_PER_PAGE
    )

    end_index = (
        start_index +
        PRODUCTS_PER_PAGE
    )

    page_items = items[
        start_index:end_index
    ]

    keyboard = []

    for item in page_items:

        product = item[
            "product"
        ]

        qty = item[
            "qty"
        ]

        sell_price = item[
            "sell_price"
        ]

        keyboard.append([

            InlineKeyboardButton(

                (
                    f"📱 {product}\n"
                    f"💰 {format_rupiah(sell_price)}"
                    f"  |  📦 Stock: {qty}"
                ),

                callback_data=(
                    f"otp_product:"
                    f"{country}:"
                    f"{product}"
                )

            )

        ])

    navigation = []

    if page > 0:

        navigation.append(

            InlineKeyboardButton(

                "⬅️ Sebelumnya",

                callback_data=(
                    f"otp_products:"
                    f"{country}:"
                    f"{page - 1}"
                )

            )

        )

    if page < total_pages - 1:

        navigation.append(

            InlineKeyboardButton(

                "Berikutnya ➡️",

                callback_data=(
                    f"otp_products:"
                    f"{country}:"
                    f"{page + 1}"
                )

            )

        )

    if navigation:

        keyboard.append(
            navigation
        )

    keyboard.append([

        InlineKeyboardButton(
            "🔄 Refresh Stock",
            callback_data=(
                f"otp_country:"
                f"{country}"
            )
        )

    ])

    keyboard.append([

        InlineKeyboardButton(
            "⬅️ Pilih Negara",
            callback_data="order"
        )

    ])

    keyboard.append([

        InlineKeyboardButton(
            "🏠 Menu Utama",
            callback_data="user_home"
        )

    ])

    await query.edit_message_text(

        f"{country_flag(country)} Negara: <b>{country}</b>\n\n"

        f"📱 <b>Pilih layanan OTP</b>\n\n"

        f"📦 Menampilkan hanya layanan "
        f"yang memiliki stok.\n"

        f"💰 Harga sudah termasuk margin.\n\n"

        f"Halaman <b>{page + 1}</b> "
        f"dari <b>{total_pages}</b>\n"

        f"Total layanan tersedia: "
        f"<b>{len(items)}</b>",

        parse_mode="HTML",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        )

    )


# =========================================================
# COMMAND MENU / CHECK-IN
# =========================================================

BOT_COMMANDS = [
    ("start", "Menu Utama Bot"),
    ("server1", "List Layanan Server1"),
    ("server2", "List Layanan Server2"),
    ("deposit", "Menu Deposit"),
    ("checkin", "Saldo Gratis"),
]


def _service_keyboard_sync(services, server, page=0):
    total_pages = max(1, (len(services) + SERVICES_PER_PAGE - 1) // SERVICES_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    page_items = services[page * SERVICES_PER_PAGE:(page + 1) * SERVICES_PER_PAGE]
    keyboard = []
    for i in range(0, len(page_items), 2):
        row = []
        for code, label in page_items[i:i + 2]:
            row.append(InlineKeyboardButton(label, callback_data=f"otp_service:{server}:{code}"))
        keyboard.append(row)
    keyboard.append([
        InlineKeyboardButton("◀️", callback_data=f"otp_services:{server}:{max(0, page-1)}"),
        InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="otp_noop"),
        InlineKeyboardButton("▶️", callback_data=f"otp_services:{server}:{min(total_pages-1, page+1)}"),
    ])
    keyboard.append([
        InlineKeyboardButton("🔎 Search", callback_data=f"otp_search:{server}"),
        InlineKeyboardButton("↩️ Server", callback_data="order"),
    ])
    return InlineKeyboardMarkup(keyboard)


async def command_server(update, context, server):
    user = update.effective_user
    create_user(user.id, user.username, user.first_name)
    try:
        services = await asyncio.wait_for(
            asyncio.to_thread(get_service_catalog, server), timeout=15
        )
    except Exception:
        services = list(OTP_SERVICES)
    await update.message.reply_text(
        "🖥 <b>PILIH LAYANAN OTP</b>\n\n"
        "Pilih layanan OTP:",
        parse_mode="HTML",
        reply_markup=_service_keyboard_sync(services, server, 0),
    )


async def command_deposit(update, context):
    context.chat_data["waiting_deposit"] = True
    await update.message.reply_text(
        "💳 <b>Deposit Saldo</b>\n\n"
        "Masukkan nominal deposit.\n\n"
        "Minimum: <b>Rp1.000</b>\n"
        "Kelipatan: <b>Rp1.000</b>\n\n"
        "🎁 <b>Bonus deposit:</b> setiap deposit <b>Rp100.000 atau lebih</b> mendapat bonus saldo <b>10%</b> secara otomatis.\n\n"
        "Contoh: <code>10000</code> atau <code>100000</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Batal", callback_data="cancel_deposit")]]),
    )


def _format_remaining(seconds):
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours} jam {minutes} menit"


async def perform_checkin(update, context):
    user = update.effective_user
    create_user(user.id, user.username, user.first_name)
    status = get_checkin_status(user.id)
    total_deposit = int(status.get("total_success_deposit") or 0)
    if not status.get("has_min_deposit"):
        text = (
            "🎁 <b>SALDO GRATIS</b>\n\n"
            "Kamu belum memenuhi syarat check-in.\n\n"
            "💳 Minimal salah satu deposit berhasil: <b>Rp10.000</b>\n"
            f"📊 Total deposit kamu: <b>{format_rupiah(total_deposit)}</b>\n\n"
            "Setelah memenuhi minimal deposit, kamu bisa check-in setiap 24 jam."
        )
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("💳 Deposit", callback_data="user_deposit")]])
    else:
        reward = random.randint(50, 110) if not status.get("last_checkin_at") else random.randint(50, 100)
        result = await asyncio.to_thread(claim_checkin, user.id, reward)
        if result.get("ok"):
            text = (
                "🎉 <b>CHECK-IN BERHASIL!</b>\n\n"
                f"🎁 Saldo gratis: <b>{format_rupiah(result['reward'])}</b>\n"
                f"💰 Saldo sekarang: <b>{format_rupiah(result['balance'])}</b>\n\n"
                "⏰ Check-in berikutnya tersedia <b>24 jam</b> setelah check-in ini."
            )
        elif result.get("reason") == "COOLDOWN":
            text = (
                "⏳ <b>CHECK-IN BELUM TERSEDIA</b>\n\n"
                f"Coba lagi dalam sekitar <b>{_format_remaining(result['remaining_seconds'])}</b>.\n\n"
                "Timer dihitung 24 jam sejak check-in terakhir."
            )
        else:
            text = "❌ Check-in belum bisa diproses. Silakan coba lagi."
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu Utama", callback_data="user_home")]])
    if isinstance(update, Update):
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await update.edit_message_text(text, parse_mode="HTML", reply_markup=markup)


async def post_init(application):
    await application.bot.set_my_commands([
        __import__('telegram').BotCommand(command, description)
        for command, description in BOT_COMMANDS
    ])
    application.bot_data["auto_expire_task"] = asyncio.create_task(auto_expire_worker(application))
    application.bot_data["rumahotp_cancel_task"] = asyncio.create_task(rumahotp_cancel_worker(application))


async def post_shutdown(application):
    task = application.bot_data.pop("auto_expire_task", None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    cancel_task = application.bot_data.pop("rumahotp_cancel_task", None)
    if cancel_task:
        cancel_task.cancel()
        try:
            await cancel_task
        except asyncio.CancelledError:
            pass


# =========================================================
# USER CALLBACK
# =========================================================


async def process_otp_order(
    query,
    user_id,
    context,
    server,
    country,
    service,
    quote=None
):
    """Order OTP dari provider terpilih dengan margin sesuai PROFIT_PERCENT (default 7%)."""

    service_label = (rumah_service_label(service) if server == "rumahotp" else dict(OTP_SERVICES).get(service, service))
    display_country = country

    # -----------------------------------------------------
    # AMBIL HARGA/STOK TERKINI
    # -----------------------------------------------------
    if quote is not None:
        server = quote["provider"]
        country = quote["country"]
        display_country = quote.get("country_name") or country
        service = quote["service"]
        operator = quote.get("provider_operator") or quote.get("operator") or "any"
        provider_cost_usd = float(quote["cost_usd"])
        quote_operators = []
        if server == "5sim":
            try:
                meta = json.loads(quote.get("pool") or "{}")
                quote_operators = meta.get("operators") or []
            except Exception:
                quote_operators = []
    elif server == "5sim":
        operator_info = await asyncio.to_thread(
            get_cheapest_operator,
            country,
            service
        )
        if not operator_info:
            await query.edit_message_text(
                "❌ <b>Stok habis.</b>\n\n"
                f"{country_flag(display_country)} Negara: <b>{display_country}</b>\n"
                f"📱 Layanan: <b>{service_label}</b>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🔄 Refresh",
                            callback_data=(
                                f"otp_service:{server}:{service}"
                            )
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "⬅️ Pilih Layanan",
                            callback_data=f"otp_server:{server}"
                        )
                    ]
                ])
            )
            return

        operator = operator_info["operator"]
        provider_cost_usd = float(operator_info["cost"])

    elif server == "rumahotp":
        quote = await asyncio.to_thread(
            get_rumahotp_cheapest_quote,
            country,
            service
        )
        if not quote:
            await query.edit_message_text(
                "❌ <b>Harga/stok Server 2 tidak tersedia.</b>\n\n"
                f"{country_flag(display_country)} Negara: <b>{display_country}</b>\n"
                f"📱 Layanan: <b>{service_label}</b>\n\n"
                "Silakan refresh atau pilih negara lain.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Refresh", callback_data=f"otp_service:{server}:{service}")],
                    [InlineKeyboardButton("⬅️ Pilih Layanan", callback_data=f"otp_server:{server}")]
                ])
            )
            return
        country = quote.get("country") or country
        display_country = quote.get("country_name") or display_country
        operator = quote.get("provider_operator") or "any"
        provider_cost_usd = float(quote.get("cost_usd") or 0)
    else:
        await query.answer("Server tidak tersedia.", show_alert=True)
        return

    sell_price = hitung_harga_jual(provider_cost_usd)
    current_balance = get_balance(user_id)

    if current_balance < sell_price:
        await query.edit_message_text(
            "❌ <b>Saldo tidak cukup.</b>\n\n"
            f"{country_flag(display_country)} Negara: <b>{display_country}</b>\n"
            f"📱 Layanan: <b>{service_label}</b>\n\n"
            f"💰 Harga: <b>{format_rupiah(sell_price)}</b>\n"
            f"💳 Saldo: <b>{format_rupiah(current_balance)}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "💳 Deposit",
                        callback_data="user_deposit"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Kembali",
                        callback_data=(
                            f"otp_service:{server}:{service}"
                        )
                    )
                ]
            ])
        )
        return

    await query.edit_message_text(
        "⏳ <b>Memproses order...</b>\n\n"
        f"{country_flag(display_country)} Negara: <b>{display_country}</b>\n"
        f"📱 Layanan: <b>{service_label}</b>\n"
        f"💰 Harga: <b>{format_rupiah(sell_price)}</b>",
        parse_mode="HTML"
    )

    order_id = (
        "OTP-" + uuid.uuid4().hex[:12].upper()
    )

    try:
        balance_after = create_pending_order(
            telegram_id=user_id,
            order_id=order_id,
            country=country,
            service=service,
            sell_price=sell_price,
            provider=server
        )
    except ValueError as error:
        await query.edit_message_text(
            f"❌ <b>Order gagal.</b>\n\n{error}",
            parse_mode="HTML"
        )
        return

    # Keep human-readable service/country in history without changing provider codes.
    await asyncio.to_thread(
        _save_order_labels, order_id, service_label, display_country
    )

    # -----------------------------------------------------
    # BELI NOMOR
    # -----------------------------------------------------
    if server == "5sim":
        result = await asyncio.to_thread(
            buy_number_any_operator,
            country,
            service,
            quote_operators or [operator],
        )
        provider_order_id = result.get("id") if result else None
        phone = result.get("phone") if result else None
        provider_expired_at = result.get("expired_at") if result else None
        provider_error = not result or result.get("response") == "ERROR"
        error_reason = "Pembelian nomor Server 1 gagal."
    elif server == "rumahotp":
        routes = []
        if quote and quote.get("pool"):
            try:
                meta = json.loads(quote.get("pool") or "{}")
                routes = meta.get("routes") or []
                if not routes and isinstance(meta, dict):
                    routes = [meta]
            except Exception:
                routes = []
        if not routes:
            routes = [None]
        result = None
        for metadata in routes:
            candidate = await asyncio.to_thread(
                buy_rumahotp_number,
                country,
                service,
                operator,
                metadata
            )
            if candidate and candidate.get("response") != "ERROR" and (candidate.get("order_id") or candidate.get("id")) and (candidate.get("phone") or candidate.get("number")):
                result = candidate
                break
            result = candidate
        provider_order_id = (
            result.get("order_id") or result.get("id") if result else None
        )
        phone = (
            result.get("phone") or result.get("number")
            if result else None
        )
        provider_expired_at = result.get("expired_at") if result else None
        provider_error = (
            not result or result.get("response") == "ERROR"
        )
        error_reason = "Pembelian nomor Server 2 gagal."
    else:
        provider_expired_at = None
        provider_error = True
        provider_order_id = None
        phone = None
        error_reason = "Server OTP tidak tersedia."

    if provider_error:
        try:
            refund = refund_order(
                order_id,
                error_reason
            )
        except Exception as error:
            logger.exception("Refund gagal")
            await query.edit_message_text(
                "⚠️ <b>Server gagal dan refund "
                "otomatis mengalami masalah.</b>\n\n"
                f"Order: <code>{order_id}</code>\n"
                f"Error: <code>{error}</code>",
                parse_mode="HTML"
            )
            return

        await query.edit_message_text(
            "❌ <b>Nomor tidak tersedia.</b>\n\n"
            f"🧾 Order: <code>{order_id}</code>\n"
            f"💸 Refund: <b>{format_rupiah(sell_price)}</b>\n"
            f"💰 Saldo: <b>{format_rupiah(refund['balance'])}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔄 Order Lagi",
                        callback_data="order"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🏠 Menu Utama",
                        callback_data="user_home"
                    )
                ]
            ])
        )
        return

    if not provider_order_id or not phone:
        refund_order(
            order_id,
            f"Respons {server} tidak lengkap."
        )
        await query.edit_message_text(
            "❌ <b>Respons server tidak valid.</b>\n\n"
            "Saldo sudah dikembalikan.",
            parse_mode="HTML"
        )
        return

    # RumahOTP returns an exact expiry timestamp. 5SIM does not expose one
    # in the response used here, so use the same 20-minute activation window
    # as a local safety timer.
    if not provider_expired_at:
        provider_expired_at = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()

    provider_cost_rp = int(
        round(provider_cost_usd * KURS_DOLAR)
    )

    # Provider may already have charged the order. Do not let a database write
    # block the Telegram callback after a successful provider purchase.
    # Save provider data without allowing a slow/locked database connection to
    # keep the Telegram order callback stuck after RumahOTP has already charged
    # and issued the number. The worker is allowed to finish in the background.
    _RUNTIME_PROVIDER_CACHE[order_id] = {
        "provider_order_id": provider_order_id,
        "phone": phone,
        "expired_at": provider_expired_at,
    }
    save_task = asyncio.create_task(
        asyncio.to_thread(
            save_provider_order,
            order_id,
            provider_order_id,
            provider_cost_rp,
            phone,
            provider_expired_at
        )
    )
    try:
        await asyncio.wait_for(asyncio.shield(save_task), timeout=5.0)
        _RUNTIME_PROVIDER_CACHE.pop(order_id, None)
        logger.info("[ORDER FLOW] provider data saved order_id=%s", order_id)
    except asyncio.TimeoutError:
        logger.warning(
            "[ORDER FLOW] database save slow after provider success; "
            "continuing with runtime cache order_id=%s provider_order_id=%s",
            order_id, provider_order_id
        )
    except Exception:
        logger.exception("[ORDER FLOW] provider data save failed order_id=%s", order_id)

    await query.edit_message_text(
        "✅ <b>ORDER BERHASIL</b>\n\n"
        f"🧾 Order: <code>{order_id}</code>\n"
        f"{country_flag(display_country)} Negara: <b>{display_country}</b>\n"
        f"📱 Layanan: <b>{service_label}</b>\n\n"
        f"📞 Nomor:\n<code>{phone}</code>\n\n"
        f"💰 Harga: <b>{format_rupiah(sell_price)}</b>\n"
        f"💳 Sisa saldo: <b>{format_rupiah(balance_after)}</b>\n\n"
        + _user_timing_text({"created_at": now(), "expired_at": provider_expired_at})
        + "\n\n⏳ <b>Menunggu SMS OTP...</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔄 Cek OTP",
                    callback_data=f"otp_check:{order_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "🔁 Resend OTP",
                    callback_data=f"otp_resend:{order_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ Batal / Refund",
                    callback_data=f"otp_cancel:{order_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "🏠 Menu Utama",
                    callback_data="user_home"
                )
            ]
        ])
    )


def _save_order_labels(order_id, service_name, country_name):
    """Persist human-readable history labels without blocking Telegram's event loop."""
    with get_db() as db:
        db.execute(
            "UPDATE orders SET service_name=%s, country_name=%s WHERE order_id=%s",
            (service_name, country_name, order_id)
        )


async def user_callback(
    query,
    user_id,
    context
):

    data = query.data

    # =====================================================
    # CARA
    # =====================================================

    if data == "cara":

        text = """📁 <b>PANDUAN PENGGUNAAN BOT</b>

1️⃣ <b>Deposit</b>
Isi saldo terlebih dahulu melalui menu <b>Deposit</b>.

2️⃣ <b>Order OTP</b>
Pilih salah satu dari 2 server OTP yang tersedia.

3️⃣ <b>Pilih Server</b>
├ Server 1
└ Server 2

4️⃣ <b>Pilih layanan</b>
Bot menampilkan layanan OTP seperti WhatsApp, Telegram, Shopee, TikTok, Facebook, Instagram, Google, Vercel, UangMe, Grab, DANA, Gojek, OVO, Any Other, dan lainnya.

5️⃣ <b>Pilih Negara</b>
Pilih negara nomor yang tersedia.

6️⃣ <b>Pilih layanan</b>
Pilih layanan yang memiliki stok.

7️⃣ <b>Gunakan Nomor</b>
Setelah order berhasil, nomor diberikan oleh bot.

8️⃣ <b>Menunggu SMS</b>
Masukkan nomor tersebut ke aplikasi tujuan dan tunggu OTP.

9️⃣ <b>Cek OTP</b>
Tekan tombol <b>🔄 Cek OTP</b> sampai SMS masuk.

🔟 <b>Refund</b>
Jika OTP tidak masuk, tekan <b>❌ Batal / Refund</b>."""

        await query.edit_message_text(

            text,

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup([

                [
                    InlineKeyboardButton(
                        "⬅️ Kembali",
                        callback_data="user_home"
                    )
                ]

            ])

        )

        return

    # =====================================================
    # ORDER
    # =====================================================

    if data == "order":

        await show_server_page(
            query
        )

        return

    # =====================================================
    # PILIH SERVER
    # =====================================================

    if data.startswith(
        "otp_server:"
    ):

        server = data.split(
            ":",
            1
        )[1]

        if server not in OTP_SERVERS:

            await query.answer(
                "Server tidak valid.",
                show_alert=True
            )

            return

        await show_service_page(

            query,

            server,

            0

        )

        return

    # =====================================================
    # SERVICE PAGE
    # =====================================================

    if data.startswith(
        "otp_services:"
    ):

        parts = data.split(
            ":",
            2
        )

        if len(parts) != 3:

            await query.answer(
                "Data layanan tidak valid.",
                show_alert=True
            )

            return

        server = parts[1]

        try:

            page = int(
                parts[2]
            )

        except Exception:

            page = 0

        await show_service_page(

            query,

            server,

            page

        )

        return

    # =====================================================
    # PILIH SERVICE
    # =====================================================

    if data.startswith(
        "otp_service:"
    ):
        parts = data.split(":", 2)

        if len(parts) != 3:
            await query.answer(
                "Data layanan tidak valid.",
                show_alert=True
            )
            return

        server = parts[1]
        service = parts[2]

        if server not in OTP_SERVERS:
            await query.answer(
                "Server tidak valid.",
                show_alert=True
            )
            return

        context.user_data["otp_server"] = server
        context.user_data["otp_service"] = service

        await show_service_country_page(
            query,
            server,
            service,
            0
        )
        return

    # =====================================================
    # SEARCH NEGARA PER SERVICE
    # =====================================================

    if data == "otp_country_search:legacy:":
        context.user_data["waiting_otp_country_search"] = True
        context.user_data["otp_country_search_server"] = "legacy"
        context.user_data["otp_country_search_service"] = ""
        await query.edit_message_text(
            "🔎 <b>SEARCH NEGARA</b>\n\nKetik nama negara yang ingin dicari.\nContoh: <code>Indonesia</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Kembali", callback_data="order")
            ]])
        )
        return

    if data.startswith("otp_country_search:"):
        parts = data.split(":", 2)
        if len(parts) != 3:
            await query.answer("Data pencarian tidak valid.", show_alert=True)
            return
        server, service = parts[1], parts[2]
        context.user_data["waiting_otp_country_search"] = True
        context.user_data["otp_country_search_server"] = server
        context.user_data["otp_country_search_service"] = service
        await query.edit_message_text(
            "🔎 <b>SEARCH NEGARA</b>\n\n"
            "Ketik nama negara yang ingin dicari.\n"
            "Contoh: <code>Indonesia</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Kembali", callback_data=f"otp_service_countries:{server}:{service}:0")
            ]])
        )
        return

    # =====================================================
    # PAGINATION NEGARA PER SERVICE
    # =====================================================

    if data.startswith(
        "otp_service_countries:"
    ):
        parts = data.split(":", 3)

        if len(parts) != 4:
            await query.answer(
                "Data negara tidak valid.",
                show_alert=True
            )
            return

        server = parts[1]
        service = parts[2]

        try:
            page = int(parts[3])
        except Exception:
            page = 0

        await show_service_country_page(
            query,
            server,
            service,
            page
        )
        return

    # =====================================================
    # SEARCH SERVICE
    # =====================================================

    if data.startswith("otp_search:"):
        server = data.split(":", 1)[1]
        context.user_data["otp_search_server"] = server
        context.user_data["waiting_otp_search"] = True

        await query.edit_message_text(
            "🔎 <b>SEARCH LAYANAN OTP</b>\n\n"
            "Ketik nama layanan yang ingin dicari.\n"
            "Contoh: <code>WhatsApp</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ Kembali",
                        callback_data=f"otp_server:{server}"
                    )
                ]
            ])
        )
        return

    if data == "otp_noop":
        await query.answer()
        return

    # =====================================================
    # PILIH SERVER SETELAH MEMILIH NEGARA
    # =====================================================

    if data.startswith("otp_choose_server:"):
        parts = data.split(":", 3)
        if len(parts) == 4:
            _, source_server, service, country = parts
        elif len(parts) == 3:  # compatibility with older buttons
            _, service, country = parts
            source_server = "5sim"
        else:
            await query.answer("Data pilihan server tidak valid.", show_alert=True)
            return
        # Persist the exact navigation context so Back always returns to
        # the same server/service country page that the user came from.
        context.user_data["otp_server"] = source_server
        context.user_data["otp_service"] = service
        context.user_data["otp_country"] = country

        await show_otp_operator_page(
            query,
            source_server,
            service,
            country,
            0,
        )
        return

    # =====================================================
    # PILIH OPERATOR -> TABEL HARGA/STOK
    # =====================================================
    if data.startswith("otp_operator:"):
        parts = data.split(":", 4)
        if len(parts) != 5:
            await query.answer("Data operator tidak valid.", show_alert=True)
            return
        _, server, service, country, operator = parts
        if server not in OTP_SERVERS:
            await query.answer("Server tidak valid.", show_alert=True)
            return
        context.user_data["otp_server"] = server
        context.user_data["otp_service"] = service
        context.user_data["otp_country"] = country
        await show_otp_price_page(query, user_id, server, service, country, operator, 0)
        return

    # =====================================================
    # PAGINASI OPERATOR
    # =====================================================
    if data.startswith("otp_operators:"):
        parts = data.split(":", 4)
        if len(parts) != 5:
            await query.answer("Data operator tidak valid.", show_alert=True)
            return
        _, server, service, country, page_raw = parts
        try:
            page = int(page_raw)
        except Exception:
            page = 0
        await show_otp_operator_page(query, server, service, country, page)
        return

    # =====================================================
    # PAGINASI HARGA/STOK
    # =====================================================
    if data.startswith("otp_prices:"):
        parts = data.split(":", 5)
        if len(parts) != 6:
            await query.answer("Data harga tidak valid.", show_alert=True)
            return
        _, server, service, country, operator, page_raw = parts
        try:
            page = int(page_raw)
        except Exception:
            page = 0
        await show_otp_price_page(query, user_id, server, service, country, operator, page)
        return

    # =====================================================
    # PILIH HARGA / SERVER RUMAHOTP (kompatibilitas lama)
    # =====================================================

    if data.startswith("otp_rquotes:"):
        parts = data.split(":", 4)
        if len(parts) != 5:
            await query.answer("Data harga tidak valid.", show_alert=True)
            return
        _, server, service, country, page_raw = parts
        try:
            page = int(page_raw)
        except Exception:
            page = 0
        if server != "rumahotp":
            await query.answer("Server tidak valid.", show_alert=True)
            return
        await show_rumahotp_quote_page(query, service, country, page)
        return

    if data.startswith("otp_quote_back:"):
        quote_id = data.split(":", 1)[1].strip()
        quote = get_otp_quote(quote_id, user_id)
        if not quote:
            await query.answer("Harga sudah kedaluwarsa. Silakan pilih ulang.", show_alert=True)
            return
        await show_otp_operator_page(
            query,
            str(quote.get("provider") or "rumahotp"),
            str(quote.get("service") or ""),
            str(quote.get("country") or ""),
            0,
        )
        return

    if data.startswith("otp_roperator:"):
        parts = data.split(":", 2)
        if len(parts) != 3:
            await query.answer("Data operator tidak valid.", show_alert=True)
            return
        base_quote_id = parts[1]
        try:
            operator_index = int(parts[2])
        except Exception:
            await query.answer("Operator tidak valid.", show_alert=True)
            return
        base_quote = get_otp_quote(base_quote_id, user_id)
        if not base_quote:
            await query.answer("Harga sudah kedaluwarsa. Silakan pilih ulang.", show_alert=True)
            return
        try:
            operator_quotes = await asyncio.wait_for(
                asyncio.to_thread(
                    get_rumahotp_operator_quotes,
                    str(base_quote.get("country") or ""),
                    str(base_quote.get("service") or ""),
                ),
                timeout=20,
            )
        except Exception:
            logger.exception("RumahOTP operator lookup failed at selection")
            operator_quotes = []

        base_cost_idr = float(base_quote.get("cost_usd") or 0) * float(KURS_DOLAR)
        target_sell = int(round(base_cost_idr * (1 + PROFIT_PERCENT / 100) / 100) * 100)
        matching = []
        seen = set()
        for item in operator_quotes or []:
            try:
                cost_idr = float(item.get("cost_idr") or item.get("price_idr") or 0)
            except Exception:
                continue
            name = str(item.get("provider_operator") or item.get("operator") or "").strip()
            sell = int(round(cost_idr * (1 + PROFIT_PERCENT / 100) / 100) * 100) if cost_idr > 0 else 0
            key = (name.lower(), str(item.get("provider_id") or ""), str(item.get("pool") or ""))
            if cost_idr <= 0 or sell != target_sell or not name or name.lower() in {"any", "all", "auto", "automatic", "-"} or key in seen:
                continue
            seen.add(key)
            matching.append(item)
        matching.sort(key=lambda x: (str(x.get("provider_operator") or x.get("operator") or "").lower(), float(x.get("cost_idr") or 0)))
        if operator_index < 0 or operator_index >= len(matching):
            await query.answer("Operator sudah berubah. Silakan pilih ulang.", show_alert=True)
            return
        selected = matching[operator_index]
        quote_id = "ROQ-" + uuid.uuid4().hex[:12].upper()
        save_otp_quote(
            quote_id=quote_id,
            telegram_id=user_id,
            provider="rumahotp",
            country=str(selected.get("country") or base_quote.get("country") or ""),
            country_name=str(selected.get("country_name") or base_quote.get("country_name") or ""),
            service=str(selected.get("service") or base_quote.get("service") or ""),
            operator=str(selected.get("provider_operator") or selected.get("operator") or "any"),
            pool=str(selected.get("pool") or ""),
            cost_usd=float(selected.get("cost_usd") or 0),
            stock=int(selected.get("stock") or base_quote.get("stock") or 0),
        )
        final_quote = get_otp_quote(quote_id, user_id)
        context.user_data["otp_server"] = "rumahotp"
        context.user_data["otp_service"] = str(final_quote.get("service") or "")
        context.user_data["otp_country"] = str(final_quote.get("country") or "")
        await process_otp_order(
            query,
            user_id,
            context,
            "rumahotp",
            str(final_quote.get("country") or ""),
            str(final_quote.get("service") or ""),
            quote=final_quote,
        )
        return

    if data.startswith("otp_quote:"):
        quote_id = data.split(":", 1)[1].strip()
        quote = get_otp_quote(quote_id, user_id)
        if not quote:
            await query.answer("Harga sudah kedaluwarsa. Silakan refresh.", show_alert=True)
            return
        if int(quote.get("stock") or 0) <= 0:
            await query.answer("Produk/stock di harga ini sedang tidak ada.", show_alert=True)
            return
        selected_provider = str(quote.get("provider") or "")
        selected_country = str(quote.get("country") or "")
        selected_service = str(quote.get("service") or "")
        # The operator was already selected (or "any") before the price table.
        # Do NOT open another operator page here: clicking a price/stock row
        # must proceed directly to checkout using the quote's saved route pool.
        context.user_data["otp_server"] = selected_provider
        context.user_data["otp_service"] = selected_service
        context.user_data["otp_country"] = selected_country

        await process_otp_order(
            query, user_id, context, selected_provider,
            selected_country,
            selected_service,
            quote=quote
        )
        return

    # =====================================================
    # PILIH NEGARA -> ORDER LANGSUNG
    # =====================================================

    if data.startswith("otp_buy:"):
        parts = data.split(":", 3)

        if len(parts) != 4:
            await query.answer(
                "Data order tidak valid.",
                show_alert=True
            )
            return

        server, service, country = parts[1], parts[2], parts[3]

        if server not in OTP_SERVERS:
            await query.answer(
                "Server tidak valid.",
                show_alert=True
            )
            return

        # Compatibility with old buttons: never purchase directly from a
        # country button; always show the server/price confirmation step.
        await show_server_choice_page(query, user_id, service, country, source_server=server)
        return

    # =====================================================
    # COUNTRY PAGE
    # =====================================================

    if data.startswith(
        "order_page:"
    ):

        try:

            page = int(
                data.split(
                    ":",
                    1
                )[1]
            )

        except Exception:

            page = 0

        await show_country_page(
            query,
            page
        )

        return

    # =====================================================
    # PILIH NEGARA
    # =====================================================

    if data.startswith(
        "otp_country:"
    ):

        country = data.split(
            ":",
            1
        )[1]

        await show_product_page(

            query,

            country,

            0

        )

        return

    # =====================================================
    # PRODUCT PAGE
    # =====================================================

    if data.startswith(
        "otp_products:"
    ):

        parts = data.split(
            ":",
            2
        )

        if len(parts) != 3:

            await query.answer(
                "Data tidak valid.",
                show_alert=True
            )

            return

        country = parts[1]

        try:

            page = int(
                parts[2]
            )

        except Exception:

            page = 0

        await show_product_page(

            query,

            country,

            page

        )

        return

    # =====================================================
    # PILIH PRODUCT
    # =====================================================

    if data.startswith(
        "otp_product:"
    ):

        parts = data.split(
            ":",
            2
        )

        if len(parts) != 3:

            await query.answer(
                "Data order tidak valid.",
                show_alert=True
            )

            return

        country = parts[1]

        product = parts[2]

        # -------------------------------------------------
        # CEK OPERATOR TERMURAH
        # -------------------------------------------------

        operator_info = (
            await asyncio.to_thread(

                get_cheapest_operator,

                country,

                product

            )
        )

        if not operator_info:

            await query.edit_message_text(

                "❌ <b>Stok habis.</b>\n\n"
                "Nomor untuk layanan ini "
                "sedang tidak tersedia.",

                parse_mode="HTML",

                reply_markup=InlineKeyboardMarkup([

                    [
                        InlineKeyboardButton(
                            "🔄 Refresh",
                            callback_data=(
                                f"otp_country:{country}"
                            )
                        )
                    ],

                    [
                        InlineKeyboardButton(
                            "⬅️ Pilih Negara",
                            callback_data="order"
                        )
                    ]

                ])

            )

            return

        operator = operator_info[
            "operator"
        ]

        provider_cost_usd = float(
            operator_info[
                "cost"
            ]
        )

        sell_price = hitung_harga_jual(
            provider_cost_usd
        )

        current_balance = get_balance(
            user_id
        )

        if current_balance < sell_price:

            await query.edit_message_text(

                "❌ <b>Saldo tidak cukup.</b>\n\n"

                f"{country_flag(country)} Negara: "
                f"<b>{country}</b>\n"

                f"📱 Layanan: "
                f"<b>{product}</b>\n\n"

                f"💰 Harga: "
                f"<b>{format_rupiah(sell_price)}</b>\n"

                f"💳 Saldo: "
                f"<b>{format_rupiah(current_balance)}</b>",

                parse_mode="HTML",

                reply_markup=InlineKeyboardMarkup([

                    [
                        InlineKeyboardButton(
                            "💳 Deposit",
                            callback_data="user_deposit"
                        )
                    ],

                    [
                        InlineKeyboardButton(
                            "⬅️ Kembali",
                            callback_data=(
                                f"otp_country:{country}"
                            )
                        )
                    ]

                ])

            )

            return

        await query.edit_message_text(

            "⏳ <b>Memproses order...</b>\n\n"

            f"{country_flag(country)} Negara: <b>{country}</b>\n"

            f"📱 Layanan: <b>{product}</b>\n"

            f"📡 Operator: <b>{operator}</b>\n"

            f"💰 Harga: "
            f"<b>{format_rupiah(sell_price)}</b>",

            parse_mode="HTML"

        )

        # -------------------------------------------------
        # INTERNAL ORDER ID
        # -------------------------------------------------

        order_id = (

            "OTP-"
            +
            uuid.uuid4()
            .hex[:12]
            .upper()

        )

        # -------------------------------------------------
        # POTONG SALDO
        # -------------------------------------------------

        try:

            balance_after = (
                create_pending_order(

                    telegram_id=user_id,

                    order_id=order_id,

                    country=country,

                    service=product,

                    sell_price=sell_price

                )
            )

        except ValueError as error:

            await query.edit_message_text(

                f"❌ <b>Order gagal.</b>\n\n"
                f"{error}",

                parse_mode="HTML"

            )

            return

        # -------------------------------------------------
        # BUY NUMBER
        # -------------------------------------------------

        result = await asyncio.to_thread(

            buy_number,

            country,

            product,

            operator

        )

        if (

            not result
            or
            result.get(
                "response"
            ) == "ERROR"

        ):

            try:

                refund = refund_order(

                    order_id,

                    "Pembelian nomor Server 1 gagal."

                )

            except Exception as error:

                logger.exception(
                    "Refund gagal"
                )

                await query.edit_message_text(

                    "⚠️ <b>Provider gagal "
                    "dan refund otomatis "
                    "mengalami masalah.</b>\n\n"

                    f"Order: "
                    f"<code>{order_id}</code>\n"

                    f"Error: "
                    f"<code>{error}</code>",

                    parse_mode="HTML"

                )

                return

            await query.edit_message_text(

                "❌ <b>Nomor tidak tersedia.</b>\n\n"

                f"🧾 Order: "
                f"<code>{order_id}</code>\n"

                f"💸 Refund: "
                f"<b>{format_rupiah(sell_price)}</b>\n"

                f"💰 Saldo: "
                f"<b>{format_rupiah(refund['balance'])}</b>",

                parse_mode="HTML",

                reply_markup=InlineKeyboardMarkup([

                    [
                        InlineKeyboardButton(
                            "🔄 Order Lagi",
                            callback_data="order"
                        )
                    ],

                    [
                        InlineKeyboardButton(
                            "🏠 Menu Utama",
                            callback_data="user_home"
                        )
                    ]

                ])

            )

            return

        provider_order_id = result.get(
            "id"
        )

        phone = result.get(
            "phone"
        )

        if (
            not provider_order_id
            or
            not phone
        ):

            refund_order(

                order_id,

                "Respons Server 1 tidak lengkap."

            )

            await query.edit_message_text(

                "❌ <b>Respons provider "
                "tidak valid.</b>\n\n"
                "Saldo sudah dikembalikan.",

                parse_mode="HTML"

            )

            return

        # -------------------------------------------------
        # SIMPAN PROVIDER ORDER
        # -------------------------------------------------

        provider_cost_rp = int(

            round(

                provider_cost_usd
                *
                17649.80

            )

        )

        save_provider_order(

            order_id,

            provider_order_id,

            provider_cost_rp

        )

        await query.edit_message_text(

            "✅ <b>ORDER BERHASIL</b>\n\n"

            f"🧾 Order: "
            f"<code>{order_id}</code>\n"

            f"{country_flag(country)} Negara: "
            f"<b>{country}</b>\n"

            f"📱 Layanan: "
            f"<b>{product}</b>\n"

            f"📡 Operator: "
            f"<b>{operator}</b>\n\n"

            f"📞 Nomor:\n"
            f"<code>{phone}</code>\n\n"

            f"💰 Harga: "
            f"<b>{format_rupiah(sell_price)}</b>\n"

            f"💳 Sisa saldo: "
            f"<b>{format_rupiah(balance_after)}</b>\n\n"
            + _user_timing_text(get_order(order_id) or {"created_at": now(), "expired_at": None})
            + "\n\n⏳ <b>Menunggu SMS OTP...</b>",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup([

                [
                    InlineKeyboardButton(
                        "🔄 Cek OTP",
                        callback_data=(
                            f"otp_check:{order_id}"
                        )
                    )
                ],

                [
                    InlineKeyboardButton(
                        "🔁 Resend OTP",
                        callback_data=f"otp_resend:{order_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ Batal / Refund",
                        callback_data=(
                            f"otp_cancel:{order_id}"
                        )
                    )
                ],

                [
                    InlineKeyboardButton(
                        "🏠 Menu Utama",
                        callback_data="user_home"
                    )
                ]

            ])

        )

        return

    # =====================================================
    # CEK OTP
    # =====================================================

    if data.startswith(
        "otp_check:"
    ):

        order_id = data.split(
            ":",
            1
        )[1]

        order = get_order(
            order_id
        )

        if not order:

            await query.answer(
                "Order tidak ditemukan.",
                show_alert=True
            )

            return

        if order["telegram_id"] != user_id:

            await query.answer(
                "Order ini bukan milik kamu.",
                show_alert=True
            )

            return

        if order["status"] != "PENDING":

            await query.answer(
                f"Status: {order['status']}",
                show_alert=True
            )

            return

        provider_order_id = order.get("provider_order_id")
        runtime_provider = _RUNTIME_PROVIDER_CACHE.get(order_id) or {}
        if not provider_order_id:
            provider_order_id = runtime_provider.get("provider_order_id")

        if not provider_order_id:

            await query.answer(
                "Order masih diproses.",
                show_alert=True
            )

            return

        provider = str(order.get("provider") or "5sim").strip().lower()

        if provider == "rumahotp":
            sms_checker = get_rumahotp_sms
        else:
            sms_checker = get_sms

        data_sms = await asyncio.to_thread(
            sms_checker,
            provider_order_id
        )

        if (

            not data_sms
            or
            data_sms.get(
                "response"
            ) == "ERROR"

        ):

            await query.answer(
                "Gagal mengecek OTP.",
                show_alert=True
            )

            return

        sms_list = data_sms.get(
            "sms",
            []
        )

        if sms_list:

            sms = sms_list[0]

            code = sms.get(
                "code"
            )

            text = sms.get(
                "text",
                ""
            )

            if code:

                success = mark_order_success(
                    order_id
                )

                if success:

                    current_order = get_order(order_id) or order
                    service_name = current_order.get("service_name") or current_order.get("service") or "-"
                    country_name = current_order.get("country_name") or current_order.get("country") or "-"
                    phone = current_order.get("phone") or runtime_provider.get("phone") or "-"
                    expired_at = data_sms.get("expired_at") or current_order.get("expired_at") or runtime_provider.get("expired_at")
                    detail_lines = (
                        "🎉 <b>OTP DITERIMA</b>\n\n"
                        f"🧾 Order: <code>{order_id}</code>\n"
                        f"📱 Layanan: <b>{escape(str(service_name))}</b>\n"
                        f"🌐 Negara: <b>{escape(str(country_name))}</b>\n"
                        f"📞 Nomor: <code>{escape(str(phone))}</code>\n"
                        + (f"⏱️ Expired: <code>{escape(str(expired_at))}</code>\n" if expired_at else "")
                        + "\n🔐 OTP:\n"
                        f"<code>{escape(str(code))}</code>\n\n"
                        f"📨 SMS:\n"
                        f"<code>{escape(str(text))}</code>"
                    )
                    await query.edit_message_text(
                        detail_lines,

                        parse_mode="HTML",

                        reply_markup=InlineKeyboardMarkup([
                            ([InlineKeyboardButton("🔁 Resend OTP", callback_data=f"otp_resend:{order_id}")]
                             if provider.strip().lower() == "rumahotp" else []),
                            [InlineKeyboardButton("⬅️ Kembali ke Order", callback_data=f"otp_order_view:{order_id}")],
                            [InlineKeyboardButton("🏠 Menu Utama", callback_data="user_home")],
                        ])

                    )

                return

        await query.answer(
            "⏳ OTP belum masuk. Coba lagi atau gunakan Resend OTP.",
            show_alert=True
        )
        if provider == "rumahotp":
            try:
                await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Cek OTP", callback_data=f"otp_check:{order_id}")],
                    [InlineKeyboardButton("🔁 Resend OTP", callback_data=f"otp_resend:{order_id}")],
                    [InlineKeyboardButton("❌ Batal / Refund", callback_data=f"otp_cancel:{order_id}")],
                    [InlineKeyboardButton("🏠 Menu Utama", callback_data="user_home")],
                ]))
            except Exception:
                logger.exception("Gagal memperbarui tombol resend order=%s", order_id)
        return

    # =====================================================
    # RESEND OTP
    # =====================================================
    if data.startswith("otp_resend:"):
        order_id = data.split(":", 1)[1]
        order = get_order(order_id)
        if not order or int(order["telegram_id"]) != int(user_id):
            await query.answer("Order tidak ditemukan.", show_alert=True)
            return

        provider = str(order.get("provider") or "").lower()
        if provider != "rumahotp":
            await query.answer("Resend OTP tidak tersedia untuk order ini.", show_alert=True)
            return
        local_status = str(order.get("status") or "").upper()
        if local_status != "PENDING" and not (provider == "rumahotp" and local_status == "SUCCESS"):
            await query.answer(f"Order sudah {order.get('status') or 'selesai'}.", show_alert=True)
            return

        provider_order_id = order.get("provider_order_id")
        if not provider_order_id:
            provider_order_id = (_RUNTIME_PROVIDER_CACHE.get(order_id) or {}).get("provider_order_id")
        if not provider_order_id:
            await query.answer("Order belum siap untuk resend.", show_alert=True)
            return

        expiry_value = order.get("expired_at") or (_RUNTIME_PROVIDER_CACHE.get(order_id) or {}).get("expired_at")
        expiry_ts = _parse_expiry_timestamp(expiry_value)
        if expiry_ts is not None and datetime.now(timezone.utc).timestamp() >= expiry_ts:
            await query.answer("Nomor sudah expired. Order akan dibatalkan otomatis.", show_alert=True)
            return

        result = await asyncio.to_thread(resend_rumahotp_otp, provider_order_id)

        if not result or result.get("response") != "OK":
            await query.answer(str((result or {}).get("error") or (result or {}).get("message") or "Resend OTP gagal."), show_alert=True)
            return

        expired = result.get("expired_at") or order.get("expired_at")
        if expired:
            with get_db() as db:
                db.execute("UPDATE orders SET expired_at=%s WHERE order_id=%s", (str(expired), order_id))

        await query.answer("✅ Resend OTP berhasil dikirim.")
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Cek OTP", callback_data=f"otp_check:{order_id}")],
            [InlineKeyboardButton("🔁 Resend OTP", callback_data=f"otp_resend:{order_id}")],
            [InlineKeyboardButton("❌ Batal / Refund", callback_data=f"otp_cancel:{order_id}")],
            [InlineKeyboardButton("🏠 Menu Utama", callback_data="user_home")],
        ]))
        return

    # =====================================================
    # KEMBALI KE ORDER
    # =====================================================
    if data.startswith("otp_order_view:"):
        order_id = data.split(":", 1)[1]
        order = get_order(order_id)
        if not order or int(order["telegram_id"]) != int(user_id):
            await query.answer("Order tidak ditemukan.", show_alert=True)
            return
        service_name = order.get("service_name") or order.get("service") or "-"
        country_name = order.get("country_name") or order.get("country") or "-"
        phone = order.get("phone") or "-"
        text = (f"📦 <b>DETAIL ORDER</b>\n\n🧾 Order: <code>{order_id}</code>\n"
                f"🌐 Negara: <b>{escape(str(country_name))}</b>\n📱 Layanan: <b>{escape(str(service_name))}</b>\n"
                f"📞 Nomor: <code>{escape(str(phone))}</code>\n💰 Harga: <b>{format_rupiah(order['sell_price'])}</b>\n\n"
                + _user_timing_text(order) + "\n"
                + f"📌 Status: <b>{escape(str(order['status']))}</b>")
        kb=[]
        if str(order["status"]).upper()=="PENDING" and not order.get("cancel_requested_at"):
            kb.append([InlineKeyboardButton("🔄 Cek OTP", callback_data=f"otp_check:{order_id}"), InlineKeyboardButton("❌ Batal / Refund", callback_data=f"otp_cancel:{order_id}")])
        elif str(order["status"]).upper()=="PENDING" and order.get("cancel_requested_at"):
            text = text.replace(f"📌 Status: <b>{escape(str(order['status']))}</b>", "⏳ <b>Pembatalan / refund sedang diproses</b>")
            kb.append([InlineKeyboardButton("🔄 Perbarui Status", callback_data=f"otp_order_view:{order_id}")])
        if str(order.get("provider") or "").lower() == "rumahotp" and str(order["status"]).upper() in {"PENDING", "SUCCESS"} and not order.get("cancel_requested_at"):
            kb.append([InlineKeyboardButton("🔁 Resend OTP", callback_data=f"otp_resend:{order_id}")])
        kb.append([InlineKeyboardButton("🏠 Menu Utama", callback_data="user_home")])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
        return

    # =====================================================
    # CANCEL / REFUND
    # =====================================================
    if data.startswith("otp_cancel:"):
        order_id = data.split(":", 1)[1]
        order = get_order(order_id)
        if not order:
            await query.answer("Order tidak ditemukan.", show_alert=True)
            return
        if int(order["telegram_id"]) != int(user_id):
            await query.answer("Order ini bukan milik kamu.", show_alert=True)
            return
        if str(order.get("status") or "").upper() != "PENDING":
            await query.answer(f"Order sudah {order.get('status') or 'selesai'}.", show_alert=True)
            return

        timing = _order_timing(order)
        if timing["expired"]:
            # Let the auto-expire worker reconcile the provider and refund only
            # after upstream cancellation is confirmed.
            await asyncio.to_thread(request_order_cancel, order_id, "Masa aktif order telah habis; menunggu pembatalan provider otomatis.")
            await query.edit_message_text(
                "⏳ <b>PEMBATALAN / REFUND SEDANG DIPROSES</b>\n\n"
                f"🧾 Order: <code>{order_id}</code>\n\n"
                "Masa aktif nomor telah habis. Pembatalan sedang diproses otomatis.\n"
                "💰 Saldo akan dikembalikan setelah pembatalan berhasil dikonfirmasi.\n\n"
                "⚠️ Mohon tunggu, tidak perlu membatalkan secara manual.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu Utama", callback_data="user_home")]])
            )
            return

        if timing["cancel_wait"] > 0:
            await query.answer(f"Tunggu {_format_countdown(timing['cancel_wait'])} sebelum klik batal.", show_alert=True)
            await query.edit_message_text(
                "⏳ <b>INFORMASI PEMBATALAN</b>\n\n"
                f"🧾 Order: <code>{order_id}</code>\n\n"
                + _user_timing_text(order) + "\n\n"
                "Silakan klik <b>Batal / Refund</b> kembali setelah waktu tunggu selesai.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Batal / Refund", callback_data=f"otp_cancel:{order_id}")],
                    [InlineKeyboardButton("🔄 Cek OTP", callback_data=f"otp_check:{order_id}")],
                    [InlineKeyboardButton("🏠 Menu Utama", callback_data="user_home")],
                ])
            )
            return

        # Never refund inline. Queue the cancellation first so the UI can show
        # the same waiting/cancellation state as the upstream service. The
        # background worker will call the provider and refund only after the
        # provider confirms cancellation.
        await asyncio.to_thread(request_order_cancel, order_id, "User meminta pembatalan/refund.")
        await query.edit_message_text(
            "⏳ <b>PEMBATALAN / REFUND SEDANG DIPROSES</b>\n\n"
            f"🧾 Order: <code>{order_id}</code>\n\n"
            "Permintaan pembatalan sudah diterima.\n"
            "Sistem sedang memproses pembatalan dan pengembalian saldo.\n\n"
            + _user_timing_text(order, include_cancel=False) + "\n\n"
            "💰 Saldo akan dikembalikan setelah pembatalan berhasil dikonfirmasi.\n"
            "⚠️ Tidak perlu melakukan pembatalan manual.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Perbarui Status", callback_data=f"otp_order_view:{order_id}")],
                [InlineKeyboardButton("🏠 Menu Utama", callback_data="user_home")],
            ])
        )
        return

    if data == "cancel_deposit":

        context.chat_data[
            "waiting_deposit"
        ] = False

        await query.edit_message_text(

            "❌ <b>Deposit dibatalkan.</b>",

            parse_mode="HTML",

            reply_markup=user_menu()

        )

        return

    # =====================================================
    # HISTORY ORDER
    # =====================================================

    if data == "user_history_order":

        orders = get_order_history(
            user_id
        )

        if not orders:

            text = (
                "📋 <b>Histori Order</b>\n\n"
                "Belum ada histori order."
            )

        else:

            text = (

                "📋 <b>5 Histori Order "
                "Terakhir</b>\n\n"

                +
                "\n".join(

                    [

                        (
                            f"├ <code>{o['order_id']}</code> - "
                            f"{o.get('status','-')}\n"
                            f"   📱 {o.get('service_name') or o.get('service') or '-'} | "
                            f"🌐 {o.get('country_name') or o.get('country') or '-'} | "
                            f"📞 {o.get('phone') or '-'} | "
                            f"💰 {format_rupiah(o.get('sell_price') or 0)}"
                        )

                        for o in orders

                    ]

                )

            )

        await query.edit_message_text(

            text,

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup([

                [
                    InlineKeyboardButton(
                        "⬅️ Kembali",
                        callback_data="user_home"
                    )
                ]

            ])

        )

        return

    # =====================================================
    # HISTORY DEPOSIT
    # =====================================================

    if data == "user_history_depo":

        deposits = get_deposit_history(
            user_id
        )

        if not deposits:

            text = (
                "📜 <b>Histori Deposit</b>\n\n"
                "Belum ada histori deposit."
            )

        else:

            text = (

                "📜 <b>5 Histori Deposit "
                "Terakhir</b>\n\n"

                +
                "\n".join(

                    [

                        (
                            f"├ {d['deposit_id']} - "
                            f"{format_rupiah(d['amount'])} - "
                            f"{d['status']}"
                        )

                        for d in deposits

                    ]

                )

            )

        await query.edit_message_text(

            text,

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup([

                [
                    InlineKeyboardButton(
                        "⬅️ Kembali",
                        callback_data="user_home"
                    )
                ]

            ])

        )

        return

    # =====================================================
    # CHECK-IN
    # =====================================================

    if data == "checkin":
        # Reuse the same atomic claim logic as /checkin.
        class _FakeUpdate:
            def __init__(self, q):
                self.message = None
                self.effective_user = q.from_user
                self._query = q
            async def edit_message_text(self, *args, **kwargs):
                return await self._query.edit_message_text(*args, **kwargs)

        status = get_checkin_status(user_id)
        total_deposit = int(status.get("total_success_deposit") or 0)
        if not status.get("has_min_deposit"):
            await query.edit_message_text(
                "🎁 <b>SALDO GRATIS</b>\n\n"
                "💳 Minimal salah satu deposit berhasil: <b>Rp10.000</b>\n"
                f"📊 Total deposit kamu: <b>{format_rupiah(total_deposit)}</b>\n\n"
                "Lakukan deposit minimal Rp10.000 terlebih dahulu untuk membuka fitur check-in.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💳 Deposit", callback_data="user_deposit")]])
            )
            return
        reward = random.randint(50, 110) if not status.get("last_checkin_at") else random.randint(50, 100)
        result = await asyncio.to_thread(claim_checkin, user_id, reward)
        if result.get("ok"):
            text = (
                "🎉 <b>CHECK-IN BERHASIL!</b>\n\n"
                f"🎁 Saldo gratis: <b>{format_rupiah(result['reward'])}</b>\n"
                f"💰 Saldo sekarang: <b>{format_rupiah(result['balance'])}</b>\n\n"
                "⏰ Check-in berikutnya tersedia <b>24 jam</b> setelah check-in ini."
            )
        elif result.get("reason") == "COOLDOWN":
            text = "⏳ <b>CHECK-IN BELUM TERSEDIA</b>\n\n" + f"Coba lagi dalam sekitar <b>{_format_remaining(result['remaining_seconds'])}</b>."
        else:
            text = "❌ Check-in belum bisa diproses. Silakan coba lagi."
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu Utama", callback_data="user_home")]]))
        return

    # =====================================================
    # REFERRAL
    # =====================================================

    if data == "referral":

        bot_username = context.bot.username

        if bot_username:

            ref_link = (

                f"https://t.me/"
                f"{bot_username}"
                f"?start=ref{user_id}"

            )

        else:

            ref_link = (
                "Username bot belum tersedia."
            )

        await query.edit_message_text(

            f"👥 <b>Referral</b>\n\n"

            f"Link kamu:\n"
            f"<code>{ref_link}</code>\n\n"

            f"Dapat 10% dari deposit teman.",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup([

                [
                    InlineKeyboardButton(
                        "⬅️ Kembali",
                        callback_data="user_home"
                    )
                ]

            ])

        )

        return

    # =====================================================
    # CS
    # =====================================================

    if data == "cs":

        await query.edit_message_text(

            "💬 <b>Contact CS</b>\n\n"
            "Hubungi: @AdminLu",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup([

                [
                    InlineKeyboardButton(
                        "⬅️ Kembali",
                        callback_data="user_home"
                    )
                ]

            ])

        )

        return

    # =====================================================
    # CEK DEPOSIT
    # =====================================================

    if data == "cek_deposit":

        with get_db() as db:

            deposit = db.execute(

                """
                SELECT
                    deposit_id,
                    amount
                FROM deposits
                WHERE telegram_id = %s
                AND status = 'PENDING'
                ORDER BY created_at DESC
                LIMIT 1
                """,

                (user_id,)

            ).fetchone()

        if not deposit:

            await query.edit_message_text(

                "❌ Kamu tidak punya "
                "deposit pending.",

                parse_mode="HTML",

                reply_markup=InlineKeyboardMarkup([

                    [
                        InlineKeyboardButton(
                            "⬅️ Kembali",
                            callback_data="user_home"
                        )
                    ]

                ])

            )

            return

        await query.edit_message_text(

            "⏳ Mengecek pembayaran "
            "ke Midtrans...",

            parse_mode="HTML"

        )

        status_data = await asyncio.to_thread(

            cek_status_midtrans,

            deposit["deposit_id"]

        )

        if not status_data:

            await query.edit_message_text(

                "❌ Gagal cek ke Midtrans.",

                parse_mode="HTML",

                reply_markup=InlineKeyboardMarkup([

                    [
                        InlineKeyboardButton(
                            "🔄 Cek Lagi",
                            callback_data="cek_deposit"
                        )
                    ],

                    [
                        InlineKeyboardButton(
                            "⬅️ Kembali",
                            callback_data="user_home"
                        )
                    ]

                ])

            )

            return

        transaction_status = status_data.get(
            "transaction_status"
        )

        if transaction_status == "settlement":

            result = complete_deposit_payment(

                deposit["deposit_id"],

                status_data.get(
                    "transaction_id"
                ),

                status_data.get(
                    "gross_amount"
                )

            )

            if result["completed"]:

                await query.edit_message_text(

                    "✅ <b>Deposit Berhasil!</b>\n\n"

                    f"💰 Deposit: <b>{format_rupiah(result['amount'])}</b>\n"
                    + (f"🎁 Bonus 10%: <b>{format_rupiah(result['bonus'])}</b>\n" if result.get('bonus', 0) else "")
                    + f"💳 Total masuk: <b>{format_rupiah(result['credited'])}</b>\n"
                    + f"💳 Saldo sekarang: <b>{format_rupiah(result['new_balance'])}</b>",

                    parse_mode="HTML",

                    reply_markup=InlineKeyboardMarkup([

                        [
                            InlineKeyboardButton(
                                "🏠 Menu Utama",
                                callback_data="user_home"
                            )
                        ]

                    ])

                )

            else:

                await query.edit_message_text(

                    "✅ <b>Deposit sudah "
                    "berhasil diproses.</b>\n\n"

                    f"💰 Saldo sekarang: "
                    f"<b>{format_rupiah(get_balance(user_id))}</b>",

                    parse_mode="HTML",

                    reply_markup=InlineKeyboardMarkup([

                        [
                            InlineKeyboardButton(
                                "🏠 Menu Utama",
                                callback_data="user_home"
                            )
                        ]

                    ])

                )

            return

        if transaction_status in [
            "expire",
            "cancel"
        ]:

            with get_db() as db:

                db.execute(

                    """
                    UPDATE deposits
                    SET status = 'EXPIRED'
                    WHERE deposit_id = %s
                    AND status = 'PENDING'
                    """,

                    (
                        deposit[
                            "deposit_id"
                        ],
                    )

                )

            await query.edit_message_text(

                "❌ <b>Deposit Expired</b>\n\n"
                "Silakan buat invoice baru.",

                parse_mode="HTML",

                reply_markup=InlineKeyboardMarkup([

                    [
                        InlineKeyboardButton(
                            "💳 Deposit Lagi",
                            callback_data="user_deposit"
                        )
                    ],

                    [
                        InlineKeyboardButton(
                            "🏠 Menu Utama",
                            callback_data="user_home"
                        )
                    ]

                ])

            )

            return

        await query.edit_message_text(

            f"⏳ <b>Status: "
            f"{str(transaction_status).upper()}</b>\n\n"
            "Belum dibayar.",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup([

                [
                    InlineKeyboardButton(
                        "🔄 Cek Lagi",
                        callback_data="cek_deposit"
                    )
                ],

                [
                    InlineKeyboardButton(
                        "⬅️ Kembali",
                        callback_data="user_home"
                    )
                ]

            ])

        )

        return

    # =====================================================
    # USER HOME
    # =====================================================

    if data == "user_home":

        context.chat_data[
            "waiting_deposit"
        ] = False

        await user_start(
            query
        )

        return


# =========================================================
# ADMIN HELPERS
# =========================================================

ADMIN_PAGE_SIZE = 8
ADMIN_PROVIDER_NAMES = {
    "5sim": "5SIM",
    "rumahotp": "RUMAHOTP",
}
ADMIN_SEARCH_USERS = set()
ADMIN_SEARCH_DEPOSITS = set()

def _admin_user_label(user):
    name = str(user.get("first_name") or "").strip()
    username = str(user.get("username") or "").strip()
    uid = int(user.get("telegram_id"))
    if username:
        return f"{escape(name or username)} (@{escape(username)}) — {uid}"
    return f"{escape(name or 'Tanpa nama')} — {uid}"


async def _admin_users_page(query, page=0):
    with get_db() as db:
        total = int(db.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"])
        total_pages = max(1, (total + ADMIN_PAGE_SIZE - 1) // ADMIN_PAGE_SIZE)
        page = max(0, min(page, total_pages - 1))
        users = db.execute(
            """
            SELECT telegram_id, username, first_name, balance, created_at
            FROM users
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            (ADMIN_PAGE_SIZE, page * ADMIN_PAGE_SIZE),
        ).fetchall()

    keyboard = []
    for user in users:
        keyboard.append([InlineKeyboardButton(
            _admin_user_label(user),
            callback_data=f"admin_user:{int(user['telegram_id'])}"
        )])

    keyboard.append([
        InlineKeyboardButton("◀️", callback_data=f"admin_users_page:{max(0, page-1)}"),
        InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="admin_noop"),
        InlineKeyboardButton("▶️", callback_data=f"admin_users_page:{min(total_pages-1, page+1)}"),
    ])
    keyboard.append([
        InlineKeyboardButton("🔎 Cari ID User", callback_data="admin_users_search"),
        InlineKeyboardButton("⬅️ Admin Panel", callback_data="admin_home"),
    ])

    await query.edit_message_text(
        f"👥 <b>USERS</b>\
\
Total user: <b>{total}</b>\
Pilih user untuk melihat saldo, transaksi, dan riwayat deposit.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def _admin_user_detail(query, telegram_id):
    with get_db() as db:
        user = db.execute("SELECT * FROM users WHERE telegram_id = %s", (telegram_id,)).fetchone()
        if not user:
            await query.answer("User tidak ditemukan.", show_alert=True)
            return
        orders = int(db.execute("SELECT COUNT(*) AS total FROM orders WHERE telegram_id = %s", (telegram_id,)).fetchone()["total"])
        deposits = int(db.execute("SELECT COUNT(*) AS total FROM deposits WHERE telegram_id = %s", (telegram_id,)).fetchone()["total"])
        success_deposits = int(db.execute("SELECT COUNT(*) AS total FROM deposits WHERE telegram_id = %s AND status = 'SUCCESS'", (telegram_id,)).fetchone()["total"])

    name = escape(str(user.get("first_name") or "Tanpa nama"))
    username = escape(str(user.get("username") or "-"))
    keyboard = [
        [
            InlineKeyboardButton("💰 Saldo", callback_data=f"admin_user:{telegram_id}"),
            InlineKeyboardButton("➕ Tambah Saldo", callback_data=f"admin_user_add_balance:{telegram_id}"),
        ],
        [
            InlineKeyboardButton("📦 Transaksi", callback_data=f"admin_user_orders:{telegram_id}"),
            InlineKeyboardButton("➖ Pengurangan Saldo", callback_data=f"admin_user_subtract_balance:{telegram_id}"),
        ],
        [
            InlineKeyboardButton("💳 Riwayat Deposit", callback_data=f"admin_user_deposits:{telegram_id}"),
            InlineKeyboardButton("📒 Ledger", callback_data=f"admin_user_ledger:{telegram_id}"),
        ],
        [InlineKeyboardButton("⬅️ Daftar User", callback_data="admin_users")],
    ]
    await query.edit_message_text(
        "👤 <b>DETAIL USER</b>\
\
"
        f"Nama: <b>{name}</b>\
"
        f"Username: <b>@{username}</b>\
"
        f"ID: <code>{telegram_id}</code>\
\
"
        f"💰 Saldo: <b>{format_rupiah(user['balance'])}</b>\
"
        f"📦 Total transaksi/order: <b>{orders}</b>\
"
        f"💳 Total deposit: <b>{deposits}</b>\
"
        f"✅ Deposit sukses: <b>{success_deposits}</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def _admin_user_orders(query, telegram_id):
    with get_db() as db:
        rows = db.execute(
            """SELECT order_id, service, service_name, country, country_name, phone, provider, sell_price, status, created_at
               FROM orders WHERE telegram_id = %s ORDER BY created_at DESC LIMIT 12""",
            (telegram_id,),
        ).fetchall()
    lines = [f"📦 <b>TRANSAKSI USER {telegram_id}</b>\
"]
    if not rows:
        lines.append("Belum ada transaksi.")
    keyboard = []
    for row in rows:
        lines.append(
            f"• <code>{escape(str(row['order_id']))}</code> - <b>{escape(str(row.get('status') or '-'))}</b>\n"
            f"   📱 {escape(str(row.get('service_name') or row.get('service') or '-'))} | "
            f"🌐 {escape(str(row.get('country_name') or row.get('country') or '-'))}\n"
            f"   📞 <code>{escape(str(row.get('phone') or '-'))}</code> | "
            f"💰 {format_rupiah(row['sell_price'])}"
        )
        if (str(row.get("status") or "").upper() == "PENDING" and
                str(row.get("provider") or "").lower() in {"rumahotp", "5sim"}):
            keyboard.append([InlineKeyboardButton(
                f"🛑 Batalkan & Refund {str(row['order_id'])}",
                callback_data=f"admin_cancel_order:{row['order_id']}"
            )])
    keyboard.append([InlineKeyboardButton("⬅️ Detail User", callback_data=f"admin_user:{telegram_id}")])
    await query.edit_message_text(
        "\
".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def _admin_cancel_order(query, context, order_id):
    """Admin: cancel an existing provider order, verify cancellation, then refund once."""
    order = get_order(order_id)
    if not order:
        await query.answer("Order tidak ditemukan di database bot.", show_alert=True)
        return

    provider = str(order.get("provider") or "5sim").lower()
    provider_order_id = str(order.get("provider_order_id") or "").strip()
    if not provider_order_id:
        await query.answer("Order belum memiliki ID order provider.", show_alert=True)
        return

    if str(order.get("refund_status") or "").upper() == "REFUNDED":
        await query.answer("Order ini sudah pernah direfund.", show_alert=True)
        return

    if str(order.get("status") or "").upper() == "SUCCESS":
        await query.answer("Order sudah SUCCESS dan tidak bisa dibatalkan.", show_alert=True)
        return

    await query.edit_message_text(
        "⏳ <b>MEMBATALKAN ORDER PROVIDER...</b>\n\n"
        f"🧾 Order Bot: <code>{escape(order_id)}</code>\n"
        f"📡 Provider: <b>{escape(ADMIN_PROVIDER_NAMES.get(provider, provider.upper()))}</b>\n"
        f"🆔 Provider Order: <code>{escape(provider_order_id)}</code>\n\n"
        "Mohon tunggu, bot sedang meminta pembatalan ke provider dan memverifikasinya.",
        parse_mode="HTML"
    )

    try:
        if provider == "rumahotp":
            cancel_result = await asyncio.to_thread(
                cancel_rumahotp_number,
                provider_order_id,
                order.get("provider_cost") or 0,
            )
        elif provider == "5sim":
            cancel_result = await asyncio.to_thread(cancel_number, provider_order_id)
        else:
            cancel_result = {"response": "ERROR", "error": f"Provider {provider} belum didukung."}

        logger.info(
            "[ADMIN CANCEL] local=%s provider=%s provider_order=%s result=%s",
            order_id, provider, provider_order_id, cancel_result
        )

        if not cancel_result or cancel_result.get("response") != "OK":
            error = str((cancel_result or {}).get("error") or "Provider belum mengonfirmasi pembatalan.")
            await query.edit_message_text(
                "⚠️ <b>PEMBATALAN BELUM BERHASIL</b>\n\n"
                f"🧾 Order: <code>{escape(order_id)}</code>\n"
                f"📡 Provider: <b>{escape(ADMIN_PROVIDER_NAMES.get(provider, provider.upper()))}</b>\n"
                f"🆔 Provider Order: <code>{escape(provider_order_id)}</code>\n"
                f"❗ {escape(error)}\n\n"
                "Saldo user <b>belum</b> dikembalikan karena provider belum mengonfirmasi cancel.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Coba Lagi", callback_data=f"admin_cancel_order:{order_id}")],
                    [InlineKeyboardButton("📦 Transaksi User", callback_data=f"admin_user_orders:{int(order['telegram_id'])}")],
                ])
            )
            return

        result = refund_order(
            order_id,
            "Admin membatalkan order provider dan provider mengonfirmasi pembatalan."
        )

        # Notify the user about the admin-triggered cancellation/refund.
        try:
            await context.bot.send_message(
                chat_id=int(order["telegram_id"]),
                text=(
                    "🎉 <b>INFO REFUND AZHURA [BOT NOKOS]</b>\n\n"
                    f"🧾 Order: <code>{escape(order_id)}</code>\n"
                    f"❌ Pesanan dibatalkan oleh admin.\n"
                    f"💸 Saldo dikembalikan: <b>{format_rupiah(order['sell_price'])}</b>\n"
                    f"💳 Saldo kamu sekarang: <b>{format_rupiah(result['balance'])}</b>\n\n"
                    "🔥 Silakan order kembali kapan saja. Semoga order lancar dan cuan terus bersama AZHURA! 💎"
                ),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🚀 Order OTP Sekarang", callback_data="user_home")
                ]])
            )
        except Exception:
            logger.exception("Gagal mengirim notifikasi refund admin ke user=%s", order["telegram_id"])

        await query.edit_message_text(
            "✅ <b>ORDER BERHASIL DIBATALKAN & REFUND</b>\n\n"
            f"🧾 Order Bot: <code>{escape(order_id)}</code>\n"
            f"📡 Provider: <b>{escape(ADMIN_PROVIDER_NAMES.get(provider, provider.upper()))}</b>\n"
            f"🆔 Provider Order: <code>{escape(provider_order_id)}</code>\n"
            f"💸 Refund user: <b>{format_rupiah(order['sell_price'])}</b>\n"
            f"💰 Saldo user sekarang: <b>{format_rupiah(result['balance'])}</b>\n\n"
            "Provider sudah mengonfirmasi pembatalan sebelum saldo dikembalikan.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📦 Transaksi User", callback_data=f"admin_user_orders:{int(order['telegram_id'])}")],
                [InlineKeyboardButton("👤 Detail User", callback_data=f"admin_user:{int(order['telegram_id'])}")],
            ])
        )
    except Exception as error:
        logger.exception("[ADMIN CANCEL] gagal order=%s", order_id)
        await query.edit_message_text(
            "❌ <b>PROSES BATAL/REFUND GAGAL</b>\n\n"
            f"Order: <code>{escape(order_id)}</code>\n"
            f"Error: <code>{escape(str(error))}</code>\n\n"
            "Saldo user tidak diubah jika refund belum berhasil.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📦 Transaksi User", callback_data=f"admin_user_orders:{int(order['telegram_id'])}")
            ]])
        )


async def _admin_user_deposits(query, telegram_id):
    with get_db() as db:
        rows = db.execute(
            """SELECT deposit_id, amount, status, payment_reference, created_at, completed_at
               FROM deposits WHERE telegram_id = %s ORDER BY created_at DESC LIMIT 12""",
            (telegram_id,),
        ).fetchall()
    lines = [f"💳 <b>RIWAYAT DEPOSIT USER {telegram_id}</b>\
"]
    if not rows:
        lines.append("Belum ada deposit.")
    for row in rows:
        lines.append(
            f"• <code>{escape(str(row['deposit_id']))}</code> | {format_rupiah(row['amount'])} | "
            f"<b>{escape(str(row['status']))}</b> | {escape(str(row['created_at'])[:19])}"
        )
    await query.edit_message_text(
        "\
".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Detail User", callback_data=f"admin_user:{telegram_id}")]])
    )


async def _admin_user_ledger(query, telegram_id):
    with get_db() as db:
        rows = db.execute(
            """SELECT amount, balance_before, balance_after, transaction_type, reference, description, created_at
               FROM ledger WHERE telegram_id = %s ORDER BY created_at DESC LIMIT 15""",
            (telegram_id,),
        ).fetchall()
    lines = [f"📒 <b>LEDGER USER {telegram_id}</b>\
"]
    if not rows:
        lines.append("Belum ada transaksi saldo.")
    for row in rows:
        lines.append(
            f"• {escape(str(row['transaction_type']))}: <b>{format_rupiah(row['amount'])}</b>\
"
            f"  {format_rupiah(row['balance_before'])} → {format_rupiah(row['balance_after'])} | {escape(str(row['created_at'])[:19])}"
        )
    await query.edit_message_text(
        "\
".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Detail User", callback_data=f"admin_user:{telegram_id}")]])
    )


async def _admin_deposits_page(query, status="ALL", page=0):
    status = str(status or "ALL").upper()
    allowed = {"ALL", "SUCCESS", "FAILED", "PENDING"}
    if status not in allowed:
        status = "ALL"
    with get_db() as db:
        if status == "ALL":
            total = int(db.execute("SELECT COUNT(*) AS total FROM deposits").fetchone()["total"])
            rows = db.execute(
                """SELECT deposit_id, telegram_id, amount, status, created_at
                   FROM deposits ORDER BY created_at DESC LIMIT %s OFFSET %s""",
                (ADMIN_PAGE_SIZE, page * ADMIN_PAGE_SIZE),
            ).fetchall()
        else:
            total = int(db.execute("SELECT COUNT(*) AS total FROM deposits WHERE status = %s", (status,)).fetchone()["total"])
            rows = db.execute(
                """SELECT deposit_id, telegram_id, amount, status, created_at
                   FROM deposits WHERE status = %s ORDER BY created_at DESC LIMIT %s OFFSET %s""",
                (status, ADMIN_PAGE_SIZE, page * ADMIN_PAGE_SIZE),
            ).fetchall()
    total_pages = max(1, (total + ADMIN_PAGE_SIZE - 1) // ADMIN_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    # Requery when page was clamped.
    if page * ADMIN_PAGE_SIZE >= total and total:
        return await _admin_deposits_page(query, status, page)

    keyboard = []
    for row in rows:
        keyboard.append([InlineKeyboardButton(
            f"{format_rupiah(row['amount'])} • {row['status']} • ID {row['telegram_id']}",
            callback_data=f"admin_user:{int(row['telegram_id'])}"
        )])
    keyboard.append([
        InlineKeyboardButton("◀️", callback_data=f"admin_deposits_page:{status}:{max(0,page-1)}"),
        InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="admin_noop"),
        InlineKeyboardButton("▶️", callback_data=f"admin_deposits_page:{status}:{min(total_pages-1,page+1)}"),
    ])
    keyboard.append([
        InlineKeyboardButton("Semua", callback_data="admin_deposits_page:ALL:0"),
        InlineKeyboardButton("✅ Sukses", callback_data="admin_deposits_page:SUCCESS:0"),
        InlineKeyboardButton("❌ Gagal", callback_data="admin_deposits_page:FAILED:0"),
    ])
    keyboard.append([
        InlineKeyboardButton("⏳ Pending", callback_data="admin_deposits_page:PENDING:0"),
        InlineKeyboardButton("🔎 Cari ID", callback_data="admin_deposits_search"),
    ])
    keyboard.append([InlineKeyboardButton("⬅️ Admin Panel", callback_data="admin_home")])
    await query.edit_message_text(
        f"💳 <b>DEPOSIT — {status}</b>\
\
Total: <b>{total}</b>",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def _admin_deposit_search_result(query, telegram_id):
    with get_db() as db:
        rows = db.execute(
            """SELECT deposit_id, telegram_id, amount, status, created_at
               FROM deposits WHERE telegram_id = %s ORDER BY created_at DESC LIMIT 30""",
            (telegram_id,),
        ).fetchall()
    lines = [f"🔎 <b>DEPOSIT USER {telegram_id}</b>\
"]
    if not rows:
        lines.append("Tidak ada deposit untuk ID tersebut.")
    for row in rows:
        lines.append(f"• <code>{escape(str(row['deposit_id']))}</code> | {format_rupiah(row['amount'])} | <b>{row['status']}</b> | {escape(str(row['created_at'])[:19])}")
    await query.edit_message_text(
        "\
".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Deposit", callback_data="admin_deposits")],
            [InlineKeyboardButton("👤 Detail User", callback_data=f"admin_user:{telegram_id}")],
        ])
    )


# =========================================================
# ADMIN CALLBACK
# =========================================================

async def admin_callback(
    query,
    context
):

    back = [

        [
            InlineKeyboardButton(
                "⬅️ Admin Panel",
                callback_data="admin_home"
            )
        ]

    ]

    if query.data == "admin_users":
        await _admin_users_page(query, 0)

    elif query.data.startswith("admin_users_page:"):
        try:
            page = int(query.data.split(":", 1)[1])
        except Exception:
            page = 0
        await _admin_users_page(query, page)

    elif query.data == "admin_users_search":
        ADMIN_SEARCH_USERS.add(query.from_user.id)
        await query.edit_message_text(
            "🔎 <b>CARI USER BERDASARKAN ID</b>\n\nKetik Telegram ID user yang ingin dicari.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data="admin_users")]])
        )

    elif query.data.startswith("admin_user:"):
        context.user_data.pop("admin_balance_target", None)
        context.user_data.pop("admin_balance_action", None)
        try:
            telegram_id = int(query.data.split(":", 1)[1])
        except Exception:
            await query.answer("ID user tidak valid.", show_alert=True)
            return
        await _admin_user_detail(query, telegram_id)

    elif query.data.startswith("admin_user_add_balance:"):
        try:
            telegram_id = int(query.data.split(":", 1)[1])
        except Exception:
            await query.answer("ID user tidak valid.", show_alert=True)
            return
        with get_db() as db:
            user = db.execute(
                "SELECT telegram_id, first_name, username, balance FROM users WHERE telegram_id = %s",
                (telegram_id,)
            ).fetchone()
        if not user:
            await query.answer("User tidak ditemukan.", show_alert=True)
            return
        ADMIN_SEARCH_USERS.discard(query.from_user.id)
        context.user_data["admin_balance_target"] = telegram_id
        context.user_data["admin_balance_action"] = "topup"
        await query.edit_message_text(
            "➕ <b>TAMBAH SALDO USER</b>\n\n"
            f"👤 User: <b>{escape(str(user.get('first_name') or user.get('username') or '-'))}</b>\n"
            f"🆔 ID: <code>{telegram_id}</code>\n"
            f"💰 Saldo sekarang: <b>{format_rupiah(user['balance'])}</b>\n\n"
            "Ketik nominal saldo yang ingin ditambahkan.\n"
            "Contoh: <code>10000</code>\n\n"
            "Nominal harus bilangan bulat lebih dari Rp0.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Batal", callback_data=f"admin_user:{telegram_id}")
            ]])
        )
        return

    elif query.data.startswith("admin_user_subtract_balance:"):
        try:
            telegram_id = int(query.data.split(":", 1)[1])
        except Exception:
            await query.answer("ID user tidak valid.", show_alert=True)
            return
        with get_db() as db:
            user = db.execute(
                "SELECT telegram_id, first_name, username, balance FROM users WHERE telegram_id = %s",
                (telegram_id,)
            ).fetchone()
        if not user:
            await query.answer("User tidak ditemukan.", show_alert=True)
            return
        ADMIN_SEARCH_USERS.discard(query.from_user.id)
        context.user_data["admin_balance_target"] = telegram_id
        context.user_data["admin_balance_action"] = "subtract"
        await query.edit_message_text(
            "➖ <b>PENGURANGAN SALDO USER</b>\n\n"
            f"👤 User: <b>{escape(str(user.get('first_name') or user.get('username') or '-'))}</b>\n"
            f"🆔 ID: <code>{telegram_id}</code>\n"
            f"💰 Saldo sekarang: <b>{format_rupiah(user['balance'])}</b>\n\n"
            "Ketik nominal saldo yang ingin dikurangi.\n"
            "Contoh: <code>10000</code>\n\n"
            "Saldo tidak boleh menjadi minus.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Batal", callback_data=f"admin_user:{telegram_id}")
            ]])
        )
        return

    elif query.data.startswith("admin_cancel_order:"):
        order_id = query.data.split(":", 1)[1].strip()
        await _admin_cancel_order(query, context, order_id)
        return

    elif query.data.startswith("admin_user_orders:"):
        try:
            telegram_id = int(query.data.split(":", 1)[1])
        except Exception:
            await query.answer("ID user tidak valid.", show_alert=True)
            return
        await _admin_user_orders(query, telegram_id)

    elif query.data.startswith("admin_user_deposits:"):
        try:
            telegram_id = int(query.data.split(":", 1)[1])
        except Exception:
            await query.answer("ID user tidak valid.", show_alert=True)
            return
        await _admin_user_deposits(query, telegram_id)

    elif query.data.startswith("admin_user_ledger:"):
        try:
            telegram_id = int(query.data.split(":", 1)[1])
        except Exception:
            await query.answer("ID user tidak valid.", show_alert=True)
            return
        await _admin_user_ledger(query, telegram_id)

    elif query.data == "admin_deposits":
        await _admin_deposits_page(query, "ALL", 0)

    elif query.data.startswith("admin_deposits_page:"):
        parts = query.data.split(":", 2)
        status = parts[1] if len(parts) > 1 else "ALL"
        try:
            page = int(parts[2]) if len(parts) > 2 else 0
        except Exception:
            page = 0
        await _admin_deposits_page(query, status, page)

    elif query.data == "admin_deposits_search":
        ADMIN_SEARCH_DEPOSITS.add(query.from_user.id)
        await query.edit_message_text(
            "🔎 <b>CARI DEPOSIT BERDASARKAN ID USER</b>\n\nKetik Telegram ID user.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data="admin_deposits")]])
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

            f"📦 <b>ORDERS</b>\n\n"
            f"Total order: <b>{total}</b>\n"
            f"Pending: <b>{pending}</b>\n"
            f"Success: <b>{success}</b>",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup(
                back
            )

        )

    elif query.data == "admin_provider":

        # Check the two configured providers concurrently so the admin panel stays fast.
        checks = await asyncio.gather(
            asyncio.to_thread(check_5sim_api),
            asyncio.to_thread(check_rumahotp_api),
            return_exceptions=True,
        )
        balances = await asyncio.gather(
            asyncio.to_thread(get_5sim_balance),
            asyncio.to_thread(get_rumahotp_balance),
            return_exceptions=True,
        )

        def ok(result):
            return isinstance(result, dict) and bool(result.get("success"))

        def money(value):
            try:
                return float(value)
            except Exception:
                return 0.0

        b1, b2 = [money(x) for x in balances]
        s1 = "🟢 CONNECTED" if ok(checks[0]) else "🔴 OFFLINE"
        s2 = "🟢 CONNECTED" if ok(checks[1]) else "🔴 OFFLINE"

        await query.edit_message_text(
            "💰 <b>PROVIDER STATUS</b>\n\n"
            f"⚡ <b>Server 1 — {ADMIN_PROVIDER_NAMES['5sim']}</b>\n{s1}\n💵 Saldo: <b>${b1:.2f}</b>\n\n"
            f"⚡ <b>Server 2 — {ADMIN_PROVIDER_NAMES['rumahotp']}</b>\n{s2}\n💵 Saldo: <b>${b2:.2f}</b>\n\n"
            f"💱 Kurs: <b>Rp{KURS_DOLAR:,.2f} / USD</b>\n"
            f"📈 Margin: <b>{PROFIT_PERCENT:g}%</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(back)
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
                SELECT
                    COALESCE(
                        SUM(balance),
                        0
                    ) AS total
                FROM users
                """

            ).fetchone()["total"]

        await query.edit_message_text(

            f"📊 <b>STATISTIK</b>\n\n"

            f"👥 Users: <b>{users}</b>\n"

            f"💳 Deposits: "
            f"<b>{deposits}</b>\n"

            f"📦 Orders: "
            f"<b>{orders}</b>\n"

            f"💰 Total saldo user: "
            f"<b>{format_rupiah(balance)}</b>",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup(
                back
            )

        )

    elif query.data == "admin_noop":
        await query.answer()

    elif query.data == "admin_home":

        await query.edit_message_text(

            "👑 <b>ADMIN PANEL</b>\n\n"
            "Pilih menu:",

            parse_mode="HTML",

            reply_markup=admin_menu()

        )


# =========================================================
# BUTTON HANDLER
# =========================================================

async def button_handler(
    update,
    context
):

    query = update.callback_query

    if not query:

        return

    try:

        await query.answer()

    except Exception:

        pass

    user_id = query.from_user.id

    admin_callbacks = {

        "admin_users",
        "admin_deposits",
        "admin_orders",
        "admin_provider",
        "admin_stats",
        "admin_home"

    }

    is_extended_admin_callback = (
        query.data in admin_callbacks
        or query.data == "admin_noop"
        or query.data.startswith("admin_users_page:")
        or query.data.startswith("admin_user:")
        or query.data.startswith("admin_user_orders:")
        or query.data.startswith("admin_user_deposits:")
        or query.data.startswith("admin_user_ledger:")
        or query.data.startswith("admin_user_add_balance:")
        or query.data.startswith("admin_user_subtract_balance:")
        or query.data.startswith("admin_cancel_order:")
        or query.data.startswith("admin_deposits_page:")
        or query.data == "admin_users_search"
        or query.data == "admin_deposits_search"
    )

    if is_extended_admin_callback:

        if not is_admin(
            user_id
        ):

            try:

                await query.answer(

                    "❌ Kamu bukan admin.",

                    show_alert=True

                )

            except Exception:

                pass

            return

        await admin_callback(
            query,
            context
        )

        return

    if query.data in [

        "user_home",
        "cancel_deposit"

    ]:

        context.chat_data[
            "waiting_deposit"
        ] = False

    await user_callback(

        query,

        user_id,

        context

    )


# =========================================================
# TEXT HANDLER
# =========================================================

async def _notify_user_admin_balance(context, telegram_id, amount, new_balance, action="topup"):
    """Notify a user after an admin manually changes the user's balance."""
    try:
        if action == "subtract":
            amount_text = f"-{format_rupiah(amount)}"
            body = (
                f"💰 Pengurangan saldo oleh admin <b>AZHURA [BOT NOKOS]</b> sebesar <b>{amount_text}</b>.\n"
                f"💳 Saldo kamu sekarang: <b>{format_rupiah(new_balance)}</b>\n\n"
                "🔥 Mohon maaf atas kekeliruan ADMIN yang salah memasukan NOMINAL🙏\n"
            )
        else:
            amount_text = f"+{format_rupiah(amount)}"
            body = (
                f"💰 Penambahan saldo oleh admin <b>AZHURA [BOT NOKOS]</b> sebesar <b>{amount_text}</b>.\n"
                f"💳 Saldo kamu sekarang: <b>{format_rupiah(new_balance)}</b>\n\n"
                "🔥 Saldo sudah masuk dan siap digunakan untuk order OTP.\n"
            )
        message = (
            "🎉 <b>INFO SALDO AZHURA [BOT NOKOS]</b>\n\n"
            + body
            + "Yuk pilih layanan favoritmu, cari harga terbaik, dan langsung order sekarang. "
              "Semoga order lancar dan cuan terus bersama AZHURA! 💎"
        )
        await context.bot.send_message(
            chat_id=telegram_id,
            text=message,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🚀 Order OTP Sekarang", callback_data="user_home")
            ]])
        )
    except Exception:
        logger.exception("Gagal mengirim notifikasi admin balance ke user=%s action=%s", telegram_id, action)


# Backward-compatible helper for any existing call sites.
async def _notify_user_admin_topup(context, telegram_id, amount, new_balance, admin_user=None):
    await _notify_user_admin_balance(context, telegram_id, amount, new_balance, "topup")


async def text_handler(
    update,
    context
):

    if not update.message:

        return

    # Admin manual balance: amount is entered after selecting a user.
    if is_admin(update.effective_user.id):
        uid = update.effective_user.id
        text = update.message.text.strip()
        target_value = context.user_data.pop("admin_balance_target", None)
        action = context.user_data.pop("admin_balance_action", "topup")
        if target_value is not None:
            target_id = int(target_value)
            try:
                normalized = text.replace(".", "").replace(",", "").replace("Rp", "").replace("rp", "").strip()
                amount = int(normalized)
                if amount <= 0:
                    raise ValueError
            except Exception:
                context.user_data["admin_balance_target"] = target_id
                context.user_data["admin_balance_action"] = action
                await update.message.reply_text(
                    "❌ Nominal tidak valid. Masukkan angka bulat, contoh: <code>10000</code>.",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Batal", callback_data=f"admin_user:{target_id}")]])
                )
                return
            try:
                reference = f"ADMIN-{uid}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
                if action == "subtract":
                    new_balance = subtract_balance(
                        target_id,
                        amount,
                        "ADMIN_DEBIT",
                        reference=reference,
                        description=f"Pengurangan saldo manual oleh admin {uid}"
                    )
                else:
                    new_balance = add_balance(
                        target_id,
                        amount,
                        "ADMIN_CREDIT",
                        reference=reference,
                        description=f"Penambahan saldo manual oleh admin {uid}"
                    )
            except Exception as error:
                logger.exception("Admin add balance gagal")
                await update.message.reply_text(
                    f"❌ Gagal menambah saldo: <code>{escape(str(error))}</code>",
                    parse_mode="HTML"
                )
                return

            if action == "subtract":
                result_text = (
                    "➖ <b>SALDO BERHASIL DIKURANGI</b>\n\n"
                    f"👤 User ID: <code>{target_id}</code>\n"
                    f"➖ Dikurangi: <b>{format_rupiah(amount)}</b>\n"
                    f"💰 Saldo baru: <b>{format_rupiah(new_balance)}</b>"
                )
            else:
                result_text = (
                    "✅ <b>SALDO BERHASIL DITAMBAHKAN</b>\n\n"
                    f"👤 User ID: <code>{target_id}</code>\n"
                    f"➕ Ditambahkan: <b>{format_rupiah(amount)}</b>\n"
                    f"💰 Saldo baru: <b>{format_rupiah(new_balance)}</b>"
                )
            await update.message.reply_text(
                result_text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("👤 Detail User", callback_data=f"admin_user:{target_id}"),
                    InlineKeyboardButton("⬅️ Users", callback_data="admin_users")
                ]])
            )
            await _notify_user_admin_balance(context, target_id, amount, new_balance, action)
            return

    # Admin search: user ID
    if is_admin(update.effective_user.id):
        uid = update.effective_user.id
        text = update.message.text.strip()
        if uid in ADMIN_SEARCH_USERS:
            ADMIN_SEARCH_USERS.discard(uid)
            try:
                target_id = int(text)
            except ValueError:
                await update.message.reply_text("❌ Telegram ID harus berupa angka.")
                return
            # Render through a lightweight synthetic callback-free message.
            with get_db() as db:
                user = db.execute("SELECT * FROM users WHERE telegram_id = %s", (target_id,)).fetchone()
            if not user:
                await update.message.reply_text("❌ User dengan ID tersebut tidak ditemukan.")
                return
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("👤 Buka Detail User", callback_data=f"admin_user:{target_id}")], [InlineKeyboardButton("⬅️ Users", callback_data="admin_users")]])
            await update.message.reply_text(
                f"🔎 User ditemukan: <b>{escape(str(user.get('first_name') or 'Tanpa nama'))}</b>\nID: <code>{target_id}</code>\nSaldo: <b>{format_rupiah(user['balance'])}</b>",
                parse_mode="HTML", reply_markup=keyboard
            )
            return
        if uid in ADMIN_SEARCH_DEPOSITS:
            ADMIN_SEARCH_DEPOSITS.discard(uid)
            try:
                target_id = int(text)
            except ValueError:
                await update.message.reply_text("❌ Telegram ID harus berupa angka.")
                return
            with get_db() as db:
                rows = db.execute(
                    """SELECT deposit_id, amount, status, created_at FROM deposits WHERE telegram_id = %s ORDER BY created_at DESC LIMIT 30""",
                    (target_id,),
                ).fetchall()
            lines = [f"🔎 <b>DEPOSIT USER {target_id}</b>"]
            if not rows:
                lines.append("\nTidak ada deposit untuk ID tersebut.")
            else:
                for row in rows:
                    lines.append(f"\n• <code>{escape(str(row['deposit_id']))}</code> | {format_rupiah(row['amount'])} | <b>{row['status']}</b> | {escape(str(row['created_at'])[:19])}")
            await update.message.reply_text(
                "".join(lines), parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Deposit", callback_data="admin_deposits")]])
            )
            return

    # Search negara dari menu pemilihan negara.
    if context.user_data.get("waiting_otp_country_search", False):
        context.user_data["waiting_otp_country_search"] = False
        server = context.user_data.get("otp_country_search_server", "5sim")
        service = context.user_data.get("otp_country_search_service", "")
        keyword = update.message.text.strip().lower()

        try:
            if server == "legacy":
                raw = await asyncio.to_thread(get_all_countries)
                items = []
                for code, data in (raw.items() if isinstance(raw, dict) else []):
                    name = data.get("text_en", code) if isinstance(data, dict) else str(data)
                    items.append({"country": str(code), "name": str(name), "cost": 0, "stock": 1})
            else:
                items = await asyncio.to_thread(get_service_countries, server, service)
        except Exception:
            items = []

        matches = []
        for item in items or []:
            name = str(item.get("name") or item.get("country_name") or item.get("country") or "")
            code = str(item.get("country") or "")
            if keyword in name.lower() or keyword in code.lower():
                matches.append(item)

        if not matches:
            await update.message.reply_text(
                "❌ Negara tidak ditemukan atau sedang tidak tersedia.\n\nCoba nama negara lain.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔎 Cari Lagi", callback_data=f"otp_country_search:{server}:{service}"),
                    InlineKeyboardButton("⬅️ Negara", callback_data=f"otp_service_countries:{server}:{service}:0")
                ]])
            )
            return

        keyboard = []
        for item in matches[:30]:
            name = str(item.get("name") or item.get("country_name") or item.get("country"))
            country = str(item.get("country") or name)
            cost = float(item.get("cost") or 0)
            stock = int(item.get("stock") or 0)
            price = hitung_harga_jual(cost) if cost > 0 else 0
            if server == "legacy":
                label = f"{country_flag(name)} {name}"
                cb = f"otp_country:{country}"
            else:
                if server == "rumahotp":
                    live_cost = float(item.get("cost_idr") or item.get("price_idr") or 0)
                    price = int(round(live_cost * (1 + PROFIT_PERCENT / 100) / 100) * 100) if live_cost > 0 else 0
                    label = f"{country_flag(item.get('iso_code') or name)} {name} | mulai {format_rupiah(price)} | 📦 {stock}"
                    cb = f"otp_choose_server:{server}:{service}:{country}"
                else:
                    label = f"{country_flag(item.get('iso_code') or name)} {name} | 💰 {format_rupiah(price)} | 📦 {stock}"
                    cb = f"otp_choose_server:{server}:{service}:{country}"
            keyboard.append([InlineKeyboardButton(label, callback_data=cb)])

        back_cb = "order" if server == "legacy" else f"otp_service_countries:{server}:{service}:0"
        keyboard.append([
            InlineKeyboardButton("🔎 Cari Lagi", callback_data=f"otp_country_search:{server}:{service}"),
            InlineKeyboardButton("⬅️ Negara", callback_data=back_cb)
        ])
        await update.message.reply_text(
            "🔎 <b>HASIL PENCARIAN NEGARA</b>\n\n"
            f"Kata kunci: <code>{keyword}</code>\n"
            f"Ditemukan: <b>{len(matches)}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # Search layanan OTP dari menu service.
    if context.user_data.get(
        "waiting_otp_search",
        False
    ):
        context.user_data["waiting_otp_search"] = False

        server = context.user_data.get(
            "otp_search_server",
            "5sim"
        )
        keyword = update.message.text.strip().lower()

        services = await asyncio.to_thread(
            get_service_catalog,
            server
        )

        matches = [
            (code, name)
            for code, name in services
            if keyword in code.lower()
            or keyword in name.lower()
        ]

        if not matches:
            await update.message.reply_text(
                "❌ Layanan tidak ditemukan.\n\n"
                "Coba kata kunci lain.",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "⬅️ Kembali",
                            callback_data=f"otp_server:{server}"
                        )
                    ]
                ])
            )
            return

        keyboard = []
        for code, name in matches[:16]:
            keyboard.append([
                InlineKeyboardButton(
                    name,
                    callback_data=f"otp_service:{server}:{code}"
                )
            ])

        await update.message.reply_text(
            "🔎 <b>HASIL PENCARIAN</b>\n\n"
            f"Kata kunci: <code>{keyword}</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if not context.chat_data.get(

        "waiting_deposit",

        False

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
            "Contoh: <code>10000</code>",

            parse_mode="HTML"

        )

        return

    amount = int(
        text
    )

    if amount < 1000:

        await update.message.reply_text(

            "❌ <b>Deposit terlalu kecil.</b>\n\n"
            "Minimum deposit adalah "
            "<b>Rp1.000</b>.",

            parse_mode="HTML"

        )

        return

    if amount % 1000 != 0:

        await update.message.reply_text(

            "❌ <b>Nominal tidak valid.</b>\n\n"
            "Deposit harus kelipatan "
            "<b>Rp1.000</b>.",

            parse_mode="HTML"

        )

        return

    create_user(

        user.id,

        user.username,

        user.first_name

    )

    deposit_id = (

        "DEP-"
        +
        uuid.uuid4()
        .hex[:12]
        .upper()

    )

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
            VALUES (%s,%s,%s,%s,%s)
            """,

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

        snap_url = snap_data.get(
            "redirect_url"
        )

        snap_token = snap_data.get(
            "token"
        )

        if not snap_url or not snap_token:

            raise RuntimeError(
                "Respons Midtrans tidak lengkap."
            )

        with get_db() as db:

            db.execute(

                """
                UPDATE deposits
                SET payment_reference = %s
                WHERE deposit_id = %s
                """,

                (
                    snap_token,
                    deposit_id
                )

            )

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

        await update.message.reply_text(

            f"💳 <b>Invoice Deposit Dibuat</b>\n\n"

            f"🧾 ID: "
            f"<code>{deposit_id}</code>\n"

            f"💰 Nominal: "
            f"<b>{format_rupiah(amount)}</b>\n"

            f"📌 Status: <b>PENDING</b>\n\n"

            "Klik tombol di bawah "
            "untuk melakukan pembayaran.",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup(
                keyboard
            )

        )

    except Exception:

        logger.exception(
            "Gagal membuat invoice Midtrans."
        )

        with get_db() as db:

            db.execute(

                """
                UPDATE deposits
                SET status = 'FAILED'
                WHERE deposit_id = %s
                AND status = 'PENDING'
                """,

                (
                    deposit_id,
                )

            )

        await update.message.reply_text(

            "❌ <b>Gagal membuat invoice "
            "pembayaran.</b>\n\n"
            "Silakan coba lagi.",

            parse_mode="HTML"

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

    if len(
        context.args
    ) != 2:

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

            "❌ Telegram ID dan nominal "
            "harus angka."

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
                +
                uuid.uuid4()
                .hex[:8]
                .upper()

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

        f"✅ <b>Saldo berhasil "
        f"ditambahkan.</b>\n\n"

        f"👤 User: "
        f"<code>{telegram_id}</code>\n"

        f"💰 Saldo baru: "
        f"<b>{format_rupiah(new_balance)}</b>",

        parse_mode="HTML"

    )

    await _notify_user_admin_balance(
        context, telegram_id, amount, new_balance, "topup"
    )


# =========================================================
# AUTO EXPIRE OTP ORDERS
# =========================================================

def _parse_expiry_timestamp(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number / 1000.0 if number > 10_000_000_000 else number
    raw = str(value).strip()
    try:
        number = float(raw)
        return number / 1000.0 if number > 10_000_000_000 else number
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).timestamp()
    except Exception:
        return None


def _pending_expired_orders():
    with get_db() as db:
        return db.execute("""
            SELECT * FROM orders
            WHERE status = 'PENDING'
              AND refund_status = 'NONE'
              AND provider_order_id IS NOT NULL
              AND expired_at IS NOT NULL
            ORDER BY created_at ASC
            LIMIT 50
        """).fetchall()


async def _auto_expire_one_order(application, order):
    order_id = str(order.get("order_id"))
    provider = str(order.get("provider") or "5sim").lower()
    provider_order_id = str(order.get("provider_order_id") or "").strip()
    if not provider_order_id:
        return
    expiry_ts = _parse_expiry_timestamp(order.get("expired_at"))
    if expiry_ts is None or datetime.now(timezone.utc).timestamp() < expiry_ts:
        return

    try:
        if provider == "rumahotp":
            # cancel_number performs its own status verification and, when the
            # provider cost is known, balance-settlement verification. Do not
            # call get_status first here: that would consume an extra RumahOTP
            # request and could push the 5-requests/10-seconds limit.
            cancel_result = await asyncio.to_thread(
                cancel_rumahotp_number,
                provider_order_id,
                order.get("provider_cost") or 0,
            )
            if not cancel_result or cancel_result.get("response") != "OK":
                logger.warning(
                    "[AUTO EXPIRE] RumahOTP cancel not confirmed order=%s result=%s",
                    order_id, cancel_result,
                )
                await asyncio.to_thread(
                    request_order_cancel,
                    order_id,
                    str((cancel_result or {}).get("error") or "RumahOTP cancel belum terkonfirmasi."),
                )
                return
        else:
            state = await asyncio.to_thread(get_sms, provider_order_id)
            status = str((state or {}).get("status") or (state or {}).get("response") or "").strip().lower()
            if status in {"status_ok", "access_activation"}:
                mark_order_success(order_id)
                return
            if status in {"no_activation", "no activation"}:
                status = "status_cancel"
            if status not in {"status_cancel", "access_cancel", "cancel", "canceled", "cancelled"}:
                cancel_result = await asyncio.to_thread(cancel_number, provider_order_id)
                raw_response = str((cancel_result or {}).get("response") or "").upper()
                if raw_response not in {"ACCESS_CANCEL", "STATUS_CANCEL"}:
                    logger.warning("[AUTO EXPIRE] 5SIM cancel not confirmed order=%s result=%s", order_id, cancel_result)
                    return

        result = await asyncio.to_thread(refund_order, order_id, f"Order {provider.upper()} otomatis dibatalkan karena melewati masa aktif 20 menit.")
        if result.get("refunded"):
            try:
                await application.bot.send_message(
                    chat_id=int(order["telegram_id"]),
                    text=(
                        "⏰ <b>ORDER OTP EXPIRED OTOMATIS</b>\n\n"
                        f"🧾 Order: <code>{escape(order_id)}</code>\n"
                        
                        "⌛ Masa aktif 20 menit telah habis tanpa penggunaan nomor.\n"
                        f"💸 Saldo dikembalikan: <b>{format_rupiah(order['sell_price'])}</b>\n"
                        f"💳 Saldo kamu sekarang: <b>{format_rupiah(result['balance'])}</b>"
                    ),
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📱 Order OTP Lagi", callback_data="order")]]),
                )
            except Exception:
                logger.exception("[AUTO EXPIRE] notification failed order=%s", order_id)
            logger.info("[AUTO EXPIRE] refunded order=%s provider=%s", order_id, provider)
    except Exception:
        logger.exception("[AUTO EXPIRE] error order=%s provider=%s", order_id, provider)




async def rumahotp_cancel_worker(application):
    """Persistent automatic cancellation/reconciliation for RumahOTP."""
    logger.info("[RUMAHOTP CANCEL WORKER] started; interval=15s")
    while True:
        try:
            orders = await asyncio.to_thread(get_cancel_queue, 10)
            for order in orders:
                order_id = str(order.get("order_id"))
                provider_order_id = str(order.get("provider_order_id") or "").strip()
                if not provider_order_id:
                    continue
                try:
                    result = await asyncio.to_thread(
                        cancel_rumahotp_number,
                        provider_order_id,
                        order.get("provider_cost") or 0,
                    )
                    logger.info("[RUMAHOTP CANCEL WORKER] order=%s result=%s", order_id, result)
                    if result and result.get("response") == "OK":
                        await asyncio.to_thread(clear_order_cancel_request, order_id)
                        refund = await asyncio.to_thread(
                            refund_order,
                            order_id,
                            "Order RumahOTP otomatis dibatalkan setelah provider mengonfirmasi cancel."
                        )
                        if refund.get("refunded"):
                            try:
                                await application.bot.send_message(
                                    chat_id=int(order["telegram_id"]),
                                    text=(
                                        "✅ <b>ORDER BERHASIL DIBATALKAN OTOMATIS</b>\n\n"
                                        f"🧾 Order: <code>{escape(order_id)}</code>\n"
                                        
                                        f"💸 Saldo dikembalikan: <b>{format_rupiah(order['sell_price'])}</b>\n"
                                        f"💳 Saldo kamu sekarang: <b>{format_rupiah(refund['balance'])}</b>"
                                    ),
                                    parse_mode="HTML",
                                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📱 Order OTP Lagi", callback_data="order")]])
                                )
                            except Exception:
                                logger.exception("[RUMAHOTP CANCEL WORKER] notification failed order=%s", order_id)
                except Exception:
                    logger.exception("[RUMAHOTP CANCEL WORKER] error order=%s", order_id)
        except asyncio.CancelledError:
            logger.info("[RUMAHOTP CANCEL WORKER] stopped")
            raise
        except Exception:
            logger.exception("[RUMAHOTP CANCEL WORKER] loop error")
        await asyncio.sleep(15)

async def auto_expire_worker(application):
    logger.info("[AUTO EXPIRE] worker started; interval=30s")
    while True:
        try:
            for order in await asyncio.to_thread(_pending_expired_orders):
                await _auto_expire_one_order(application, order)
        except asyncio.CancelledError:
            logger.info("[AUTO EXPIRE] worker stopped")
            raise
        except Exception:
            logger.exception("[AUTO EXPIRE] worker loop error")
        await asyncio.sleep(30)


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update,
    context
):

    logger.error(

        "Exception while handling update:",

        exc_info=context.error

    )


# =========================================================
# FLASK THREAD
# =========================================================

def run_flask():

    port = int(

        os.getenv(
            "PORT",
            "5000"
        )

    )

    logger.info(
        "Flask webhook berjalan di port %s",
        port
    )

    app.run(

        host="0.0.0.0",

        port=port

    )


# =========================================================
# RUN BOT
# =========================================================

def run():

    init_database()

    application = (

        Application
        .builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()

    )

    # -----------------------------------------------------
    # START
    # -----------------------------------------------------

    application.add_handler(

        CommandHandler(
            "start",
            start
        )

    )

    # -----------------------------------------------------
    # ADD BALANCE
    # -----------------------------------------------------

    application.add_handler(

        CommandHandler(
            "addbalance",
            admin_add_balance
        )

    )

    application.add_handler(CommandHandler("server1", lambda u, c: command_server(u, c, "5sim")))
    application.add_handler(CommandHandler("server2", lambda u, c: command_server(u, c, "rumahotp")))
    application.add_handler(CommandHandler("deposit", command_deposit))
    application.add_handler(CommandHandler("checkin", perform_checkin))

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

            filters.TEXT
            &
            ~filters.COMMAND,

            text_handler

        )

    )

    # -----------------------------------------------------
    # ERROR
    # -----------------------------------------------------

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "Bot berhasil dijalankan."
    )

    # -----------------------------------------------------
    # FLASK
    # -----------------------------------------------------

    threading.Thread(

        target=run_flask,

        daemon=True

    ).start()

    # -----------------------------------------------------
    # TELEGRAM
    # -----------------------------------------------------

    application.run_polling(

        allowed_updates=
            Update.ALL_TYPES

    )


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    run()
