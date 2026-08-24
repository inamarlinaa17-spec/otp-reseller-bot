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
from datetime import timedelta
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
    hitung_harga_jual,
    buy_number,
    get_sms,
    cancel_number
)

from aggregator import (
    get_aggregated_countries,
    get_aggregated_quotes,
    get_aggregator_service_catalog,
    _operator_label,
    _is_displayable_operator,
    get_rumahotp_operator_quotes_for_country,
)

from rumahotp import (
    check_api as check_rumahotp_api,
    get_balance as get_rumahotp_balance,
    buy_number as buy_rumahotp_number,
    get_sms as get_rumahotp_sms,
    cancel_number as cancel_rumahotp_number,
)

from smspool import (
    check_api as check_smspool_api,
    get_balance as get_smspool_balance,
    get_prices as get_smspool_prices,
    get_suggested_countries as get_smspool_suggested_countries,
    get_all_countries as get_smspool_countries,
    get_all_services as get_smspool_services,
    find_service as find_smspool_service,
    buy_number as buy_smspool_number,
    get_sms as get_smspool_sms,
    cancel_number as cancel_smspool_number
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
PRODUCTS_PER_PAGE = 12
SERVICES_PER_PAGE = 16


# =========================================================
# SERVER OTP
# =========================================================

OTP_SERVERS = {

    # Server 1 = RumahOTP
    "rumahotp":
        "⚡ Server 1",

    # Server 2 = SMSPool
    "smspool":
        "⚡ Server 2",

    # Server 3 = 5SIM
    "5sim":
        "⚡ Server 3",

    "aggregator":
        "🔥 Multi Server"

}


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

        [
            InlineKeyboardButton(
                OTP_SERVERS["rumahotp"],
                callback_data="otp_server:rumahotp"
            )
        ],

        [
            InlineKeyboardButton(
                OTP_SERVERS["smspool"],
                callback_data="otp_server:smspool"
            )
        ],

        [
            InlineKeyboardButton(
                OTP_SERVERS["5sim"],
                callback_data="otp_server:5sim"
            )
        ],

        [
            InlineKeyboardButton(
                OTP_SERVERS["aggregator"],
                callback_data="otp_server:aggregator"
            )
        ],

        [
            InlineKeyboardButton(
                "🏠 Menu Utama",
                callback_data="user_home"
            )
        ]

    ]

    await query.edit_message_text(
        "🌟 <b>PILIH SERVER OTP</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚡ <b>SERVER 1 — HIGH STOCK</b>\n"
        "Server utama dengan stok nomor dalam jumlah besar dan performa stabil.\n\n"
        "⚡ <b>SERVER 2 — HARGA LEBIH RENDAH</b>\n"
        "Harga umumnya lebih rendah dari Server 1.\n\n"
        "⚡ <b>SERVER 3 — STOCK & VARIAN</b>\n"
        "Stock banyak dengan pilihan harga yang bervariasi.\n\n"
        "🔥 <b>MULTI SERVER</b> — Pilihan banyak negara, layanan, operator, serta harga/stok dari beberapa server sekaligus.\n\n"
        "Silakan pilih server melalui tombol di bawah ini:",
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
    """Katalog layanan untuk provider dan Multi Server."""
    catalog = list(OTP_SERVICES)
    seen = {code.lower() for code, _ in catalog}

    if server == "aggregator":
        # Aggregator memakai satu canonical key per layanan. Saat order,
        # aggregator.py menerjemahkan key tersebut ke ID layanan masing-masing provider.
        try:
            return get_aggregator_service_catalog()
        except Exception:
            logger.exception("Aggregator: gagal mengambil katalog gabungan")
            return catalog

    if server == "5sim":
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

    elif server == "smspool":
        _merge_service_catalog(
            catalog, seen, get_smspool_services(),
            ("ID", "id", "service_id", "name", "service"),
            ("name", "service", "title")
        )



    return catalog


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
        f"Server: <b>{OTP_SERVERS.get(server, server)}</b>\n\n"
        "Pilih layanan OTP:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


def _country_items_5sim(service):
    data = get_prices(product=service)

    if not isinstance(data, dict):
        return []

    # Endpoint product dapat mengembalikan:
    # {service: {country: {operator: {cost,count}}}}
    if service in data and isinstance(data.get(service), dict):
        data = data[service]

    items = []

    for country, country_data in data.items():
        if not isinstance(country_data, dict):
            continue

        best = None

        for operator, info in country_data.items():
            if not isinstance(info, dict):
                continue
            try:
                cost = float(info.get("cost", 0) or 0)
                count = int(info.get("count", 0) or 0)
            except Exception:
                continue

            if cost <= 0 or count <= 0:
                continue

            if best is None or cost < best["cost"]:
                best = {
                    "country": str(country),
                    "name": str(country).replace("_", " ").title(),
                    "cost": cost,
                    "stock": count
                }

        if best:
            items.append(best)

    return items


def _flatten_smspool_prices(data, fallback_service):
    """Normalisasi beberapa bentuk response SMSPool price endpoint."""
    result = []

    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            country = (
                item.get("country")
                or item.get("country_name")
                or item.get("short_name")
                or item.get("cc")
            )
            cost = (
                item.get("cost")
                or item.get("price")
                or item.get("amount")
            )
            stock = (
                item.get("count")
                or item.get("stock")
                or item.get("available")
                or 1
            )
            try:
                cost = float(cost or 0)
                stock = int(stock or 0)
            except Exception:
                continue
            if country and cost > 0 and stock > 0:
                result.append({
                    "country": str(country),
                    "name": str(country),
                    "cost": cost,
                    "stock": stock
                })
        return result

    if not isinstance(data, dict):
        return result

    # Bentuk umum: {country: {...}}
    for country, value in data.items():
        if not isinstance(value, (dict, list)):
            continue

        # country-level langsung
        if isinstance(value, dict):
            direct_cost = (
                value.get("cost")
                or value.get("price")
                or value.get("amount")
            )
            direct_stock = (
                value.get("count")
                or value.get("stock")
                or value.get("available")
                or 1
            )

            if direct_cost is not None:
                try:
                    cost = float(direct_cost)
                    stock = int(direct_stock or 0)
                except Exception:
                    cost, stock = 0, 0
                if cost > 0 and stock > 0:
                    result.append({
                        "country": str(country),
                        "name": str(
                            value.get("country_name")
                            or value.get("name")
                            or country
                        ),
                        "cost": cost,
                        "stock": stock
                    })
                    continue

            # nested service/operator/pool
            candidates = []
            for key, sub in value.items():
                if isinstance(sub, dict):
                    c = (
                        sub.get("cost")
                        or sub.get("price")
                        or sub.get("amount")
                    )
                    s = (
                        sub.get("count")
                        or sub.get("stock")
                        or sub.get("available")
                        or 1
                    )
                    if c is not None:
                        try:
                            c = float(c)
                            s = int(s or 0)
                        except Exception:
                            continue
                        if c > 0 and s > 0:
                            candidates.append((c, s))

            if candidates:
                cost, stock = min(candidates, key=lambda x: x[0])
                result.append({
                    "country": str(country),
                    "name": str(country).replace("_", " ").title(),
                    "cost": cost,
                    "stock": stock
                })

    return result


def _flatten_smspool_suggested_countries(data):
    result = []
    if not isinstance(data, list):
        return result

    for item in data:
        if not isinstance(item, dict):
            continue
        country = item.get("country_id") or item.get("country") or item.get("ID")
        name = item.get("name") or item.get("country_name") or item.get("short_name") or str(country or "")
        price = item.get("price") or item.get("cost")
        try:
            cost = float(price or 0)
        except Exception:
            continue
        if country is not None and cost > 0:
            result.append({
                "country": str(country),
                "name": str(name),
                "cost": cost,
                # suggested_countries tidak mengembalikan stock count;
                # gunakan 1 sebagai indikator bahwa provider menyarankan negara ini.
                "stock": 1
            })
    return result


def _smspool_country_name_map():
    data = get_smspool_countries()
    mapping = {}

    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, dict):
                name = (
                    value.get("name")
                    or value.get("country")
                    or value.get("text")
                    or value.get("country_name")
                )
                if name:
                    mapping[str(key)] = str(name)
            elif value:
                mapping[str(key)] = str(value)

    elif isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            key = (
                item.get("ID")
                or item.get("id")
                or item.get("country")
                or item.get("code")
            )
            name = (
                item.get("name")
                or item.get("country_name")
                or item.get("country")
            )
            if key and name:
                mapping[str(key)] = str(name)

    return mapping


