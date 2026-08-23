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

from provider import (
    get_balance as get_5sim_balance,
    get_products,
    get_all_countries,
    get_cheapest_operator,
    hitung_harga_jual,
    buy_number,
    get_sms,
    cancel_number
)

# =========================================================
# VALIDASI MIDTRANS
# =========================================================

if not MIDTRANS_SERVER_KEY:
    raise RuntimeError(
        "MIDTRANS_SERVER_KEY belum diatur di Railway."
    )


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)


# =========================================================
# MIDTRANS SNAP
# PRODUCTION / LIVE
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
    return f"Rp{amount:,}".replace(",", ".")


def get_wib_time():
    wib = pytz.timezone("Asia/Jakarta")
    return datetime.now(wib).strftime(
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

def create_midtrans_snap(amount, deposit_id):

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
            "first_name": f"User {deposit_id}"
        },

        "expiry": {
            "start_time": datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S +07:00"
            ),
            "unit": "hours",
            "duration": 24
        }
    }

    try:

        transaction = snap.create_transaction(param)

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

def cek_status_midtrans(order_id):

    url = f"{MIDTRANS_API_URL}/{order_id}/status"

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

        with urlopen(req, timeout=20) as response:

            data = json.loads(
                response.read().decode()
            )

            return data

    except HTTPError as e:

        logger.error(
            "Cek status gagal: %s",
            e.read().decode()
        )

        return None

    except Exception as e:

        logger.error(
            "Cek status Midtrans error: %s",
            e
        )

        return None


# =========================================================
# TELEGRAM NOTIFICATION
# =========================================================

def send_telegram_message(chat_id, text):

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }

    telegram_request = Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json"
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

            logger.error(
                "Webhook Midtrans menerima body kosong."
            )

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
            "Webhook Midtrans masuk | "
            "order_id=%s | status=%s | "
            "payment_type=%s | fraud=%s | amount=%s",
            order_id,
            status,
            payment_type,
            fraud,
            gross_amount
        )

        # -------------------------------------------------
        # VALIDASI DASAR
        # -------------------------------------------------

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

        # -------------------------------------------------
        # VALIDASI SIGNATURE
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
                "Signature Midtrans tidak valid "
                "untuk order %s.",
                order_id
            )

            return jsonify({
                "status": "error",
                "message": "Invalid signature"
            }), 403

        logger.info(
            "Signature Midtrans valid "
            "untuk order %s.",
            order_id
        )

        # -------------------------------------------------
        # STATUS SUKSES
        # -------------------------------------------------

        is_success = False

        if status == "settlement":

            is_success = True

        elif (
            status == "capture"
            and payment_type == "credit_card"
        ):

            is_success = True

        if is_success and fraud is not None:

            if str(fraud).lower() != "accept":

                logger.warning(
                    "Pembayaran %s ditolak "
                    "karena fraud_status=%s",
                    order_id,
                    fraud
                )

                return jsonify({
                    "status": "ok",
                    "message":
                        "Fraud status not accepted"
                }), 200

        if (
            is_success
            and status_code != "200"
        ):

            logger.warning(
                "Pembayaran %s memiliki "
                "status_code=%s",
                order_id,
                status_code
            )

            return jsonify({
                "status": "ok",
                "message":
                    "Invalid success status code"
            }), 200

        # -------------------------------------------------
        # PROSES PAYMENT
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
                        f"💰 Deposit: "
                        f"<b>{format_rupiah(result['amount'])}</b>\n"
                        f"💳 Status: <b>PAID</b>\n"
                        f"🧾 ID: "
                        f"<code>{order_id}</code>\n\n"
                        f"💰 Saldo sekarang: "
                        f"<b>{format_rupiah(result['new_balance'])}</b>"
                    )

                    logger.info(
                        "SALDO BERHASIL MASUK | "
                        "user=%s | amount=%s | order=%s",
                        result["telegram_id"],
                        result["amount"],
                        order_id
                    )

                elif result["already_completed"]:

                    logger.info(
                        "Webhook duplikat "
                        "diabaikan | order=%s",
                        order_id
                    )

            except Exception as e:

                logger.exception(
                    "Gagal proses pembayaran "
                    "order %s: %s",
                    order_id,
                    e
                )

                return jsonify({
                    "status": "error",
                    "message":
                        "Failed to process payment"
                }), 500

        # -------------------------------------------------
        # EXPIRED / CANCEL
        # -------------------------------------------------

        elif status in [
            "expire",
            "cancel"
        ]:

            logger.info(
                "Transaksi %s berstatus %s.",
                order_id,
                status
            )

            try:

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

            except Exception as e:

                logger.error(
                    "Gagal update expired "
                    "deposit %s: %s",
                    order_id,
                    e
                )

        else:

            logger.info(
                "Webhook %s diterima "
                "tetapi belum sukses. status=%s",
                order_id,
                status
            )

        return jsonify({
            "status": "ok"
        }), 200

    except Exception as e:

        logger.exception(
            "Error tidak terduga "
            "pada webhook Midtrans: %s",
            e
        )

        return jsonify({
            "status": "error",
            "message": "Internal server error"
        }), 500


