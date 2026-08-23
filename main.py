import os
import sys
import json
import time
import uuid
import sqlite3
import logging
import threading
import asyncio
import requests

from datetime import datetime, timedelta

from flask import Flask

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
    filters
)


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv(
    "BOT_TOKEN",
    ""
)

ADMIN_ID = int(
    os.getenv(
        "ADMIN_ID",
        "0"
    )
)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "bot.db"
)

PORT = int(
    os.getenv(
        "PORT",
        "8080"
    )
)


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s - "
        "%(levelname)s - "
        "%(message)s"
    )
)

logger = logging.getLogger(
    __name__
)


# =========================================================
# FLASK
# =========================================================

app = Flask(
    __name__
)


@app.route("/")
def home():

    return (
        "OTP Reseller Bot is running."
    )


@app.route("/health")
def health():

    return {
        "status": "ok"
    }


def run_flask():

    app.run(
        host="0.0.0.0",
        port=PORT
    )


# =========================================================
# DATABASE
# =========================================================

def get_db():

    conn = sqlite3.connect(
        DATABASE_URL,
        check_same_thread=False
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    with get_db() as db:

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance REAL DEFAULT 0,
                created_at TEXT
            )
            """
        )

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS deposits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                deposit_id TEXT,
                amount REAL,
                status TEXT,
                created_at TEXT
            )
            """
        )

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                transaction_id TEXT,
                type TEXT,
                amount REAL,
                status TEXT,
                created_at TEXT
            )
            """
        )

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                order_id TEXT,
                service TEXT,
                amount REAL,
                status TEXT,
                created_at TEXT
            )
            """
        )

        db.commit()


# =========================================================
# USER HELPERS
# =========================================================

