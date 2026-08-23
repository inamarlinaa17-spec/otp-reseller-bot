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
    MIDTRANS_SNAP_URL
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
    save_provider_order,
    get_order,
    mark_order_success,
    refund_order
)

import midtransclient

# =========================================================
# 5SIM
# =========================================================

from provider import (
    get_balance as get_5sim_balance,
    get_products as get_5sim_products,
    get_all_countries as get_5sim_countries,
    get_cheapest_operator,
    hitung_harga_jual as hitung_harga_5sim,
    buy_number as buy_5sim_number,
    get_sms as get_5sim_sms,
    cancel_number as cancel_5sim_number
)

# =========================================================
# SMSPOOL
# =========================================================

from smspool import (
    get_balance as get_smspool_balance,
    get_all_countries as get_smspool_countries,
    get_services as get_smspool_services,
    get_prices as get_smspool_prices,
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


COUNTRIES_PER_PAGE = 12
PRODUCTS_PER_PAGE = 12
SERVICES_PER_PAGE = 12


# =========================================================
# SMSPOOL SERVICE MENU
# =========================================================

SMSPOOL_SERVICE_PRIORITY = [

    "WhatsApp",
    "Telegram",
    "Shopee",
    "TikTok",
    "Facebook",
    "Instagram",
    "Google",
    "Gmail",
    "YouTube",
    "Vercel",
    "UangMe",
    "Grab",
    "DANA",
    "Gojek",
    "Any Other",
    "OVO"

]


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
# HELPER
# =========================================================

def is_admin(user_id):

    return user_id == ADMIN_ID


def format_rupiah(amount):

    return (
        f"Rp{int(float(amount)):,}"
        .replace(",", ".")
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


def provider_order_key(
    provider,
    provider_order_id
):

    if provider == "SMSPOOL":

        return (
            "SMSPOOL:"
            +
            str(provider_order_id)
        )

    return str(
        provider_order_id
    )


def detect_provider(
    provider_order_id
):

    value = str(
        provider_order_id or ""
    )

    if value.startswith(
        "SMSPOOL:"
    ):

        return "SMSPOOL"

    return "5SIM"


def clean_provider_order_id(
    provider_order_id
):

    value = str(
        provider_order_id or ""
    )

    if value.startswith(
        "SMSPOOL:"
    ):

        return value[
            len("SMSPOOL:"):
        ]

    return value


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
                "💬 Contact CS",
                callback_data="cs"
            )
        ]

    ])


# =========================================================
# SERVER MENU
# =========================================================

