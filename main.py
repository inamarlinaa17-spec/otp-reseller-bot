import logging
import uuid

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
    get_db,
    now,
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
# HELPER
# =========================================================

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def format_rupiah(amount: int) -> str:
    return f"Rp{amount:,}".replace(",", ".")


# =========================================================
# USER MENU
# =========================================================

def user_menu():

    keyboard = [
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
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================================================
# ADMIN MENU
# =========================================================

def admin_menu():

    keyboard = [
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
            ),
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================================================
# USER START
# =========================================================

async def user_start(update: Update):

    user = update.effective_user

    create_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )

    balance = get_balance(user.id)

    text = (
        "👋 <b>Selamat datang!</b>\n\n"
        "Bot layanan digital kamu sudah aktif.\n\n"
        f"💰 Saldo: <b>{format_rupiah(balance)}</b>\n\n"
        "Silakan pilih menu:"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=user_menu(),
    )


# =========================================================
# ADMIN START
# =========================================================

async def admin_start(update: Update):

    text = (
        "👑 <b>ADMIN PANEL</b>\n\n"
        "Selamat datang, Admin.\n\n"
        "Dari panel ini kamu nantinya bisa mengelola:\n\n"
        "👥 User\n"
        "💳 Deposit\n"
        "📦 Order\n"
        "💰 Provider\n"
        "📊 Statistik\n\n"
        "Pilih menu:"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=admin_menu(),
    )


# =========================================================
# /START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    create_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name,
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
    user_id,
):

    # -------------------------
    # SALDO
    # -------------------------

    if query.data == "user_balance":

        balance = get_balance(user_id)

        keyboard = [
            [
                InlineKeyboardButton(
                    "💳 Deposit",
                    callback_data="user_deposit",
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Kembali",
                    callback_data="user_home",
                )
            ],
        ]

        await query.edit_message_text(
            "💰 <b>Saldo Kamu</b>\n\n"
            f"Saldo: <b>{format_rupiah(balance)}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    # -------------------------
    # DEPOSIT
    # -------------------------

    elif query.data == "user_deposit":

        return "WAIT_DEPOSIT"

    # -------------------------
    # SERVICES
    # -------------------------

    elif query.data == "user_services":

        keyboard = [
            [
                InlineKeyboardButton(
                    "⬅️ Kembali",
                    callback_data="user_home",
                )
            ]
        ]

        await query.edit_message_text(
            "📱 <b>Layanan</b>\n\n"
            "Modul layanan belum diaktifkan.\n\n"
            "Nanti menu ini akan terhubung "
            "ke provider API.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    # -------------------------
    # HISTORY
    # -------------------------

    elif query.data == "user_history":

        keyboard = [
            [
                InlineKeyboardButton(
                    "⬅️ Kembali",
                    callback_data="user_home",
                )
            ]
        ]

        await query.edit_message_text(
            "📜 <b>Riwayat Transaksi</b>\n\n"
            "Belum ada transaksi.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    # -------------------------
    # HOME
    # -------------------------

    elif query.data == "user_home":

        balance = get_balance(user_id)

        await query.edit_message_text(
            "🏠 <b>Menu Utama</b>\n\n"
            f"💰 Saldo: <b>{format_rupiah(balance)}</b>\n\n"
            "Pilih menu:",
            parse_mode="HTML",
            reply_markup=user_menu(),
        )


# =========================================================
# ADMIN CALLBACK
# =========================================================

async def admin_callback(
    query,
):

    # -------------------------
    # USERS
    # -------------------------

    if query.data == "admin_users":

        with get_db() as db:

            row = db.execute(
                "SELECT COUNT(*) AS total FROM users"
            ).fetchone()

        total_users = row["total"]

        keyboard = [
            [
                InlineKeyboardButton(
                    "⬅️ Admin Panel",
                    callback_data="admin_home",
                )
            ]
        ]

        await query.edit_message_text(
            "👥 <b>USERS</b>\n\n"
            f"Total user: <b>{total_users}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    # -------------------------
    # DEPOSITS
    # -------------------------

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

        keyboard = [
            [
                InlineKeyboardButton(
                    "⬅️ Admin Panel",
                    callback_data="admin_home",
                )
            ]
        ]

        await query.edit_message_text(
            "💳 <b>DEPOSIT</b>\n\n"
            f"Total transaksi: <b>{total}</b>\n"
            f"Pending: <b>{pending}</b>\n"
            f"Success: <b>{success}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    # -------------------------
    # ORDERS
    # -------------------------

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

        keyboard = [
            [
                InlineKeyboardButton(
                    "⬅️ Admin Panel",
                    callback_data="admin_home",
                )
            ]
        ]

        await query.edit_message_text(
            "📦 <b>ORDERS</b>\n\n"
            f"Total order: <b>{total}</b>\n"
            f"Pending: <b>{pending}</b>\n"
            f"Success: <b>{success}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    # -------------------------
    # PROVIDER
    # -------------------------

    elif query.data == "admin_provider":

        keyboard = [
            [
                InlineKeyboardButton(
                    "⬅️ Admin Panel",
                    callback_data="admin_home",
                )
            ]
        ]

        await query.edit_message_text(
            "💰 <b>PROVIDER</b>\n\n"
            "Provider API belum terhubung.\n\n"
            "Nanti bagian ini akan menampilkan:\n\n"
            "• Balance provider\n"
            "• Status API\n"
            "• Order aktif\n"
            "• Refund\n"
            "• Provider cost",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    # -------------------------
    # STATISTICS
    # -------------------------

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
                SELECT COALESCE(SUM(balance), 0) AS total
                FROM users
                """
            ).fetchone()["total"]

        keyboard = [
            [
                InlineKeyboardButton(
                    "⬅️ Admin Panel",
                    callback_data="admin_home",
                )
            ]
        ]

        await query.edit_message_text(
            "📊 <b>STATISTIK</b>\n\n"
            f"👥 Users: <b>{users}</b>\n"
            f"💳 Deposits: <b>{deposits}</b>\n"
            f"📦 Orders: <b>{orders}</b>\n"
            f"💰 Total saldo user: <b>{format_rupiah(balance)}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    # -------------------------
    # ADMIN HOME
    # -------------------------

    elif query.data == "admin_home":

        await query.edit_message_text(
            "👑 <b>ADMIN PANEL</b>\n\n"
            "Pilih menu:",
            parse_mode="HTML",
            reply_markup=admin_menu(),
        )


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

        context.user_data["waiting_deposit"] = True

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

    if not context.user_data.get("waiting_deposit"):
        return

    user = update.effective_user

    text = update.message.text.strip()

    context.user_data["waiting_deposit"] = False

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

    deposit_id = (
        "DEP-"
        + uuid.uuid4().hex[:12].upper()
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
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                deposit_id,
                user.id,
                amount,
                "PENDING",
                now(),
            ),
        )

    await update.message.reply_text(
        "✅ <b>Deposit berhasil dibuat.</b>\n\n"
        f"🧾 ID: <code>{deposit_id}</code>\n"
        f"💰 Nominal: <b>{format_rupiah(amount)}</b>\n"
        "📌 Status: <b>PENDING</b>\n\n"
        "Payment gateway akan kita sambungkan "
        "di tahap berikutnya.",
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
        f"💰 Saldo baru: <b>{format_rupiah(new_balance)}</b>",
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

    application = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        CommandHandler(
            "addbalance",
            admin_add_balance,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler,
        )
    )

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "Bot berhasil dijalankan."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    run()