def ensure_user(
    user
):

    if user is None:
        return

    user_id = user.id

    username = (
        user.username
        or ""
    )

    with get_db() as db:

        existing = db.execute(
            """
            SELECT user_id
            FROM users
            WHERE user_id = ?
            """,
            (
                user_id,
            )
        ).fetchone()

        if existing is None:

            db.execute(
                """
                INSERT INTO users (
                    user_id,
                    username,
                    balance,
                    created_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    user_id,
                    username,
                    0,
                    datetime.utcnow().isoformat()
                )
            )

        else:

            db.execute(
                """
                UPDATE users
                SET username = ?
                WHERE user_id = ?
                """,
                (
                    username,
                    user_id
                )
            )

        db.commit()


def get_balance(
    user_id
):

    with get_db() as db:

        row = db.execute(
            """
            SELECT balance
            FROM users
            WHERE user_id = ?
            """,
            (
                user_id,
            )
        ).fetchone()

        if row is None:
            return 0

        return float(
            row["balance"]
        )


def add_balance(
    user_id,
    amount
):

    with get_db() as db:

        db.execute(
            """
            UPDATE users
            SET balance = balance + ?
            WHERE user_id = ?
            """,
            (
                amount,
                user_id
            )
        )

        db.commit()


def deduct_balance(
    user_id,
    amount
):

    with get_db() as db:

        row = db.execute(
            """
            SELECT balance
            FROM users
            WHERE user_id = ?
            """,
            (
                user_id,
            )
        ).fetchone()

        if row is None:
            return False

        balance = float(
            row["balance"]
        )

        if balance < amount:
            return False

        db.execute(
            """
            UPDATE users
            SET balance = balance - ?
            WHERE user_id = ?
            """,
            (
                amount,
                user_id
            )
        )

        db.commit()

        return True


# =========================================================
# ADMIN CHECK
# =========================================================

def is_admin(
    user_id
):

    return (
        int(user_id)
        == ADMIN_ID
    )


# =========================================================
# KEYBOARDS
# =========================================================

def main_keyboard():

    return InlineKeyboardMarkup(

        [

            [
                InlineKeyboardButton(
                    "💰 Deposit",
                    callback_data="user_deposit"
                )
            ],

            [
                InlineKeyboardButton(
                    "📱 Beli OTP",
                    callback_data="buy_otp"
                )
            ],

            [
                InlineKeyboardButton(
                    "💳 Saldo",
                    callback_data="user_balance"
                )
            ],

            [
                InlineKeyboardButton(
                    "📦 Pesanan",
                    callback_data="user_orders"
                )
            ],

            [
                InlineKeyboardButton(
                    "👤 Profil",
                    callback_data="user_profile"
                )
            ],

            [
                InlineKeyboardButton(
                    "ℹ️ Bantuan",
                    callback_data="user_help"
                )
            ]

        ]

    )


def admin_keyboard():

    return InlineKeyboardMarkup(

        [

            [
                InlineKeyboardButton(
                    "👥 Users",
                    callback_data="admin_users"
                )
            ],

            [
                InlineKeyboardButton(
                    "💳 Deposits",
                    callback_data="admin_deposits"
                )
            ],

            [
                InlineKeyboardButton(
                    "📦 Orders",
                    callback_data="admin_orders"
                )
            ],

            [
                InlineKeyboardButton(
                    "💰 Add Balance",
                    callback_data="admin_add_balance"
                )
            ],

            [
                InlineKeyboardButton(
                    "🏠 Menu",
                    callback_data="user_home"
                )
            ]

        ]

    )


# =========================================================
# START
# =========================================================

async def user_start(
    query
):

    user = query.from_user

    ensure_user(
        user
    )

    balance = get_balance(
        user.id
    )

    text = (

        "🤖 <b>OTP RESELLER BOT</b>\n\n"

        f"👤 User: "
        f"<b>{user.first_name}</b>\n"

        f"💰 Saldo: "
        f"<b>Rp {balance:,.0f}</b>\n\n"

        "Silakan pilih menu di bawah."

    )

    await query.edit_message_text(

        text,

        parse_mode="HTML",

        reply_markup=main_keyboard()

    )


async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    ensure_user(
        user
    )

    balance = get_balance(
        user.id
    )

    text = (

        "🤖 <b>OTP RESELLER BOT</b>\n\n"

        f"👤 User: "
        f"<b>{user.first_name}</b>\n"

        f"💰 Saldo: "
        f"<b>Rp {balance:,.0f}</b>\n\n"

        "Silakan pilih menu di bawah."

    )

    await update.message.reply_text(

        text,

        parse_mode="HTML",

        reply_markup=main_keyboard()

    )


# =========================================================
# USER PROFILE
# =========================================================

async def user_profile(
    query
):

    user = query.from_user

    ensure_user(
        user
    )

    balance = get_balance(
        user.id
    )

    username = (
        f"@{user.username}"
        if user.username
        else "-"
    )

    text = (

        "👤 <b>PROFILE</b>\n\n"

        f"🆔 ID: "
        f"<code>{user.id}</code>\n"

        f"👤 Username: "
        f"<b>{username}</b>\n"

        f"💰 Saldo: "
        f"<b>Rp {balance:,.0f}</b>\n"

    )

    keyboard = InlineKeyboardMarkup(

        [

            [
                InlineKeyboardButton(
                    "⬅️ Kembali",
                    callback_data="user_home"
                )
            ]

        ]

    )

    await query.edit_message_text(

        text,

        parse_mode="HTML",

        reply_markup=keyboard

    )


# =========================================================
# USER BALANCE
# =========================================================

async def user_balance(
    query
):

    user = query.from_user

    ensure_user(
        user
    )

    balance = get_balance(
        user.id
    )

    text = (

        "💳 <b>SALDO</b>\n\n"

        f"Saldo kamu:\n"
        f"<b>Rp {balance:,.0f}</b>"

    )

    keyboard = InlineKeyboardMarkup(

        [

            [
                InlineKeyboardButton(
                    "💰 Deposit",
                    callback_data="user_deposit"
                )
            ],

            [
                InlineKeyboardButton(
                    "⬅️ Kembali",
                    callback_data="user_home"
                )
            ]

        ]

    )

    await query.edit_message_text(

        text,

        parse_mode="HTML",

        reply_markup=keyboard

    )


# =========================================================
# HELP
# =========================================================

async def user_help(
    query
):

    text = (

        "ℹ️ <b>BANTUAN</b>\n\n"

        "Cara menggunakan bot:\n\n"

        "1. Deposit saldo.\n"
        "2. Pilih layanan OTP.\n"
        "3. Bayar menggunakan saldo.\n"
        "4. Tunggu OTP masuk.\n\n"

        "Jika mengalami masalah, "
        "hubungi admin."

    )

    keyboard = InlineKeyboardMarkup(

        [

            [
                InlineKeyboardButton(
                    "⬅️ Kembali",
                    callback_data="user_home"
                )
            ]

        ]

    )

    await query.edit_message_text(

        text,

        parse_mode="HTML",

        reply_markup=keyboard

    )


# =========================================================
# DEPOSIT
# =========================================================

async def user_deposit(
    query
):

    context = query._bot_data

    text = (

        "💰 <b>DEPOSIT</b>\n\n"

        "Masukkan nominal deposit.\n\n"

        "Contoh:\n"
        "<code>10000</code>\n\n"

        "Minimal deposit: "
        "<b>Rp 10.000</b>"

    )

    keyboard = InlineKeyboardMarkup(

        [

            [
                InlineKeyboardButton(
                    "⬅️ Kembali",
                    callback_data="user_home"
                )
            ]

        ]

    )

    await query.edit_message_text(

        text,

        parse_mode="HTML",

        reply_markup=keyboard

    )

    return


# =========================================================
# ADMIN HOME
# =========================================================

async def admin_home(
    query
):

    if not is_admin(
        query.from_user.id
    ):

        await query.answer(
            "❌ Tidak memiliki akses.",
            show_alert=True
        )

        return

    text = (

        "🛠 <b>ADMIN PANEL</b>\n\n"

        "Pilih menu admin."

    )

    await query.edit_message_text(

        text,

        parse_mode="HTML",

        reply_markup=admin_keyboard()

    )


# =========================================================
# CALLBACK HANDLER
# =========================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    data = query.data

    user = query.from_user

    ensure_user(
        user
    )

    # =====================================================
    # ADMIN HOME
    # =====================================================

    if data == "admin_home":

        await admin_home(
            query
        )

        return

    # =====================================================
    # USER HOME
    # =====================================================

    if data == "user_home":

        await user_start(
            query
        )

        return

    # =====================================================
    # USER PROFILE
    # =====================================================

    if data == "user_profile":

        await user_profile(
            query
        )

        return

    # =====================================================
    # USER BALANCE
    # =====================================================

    if data == "user_balance":

        await user_balance(
            query
        )

        return

    # =====================================================
    # USER HELP
    # =====================================================

    if data == "user_help":

        await user_help(
            query
        )

        return

    # =====================================================
    # USER DEPOSIT
    # =====================================================

    if data == "user_deposit":

        context.user_data[
            "waiting_deposit"
        ] = True

        await user_deposit(
            query
        )

        return

    # =====================================================
    # BUY OTP
    # =====================================================

    if data == "buy_otp":

        text = (

            "📱 <b>BELI OTP</b>\n\n"

            "Silakan pilih layanan OTP."

        )

        keyboard = InlineKeyboardMarkup(

            [

                [
                    InlineKeyboardButton(
                        "📱 OTP Service",
                        callback_data="otp_service"
                    )
                ],

                [
                    InlineKeyboardButton(
                        "⬅️ Kembali",
                        callback_data="user_home"
                    )
                ]

            ]

        )

        await query.edit_message_text(

            text,

            parse_mode="HTML",

            reply_markup=keyboard

        )

        return

    # =====================================================
    # OTP SERVICE
    # =====================================================

    if data == "otp_service":

        text = (

            "📱 <b>OTP SERVICE</b>\n\n"

            "Pilih layanan yang tersedia."

        )

        keyboard = InlineKeyboardMarkup(

            [

                [
                    InlineKeyboardButton(
                        "🔐 OTP",
                        callback_data="otp_create"
                    )
                ],

                [
                    InlineKeyboardButton(
                        "⬅️ Kembali",
                        callback_data="buy_otp"
                    )
                ]

            ]

        )

        await query.edit_message_text(

            text,

            parse_mode="HTML",

            reply_markup=keyboard

        )

        return

    # =====================================================
    # OTP CREATE
    # =====================================================

    if data == "otp_create":

        balance = get_balance(
            user.id
        )

        price = 5000

        if balance < price:

            await query.edit_message_text(

                "❌ <b>Saldo tidak cukup.</b>\n\n"

                f"Harga OTP: "
                f"<b>Rp {price:,.0f}</b>\n"

                f"Saldo kamu: "
                f"<b>Rp {balance:,.0f}</b>",

                parse_mode="HTML",

                reply_markup=InlineKeyboardMarkup(

                    [

                        [
                            InlineKeyboardButton(
                                "💰 Deposit",
                                callback_data="user_deposit"
                            )
                        ],

                        [
                            InlineKeyboardButton(
                                "⬅️ Kembali",
                                callback_data="user_home"
                            )
                        ]

                    ]

                )

            )

            return

        text = (

            "📱 <b>ORDER OTP</b>\n\n"

            "Harga: "
            "<b>Rp 5.000</b>\n\n"

            "Lanjutkan pembelian?"

        )

        keyboard = InlineKeyboardMarkup(

            [

                [
                    InlineKeyboardButton(
                        "✅ Beli",
                        callback_data="otp_confirm"
                    )
                ],

                [
                    InlineKeyboardButton(
                        "❌ Batal",
                        callback_data="buy_otp"
                    )
                ]

            ]

        )

        await query.edit_message_text(

            text,

            parse_mode="HTML",

            reply_markup=keyboard

        )

        return

    # =====================================================
    # OTP CONFIRM
    # =====================================================

    if data == "otp_confirm":

        price = 5000

        success = deduct_balance(
            user.id,
            price
        )

        if not success:

            await query.edit_message_text(

                "❌ Saldo tidak cukup.",

                reply_markup=InlineKeyboardMarkup(

                    [

                        [
                            InlineKeyboardButton(
                                "💰 Deposit",
                                callback_data="user_deposit"
                            )
                        ],

                        [
                            InlineKeyboardButton(
                                "⬅️ Kembali",
                                callback_data="user_home"
                            )
                        ]

                    ]

                )

            )

            return

        order_id = (
            "ORD-"
            + uuid.uuid4().hex[:10].upper()
        )

        with get_db() as db:

            db.execute(

                """
                INSERT INTO orders (
                    user_id,
                    order_id,
                    service,
                    amount,
                    status,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,

                (
                    user.id,
                    order_id,
                    "OTP",
                    price,
                    "PENDING",
                    datetime.utcnow().isoformat()
                )

            )

            db.commit()

        text = (

            "✅ <b>ORDER BERHASIL</b>\n\n"

            f"Order ID:\n"
            f"<code>{order_id}</code>\n\n"

            "Status: "
            "<b>PENDING</b>\n\n"

            "Silakan tunggu OTP."

        )

        keyboard = InlineKeyboardMarkup(

            [

                [
                    InlineKeyboardButton(
                        "🔄 Cek Status",
                        callback_data=f"order_status:{order_id}"
                    )
                ],

                [
                    InlineKeyboardButton(
                        "🏠 Menu Utama",
                        callback_data="user_home"
                    )
                ]

            ]

        )

        await query.edit_message_text(

            text,

            parse_mode="HTML",

            reply_markup=keyboard

        )

        return

    # =====================================================
    # USER ORDERS
    # =====================================================

    if data == "user_orders":

        with get_db() as db:

            rows = db.execute(

                """
                SELECT
                    order_id,
                    service,
                    amount,
                    status,
                    created_at
                FROM orders
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT 10
                """,

                (
                    user.id,
                )

            ).fetchall()

        if not rows:

            text = (

                "📦 <b>PESANAN</b>\n\n"

                "Belum ada pesanan."

            )

        else:

            lines = [

                "📦 <b>PESANAN</b>\n"

            ]

            for row in rows:

                lines.append(

                    f"• <code>{row['order_id']}</code>\n"
                    f"  Service: {row['service']}\n"
                    f"  Amount: Rp {float(row['amount']):,.0f}\n"
                    f"  Status: <b>{row['status']}</b>\n"

                )

            text = "\n".join(
                lines
            )

        keyboard = InlineKeyboardMarkup(

            [

                [
                    InlineKeyboardButton(
                        "⬅️ Kembali",
                        callback_data="user_home"
                    )
                ]

            ]

        )

        await query.edit_message_text(

            text,

            parse_mode="HTML",

            reply_markup=keyboard

        )

        return

    # =====================================================
    # ORDER STATUS
    # =====================================================

    if data.startswith(
        "order_status:"
    ):

        order_id = data.split(
            ":",
            1
        )[1]

        with get_db() as db:

            row = db.execute(

                """
                SELECT
                    order_id,
                    service,
                    amount,
                    status
                FROM orders
                WHERE order_id = ?
                AND user_id = ?
                """,

                (
                    order_id,
                    user.id
                )

            ).fetchone()

        if row is None:

            await query.edit_message_text(

                "❌ Order tidak ditemukan.",

                reply_markup=InlineKeyboardMarkup(

                    [

                        [
                            InlineKeyboardButton(
                                "🏠 Menu",
                                callback_data="user_home"
                            )
                        ]

                    ]

                )

            )

            return

        text = (

            "📦 <b>ORDER STATUS</b>\n\n"

            f"Order ID:\n"
            f"<code>{row['order_id']}</code>\n\n"

            f"Service: "
            f"<b>{row['service']}</b>\n"

            f"Amount: "
            f"<b>Rp {float(row['amount']):,.0f}</b>\n"

            f"Status: "
            f"<b>{row['status']}</b>"

        )

        keyboard = InlineKeyboardMarkup(

            [

                [
                    InlineKeyboardButton(
                        "🔄 Refresh",
                        callback_data=f"order_status:{order_id}"
                    )
                ],

                [
                    InlineKeyboardButton(
                        "⬅️ Kembali",
                        callback_data="user_orders"
                    )
                ]

            ]

        )

        await query.edit_message_text(

            text,

            parse_mode="HTML",

            reply_markup=keyboard

        )

        return

    # =====================================================
    # CHECK DEPOSIT
    # =====================================================

    if data == "cek_deposit":

        deposit_id = context.user_data.get(
            "deposit_id"
        )

        if not deposit_id:

            await query.edit_message_text(

                "❌ Tidak ada deposit aktif.",

                reply_markup=InlineKeyboardMarkup(

                    [

                        [
                            InlineKeyboardButton(
                                "💰 Deposit",
                                callback_data="user_deposit"
                            )
                        ],

                        [
                            InlineKeyboardButton(
                                "🏠 Menu Utama",
                                callback_data="user_home"
                            )
                        ]

                    ]

                )

            )

            return

        with get_db() as db:

            deposit = db.execute(

                """
                SELECT *
                FROM deposits
                WHERE deposit_id = ?
                AND user_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,

                (
                    deposit_id,
                    user.id
                )

            ).fetchone()

        if deposit is None:

            await query.edit_message_text(

                "❌ Deposit tidak ditemukan.",

                reply_markup=InlineKeyboardMarkup(

                    [

                        [
                            InlineKeyboardButton(
                                "💰 Deposit Lagi",
                                callback_data="user_deposit"
                            )
                        ],

                        [
                            InlineKeyboardButton(
                                "🏠 Menu Utama",
                                callback_data="user_home"
                            )
                        ]

                    ]

                )

            )

            return

        transaction_status = (
            deposit["status"]
        )

        if transaction_status in [

            "SUCCESS",
            "PAID"

        ]:

            amount = float(
                deposit["amount"]
            )

            add_balance(
                user.id,
                amount
            )

            with get_db() as db:

                db.execute(

                    """
                    UPDATE deposits
                    SET status = 'BALANCE_ADDED'
                    WHERE deposit_id = ?
                    AND status IN ('SUCCESS', 'PAID')
                    """,

                    (
                        deposit_id,
                    )

                )

                db.commit()

            new_balance = get_balance(
                user.id
            )

            await query.edit_message_text(

                "✅ <b>Deposit Berhasil</b>\n\n"

                f"Jumlah: "
                f"<b>Rp {amount:,.0f}</b>\n"

                f"Saldo sekarang: "
                f"<b>Rp {new_balance:,.0f}</b>",

                parse_mode="HTML",

                reply_markup=InlineKeyboardMarkup(

                    [

                        [
                            InlineKeyboardButton(
                                "🏠 Menu Utama",
                                callback_data="user_home"
                            )
                        ]

                    ]

                )

            )

            context.user_data.pop(
                "deposit_id",
                None
            )

            return

        if transaction_status in [

            "EXPIRED",
            "FAILED",
            "CANCELLED"

        ]:

            await query.edit_message_text(

                "❌ <b>Deposit Expired</b>\n\n"
                "Silakan buat invoice baru.",

                parse_mode="HTML",

                reply_markup=InlineKeyboardMarkup(

                    [

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

                    ]

                )

            )

            return

        await query.edit_message_text(

            f"⏳ <b>Status: "
            f"{str(transaction_status).upper()}</b>\n\n"
            "Belum dibayar.",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup(

                [

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

                ]

            )

        )

        return

    # =====================================================
    # UNKNOWN CALLBACK
    # =====================================================

    await query.edit_message_text(

        "❌ Menu tidak dikenal.",

        reply_markup=InlineKeyboardMarkup(

            [

                [
                    InlineKeyboardButton(
                        "🏠 Menu Utama",
                        callback_data="user_home"
                    )
                ]

            ]

        )

    )


# =========================================================
# TEXT HANDLER
# =========================================================

async def text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    ensure_user(
        user
    )

    text = (
        update.message.text
        or ""
    ).strip()

    # =====================================================
    # WAITING DEPOSIT
    # =====================================================

    if context.user_data.get(
        "waiting_deposit"
    ):

        try:

            amount = int(
                text
            )

        except ValueError:

            await update.message.reply_text(

                "❌ Nominal harus berupa angka.\n\n"
                "Contoh: <code>10000</code>",

                parse_mode="HTML"

            )

            return

        if amount < 10000:

            await update.message.reply_text(

                "❌ Minimal deposit adalah "
                "<b>Rp 10.000</b>.",

                parse_mode="HTML"

            )

            return

        deposit_id = (
            "DEP-"
            + uuid.uuid4().hex[:12].upper()
        )

        with get_db() as db:

            db.execute(

                """
                INSERT INTO deposits (
                    user_id,
                    deposit_id,
                    amount,
                    status,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,

                (
                    user.id,
                    deposit_id,
                    amount,
                    "PENDING",
                    datetime.utcnow().isoformat()
                )

            )

            db.commit()

        context.user_data[
            "deposit_id"
        ] = deposit_id

        context.user_data[
            "waiting_deposit"
        ] = False

        text_response = (

            "💰 <b>INVOICE DEPOSIT</b>\n\n"

            f"Deposit ID:\n"
            f"<code>{deposit_id}</code>\n\n"

            f"Nominal:\n"
            f"<b>Rp {amount:,.0f}</b>\n\n"

            "Status: "
            "<b>PENDING</b>\n\n"

            "Silakan lakukan pembayaran, "
            "kemudian tekan tombol "
            "<b>Cek Status</b>."

        )

        keyboard = InlineKeyboardMarkup(

            [

                [
                    InlineKeyboardButton(
                        "🔄 Cek Status",
                        callback_data="cek_deposit"
                    )
                ],

                [
                    InlineKeyboardButton(
                        "❌ Batal",
                        callback_data="user_home"
                    )
                ]

            ]

        )

        await update.message.reply_text(

            text_response,

            parse_mode="HTML",

            reply_markup=keyboard

        )

        return

    await update.message.reply_text(

        "Gunakan tombol menu untuk memilih fitur.",

        reply_markup=main_keyboard()

    )


# =========================================================
# ADMIN COMMANDS
# =========================================================

async def admin_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(
        update.effective_user.id
    ):

        await update.message.reply_text(
            "❌ Tidak memiliki akses."
        )

        return

    text = (

        "🛠 <b>ADMIN PANEL</b>\n\n"

        "Silakan pilih menu."

    )

    await update.message.reply_text(

        text,

        parse_mode="HTML",

        reply_markup=admin_keyboard()

    )


async def admin_add_balance(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(
        update.effective_user.id
    ):

        await update.message.reply_text(
            "❌ Tidak memiliki akses."
        )

        return

    if not context.args:

        await update.message.reply_text(

            "Format:\n"
            "<code>/addbalance USER_ID AMOUNT</code>",

            parse_mode="HTML"

        )

        return

    if len(
        context.args
    ) < 2:

        await update.message.reply_text(

            "Format:\n"
            "<code>/addbalance USER_ID AMOUNT</code>",

            parse_mode="HTML"

        )

        return

    try:

        user_id = int(
            context.args[0]
        )

        amount = float(
            context.args[1]
        )

    except ValueError:

        await update.message.reply_text(
            "❌ User ID dan amount harus valid."
        )

        return

    add_balance(
        user_id,
        amount
    )

    await update.message.reply_text(

        "✅ Balance berhasil ditambahkan.\n\n"

        f"User ID: <code>{user_id}</code>\n"
        f"Amount: <b>Rp {amount:,.0f}</b>",

        parse_mode="HTML"

    )


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

        return

    if query.data == "admin_deposits":

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

        return

    if query.data == "admin_orders":

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
            f"Total: <b>{total}</b>\n"
            f"Pending: <b>{pending}</b>\n"
            f"Success: <b>{success}</b>",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup(
                back
            )

        )

        return

    if query.data == "admin_add_balance":

        await query.edit_message_text(

            "💰 <b>ADD BALANCE</b>\n\n"
            "Gunakan command:\n\n"
            "<code>/addbalance USER_ID AMOUNT</code>",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup(
                back
            )

        )

        return

    await query.edit_message_text(

        "❌ Menu admin tidak ditemukan.",

        reply_markup=InlineKeyboardMarkup(
            back
        )

    )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update,
    context
):

    logger.exception(
        "Exception while handling an update:",
        exc_info=context.error
    )


# =========================================================
# MAIN
# =========================================================

def run():

    init_db()

    application = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(

        CommandHandler(
            "start",
            start_command
        )

    )

    application.add_handler(

        CommandHandler(
            "admin",
            admin_command
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
                    service_id
                )

                items.append(
                    (
                        str(sid),
                        str(name)
                    )
                )

            else:

                items.append(
                    (
                        str(service_id),
                        str(value)
                    )
                )

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
                    "service_id"
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

                items.append(
                    (
                        str(sid),
                        str(name)
                    )
                )

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
            f"📱 Layanan: <b>{product}</b>\n"
            f"📡 Operator: <b>{operator}</b>\n\n"
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