# =========================================================
# HEALTH CHECK RAILWAY
# =========================================================

@app.route(
    "/",
    methods=["GET"]
)
def health_check():

    return jsonify({
        "status": "online",
        "service": "otp-reseller-bot"
    }), 200


# =========================================================
# USER START
# =========================================================

async def user_start(update_or_query):

    # -----------------------------------------------------
    # INI PERBAIKAN UTAMA
    #
    # Jangan pakai hasattr(message), karena CallbackQuery
    # juga memiliki .message.
    # -----------------------------------------------------

    if isinstance(update_or_query, Update):

        user = update_or_query.effective_user

        send = (
            update_or_query
            .message
            .reply_text
        )

    else:

        # CallbackQuery
        user = update_or_query.from_user

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

async def admin_start(update):

    await update.message.reply_text(
        "👑 <b>ADMIN PANEL</b>\n\n"
        "Selamat datang, Admin.\n\n"
        "Pilih menu:",
        parse_mode="HTML",
        reply_markup=admin_menu()
    )


# =========================================================
# START COMMAND
# =========================================================

async def start(update, context):

    user = update.effective_user

    create_user(
        user.id,
        user.username,
        user.first_name
    )

    if is_admin(user.id):

        await admin_start(update)

    else:

        # Pastikan mode deposit bersih
        context.chat_data[
            "waiting_deposit"
        ] = False

        await user_start(update)


# =========================================================
# USER CALLBACK
# =========================================================