def server_menu():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "🖥 Server 1 • 5SIM",
                callback_data="server_5sim"
            )
        ],

        [
            InlineKeyboardButton(
                "🖥 Server 2 • SMSPOOL",
                callback_data="server_smspool"
            )
        ],

        [
            InlineKeyboardButton(
                "🏠 Menu Utama",
                callback_data="user_home"
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

        before = user["balance"]

        after = (
            before +
            deposit["amount"]
        )

        db.execute(
            """
            UPDATE users
            SET balance = %s
            WHERE telegram_id = %s
            """,
            (
                after,
                deposit["telegram_id"]
            )
        )

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
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """,
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
# SERVER SELECTION
# =========================================================

async def show_server_page(
    query
):

    await query.edit_message_text(

        "📱 <b>ORDER OTP</b>\n\n"

        "Pilih server/provider yang "
        "ingin digunakan:\n\n"

        "🖥 <b>Server 1 — 5SIM</b>\n"
        "Provider OTP 5SIM.\n\n"

        "🖥 <b>Server 2 — SMSPOOL</b>\n"
        "Provider OTP SMSPool.",

        parse_mode="HTML",

        reply_markup=server_menu()

    )


# =========================================================
# 5SIM COUNTRY PAGE
# =========================================================

async def show_5sim_country_page(
    query,
    page=0
):

    countries = await asyncio.to_thread(
        get_5sim_countries
    )

    if not countries:

        await query.edit_message_text(

            "❌ <b>5SIM tidak dapat "
            "dihubungi.</b>\n\n"
            "Silakan coba lagi.",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup([

                [
                    InlineKeyboardButton(
                        "🔄 Coba Lagi",
                        callback_data="server_5sim"
                    )
                ],

                [
                    InlineKeyboardButton(
                        "⬅️ Pilih Server",
                        callback_data="order"
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

    indonesia = []
    others = []

    for item in items:

        code = str(
            item[0]
        ).lower()

        name = str(
            item[1]
        ).lower()

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

    total_pages = max(
        1,
        (
            len(items)
            +
            COUNTRIES_PER_PAGE
            - 1
        ) // COUNTRIES_PER_PAGE
    )

    page = max(
        0,
        min(
            page,
            total_pages - 1
        )
    )

    start_index = (
        page *
        COUNTRIES_PER_PAGE
    )

    page_items = items[
        start_index:
        start_index +
        COUNTRIES_PER_PAGE
    ]

    keyboard = []

    for country_code, name in page_items:

        keyboard.append([

            InlineKeyboardButton(

                f"🌍 {name}",

                callback_data=(
                    f"5sim_country:"
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
                    f"5sim_country_page:"
                    f"{page - 1}"
                )
            )

        )

    if page < total_pages - 1:

        navigation.append(

            InlineKeyboardButton(
                "Berikutnya ➡️",
                callback_data=(
                    f"5sim_country_page:"
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
            "⬅️ Pilih Server",
            callback_data="order"
        )

    ])

    await query.edit_message_text(

        "🖥 <b>SERVER 1 • 5SIM</b>\n\n"

        "🌍 Pilih negara nomor.\n\n"

        f"Halaman <b>{page + 1}</b> "
        f"dari <b>{total_pages}</b>\n"

        f"Total negara: <b>{len(items)}</b>",

        parse_mode="HTML",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        )

    )


# =========================================================
# 5SIM PRODUCT PAGE
# =========================================================

async def show_5sim_product_page(
    query,
    country,
    page=0
):

    products = await asyncio.to_thread(
        get_5sim_products,
        country,
        "any"
    )

    if not products:

        await query.edit_message_text(

            "❌ <b>Tidak ada layanan 5SIM.</b>",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup([

                [
                    InlineKeyboardButton(
                        "⬅️ Pilih Negara",
                        callback_data="server_5sim"
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

        if qty <= 0 or cost <= 0:

            continue

        sell_price = hitung_harga_5sim(
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
            "yang tersedia.",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup([

                [
                    InlineKeyboardButton(
                        "🔄 Refresh",
                        callback_data=(
                            f"5sim_country:{country}"
                        )
                    )
                ],

                [
                    InlineKeyboardButton(
                        "⬅️ Pilih Negara",
                        callback_data="server_5sim"
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

    page = max(
        0,
        min(
            page,
            total_pages - 1
        )
    )

    start_index = (
        page *
        PRODUCTS_PER_PAGE
    )

    page_items = items[
        start_index:
        start_index +
        PRODUCTS_PER_PAGE
    ]

    keyboard = []

    for item in page_items:

        keyboard.append([

            InlineKeyboardButton(

                (
                    f"📱 {item['product']}\n"
                    f"💰 "
                    f"{format_rupiah(item['sell_price'])}"
                    f" | 📦 Stock: {item['qty']}"
                ),

                callback_data=(
                    f"5sim_product:"
                    f"{country}:"
                    f"{item['product']}"
                )

            )

        ])

    navigation = []

    if page > 0:

        navigation.append(

            InlineKeyboardButton(

                "⬅️ Sebelumnya",

                callback_data=(
                    f"5sim_products:"
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
                    f"5sim_products:"
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
                f"5sim_country:"
                f"{country}"
            )
        )

    ])

    keyboard.append([

        InlineKeyboardButton(
            "⬅️ Pilih Negara",
            callback_data="server_5sim"
        )

    ])

    await query.edit_message_text(

        f"🖥 <b>SERVER 1 • 5SIM</b>\n\n"
        f"🌍 Negara: <b>{country}</b>\n\n"
        f"📱 <b>Pilih layanan OTP</b>\n\n"
        f"📦 Hanya layanan yang memiliki "
        f"stok ditampilkan.\n"
        f"💰 Harga sudah termasuk margin.\n\n"
        f"Halaman <b>{page + 1}</b> "
        f"dari <b>{total_pages}</b>",

        parse_mode="HTML",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        )

    )


# =========================================================
# SMSPOOL COUNTRY NORMALIZER
# =========================================================

def normalize_smspool_countries(
    data
):

    items = []

    if isinstance(
        data,
        dict
    ):

        for key, value in data.items():

            if isinstance(
                value,
                dict
            ):

                code = value.get(
                    "code",
                    value.get(
                        "id",
                        key
                    )
                )

                name = value.get(
                    "name",
                    value.get(
                        "country",
                        value.get(
                            "text",
                            key
                        )
                    )
                )

            else:

                code = key
                name = value

            items.append(
                (
                    str(code),
                    str(name)
                )
            )

    elif isinstance(
        data,
        list
    ):

        for item in data:

            if isinstance(
                item,
                dict
            ):

                code = item.get(
                    "code",
                    item.get(
                        "id",
                        item.get(
                            "country"
                        )
                    )
                )

                name = item.get(
                    "name",
                    item.get(
                        "country",
                        item.get(
                            "text",
                            code
                        )
                    )
                )

                if code is not None:

                    items.append(
                        (
                            str(code),
                            str(name)
                        )
                    )

    return items


# =========================================================
# SMSPOOL COUNTRY PAGE
# =========================================================

async def show_smspool_country_page(
    query,
    page=0
):

    countries = await asyncio.to_thread(
        get_smspool_countries
    )

    items = normalize_smspool_countries(
        countries
    )

    if not items:

        await query.edit_message_text(

            "❌ <b>SMSPOOL tidak "
            "mengembalikan negara.</b>\n\n"
            "Periksa koneksi API SMSPOOL.",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup([

                [
                    InlineKeyboardButton(
                        "🔄 Coba Lagi",
                        callback_data="server_smspool"
                    )
                ],

                [
                    InlineKeyboardButton(
                        "⬅️ Pilih Server",
                        callback_data="order"
                    )
                ]

            ])

        )

        return

    indonesia = []
    others = []

    for item in items:

        code = item[0].lower()
        name = item[1].lower()

        if (
            "indonesia" in name
            or
            code in (
                "id",
                "indonesia"
            )
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

    total_pages = max(
        1,
        (
            len(items)
            +
            COUNTRIES_PER_PAGE
            - 1
        ) // COUNTRIES_PER_PAGE
    )

    page = max(
        0,
        min(
            page,
            total_pages - 1
        )
    )

    start_index = (
        page *
        COUNTRIES_PER_PAGE
    )

    page_items = items[
        start_index:
        start_index +
        COUNTRIES_PER_PAGE
    ]

    keyboard = []

    for code, name in page_items:

        keyboard.append([

            InlineKeyboardButton(

                f"🌍 {name}",

                callback_data=(
                    f"sp_country:"
                    f"{code}"
                )

            )

        ])

    navigation = []

    if page > 0:

        navigation.append(

            InlineKeyboardButton(
                "⬅️ Sebelumnya",
                callback_data=(
                    f"sp_country_page:"
                    f"{page - 1}"
                )
            )

        )

    if page < total_pages - 1:

        navigation.append(

            InlineKeyboardButton(
                "Berikutnya ➡️",
                callback_data=(
                    f"sp_country_page:"
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
            "⬅️ Pilih Server",
            callback_data="order"
        )

    ])

    await query.edit_message_text(

        "🖥 <b>SERVER 2 • SMSPOOL</b>\n\n"

        "🌍 Pilih negara nomor.\n\n"

        f"Halaman <b>{page + 1}</b> "
        f"dari <b>{total_pages}</b>\n"

        f"Total negara: <b>{len(items)}</b>",

        parse_mode="HTML",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        )

    )


# =========================================================
# SMSPOOL SERVICE PARSER
# =========================================================

def normalize_smspool_services(
    data
):

    items = []

    if isinstance(
        data,
        dict
    ):

        for service_id, value in data.items():

            if isinstance(
                value,
                dict
            ):

                name = value.get(
                    "name",
                    value.get(
                        "service",
                        value.get(
                            "text",
                            service_id
                        )
                    )
                )

                sid = value.get(
                    "id",
                    value.get(
                        "service_id",
                        service_id
                    )
                )

            else:

                sid = service_id
                name = value

            items.append({
                "id":
                    str(sid),

                "name":
                    str(name)
            })

    elif isinstance(
        data,
        list
    ):

        for item in data:

            if not isinstance(
                item,
                dict
            ):

                continue

            sid = item.get(
                "id",
                item.get(
                    "service_id",
                    item.get(
                        "code"
                    )
                )
            )

            name = item.get(
                "name",
                item.get(
                    "service",
                    item.get(
                        "text",
                        sid
                    )
                )
            )

            if sid is not None:

                items.append({
                    "id":
                        str(sid),

                    "name":
                        str(name)
                })

    return items


# =========================================================
# SERVICE SORT
# =========================================================

def sort_smspool_services(
    items
):

    priority = {
        name.lower():
            index
        for index, name
        in enumerate(
            SMSPOOL_SERVICE_PRIORITY
        )
    }

    def service_key(item):

        name = item[
            "name"
        ].lower()

        for priority_name, index in priority.items():

            if (
                name == priority_name
                or
                priority_name in name
            ):

                return (
                    0,
                    index,
                    name
                )

        return (
            1,
            999,
            name
        )

    return sorted(
        items,
        key=service_key
    )


# =========================================================
# SMSPOOL SERVICE PAGE
# =========================================================

async def show_smspool_service_page(
    query,
    country,
    page=0
):

    services_data = await asyncio.to_thread(
        get_smspool_services
    )

    services = normalize_smspool_services(
        services_data
    )

    if not services:

        await query.edit_message_text(

            "❌ <b>SMSPOOL tidak "
            "mengembalikan layanan.</b>",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup([

                [
                    InlineKeyboardButton(
                        "🔄 Refresh",
                        callback_data=(
                            f"sp_country:"
                            f"{country}"
                        )
                    )
                ],

                [
                    InlineKeyboardButton(
                        "⬅️ Pilih Negara",
                        callback_data="server_smspool"
                    )
                ]

            ])

        )

        return

    services = sort_smspool_services(
        services
    )

    total_pages = max(
        1,
        (
            len(services)
            +
            SERVICES_PER_PAGE
            - 1
        ) // SERVICES_PER_PAGE
    )

    page = max(
        0,
        min(
            page,
            total_pages - 1
        )
    )

    start_index = (
        page *
        SERVICES_PER_PAGE
    )

    page_items = services[
        start_index:
        start_index +
        SERVICES_PER_PAGE
    ]

    keyboard = []

    for service in page_items:

        service_id = service[
            "id"
        ]

        name = service[
            "name"
        ]

        keyboard.append([

            InlineKeyboardButton(

                f"📱 {name}",

                callback_data=(
                    f"sp_service:"
                    f"{country}:"
                    f"{service_id}"
                )

            )

        ])

    navigation = []

    if page > 0:

        navigation.append(

            InlineKeyboardButton(
                "⬅️ Sebelumnya",
                callback_data=(
                    f"sp_services:"
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
                    f"sp_services:"
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
            "🔄 Refresh Layanan",
            callback_data=(
                f"sp_country:"
                f"{country}"
            )
        )

    ])

    keyboard.append([

        InlineKeyboardButton(
            "⬅️ Pilih Negara",
            callback_data="server_smspool"
        )

    ])

    await query.edit_message_text(

        f"🖥 <b>SERVER 2 • SMSPOOL</b>\n\n"

        f"🌍 Negara: <b>{country}</b>\n\n"

        "📱 <b>Pilih layanan OTP</b>\n\n"

        "Layanan utama diprioritaskan "
        "di bagian atas.\n\n"

        f"Halaman <b>{page + 1}</b> "
        f"dari <b>{total_pages}</b>\n"

        f"Total layanan: <b>{len(services)}</b>",

        parse_mode="HTML",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        )

    )


# =========================================================
# SMSPOOL SERVICE PRICE
# =========================================================

def extract_smspool_price(
    data
):

    if isinstance(
        data,
        (int, float, str)
    ):

        try:

            value = float(
                data
            )

            if value > 0:

                return value

        except Exception:

            pass

    if isinstance(
        data,
        dict
    ):

        for key in [
            "price",
            "cost",
            "amount",
            "rate"
        ]:

            value = data.get(
                key
            )

            try:

                value = float(
                    value
                )

                if value > 0:

                    return value

            except Exception:

                pass

        for value in data.values():

            found = extract_smspool_price(
                value
            )

            if found:

                return found

    if isinstance(
        data,
        list
    ):

        for item in data:

            found = extract_smspool_price(
                item
            )

            if found:

                return found

    return 0.0


# =========================================================
# SMSPOOL SERVICE INFO
# =========================================================

async def get_smspool_service_price(
    country,
    service
):

    data = await asyncio.to_thread(

        get_smspool_prices,

        country,
        service

    )

    return extract_smspool_price(
        data
    )


# =========================================================
# BUY 5SIM
# =========================================================

async def process_5sim_order(
    query,
    user_id,
    country,
    product
):

    operator_info = await asyncio.to_thread(

        get_cheapest_operator,

        country,

        product

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
                            f"5sim_country:{country}"
                        )
                    )
                ],

                [
                    InlineKeyboardButton(
                        "⬅️ Pilih Negara",
                        callback_data="server_5sim"
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

    sell_price = hitung_harga_5sim(
        provider_cost_usd
    )

    current_balance = get_balance(
        user_id
    )

    if current_balance < sell_price:

        await query.edit_message_text(

            "❌ <b>Saldo tidak cukup.</b>\n\n"

            f"🖥 Server: <b>5SIM</b>\n"
            f"🌍 Negara: <b>{country}</b>\n"
            f"📱 Layanan: <b>{product}</b>\n\n"

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
                            f"5sim_country:{country}"
                        )
                    )
                ]

            ])

        )

        return

    await query.edit_message_text(

        "⏳ <b>Memproses order 5SIM...</b>\n\n"

        f"🌍 Negara: <b>{country}</b>\n"
        f"📱 Layanan: <b>{product}</b>\n"
        f"📡 Operator: <b>{operator}</b>\n"
        f"💰 Harga: "
        f"<b>{format_rupiah(sell_price)}</b>",

        parse_mode="HTML"

    )

    order_id = (
        "OTP-"
        +
        uuid.uuid4()
        .hex[:12]
        .upper()
    )

    try:

        balance_after = create_pending_order(

            telegram_id=user_id,

            order_id=order_id,

            country=country,

            service=product,

            sell_price=sell_price

        )

    except ValueError as error:

        await query.edit_message_text(

            f"❌ <b>Order gagal.</b>\n\n"
            f"{error}",

            parse_mode="HTML"

        )

        return

    result = await asyncio.to_thread(

        buy_5sim_number,

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
                "Refund 5SIM gagal"
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

    provider_cost_rp = int(
        round(
            provider_cost_usd *
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

        f"🖥 Server: <b>5SIM</b>\n"
        f"🧾 Order: "
        f"<code>{order_id}</code>\n"
        f"🌍 Negara: <b>{country}</b>\n"
        f"📱 Layanan: <b>{product}</b>\n"
        f"📡 Operator: <b>{operator}</b>\n\n"

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


# =========================================================
# BUY SMSPOOL
# =========================================================

async def process_smspool_order(
    query,
    user_id,
    country,
    service
):

    service_info = await asyncio.to_thread(

        find_smspool_service,

        service

    )

    if not service_info:

        await query.edit_message_text(

            "❌ <b>Layanan SMSPOOL tidak "
            "ditemukan.</b>\n\n"
            "Silakan refresh daftar layanan.",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup([

                [
                    InlineKeyboardButton(
                        "🔄 Refresh",
                        callback_data=(
                            f"sp_country:{country}"
                        )
                    )
                ],

                [
                    InlineKeyboardButton(
                        "⬅️ Pilih Negara",
                        callback_data="server_smspool"
                    )
                ]

            ])

        )

        return

    service_id = service_info[
        "id"
    ]

    service_name = service_info[
        "name"
    ]

    provider_cost = await get_smspool_service_price(

        country,

        service_id

    )

    if provider_cost <= 0:

        # Coba nama service apabila endpoint
        # harga menggunakan nama, bukan ID.

        provider_cost = await get_smspool_service_price(

            country,

            service_name

        )

    if provider_cost <= 0:

        await query.edit_message_text(

            "❌ <b>Harga layanan tidak "
            "tersedia dari SMSPOOL.</b>\n\n"

            f"🌍 Negara: <b>{country}</b>\n"
            f"📱 Layanan: <b>{service_name}</b>\n\n"

            "Silakan coba layanan lain.",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup([

                [
                    InlineKeyboardButton(
                        "⬅️ Layanan",
                        callback_data=(
                            f"sp_country:{country}"
                        )
                    )
                ]

            ])

        )

        return

    harga_jual = int(
        round(
            (
                provider_cost
                *
                17649.80
                *
                (
                    1 +
                    (
                        0.10
                    )
                )
            )
            / 100
        )
        *
        100
    )

    current_balance = get_balance(
        user_id
    )

    if current_balance < harga_jual:

        await query.edit_message_text(

            "❌ <b>Saldo tidak cukup.</b>\n\n"

            f"🖥 Server: <b>SMSPOOL</b>\n"
            f"🌍 Negara: <b>{country}</b>\n"
            f"📱 Layanan: <b>{service_name}</b>\n\n"

            f"💰 Harga: "
            f"<b>{format_rupiah(harga_jual)}</b>\n"

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
                            f"sp_country:{country}"
                        )
                    )
                ]

            ])

        )

        return

    await query.edit_message_text(

        "⏳ <b>Memproses order SMSPOOL...</b>\n\n"

        f"🌍 Negara: <b>{country}</b>\n"
        f"📱 Layanan: <b>{service_name}</b>\n"
        f"💰 Harga: "
        f"<b>{format_rupiah(harga_jual)}</b>",

        parse_mode="HTML"

    )

    order_id = (
        "OTP-"
        +
        uuid.uuid4()
        .hex[:12]
        .upper()
    )

    try:

        balance_after = create_pending_order(

            telegram_id=user_id,

            order_id=order_id,

            country=country,

            service=service_name,

            sell_price=harga_jual

        )

    except ValueError as error:

        await query.edit_message_text(

            f"❌ <b>Order gagal.</b>\n\n"
            f"{error}",

            parse_mode="HTML"

        )

        return

    result = await asyncio.to_thread(

        buy_smspool_number,

        country,

        service_id

    )

    if (
        not result
        or
        result.get(
            "response"
        ) != "SUCCESS"
    ):

        try:

            refund = refund_order(

                order_id,

                "Pembelian nomor SMSPOOL gagal."

            )

        except Exception as error:

            logger.exception(
                "Refund SMSPOOL gagal"
            )

            await query.edit_message_text(

                "⚠️ <b>SMSPOOL gagal "
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

            "❌ <b>Nomor SMSPOOL tidak tersedia.</b>\n\n"

            f"🧾 Order: "
            f"<code>{order_id}</code>\n"

            f"💸 Refund: "
            f"<b>{format_rupiah(harga_jual)}</b>\n"

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
        "order_id",
        result.get(
            "id"
        )
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

            "Respons SMSPOOL tidak lengkap."

        )

        await query.edit_message_text(

            "❌ <b>Respons SMSPOOL "
            "tidak valid.</b>\n\n"
            "Saldo sudah dikembalikan.",

            parse_mode="HTML"

        )

        return

    provider_cost_rp = int(
        round(
            provider_cost *
            17649.80
        )
    )

    save_provider_order(

        order_id,

        provider_order_key(
            "SMSPOOL",
            provider_order_id
        ),

        provider_cost_rp

    )

    await query.edit_message_text(

        "✅ <b>ORDER BERHASIL</b>\n\n"

        f"🖥 Server: <b>SMSPOOL</b>\n"
        f"🧾 Order: "
        f"<code>{order_id}</code>\n"
        f"🌍 Negara: <b>{country}</b>\n"
        f"📱 Layanan: <b>{service_name}</b>\n\n"

        f"📞 Nomor:\n"
        f"<code>{phone}</code>\n\n"

        f"💰 Harga: "
        f"<b>{format_rupiah(harga_jual)}</b>\n"

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


# =========================================================
# USER CALLBACK
# =========================================================

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
Tekan Order OTP lalu pilih server.

3️⃣ <b>Pilih Server</b>
├ Server 1 → 5SIM
└ Server 2 → SMSPOOL

4️⃣ <b>Pilih Negara</b>
Pilih negara nomor yang tersedia.

5️⃣ <b>Pilih Layanan</b>
Pilih aplikasi/layanan OTP.

6️⃣ <b>Gunakan Nomor</b>
Setelah order berhasil, nomor diberikan oleh bot.

7️⃣ <b>Menunggu SMS</b>
Masukkan nomor ke aplikasi tujuan dan tunggu OTP.

8️⃣ <b>Cek OTP</b>
Tekan tombol <b>🔄 Cek OTP</b> sampai SMS masuk.

9️⃣ <b>Refund</b>
Jika order dapat dibatalkan, tekan <b>❌ Batal / Refund</b>."""

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
    # SERVER 1 5SIM
    # =====================================================

    if data == "server_5sim":

        await show_5sim_country_page(
            query,
            0
        )

        return

    # =====================================================
    # SERVER 2 SMSPOOL
    # =====================================================

    if data == "server_smspool":

        await show_smspool_country_page(
            query,
            0
        )

        return

    # =====================================================
    # 5SIM COUNTRY PAGE
    # =====================================================

    if data.startswith(
        "5sim_country_page:"
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

        await show_5sim_country_page(
            query,
            page
        )

        return

    # =====================================================
    # 5SIM COUNTRY
    # =====================================================

    if data.startswith(
        "5sim_country:"
    ):

        country = data.split(
            ":",
            1
        )[1]

        await show_5sim_product_page(

            query,

            country,

            0

        )

        return

    # =====================================================
    # 5SIM PRODUCT PAGE
    # =====================================================

    if data.startswith(
        "5sim_products:"
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

        await show_5sim_product_page(

            query,

            country,

            page

        )

        return

    # =====================================================
    # 5SIM PRODUCT
    # =====================================================

    if data.startswith(
        "5sim_product:"
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

        await process_5sim_order(

            query,

            user_id,

            parts[1],

            parts[2]

        )

        return

    # =====================================================
    # SMSPOOL COUNTRY PAGE
    # =====================================================

    if data.startswith(
        "sp_country_page:"
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

        await show_smspool_country_page(
            query,
            page
        )

        return

    # =====================================================
    # SMSPOOL COUNTRY
    # =====================================================

    if data.startswith(
        "sp_country:"
    ):

        country = data.split(
            ":",
            1
        )[1]

        await show_smspool_service_page(

            query,

            country,

            0

        )

        return

    # =====================================================
    # SMSPOOL SERVICE PAGE
    # =====================================================

    if data.startswith(
        "sp_services:"
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

        await show_smspool_service_page(

            query,

            country,

            page

        )

        return

    # =====================================================
    # SMSPOOL SERVICE
    # =====================================================

    if data.startswith(
        "sp_service:"
    ):

        parts = data.split(
            ":",
            2
        )

        if len(parts) != 3:

            await query.answer(
                "Data service tidak valid.",
                show_alert=True
            )

            return

        country = parts[1]
        service_id = parts[2]

        await process_smspool_order(

            query,

            user_id,

            country,

            service_id

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

        stored_provider_order_id = (
            order[
                "provider_order_id"
            ]
        )

        if not stored_provider_order_id:

            await query.answer(
                "Order masih diproses.",
                show_alert=True
            )

            return

        provider = detect_provider(
            stored_provider_order_id
        )

        real_provider_order_id = (
            clean_provider_order_id(
                stored_provider_order_id
            )
        )

        if provider == "SMSPOOL":

            data_sms = await asyncio.to_thread(

                get_smspool_sms,

                real_provider_order_id

            )

        else:

            data_sms = await asyncio.to_thread(

                get_5sim_sms,

                real_provider_order_id

            )

        if not data_sms:

            await query.answer(
                "Gagal mengecek OTP.",
                show_alert=True
            )

            return

        response_type = data_sms.get(
            "response"
        )

        if response_type == "ERROR":

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

                        f"🖥 Server: "
                        f"<b>{provider}</b>\n"

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

        stored_provider_order_id = (
            order[
                "provider_order_id"
            ]
        )

        provider = detect_provider(
            stored_provider_order_id
        )

        real_provider_order_id = (
            clean_provider_order_id(
                stored_provider_order_id
            )
        )

        await query.edit_message_text(

            "⏳ <b>Membatalkan order...</b>",

            parse_mode="HTML"

        )

        if real_provider_order_id:

            if provider == "SMSPOOL":

                await asyncio.to_thread(

                    cancel_smspool_number,

                    real_provider_order_id

                )

            else:

                await asyncio.to_thread(

                    cancel_5sim_number,

                    real_provider_order_id

                )

        try:

            result = refund_order(

                order_id,

                f"User membatalkan order {provider}."

            )

        except Exception as error:

            logger.exception(
                "Refund gagal."
            )

            await query.edit_message_text(

                "❌ <b>Refund gagal diproses.</b>\n\n"

                f"Order: "
                f"<code>{order_id}</code>\n"

                f"Provider: <b>{provider}</b>\n"

                f"Error: "
                f"<code>{error}</code>",

                parse_mode="HTML"

            )

            return

        await query.edit_message_text(

            "✅ <b>ORDER DIBATALKAN</b>\n\n"

            f"🖥 Server: <b>{provider}</b>\n"

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

                    f"💰 Masuk: "
                    f"<b>{format_rupiah(result['amount'])}</b>\n"

                    f"💳 Saldo sekarang: "
                    f"<b>{format_rupiah(result['new_balance'])}</b>",

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

        balance_5sim = await asyncio.to_thread(
            get_5sim_balance
        )

        balance_smspool = await asyncio.to_thread(
            get_smspool_balance
        )

        await query.edit_message_text(

            "💰 <b>PROVIDER</b>\n\n"

            "🖥 <b>Server 1 • 5SIM</b>\n"
            "🟢 Status: <b>CONNECTED</b>\n"
            f"💵 Saldo: "
            f"<b>${balance_5sim:.2f}</b>\n\n"

            "🖥 <b>Server 2 • SMSPOOL</b>\n"
            "🟢 Status: <b>CONNECTED</b>\n"
            f"💵 Saldo: "
            f"<b>${balance_smspool:.2f}</b>\n\n"

            "💱 Kurs: "
            "<b>Rp17.649,80 / USD</b>\n"
            "📈 Margin: mengikuti konfigurasi.",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup(
                back
            )

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
            &
            ~filters.COMMAND,

            text_handler

        )

    )

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "Bot berhasil dijalankan."
    )

    threading.Thread(

        target=run_flask,

        daemon=True

    ).start()

    application.run_polling(

        allowed_updates=
            Update.ALL_TYPES

    )


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    run()
