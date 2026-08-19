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
)


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

logger = logging.getLogger(__name__)


def main_menu():
    keyboard = [
        [
            InlineKeyboardButton(
                "💰 Saldo",
                callback_data="balance"
            ),
            InlineKeyboardButton(
                "💳 Deposit",
                callback_data="deposit"
            ),
        ],
        [
            InlineKeyboardButton(
                "📱 Layanan",
                callback_data="services"
            ),
            InlineKeyboardButton(
                "📜 Riwayat",
                callback_data="history"
            ),
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    create_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name
    )

    balance = get_balance(user.id)

    text = (
        "👋 <b>Selamat datang!</b>\n\n"
        "Bot layanan digital kamu sudah aktif.\n\n"
        f"💰 Saldo: <b>Rp{balance:,}</b>\n\n"
        "Silakan pilih menu:"
    ).replace(",", ".")

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=main_menu()
    )


async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    if query.data == "balance":

        balance = get_balance(user_id)

        text = (
            "💰 <b>Saldo Kamu</b>\n\n"
            f"Saldo: <b>Rp{balance:,}</b>"
        ).replace(",", ".")

        keyboard = [
            [
                InlineKeyboardButton(
                    "💳 Deposit",
                    callback_data="deposit"
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Kembali",
                    callback_data="home"
                )
            ]
        ]

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data == "deposit":

        context.user_data["waiting_deposit"] = True

        text = (
            "💳 <b>Deposit Saldo</b>\n\n"
            "Masukkan nominal deposit.\n\n"
            "Minimum: <b>Rp1.000</b>\n"
            "Harus kelipatan: <b>Rp1.000</b>\n\n"
            "Contoh:\n"
            "• 1000\n"
            "• 5000\n"
            "• 10000\n"
            "• 25000\n\n"
            "Ketik nominal sekarang."
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    "❌ Batal",
                    callback_data="home"
                )
            ]
        ]

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data == "services":

        text = (
            "📱 <b>Layanan</b>\n\n"
            "Modul layanan belum diaktifkan.\n\n"
            "Setelah sistem saldo dan pembayaran selesai, "
            "menu ini akan terhubung ke provider layanan yang "
            "mendukung penggunaan yang sah."
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    "⬅️ Kembali",
                    callback_data="home"
                )
            ]
        ]

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data == "history":

        text = (
            "📜 <b>Riwayat</b>\n\n"
            "Belum ada transaksi."
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    "⬅️ Kembali",
                    callback_data="home"
                )
            ]
        ]

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data == "home":

        balance = get_balance(user_id)

        text = (
            "🏠 <b>Menu Utama</b>\n\n"
            f"💰 Saldo: <b>Rp{balance:,}</b>\n\n"
            "Pilih menu:"
        ).replace(",", ".")

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=main_menu()
        )


async def text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    user = update.effective_user
    text = update.message.text.strip()

    if not context.user_data.get("waiting_deposit"):
        return

    context.user_data["waiting_deposit"] = False

    text = text.replace(".", "").replace(",", "")

    if not text.isdigit():

        await update.message.reply_text(
            "❌ Nominal harus berupa angka.\n\n"
            "Contoh: <b>10000</b>",
            parse_mode="HTML"
        )

        return

    amount = int(text)

    if amount < 1000:

        await update.message.reply_text(
            "❌ Minimum deposit adalah <b>Rp1.000</b>.",
            parse_mode="HTML"
        )

        return

    if amount % 1000 != 0:

        await update.message.reply_text(
            "❌ Deposit harus kelipatan <b>Rp1.000</b>.\n\n"
            "Contoh yang benar:\n"
            "Rp1.000\n"
            "Rp5.000\n"
            "Rp10.000",
            parse_mode="HTML"
        )

        return

    deposit_id = "DEP-" + uuid.uuid4().hex[:12].upper()

    # Untuk sekarang transaksi hanya dicatat sebagai PENDING.
    # Nanti akan diganti dengan pembuatan invoice/payment gateway.
    from database import get_db, now

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
                now()
            )
        )

    formatted = f"Rp{amount:,}".replace(",", ".")

    await update.message.reply_text(
        "✅ <b>Deposit dibuat</b>\n\n"
        f"ID: <code>{deposit_id}</code>\n"
        f"Nominal: <b>{formatted}</b>\n"
        "Status: <b>PENDING</b>\n\n"
        "💳 Payment gateway akan kita sambungkan "
        "pada tahap berikutnya.",
        parse_mode="HTML"
    )


async def admin_add_balance(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != ADMIN_ID:
        return

    if len(context.args) != 2:

        await update.message.reply_text(
            "Format:\n"
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
            reference=f"ADMIN-{uuid.uuid4().hex[:8]}",
            description="Saldo ditambahkan oleh admin"
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Gagal: {e}"
        )

        return

    formatted = f"Rp{new_balance:,}".replace(",", ".")

    await update.message.reply_text(
        "✅ Saldo berhasil ditambahkan.\n\n"
        f"User: <code>{telegram_id}</code>\n"
        f"Saldo baru: <b>{formatted}</b>",
        parse_mode="HTML"
    )


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    logger.error(
        "Exception while handling update:",
        exc_info=context.error
    )


def run():

    init_database()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

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

    application.add_error_handler(error_handler)

    logger.info("Bot started.")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    run()