async def user_callback(
    query,
    user_id,
    context
):

    # =====================================================
    # CARA
    # =====================================================

    if query.data == "cara":

        text = """📁 <b>PANDUAN PENGGUNAAN BOT</b>

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

        keyboard = [
            [
                InlineKeyboardButton(
                    "🗑️ Kembali",
                    callback_data="user_home"
                )
            ]
        ]

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )

    # =====================================================
    # ORDER
    # =====================================================

    elif query.data == "order":

    countries = await asyncio.to_thread(
        get_all_countries
    )

    if not countries:

        await query.edit_message_text(
            "❌ <b>Provider 5SIM tidak dapat dihubungi.</b>\n\n"
            "Silakan coba lagi beberapa saat.",
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

    keyboard = []

    items = []

    for country_code, country_data in countries.items():

        if isinstance(country_data, dict):

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

    items.sort(
        key=lambda item:
            item[1].lower()
    )

    # Tampilkan 20 negara pertama
    for country_code, name in items[:20]:

        keyboard.append([
            InlineKeyboardButton(
                f"🌍 {name}",
                callback_data=(
                    f"otp_country:{country_code}"
                )
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "🏠 Menu Utama",
            callback_data="user_home"
        )
    ])

    await query.edit_message_text(
        "📱 <b>ORDER OTP</b>\n\n"
        "Pilih negara nomor:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )

    elif query.data.startswith(
    "otp_country:"
):

    country = query.data.split(
        ":",
        1
    )[1]

    products = await asyncio.to_thread(
        get_products,
        country,
        "any"
    )

    if not products:

        await query.edit_message_text(
            "❌ <b>Tidak ada layanan tersedia.</b>\n\n"
            "Coba pilih negara lain.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ Pilih Negara",
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

    keyboard = []

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

        qty = int(
            info.get(
                "Qty",
                0
            ) or 0
        )

        price = float(
            info.get(
                "Price",
                0
            ) or 0
        )

        if qty <= 0:
            continue

        sell_price = hitung_harga_jual(
            price
        )

        if sell_price <= 0:
            continue

        keyboard.append([
            InlineKeyboardButton(
                (
                    f"📱 {product} — "
                    f"{format_rupiah(sell_price)}"
                ),
                callback_data=(
                    f"otp_product:"
                    f"{country}:"
                    f"{product}"
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

    if len(keyboard) <= 2:

        await query.edit_message_text(
            "❌ <b>Tidak ada stok OTP.</b>",
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

    await query.edit_message_text(
        f"🌍 Negara: <b>{country}</b>\n\n"
        "📱 <b>Pilih layanan:</b>\n\n"
        "Harga sudah termasuk margin reseller "
        "20%.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )

    elif query.data.startswith(
    "otp_product:"
):

    parts = query.data.split(
        ":",
        2
    )

    if len(parts) != 3:

        await query.edit_message_text(
            "❌ Data order tidak valid."
        )

        return

    country = parts[1]
    product = parts[2]

    # Cari operator termurah
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
                        "⬅️ Kembali",
                        callback_data=(
                            f"otp_country:{country}"
                        )
                    )
                ]
            ])
        )

        return

    operator = operator_info[
        "operator"
    ]

    provider_cost_usd = float(
        operator_info["cost"]
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
            f"💰 Harga: "
            f"<b>{format_rupiah(sell_price)}</b>\n"
            f"💳 Saldo: "
            f"<b>{format_rupiah(current_balance)}</b>\n\n"
            "Silakan deposit terlebih dahulu.",
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
        f"💰 Harga: "
        f"<b>{format_rupiah(sell_price)}</b>",
        parse_mode="HTML"
    )

    # ID internal bot
    order_id = (
        "OTP-" +
        uuid.uuid4().hex[:12].upper()
    )

    # Potong saldo + buat order internal
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

    # Beli nomor di 5SIM
    result = await asyncio.to_thread(
        buy_number,
        country,
        product,
        operator
    )

    if (
        not result
        or
        result.get("response") == "ERROR"
    ):

        try:

            refund = refund_order(
                order_id,
                "Pembelian nomor 5SIM gagal."
            )

        except Exception as error:

            logger.exception(
                "Refund gagal: %s",
                order_id
            )

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

    if not provider_order_id or not phone:

        refund_order(
            order_id,
            "Respons 5SIM tidak lengkap."
        )

        await query.edit_message_text(
            "❌ <b>Respons provider tidak valid.</b>\n\n"
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
        f"🧾 Order: "
        f"<code>{order_id}</code>\n"
        f"🌍 Negara: <b>{country}</b>\n"
        f"📱 Layanan: <b>{product}</b>\n\n"
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

    elif query.data.startswith(
    "otp_check:"
):

    order_id = query.data.split(
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
        order["provider_order_id"]
    )

    if not provider_order_id:

        await query.answer(
            "Order masih diproses.",
            show_alert=True
        )

        return

    data = await asyncio.to_thread(
        get_sms,
        provider_order_id
    )

    if (
        not data
        or
        data.get("response") == "ERROR"
    ):

        await query.answer(
            "Gagal mengecek OTP.",
            show_alert=True
        )

        return

    sms_list = data.get(
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
        "⏳ OTP belum masuk. Coba lagi.",
        show_alert=True
    )

    elif query.data.startswith(
    "otp_cancel:"
):

    order_id = query.data.split(
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
        order["provider_order_id"]
    )

    await query.edit_message_text(
        "⏳ <b>Membatalkan order...</b>",
        parse_mode="HTML"
    )

    if provider_order_id:

        await asyncio.to_thread(
            cancel_number,
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
            f"Order: <code>{order_id}</code>\n"
            f"Error: <code>{error}</code>",
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

    # =====================================================
    # DEPOSIT
    # =====================================================

    elif query.data == "user_deposit":

        # Aktifkan mode menunggu nominal
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

    # =====================================================
    # CANCEL DEPOSIT
    # =====================================================

    elif query.data == "cancel_deposit":

        # MATIKAN MODE INPUT
        context.chat_data[
            "waiting_deposit"
        ] = False

        await query.edit_message_text(
            "❌ <b>Deposit dibatalkan.</b>\n\n"
            "Tidak ada nominal yang diproses.",
            parse_mode="HTML",
            reply_markup=user_menu()
        )

    # =====================================================
    # HISTORY ORDER
    # =====================================================

    elif query.data == "user_history_order":

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
                "📋 <b>5 Histori Order Terakhir</b>\n\n"
                +
                "\n".join(
                    [
                        f"├ {o['order_id']} - {o['status']}"
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

    # =====================================================
    # HISTORY DEPOSIT
    # =====================================================

    elif query.data == "user_history_depo":

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
                "📜 <b>5 Histori Deposit Terakhir</b>\n\n"
                +
                "\n".join(
                    [
                        f"├ {d['deposit_id']} - "
                        f"{format_rupiah(d['amount'])} - "
                        f"{d['status']}"
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

    # =====================================================
    # REFERRAL
    # =====================================================

    elif query.data == "referral":

        username = query.from_user.username

        if username:

            ref_link = (
                f"https://t.me/"
                f"{username}"
                f"?start=ref{user_id}"
            )

        else:

            ref_link = (
                "Username Telegram kamu belum diatur."
            )

        await query.edit_message_text(
            f"👥 <b>Referral</b>\n\n"
            f"Link kamu:\n"
            f"<code>{ref_link}</code>\n\n"
            f"Dapet 10% dari deposit teman",

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

    # =====================================================
    # CS
    # =====================================================

    elif query.data == "cs":

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

    # =====================================================
    # CEK DEPOSIT
    # =====================================================

    elif query.data == "cek_deposit":

        with get_db() as db:

            deposits = db.execute(
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

        if not deposits:

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
            deposits["deposit_id"]
        )

        if not status_data:

            await query.edit_message_text(
                "❌ Gagal cek ke Midtrans.\n\n"
                "Coba lagi beberapa detik.",

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

        # -------------------------------------------------
        # SUCCESS
        # -------------------------------------------------

        if transaction_status == "settlement":

            result = complete_deposit_payment(
                deposits["deposit_id"],
                status_data.get(
                    "transaction_id"
                ),
                status_data.get(
                    "gross_amount"
                )
            )

            if result["completed"]:

                await query.edit_message_text(
                    f"✅ <b>Deposit Berhasil!</b>\n\n"
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

                send_telegram_message(
                    result["telegram_id"],

                    f"✅ <b>Deposit berhasil!</b>\n\n"
                    f"💰 Deposit: "
                    f"<b>{format_rupiah(result['amount'])}</b>\n"
                    f"💳 Status: <b>PAID</b>\n"
                    f"🧾 ID: "
                    f"<code>{deposits['deposit_id']}</code>\n\n"
                    f"💰 Saldo sekarang: "
                    f"<b>{format_rupiah(result['new_balance'])}</b>"
                )

            elif result["already_completed"]:

                await query.edit_message_text(
                    "✅ <b>Deposit sudah "
                    "berhasil diproses.</b>\n\n"
                    "💰 Saldo sekarang: "
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

        # -------------------------------------------------
        # EXPIRED / CANCEL
        # -------------------------------------------------

        elif transaction_status in [
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
                        deposits["deposit_id"],
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

        # -------------------------------------------------
        # BELUM BAYAR
        # -------------------------------------------------

        else:

            await query.edit_message_text(
                f"⏳ <b>Status: "
                f"{str(transaction_status).upper()}</b>\n\n"
                "Belum dibayar.\n"
                "Klik cek lagi setelah bayar.",

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

    # =====================================================
    # USER HOME
    # =====================================================

    elif query.data == "user_home":

        # PASTIKAN MODE INPUT DEPOSIT MATI
        context.chat_data[
            "waiting_deposit"
        ] = False

        await user_start(query)


# =========================================================
# ADMIN CALLBACK
# =========================================================

async def admin_callback(query):

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

    provider_balance = await asyncio.to_thread(
        get_5sim_balance
    )

    await query.edit_message_text(
        "💰 <b>5SIM PROVIDER</b>\n\n"
        "🟢 Status: <b>CONNECTED</b>\n\n"
        f"💵 Saldo 5SIM: "
        f"<b>${provider_balance:.2f}</b>\n\n"
        "💱 Kurs: "
        f"<b>Rp17.649,80 / USD</b>\n"
        "📈 Margin: <b>20%</b>",

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
            f"💳 Deposits: <b>{deposits}</b>\n"
            f"📦 Orders: <b>{orders}</b>\n"
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

    # -----------------------------------------------------
    # ADMIN CALLBACK
    # -----------------------------------------------------

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

            try:

                await query.answer(
                    "❌ Kamu bukan admin.",
                    show_alert=True
                )

            except Exception:

                pass

            return

        await admin_callback(query)

        return

    # -----------------------------------------------------
    # BATAL / HOME
    # -----------------------------------------------------

    if query.data in [
        "user_home",
        "cancel_deposit"
    ]:

        context.chat_data[
            "waiting_deposit"
        ] = False

    # -----------------------------------------------------
    # USER CALLBACK
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # HANYA TERIMA NOMINAL KALAU MODE DEPOSIT AKTIF
    # -----------------------------------------------------

    if not context.chat_data.get(
        "waiting_deposit",
        False
    ):

        return

    user = update.effective_user

    # -----------------------------------------------------
    # MATIKAN MODE SEBELUM PROSES
    # -----------------------------------------------------

    context.chat_data[
        "waiting_deposit"
    ] = False

    text = (
        update.message.text
        .strip()
        .replace(".", "")
        .replace(",", "")
    )

    # -----------------------------------------------------
    # VALIDASI ANGKA
    # -----------------------------------------------------

    if not text.isdigit():

        await update.message.reply_text(
            "❌ Nominal harus berupa angka.\n\n"
            "Contoh: <code>10000</code>",
            parse_mode="HTML"
        )

        return

    amount = int(text)

    # -----------------------------------------------------
    # MINIMUM
    # -----------------------------------------------------

    if amount < 1000:

        await update.message.reply_text(
            "❌ <b>Deposit terlalu kecil.</b>\n\n"
            "Minimum deposit adalah "
            "<b>Rp1.000</b>.",
            parse_mode="HTML"
        )

        return

    # -----------------------------------------------------
    # KELIPATAN
    # -----------------------------------------------------

    if amount % 1000 != 0:

        await update.message.reply_text(
            "❌ <b>Nominal tidak valid.</b>\n\n"
            "Deposit harus kelipatan "
            "<b>Rp1.000</b>.",
            parse_mode="HTML"
        )

        return

    # -----------------------------------------------------
    # CREATE USER
    # -----------------------------------------------------

    create_user(
        user.id,
        user.username,
        user.first_name
    )

    # -----------------------------------------------------
    # CREATE DEPOSIT ID
    # -----------------------------------------------------

    deposit_id = (
        "DEP-" +
        uuid.uuid4().hex[:12].upper()
    )

    # -----------------------------------------------------
    # INSERT PENDING
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

    # -----------------------------------------------------
    # CREATE MIDTRANS
    # -----------------------------------------------------

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
                "Respons Midtrans tidak berisi "
                "redirect_url/token."
            )

        # -------------------------------------------------
        # SAVE TOKEN
        # -------------------------------------------------

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

        # -------------------------------------------------
        # PAYMENT BUTTONS
        # -------------------------------------------------

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

            f"Klik tombol di bawah "
            f"untuk melakukan pembayaran.\n\n"

            f"Setelah bayar, klik "
            f"'Cek Pembayaran' "
            f"untuk konfirmasi.",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )

    except Exception:

        logger.exception(
            "Gagal membuat invoice Midtrans."
        )

        # -------------------------------------------------
        # MARK FAILED
        # -------------------------------------------------

        with get_db() as db:

            db.execute(
                """
                UPDATE deposits
                SET status = 'FAILED'
                WHERE deposit_id = %s
                AND status = 'PENDING'
                """,
                (deposit_id,)
            )

        await update.message.reply_text(
            "❌ <b>Gagal membuat invoice "
            "pembayaran.</b>\n\n"
            "Silakan coba lagi beberapa "
            "saat kemudian.",
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

    if len(context.args) != 2:

        await update.message.reply_text(
            "Format:\n\n"
            "/addbalance TELEGRAM_ID NOMINAL\n"
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
# FLASK
# =========================================================

def run_flask():

    port = int(
        os.getenv(
            "PORT",
            "5000"
        )
    )

    logger.info(
        "Flask webhook berjalan "
        "di port %s",
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

    # -----------------------------------------------------
    # CALLBACK BUTTON
    # -----------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    # -----------------------------------------------------
    # TEXT / DEPOSIT NOMINAL
    # -----------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.TEXT &
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
    # FLASK + TELEGRAM
    # -----------------------------------------------------

    threading.Thread(
        target=run_flask,
        daemon=True
    ).start()

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":
    run()