def get_service_countries(server, service):
    if server == "aggregator":
        return get_aggregated_countries(service)

    if server == "5sim":
        return _country_items_5sim(service)


    # SMSPool: gunakan endpoint all_stock yang mengembalikan country,
    # service, stock dan price. Jika kosong/error, coba suggested_countries
    # sebagai fallback sehingga menu negara + harga tetap dapat ditampilkan.
    found = find_smspool_service(service)
    lookup_service = (
        found.get("id")
        if found and found.get("id") is not None
        else service
    )

    data = get_smspool_prices(service=lookup_service)
    items = _flatten_smspool_prices(data, service)

    if not items:
        try:
            suggested = get_smspool_suggested_countries(lookup_service)
            items = _flatten_smspool_suggested_countries(suggested)
        except Exception as error:
            logger.warning(
                "SMSPool suggested countries fallback failed: %s", error
            )

    names = _smspool_country_name_map()
    for item in items:
        item["name"] = names.get(
            str(item["country"]),
            item["name"]
        )

    return items


async def show_service_country_page(
    query,
    server,
    service,
    page=0
):
    """Menampilkan hanya negara yang punya stok untuk service yang dipilih."""

    service_label = dict(OTP_SERVICES).get(
        service,
        service.title()
    )

    try:
        items = await asyncio.wait_for(
            asyncio.to_thread(
                get_service_countries,
                server,
                service
            ),
            timeout=20
        )
    except asyncio.TimeoutError:
        logger.warning(
            "OTP stock timeout: server=%s service=%s",
            server,
            service
        )
        await query.edit_message_text(
            "⚠️ <b>Provider terlalu lama merespons.</b>\n\n"
            f"🖥 Server: <b>{OTP_SERVERS.get(server, server)}</b>\n"
            f"📱 Layanan: <b>{service_label}</b>\n\n"
            "Silakan tekan Refresh dan coba lagi.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "🔄 Refresh",
                    callback_data=f"otp_service:{server}:{service}"
                )],
                [InlineKeyboardButton(
                    "⬅️ Pilih Layanan",
                    callback_data=f"otp_server:{server}"
                )]
            ])
        )
        return
    except Exception as error:
        logger.exception(
            "OTP stock error: server=%s service=%s",
            server,
            service
        )
        await query.edit_message_text(
            "⚠️ <b>Gagal mengambil stok provider.</b>\n\n"
            f"🖥 Server: <b>{OTP_SERVERS.get(server, server)}</b>\n"
            f"📱 Layanan: <b>{service_label}</b>\n\n"
            "Periksa API provider atau tekan Refresh.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "🔄 Refresh",
                    callback_data=f"otp_service:{server}:{service}"
                )],
                [InlineKeyboardButton(
                    "⬅️ Pilih Layanan",
                    callback_data=f"otp_server:{server}"
                )]
            ])
        )
        return

    # Multi Server: urutkan negara dari harga termurah ke termahal.
    # Jika harga sama, gunakan stok terbesar lalu nama negara.
    if server == "aggregator":
        items.sort(
            key=lambda x: (
                float(x.get("cost") or 999999),
                -int(x.get("stock") or 0),
                str(x.get("name") or "").lower()
            )
        )
    else:
        # Server individual tetap memakai urutan lama dengan Indonesia di atas.
        items.sort(
            key=lambda x: (
                0 if str(x["name"]).lower() == "indonesia"
                or str(x["country"]).lower() == "indonesia"
                else 1,
                str(x["name"]).lower()
            )
        )

    if not items:
        await query.edit_message_text(
            "❌ <b>Stok tidak tersedia</b>\n\n"
            f"🖥 Server: <b>{OTP_SERVERS.get(server, server)}</b>\n"
            f"📱 Layanan: <b>{service_label}</b>\n\n"
            "Saat ini tidak ada negara yang memiliki "
            "stok untuk layanan tersebut.",
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

    total_pages = (
        len(items) + COUNTRIES_PER_PAGE - 1
    ) // COUNTRIES_PER_PAGE
    page = max(0, min(page, total_pages - 1))

    page_items = items[
        page * COUNTRIES_PER_PAGE:
        (page + 1) * COUNTRIES_PER_PAGE
    ]

    keyboard = []

    if server == "aggregator":
        # get_aggregated_countries() sudah berisi harga termurah dan stok
        # gabungan dari RumahOTP + SMSPool + 5SIM. Tampilkan 2 kolom agar
        # daftar negara tidak memanjang ke bawah.
        country_buttons = []
        for item in page_items:
            cost = float(item.get("cost") or 0)
            stock = int(item.get("stock") or 0)
            if cost > 0 and stock > 0:
                price = hitung_harga_jual(cost)
                label = (
                    f"🌍 {item['name']}\n"
                    f"💰 {format_rupiah(price)} | 📦 {stock}"
                )
            else:
                label = f"🌍 {item['name']}\n❌ Tidak tersedia"
            country_buttons.append(
                InlineKeyboardButton(
                    label,
                    callback_data=f"otp_operators:{service}:{item['country']}"
                )
            )

        for i in range(0, len(country_buttons), 2):
            keyboard.append(country_buttons[i:i + 2])
    else:
        for item in page_items:
            price = hitung_harga_jual(item["cost"])
            keyboard.append([
                InlineKeyboardButton(
                    (
                        f"🌍 {item['name']}\n"
                        f"💰 {format_rupiah(price)}"
                        f"  |  📦 {item['stock']}"
                    ),
                    callback_data=(
                        f"otp_buy:{server}:{service}:"
                        f"{item['country']}"
                    )
                )
            ])

    # Navigasi negara + tombol kembali ke layanan.
    nav = []
    if total_pages > 1:
        nav.extend([
            InlineKeyboardButton("◀️", callback_data=f"otp_service_countries:{server}:{service}:{max(0, page - 1)}"),
            InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="otp_noop"),
            InlineKeyboardButton("▶️", callback_data=f"otp_service_countries:{server}:{service}:{min(total_pages - 1, page + 1)}"),
        ])
        keyboard.append(nav)
    keyboard.append([
        InlineKeyboardButton("🔎 Cari Negara", callback_data=f"otp_country_search:{server}:{service}")
    ])
    keyboard.append([
        InlineKeyboardButton("↩️ Kembali", callback_data=f"otp_server:{server}"),
        InlineKeyboardButton("🏠 Menu Utama", callback_data="user_home")
    ])

    await query.edit_message_text(
        "🌍 <b>PILIH NEGARA</b>\n\n"
        f"🖥 Server: <b>{OTP_SERVERS.get(server, server)}</b>\n"
        f"📱 Layanan: <b>{service_label}</b>\n\n"
        "Hanya negara dengan stok aktif yang ditampilkan.",
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

            "❌ <b>Provider 5SIM tidak dapat "
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

                f"🌍 {name}",

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
        f"🌍 Pilih negara nomor.\n\n"
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
            "Provider tidak mengembalikan "
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

            f"🌍 <b>{country}</b>\n\n"
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

        f"🌍 Negara: <b>{country}</b>\n\n"

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
    ("server3", "List Layanan Server3"),
    ("multiserver", "List Layanan Multi Server"),
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
        f"Server: <b>{OTP_SERVERS.get(server, server)}</b>\n\n"
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


# =========================================================
# USER CALLBACK
# =========================================================


async def show_aggregated_operator_page(query, service, country):
    """MOCHI-style aggregator step: country -> carrier -> price/stock.

    Only real carrier/operator labels are shown individually. Provider
    internals such as 5SIM virtual pools stay inside the catch-all option.
    """
    service_label = dict(OTP_SERVICES).get(service, service)
    try:
        # Operator list must load the full set of quotes for the selected
        # country/service first.  `operator` is not defined at this stage;
        # it is supplied only after the user taps a carrier button.
        quotes = await asyncio.wait_for(
            asyncio.to_thread(get_aggregated_quotes, country, service, None),
            timeout=25
        )
    except asyncio.TimeoutError:
        await query.edit_message_text(
            "⚠️ <b>Provider terlalu lama merespons.</b>\n\n"
            "Silakan refresh dan coba lagi.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh", callback_data=f"otp_operators:{service}:{country}")
            ], [
                InlineKeyboardButton("⬅️ Negara", callback_data=f"otp_service_countries:aggregator:{service}:0")
            ]])
        )
        return

    if not quotes:
        await query.edit_message_text(
            "❌ <b>Stok tidak tersedia</b>\n\n"
            f"🌍 Negara: <b>{country}</b>\n"
            f"📱 Layanan: <b>{service_label}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh", callback_data=f"otp_operators:{service}:{country}"),
                InlineKeyboardButton("⬅️ Negara", callback_data=f"otp_service_countries:aggregator:{service}:0")
            ]])
        )
        return

    # Group only genuine operator/carrier names. AUTO/virtual pools remain
    # available through "Semua Operator (Acak)" and are never exposed.
    groups = {}
    has_real_operator = False
    for q in quotes:
        raw_operator = q.get("operator") or "AUTO"
        operator_label = _operator_label(raw_operator)
        if not _is_displayable_operator(operator_label):
            continue

        has_real_operator = True
        key = _norm(operator_label)
        group = groups.setdefault(
            key,
            {"operator": operator_label, "stock": 0, "cost": None}
        )
        group["stock"] += int(q.get("stock") or 0)
        cost = float(q.get("cost_usd") or 0)
        if cost > 0 and (group["cost"] is None or cost < group["cost"]):
            group["cost"] = cost

    # RumahOTP exposes real carrier names through a dedicated operator endpoint.
    try:
        rumah_quotes = await asyncio.to_thread(get_rumahotp_operator_quotes_for_country, country, service)
        for q in rumah_quotes:
            label = _operator_label(q.get("operator"))
            if not _is_displayable_operator(label):
                continue
            key = _norm(label)
            group = groups.setdefault(key, {"operator": label, "stock": 0, "cost": None})
            group["stock"] += int(q.get("stock") or 0)
            cost = float(q.get("cost_usd") or 0)
            if cost > 0 and (group["cost"] is None or cost < group["cost"]):
                group["cost"] = cost
    except Exception:
        logger.exception("RumahOTP operator list failed")

    operators = sorted(
        groups.values(),
        key=lambda x: (
            x["cost"] if x["cost"] is not None else 999999,
            x["operator"]
        )
    )

    keyboard = []
    for item in operators:
        price = hitung_harga_jual(item["cost"]) if item["cost"] else 0
        keyboard.append([
            InlineKeyboardButton(
                f"📡 {item['operator']} | 💰 mulai {format_rupiah(price)} | 📦 {item['stock']}",
                callback_data=f"otp_operator:{service}:{country}:{item['operator']}"
            )
        ])

    # Match the requested MOCHI-style catch-all button.
    keyboard.append([
        InlineKeyboardButton(
            "🌐 Semua Operator (Acak)",
            callback_data=f"otp_quotes:{service}:{country}"
        )
    ])
    keyboard.append([
        InlineKeyboardButton(
            "🔄 Refresh",
            callback_data=f"otp_operators:{service}:{country}"
        ),
        InlineKeyboardButton(
            "⬅️ Negara",
            callback_data=f"otp_service_countries:aggregator:{service}:0"
        )
    ])

    if not has_real_operator:
        intro = (
            "📡 <b>Operator spesifik belum diberikan oleh provider untuk negara ini.</b>\n"
            "Gunakan <b>Semua Operator (Acak)</b> untuk memilih otomatis dari seluruh stok aktif.\n\n"
        )
    else:
        intro = "📡 <b>Pilih operator untuk melihat harga dan stok yang tersedia:</b>\n\n"

    await query.edit_message_text(
        "✨ <b>LAYANAN TERPILIH</b>\n\n"
        f"📱 Layanan: <b>{service_label}</b>\n"
        f"🌍 Negara: <b>{country}</b>\n\n"
        f"{intro}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_aggregated_quotes_page(query, user_id, service, country, operator=None):
    service_label = dict(OTP_SERVICES).get(service, service)
    try:
        quotes = await asyncio.wait_for(
            asyncio.to_thread(get_aggregated_quotes, country, service),
            timeout=25
        )
    except asyncio.TimeoutError:
        await query.edit_message_text(
            "⚠️ <b>Provider terlalu lama merespons.</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh", callback_data=f"otp_quotes:{service}:{country}")
            ], [
                InlineKeyboardButton("⬅️ Negara", callback_data=f"otp_service_countries:aggregator:{service}:0")
            ]])
        )
        return

    if operator and operator.upper() != "ALL":
        operator_key = operator.strip().lower()
        quotes = [q for q in quotes if str(q.get("operator") or "AUTO").strip().lower() == operator_key]

    if not quotes:
        await query.edit_message_text(
            "❌ <b>Stok tidak tersedia</b>\n\n"
            f"🌍 Negara: <b>{country}</b>\n"
            f"📱 Layanan: <b>{service_label}</b>\n\n"
            "Tidak ada quote aktif dari provider yang tersedia.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh", callback_data=f"otp_quotes:{service}:{country}")
            ], [
                InlineKeyboardButton("⬅️ Negara", callback_data=f"otp_service_countries:aggregator:{service}:0")
            ]])
        )
        return

    keyboard = []
    for q in quotes:
        quote_id = uuid.uuid4().hex[:10].upper()
        save_otp_quote(
            quote_id=quote_id, telegram_id=user_id, provider=q["provider"],
            country=q["country"], service=q["service"],
            operator=q.get("provider_operator") or q.get("operator"),
            pool=q.get("pool"), cost_usd=q["cost_usd"], stock=q["stock"],
            country_name=q.get("country_name") or country
        )
        sell = hitung_harga_jual(q["cost_usd"])
        # Keep provider identities private. Users only see the internal server number.
        server_label = {
            "rumahotp": "⚡ Server 1",
            "smspool": "⚡ Server 2",
            "5sim": "⚡ Server 3",
        }.get(q.get("provider"), "⚡ Server")
        keyboard.append([
            InlineKeyboardButton(
                f"{server_label} | 💰 {format_rupiah(sell)} | 📦 {q['stock']}",
                callback_data=f"otp_quote:{quote_id}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton("🔄 Refresh", callback_data=f"otp_quotes:{service}:{country}"),
        InlineKeyboardButton("📡 Operator", callback_data=f"otp_operators:{service}:{country}"),
        InlineKeyboardButton("⬅️ Negara", callback_data=f"otp_service_countries:aggregator:{service}:0")
    ])

    operator_label = ("Semua Operator" if not operator or operator.upper() == "ALL"
                      else ("Semua / Otomatis" if operator.upper() == "AUTO" else operator.replace("_", " ").title()))
    await query.edit_message_text(
        "✨ <b>HARGA TERBAIK</b>\n\n"
        f"🌍 Negara: <b>{country}</b>\n"
        f"📱 Layanan: <b>{service_label}</b>\n"
        f"📡 Operator: <b>{operator_label}</b>\n\n"
        "Pilih harga yang ingin digunakan:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


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

    service_label = dict(OTP_SERVICES).get(service, service)
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
    elif server == "5sim":
        operator_info = await asyncio.to_thread(
            get_cheapest_operator,
            country,
            service
        )
        if not operator_info:
            await query.edit_message_text(
                "❌ <b>Stok habis.</b>\n\n"
                f"🌍 Negara: <b>{country}</b>\n"
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

    elif server == "smspool":
        found = await asyncio.to_thread(
            find_smspool_service,
            service
        )
        lookup_service = (
            found.get("id")
            if found and found.get("id") is not None
            else service
        )

        price_data = await asyncio.to_thread(
            get_smspool_prices,
            country,
            lookup_service
        )
        quotes = _flatten_smspool_prices(
            price_data,
            service
        )

        # Jika response memakai country sebagai key, pilih quote pertama.
        quote = None
        for q in quotes:
            if str(q["country"]).lower() == str(country).lower():
                quote = q
                break
        if quote is None and quotes:
            quote = quotes[0]

        if not quote:
            await query.edit_message_text(
                "❌ <b>Harga/stok SMSPOOL tidak tersedia.</b>\n\n"
                f"🌍 Negara: <b>{country}</b>\n"
                f"📱 Layanan: <b>{service_label}</b>\n\n"
                "Silakan refresh atau pilih negara lain.",
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

        operator = "AUTO"
        provider_cost_usd = float(quote["cost"])

    else:
        await query.answer("Server tidak tersedia.", show_alert=True)
        return

    sell_price = hitung_harga_jual(provider_cost_usd)
    current_balance = get_balance(user_id)

    if current_balance < sell_price:
        await query.edit_message_text(
            "❌ <b>Saldo tidak cukup.</b>\n\n"
            f"🌍 Negara: <b>{display_country}</b>\n"
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
        f"🖥 Server: <b>{OTP_SERVERS.get(server, server)}</b>\n"
        f"🌍 Negara: <b>{country}</b>\n"
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

    # -----------------------------------------------------
    # BELI NOMOR
    # -----------------------------------------------------
    if server == "5sim":
        result = await asyncio.to_thread(buy_number, country, service, operator)
        provider_order_id = result.get("id") if result else None
        phone = result.get("phone") if result else None
        provider_error = not result or result.get("response") == "ERROR"
        error_reason = "Pembelian nomor 5SIM gagal."
    elif server == "rumahotp":
        result = await asyncio.to_thread(buy_rumahotp_number, country, service, operator, json.loads(quote.get("pool") or "{}") if quote and quote.get("pool") else None)
        provider_order_id = result.get("order_id") or result.get("id") if result else None
        phone = result.get("phone") or result.get("number") if result else None
        provider_error = not result or result.get("response") == "ERROR"
        error_reason = "Pembelian nomor RumahOTP gagal."

    else:
        found = await asyncio.to_thread(find_smspool_service, service)
        smspool_service = (
            found.get("id")
            if found and found.get("id") is not None
            else service
        )
        if quote is not None:
            result = await asyncio.to_thread(
                buy_smspool_number,
                country,
                smspool_service,
                quote.get("pool"),
                float(quote["cost_usd"])
            )
        else:
            result = await asyncio.to_thread(
                buy_smspool_number,
                country,
                smspool_service
            )
        provider_order_id = (
            result.get("order_id") if result else None
        )
        phone = (
            result.get("phone")
            or result.get("number")
            if result else None
        )
        provider_error = (
            not result
            or result.get("response") == "ERROR"
        )
        error_reason = "Pembelian nomor SMSPOOL gagal."

    if provider_error:
        try:
            refund = refund_order(
                order_id,
                error_reason
            )
        except Exception as error:
            logger.exception("Refund gagal")
            await query.edit_message_text(
                "⚠️ <b>Provider gagal dan refund "
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
            "❌ <b>Respons provider tidak valid.</b>\n\n"
            "Saldo sudah dikembalikan.",
            parse_mode="HTML"
        )
        return

    provider_cost_rp = int(
        round(provider_cost_usd * KURS_DOLAR)
    )

    save_provider_order(
        order_id,
        provider_order_id,
        provider_cost_rp
    )

    await query.edit_message_text(
        "✅ <b>ORDER BERHASIL</b>\n\n"
        f"🧾 Order: <code>{order_id}</code>\n"
        f"🖥 Server: <b>{OTP_SERVERS.get(server, server)}</b>\n"
        f"🌍 Negara: <b>{country}</b>\n"
        f"📱 Layanan: <b>{service_label}</b>\n\n"
        f"📞 Nomor:\n<code>{phone}</code>\n\n"
        f"💰 Harga: <b>{format_rupiah(sell_price)}</b>\n"
        f"💳 Sisa saldo: <b>{format_rupiah(balance_after)}</b>\n\n"
        "⏳ <b>Menunggu SMS OTP...</b>",
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
Pilih server OTP atau gunakan Price Aggregator.

3️⃣ <b>Pilih Server</b>
├ Server 1
├ Server 2
├ Server 3
└ Multi Server → mencari harga, stok, negara, layanan, dan operator dari seluruh server

4️⃣ <b>Pilih layanan</b>
Bot menampilkan layanan OTP seperti WhatsApp, Telegram, Shopee, TikTok, Facebook, Instagram, Google, Vercel, UangMe, Grab, DANA, Gojek, OVO, Any Other, dan lainnya.

5️⃣ <b>Pilih Negara</b>
Pilih negara nomor yang tersedia.

6️⃣ <b>Pilih layanan/provider</b>
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
    # SEARCH NEGARA PER SERVICE / AGGREGATOR
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
    # MULTI SERVER -> PILIH OPERATOR
    # =====================================================

    if data.startswith("otp_operators:"):
        parts = data.split(":", 2)
        if len(parts) != 3:
            await query.answer("Data operator tidak valid.", show_alert=True)
            return
        _, service, country = parts
        await show_aggregated_operator_page(query, service, country)
        return

    if data.startswith("otp_operator:"):
        parts = data.split(":", 3)
        if len(parts) != 4:
            await query.answer("Data operator tidak valid.", show_alert=True)
            return
        _, service, country, operator = parts
        await show_aggregated_quotes_page(query, user_id, service, country, operator=operator)
        return

    # =====================================================
    # PRICE AGGREGATOR / MULTI SERVER -> PILIH QUOTE
    # =====================================================

    if data.startswith("otp_quotes:"):
        parts = data.split(":", 2)
        if len(parts) != 3:
            await query.answer("Data quote tidak valid.", show_alert=True)
            return
        _, service, country = parts
        await show_aggregated_quotes_page(query, user_id, service, country)
        return

    if data.startswith("otp_quote:"):
        quote_id = data.split(":", 1)[1]
        quote = await asyncio.to_thread(get_otp_quote, quote_id, user_id)
        if not quote:
            await query.answer("Quote sudah tidak tersedia. Silakan refresh.", show_alert=True)
            return
        await process_otp_order(
            query, user_id, context, quote["provider"], quote["country"], quote["service"], quote=quote
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

        await process_otp_order(
            query,
            user_id,
            context,
            server,
            country,
            service
        )
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

                f"🌍 Negara: "
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

            f"🌍 Negara: <b>{country}</b>\n"

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

                    "Pembelian nomor 5SIM gagal."

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

                "Respons 5SIM tidak lengkap."

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

            f"🌍 Negara: "
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

            "⏳ <b>Menunggu SMS OTP...</b>",

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

        provider_order_id = (
            order[
                "provider_order_id"
            ]
        )

        if not provider_order_id:

            await query.answer(
                "Order masih diproses.",
                show_alert=True
            )

            return

        provider = (
            order.get("provider")
            or "5sim"
        )

        if provider == "smspool":
            sms_checker = get_smspool_sms
        elif provider == "rumahotp":
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

                    await query.edit_message_text(

                        "🎉 <b>OTP DITERIMA</b>\n\n"

                        f"🧾 Order: "
                        f"<code>{order_id}</code>\n\n"

                        f"🔐 OTP:\n"
                        f"<code>{code}</code>\n\n"

                        f"📨 SMS:\n"
                        f"<code>{text}</code>",

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

        await query.answer(

            "⏳ OTP belum masuk. "
            "Coba lagi.",

            show_alert=True

        )

        return

    # =====================================================
    # CANCEL / REFUND
    # =====================================================

    if data.startswith(
        "otp_cancel:"
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
                f"Order sudah {order['status']}.",
                show_alert=True
            )

            return

        provider_order_id = (
            order[
                "provider_order_id"
            ]
        )

        await query.edit_message_text(

            "⏳ <b>Membatalkan order...</b>",

            parse_mode="HTML"

        )

        if provider_order_id:

            provider = (
                order.get("provider")
                or "5sim"
            )

            if provider == "smspool":
                canceler = cancel_smspool_number
            elif provider == "rumahotp":
                canceler = cancel_rumahotp_number
            else:
                canceler = cancel_number

            await asyncio.to_thread(
                canceler,
                provider_order_id
            )

        try:

            result = refund_order(

                order_id,

                "User membatalkan order OTP."

            )

        except Exception as error:

            logger.exception(
                "Refund gagal."
            )

            await query.edit_message_text(

                "❌ <b>Refund gagal diproses.</b>\n\n"

                f"Order: "
                f"<code>{order_id}</code>\n"

                f"Error: "
                f"<code>{error}</code>",

                parse_mode="HTML"

            )

            return

        await query.edit_message_text(

            "✅ <b>ORDER DIBATALKAN</b>\n\n"

            f"🧾 Order: "
            f"<code>{order_id}</code>\n"

            f"💸 Refund: "
            f"<b>{format_rupiah(order['sell_price'])}</b>\n"

            f"💰 Saldo sekarang: "
            f"<b>{format_rupiah(result['balance'])}</b>",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup([

                [
                    InlineKeyboardButton(
                        "📱 Order Lagi",
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

    # =====================================================
    # DEPOSIT
    # =====================================================

    if data == "user_deposit":

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

            reply_markup=InlineKeyboardMarkup([

                [
                    InlineKeyboardButton(
                        "❌ Batal",
                        callback_data="cancel_deposit"
                    )
                ]

            ])

        )

        return

    # =====================================================
    # CANCEL DEPOSIT
    # =====================================================

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
                            f"├ {o['order_id']} "
                            f"- {o['status']}"
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
# ADMIN CALLBACK
# =========================================================

async def admin_callback(
    query
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

        with get_db() as db:

            total = db.execute(

                """
                SELECT COUNT(*) AS total
                FROM users
                """

            ).fetchone()["total"]

        await query.edit_message_text(

            f"👥 <b>USERS</b>\n\n"
            f"Total user: <b>{total}</b>",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup(
                back
            )

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

            f"💳 <b>DEPOSIT</b>\n\n"
            f"Total transaksi: <b>{total}</b>\n"
            f"Pending: <b>{pending}</b>\n"
            f"Success: <b>{success}</b>",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup(
                back
            )

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

        # Check the three active providers concurrently so the admin panel stays fast.
        checks = await asyncio.gather(
            asyncio.to_thread(check_rumahotp_api),
            asyncio.to_thread(check_smspool_api),
            asyncio.to_thread(check_5sim_api),
            return_exceptions=True,
        )
        balances = await asyncio.gather(
            asyncio.to_thread(get_rumahotp_balance),
            asyncio.to_thread(get_smspool_balance),
            asyncio.to_thread(get_5sim_balance),
            return_exceptions=True,
        )

        def ok(result):
            return isinstance(result, dict) and bool(result.get("success"))

        def money(value):
            try:
                return float(value)
            except Exception:
                return 0.0

        b1, b2, b3 = [money(x) for x in balances]
        s1 = "🟢 CONNECTED" if ok(checks[0]) else "🔴 OFFLINE / API KEY BELUM DIATUR"
        s2 = "🟢 CONNECTED" if ok(checks[1]) else "🔴 OFFLINE / API KEY BELUM DIATUR"
        s3 = "🟢 CONNECTED" if ok(checks[2]) else "🔴 OFFLINE / API KEY BELUM DIATUR"

        await query.edit_message_text(
            "💰 <b>PROVIDER STATUS</b>\n\n"
            f"⚡ <b>Server 1 — RumahOTP</b>\n{s1}\n💵 Saldo: <b>Rp{b1:,.0f}</b>\n\n"
            f"⚡ <b>Server 2 — SMSPool</b>\n{s2}\n💵 Saldo: <b>${b2:.2f}</b>\n\n"
            f"⚡ <b>Server 3 — 5SIM</b>\n{s3}\n💵 Saldo: <b>{b3:.4f}</b> (currency provider)\n\n"
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

    if query.data in admin_callbacks:

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
            query
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

async def text_handler(
    update,
    context
):

    if not update.message:

        return

    # Search negara dari menu pemilihan negara.
    if context.user_data.get("waiting_otp_country_search", False):
        context.user_data["waiting_otp_country_search"] = False
        server = context.user_data.get("otp_country_search_server", "5sim")
        service = context.user_data.get("otp_country_search_service", "")
        keyword = update.message.text.strip().lower()

        try:
            if server == "aggregator":
                items = await asyncio.to_thread(get_aggregated_countries, service)
            elif server == "legacy":
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
            if server == "aggregator":
                label = f"🌍 {name} | 💰 mulai {format_rupiah(price)} | 📦 {stock}"
                cb = f"otp_operators:{service}:{country}"
            elif server == "legacy":
                label = f"🌍 {name}"
                cb = f"otp_country:{country}"
            else:
                label = f"🌍 {name} | 💰 {format_rupiah(price)} | 📦 {stock}"
                cb = f"otp_buy:{server}:{service}:{country}"
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

    application.add_handler(CommandHandler("server1", lambda u, c: command_server(u, c, "rumahotp")))
    application.add_handler(CommandHandler("server2", lambda u, c: command_server(u, c, "smspool")))
    application.add_handler(CommandHandler("server3", lambda u, c: command_server(u, c, "5sim")))
    application.add_handler(CommandHandler("multiserver", lambda u, c: command_server(u, c, "aggregator")))
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
