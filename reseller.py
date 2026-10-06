"""Menu RESELLER di bot Telegram + pengelola bot reseller (whitelabel)."""
import asyncio
import logging
import time
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, ApplicationHandlerStop, TypeHandler

import database as db
import reseller_core as core
from config import ADMIN_ID
from reseller_ctx import CURRENT_RESELLER

logger = logging.getLogger(__name__)

MANAGER = None  # diisi oleh init_manager()


def _kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in row] for row in rows])


# =========================================================
# TAMPILAN
# =========================================================

INTRO_TEXT = (
    "🤖 <b>RESELLER / WHITELABEL BOT</b>\n\n"
    "Kelola bot Telegram Anda sendiri untuk menjual layanan ini secara <b>whitelabel</b> — "
    "tanpa coding, tanpa modal.\n\n"
    "• Harga jual = harga asli bot utama + <b>margin Anda</b>\n"
    f"• Margin minimum <b>{core.MIN_MARGIN:g}%</b> per transaksi\n"
    "• Semua katalog, stok, & transaksi memakai sistem pusat yang sama\n"
    "• Hasil margin otomatis masuk ke <b>saldo akun Anda</b> di bot ini\n\n"
    "<i>Silakan klik tombol di bawah untuk memulai.</i>"
)

TOKEN_HELP_TEXT = (
    "🤖 <b>CARA MENDAPATKAN TOKEN BOT</b>\n(dari @BotFather)\n\n"
    "1. Buka @BotFather di Telegram\n"
    "2. Ketik <code>/newbot</code> dan ikuti instruksinya\n"
    "3. Setelah selesai, Anda mendapat token seperti:\n"
    "<code>1234567890:AAHfXHzemLQ4...</code>\n"
    "4. <b>Salin token</b> tersebut lalu tempel/kirim ke chat ini\n\n"
    "⚠️ <b>PENTING:</b>\n"
    "• Token hanya milik Anda — <b>jangan</b> gunakan di bot lain\n"
    "• Anda hanya memiliki satu bot reseller\n\n"
    "<i>Silakan kirim token Anda sekarang.</i>"
)


def _running(bot_id):
    return bool(MANAGER and MANAGER.is_running(bot_id))


def panel_text(data):
    bot, stats = data["bot"], data["stats"]
    status, active = core.status_label(bot, _running(bot["id"]))
    text = (
        "🤖 <b>BOT RESELLER ANDA</b>\n\n"
        f"📛 Nama : <b>{escape(bot['bot_name'] or '-')}</b>\n"
        f"👤 Username : @{escape(bot['bot_username'] or '-')}\n"
        f"📶 Status : <b>{escape(status)}</b>\n"
        f"💰 Margin : <b>{core.pct(bot['margin_percent'])}</b>\n\n"
        "<b>Statistik Ringkas:</b>\n"
        f"👥 Pembeli : <b>{stats['buyers']}</b>\n"
        f"✅ Transaksi Sukses : <b>{stats['success']}</b>\n"
        f"💵 Saldo Margin : <b>{core.rp(bot['margin_balance'])}</b>"
    )
    if not bot.get("cs_url"):
        text += (
            "\n\n⚠️ <b>Bot belum bisa aktif.</b>\n"
            "Tekan <b>Contact CS</b> dan atur jalur komplain pembeli. "
            "Bot akan langsung menyala setelah itu."
        )
    return text


def panel_markup(bot):
    toggle = (
        ("▶️ Aktifkan Bot", "rs:start") if not bot.get("enabled") else ("⏹ Hentikan Bot", "rs:stop")
    )
    return _kb([
        [("✏️ Ubah Margin", "rs:margin"), ("🔄 Restart Bot", "rs:restart")],
        [("📤 Withdraw", "rs:wd"), ("📋 Riwayat WD", "rs:wdhist")],
        [("💬 Contact CS", "rs:cs"), ("🔑 Ganti Token", "rs:token")],
        [toggle],
        [("⌫ Kembali ke Menu Utama", "user_home")],
    ])


async def _edit(query, text, markup):
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)
    except Exception as exc:  # pesan sama / sudah dihapus
        if "not modified" not in str(exc).lower():
            await query.message.reply_text(text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)


async def show_menu(query_or_message, owner_id, edit=True, prefix=""):
    """Tampilkan menu reseller: intro (belum punya bot) atau panel."""
    data = await asyncio.to_thread(core.panel_data, owner_id)
    if data:
        text, markup = prefix + panel_text(data), panel_markup(data["bot"])
    else:
        text = prefix + INTRO_TEXT
        markup = _kb([[("➕ Buat Bot Baru", "rs:new")], [("⌫ Kembali ke Menu Utama", "user_home")]])
    if edit:
        await _edit(query_or_message, text, markup)
    else:
        await query_or_message.reply_text(text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)


# =========================================================
# CALLBACK
# =========================================================

BACK = _kb([[("⌫ Kembali", "rs:menu")]])


async def handle_callback(query, context):
    """Return True bila callback milik fitur reseller."""
    data = query.data or ""
    if data != "reseller" and not data.startswith("rs:"):
        return False

    # Fitur reseller hanya ada di bot utama.
    if CURRENT_RESELLER.get() is not None:
        await query.answer("Menu ini hanya tersedia di bot utama.", show_alert=True)
        return True

    uid = query.from_user.id
    action = "menu" if data == "reseller" else data.split(":")[1]
    arg = data.split(":", 2)[2] if data.count(":") >= 2 else ""

    if action.startswith("adm_"):
        await _admin_action(query, context, action, arg)
        return True

    context.user_data.pop("rs_state", None)
    bot = await asyncio.to_thread(db.get_reseller_by_owner, uid)

    if action == "menu":
        await show_menu(query, uid)
    elif action == "new":
        if bot:
            await show_menu(query, uid)
            return True
        context.user_data["rs_state"] = {"type": "token", "mode": "new"}
        await _edit(query, TOKEN_HELP_TEXT, BACK)
    elif not bot:
        await show_menu(query, uid)
    elif action == "token":
        context.user_data["rs_state"] = {"type": "token", "mode": "change"}
        await _edit(
            query,
            "🔑 <b>GANTI TOKEN</b>\n\nKirim token bot baru dari @BotFather. "
            "Token lama otomatis tidak dipakai lagi.\n\n<i>Silakan kirim token baru Anda.</i>",
            BACK,
        )
    elif action == "cs":
        context.user_data["rs_state"] = {"type": "cs"}
        current = f"\nSaat ini: {escape(bot['cs_url'])}\n" if bot.get("cs_url") else ""
        await _edit(
            query,
            "💬 <b>CONTACT CS</b>\n\n"
            "Kirim <b>username Telegram Anda sendiri</b> sebagai kontak komplain pembeli di bot Anda."
            f"{current}\nContoh: <code>@username</code>",
            BACK,
        )
    elif action == "margin":
        context.user_data["rs_state"] = {"type": "margin"}
        await _edit(
            query,
            "✏️ <b>UBAH MARGIN</b>\n\n"
            f"Margin saat ini: <b>{core.pct(bot['margin_percent'])}</b>\n"
            f"Kirim margin baru ({core.MIN_MARGIN:g}% - {core.MAX_MARGIN:g}%), contoh: <code>7</code>\n\n"
            "Harga jual pembeli = harga bot utama + margin Anda.",
            BACK,
        )
    elif action == "restart":
        if not bot.get("cs_url") or not bot.get("enabled"):
            await query.answer("Bot belum aktif (atur Contact CS / aktifkan bot dahulu).", show_alert=True)
            return True
        await query.answer("Memulai ulang bot...")
        ok = await MANAGER.restart(bot["id"]) if MANAGER else False
        await show_menu(query, uid, prefix="" if ok else "⚠️ Gagal restart, cek token Anda.\n\n")
    elif action == "stop":
        await _edit(
            query,
            "⏹ <b>Hentikan bot?</b>\n\nPembeli tidak bisa memakai bot Anda sampai diaktifkan lagi.",
            _kb([[("✅ Ya, Hentikan", "rs:stop_ok"), ("❌ Batal", "rs:menu")]]),
        )
    elif action == "stop_ok":
        await asyncio.to_thread(core.set_enabled, uid, False)
        if MANAGER:
            await MANAGER.stop_bot(bot["id"])
        await show_menu(query, uid)
    elif action == "start":
        try:
            await asyncio.to_thread(core.set_enabled, uid, True)
        except ValueError as exc:
            await query.answer(str(exc), show_alert=True)
            return True
        if MANAGER:
            await MANAGER.restart(bot["id"])
        await show_menu(query, uid)
    elif action == "wd":
        await _start_withdraw(query, context, bot)
    elif action == "wd_confirm":
        await _confirm_withdraw(query, context, bot)
    elif action == "wdhist":
        await _withdraw_history(query, uid)
    else:
        await show_menu(query, uid)
    return True


# =========================================================
# WITHDRAW
# =========================================================

async def _start_withdraw(query, context, bot):
    balance = int(bot["margin_balance"])
    minimum = core.MIN_WITHDRAW
    if balance < minimum:
        await _edit(
            query,
            "📤 <b>WITHDRAW MARGIN</b>\n\n"
            f"💰 Saldo margin: <b>{core.rp(balance)}</b>\n"
            f"🔻 Minimum penarikan: <b>{core.rp(minimum)}</b>\n\n"
            f"Saldo Anda belum mencapai minimum. Tambahkan margin sampai minimal <b>{core.rp(minimum)}</b> untuk dapat melakukan penarikan.",
            BACK,
        )
        return
    context.user_data["rs_state"] = {"type": "wd_amount"}
    await _edit(
        query,
        "📤 <b>WITHDRAW MARGIN</b>\n\n"
        f"💰 Saldo margin: <b>{core.rp(balance)}</b>\n"
        f"🔻 Minimum penarikan: <b>{core.rp(minimum)}</b>\n\n"
        "Kirim nominal yang ingin ditarik, contoh: <code>50000</code>\n"
        "Kirim <code>semua</code> untuk menarik seluruh saldo.",
        BACK,
    )


async def _confirm_withdraw(query, context, bot):
    draft = context.user_data.pop("rs_wd_draft", None)
    if not draft:
        await show_menu(query, query.from_user.id)
        return
    try:
        wd = await asyncio.to_thread(
            core.request_withdrawal, query.from_user.id,
            draft["amount"], draft["provider"], draft["number"], draft["name"],
        )
    except ValueError as exc:
        await _edit(query, f"❌ {escape(str(exc))}", BACK)
        return
    await _edit(
        query,
        "✅ <b>Permintaan penarikan dikirim</b>\n\n"
        f"🧾 ID: <code>WD-{wd['id']}</code>\n"
        f"💰 Nominal: <b>{core.rp(wd['amount'])}</b>\n"
        f"🏦 Tujuan: {escape(wd['provider_name'])} • <code>{escape(wd['account_number'])}</code> a.n. {escape(wd['account_name'])}\n\n"
        "Admin akan memproses dan mentransfer secara manual. Anda akan mendapat notifikasi.",
        BACK,
    )
    owner = query.from_user
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "📤 <b>PERMINTAAN WITHDRAW RESELLER</b>\n\n"
                f"🧾 ID: <code>WD-{wd['id']}</code>\n"
                f"👤 Owner: <code>{owner.id}</code> @{escape(owner.username or '-')}\n"
                f"🤖 Bot: @{escape(bot['bot_username'] or '-')}\n"
                f"💰 Nominal: <b>{core.rp(wd['amount'])}</b>\n"
                f"🏦 {escape(wd['method'])} • {escape(wd['provider_name'])}\n"
                f"🔢 No: <code>{escape(wd['account_number'])}</code>\n"
                f"📛 a.n. {escape(wd['account_name'])}\n\n"
                "Transfer manual lalu tekan <b>Sudah Ditransfer</b>."
            ),
            parse_mode="HTML",
            reply_markup=_kb([[("✅ Sudah Ditransfer", f"rs:adm_ok:{wd['id']}"), ("❌ Tolak", f"rs:adm_no:{wd['id']}")]]),
        )
    except Exception:
        logger.exception("Gagal kirim notifikasi WD ke admin")


async def _withdraw_history(query, uid):
    rows = await asyncio.to_thread(db.list_reseller_withdrawals, uid, 10)
    icon = {"PENDING": "⏳", "PAID": "✅", "REJECTED": "❌"}
    if not rows:
        body = "Belum ada riwayat penarikan."
    else:
        body = "\n\n".join(
            f"{icon.get(r['status'], '•')} <b>WD-{r['id']}</b> • {core.rp(r['amount'])}\n"
            f"{escape(r['provider_name'])} {escape(r['account_number'])} • {escape(r['status'])}\n"
            f"<i>{escape(str(r['created_at']))}</i>"
            for r in rows
        )
    await _edit(query, "📋 <b>RIWAYAT WITHDRAW</b>\n\n" + body, BACK)


async def _admin_action(query, context, action, arg):
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ Kamu bukan admin.", show_alert=True)
        return
    try:
        wd_id = int(arg)
        approve = action == "adm_ok"
        wd = await asyncio.to_thread(db.process_reseller_withdrawal, wd_id, approve, None)
    except ValueError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    label = "✅ SUDAH DITRANSFER" if approve else "❌ DITOLAK (saldo dikembalikan)"
    try:
        await query.edit_message_text(
            (query.message.text_html or "") + f"\n\n<b>{label}</b>", parse_mode="HTML"
        )
    except Exception:
        pass
    notice = (
        f"✅ <b>Withdraw WD-{wd['id']} berhasil</b>\n💰 {core.rp(wd['amount'])} sudah ditransfer ke "
        f"{escape(wd['provider_name'])} {escape(wd['account_number'])}."
        if approve else
        f"❌ <b>Withdraw WD-{wd['id']} ditolak</b>\n💰 {core.rp(wd['amount'])} dikembalikan ke saldo margin Anda. "
        "Hubungi admin untuk info lebih lanjut."
    )
    try:
        await context.bot.send_message(chat_id=wd["owner_telegram_id"], text=notice, parse_mode="HTML")
    except Exception:
        logger.warning("Gagal notifikasi owner WD-%s", wd_id)


# =========================================================
# TEXT (state machine)
# =========================================================

async def handle_text(update, context):
    """Return True bila pesan dipakai oleh alur reseller."""
    state = context.user_data.get("rs_state")
    if not state or CURRENT_RESELLER.get() is not None:
        return False
    msg = update.message
    uid = update.effective_user.id
    text = (msg.text or "").strip()
    kind = state["type"]

    async def fail(error):
        await msg.reply_text(f"❌ {escape(str(error))}", parse_mode="HTML", reply_markup=BACK)

    if kind == "token":
        try:
            await msg.delete()  # jangan biarkan token tersimpan di chat
        except Exception:
            pass
        wait = await msg.chat.send_message("⏳ Memeriksa token...")
        try:
            if state["mode"] == "new":
                bot = await asyncio.to_thread(core.register_bot, uid, text)
            else:
                bot = await asyncio.to_thread(core.change_token, uid, text)
        except ValueError as exc:
            await wait.edit_text(f"❌ {escape(str(exc))}\n\nKirim ulang token yang benar.", parse_mode="HTML", reply_markup=BACK)
            return True
        except Exception:
            logger.exception("Gagal membuat bot reseller")
            await wait.edit_text("❌ Terjadi kesalahan. Coba lagi.", reply_markup=BACK)
            return True
        context.user_data.pop("rs_state", None)
        if MANAGER:
            await MANAGER.restart(bot["id"]) if bot.get("cs_url") and bot.get("enabled") else None
        title = "✅ <b>Bot reseller berhasil dibuat!</b>" if state["mode"] == "new" else "✅ <b>Token berhasil diganti!</b>"
        extra = "" if bot.get("cs_url") else (
            "\n\n⚠️ <b>Satu langkah lagi:</b> atur <b>Contact CS</b> di panel agar bot bisa aktif.\n"
            "Bot akan langsung menyala setelah Contact CS diatur."
        )
        await wait.edit_text(title + extra, parse_mode="HTML")
        await show_menu(msg, uid, edit=False)
        return True

    if kind == "cs":
        try:
            bot = await asyncio.to_thread(core.set_cs, uid, text)
        except ValueError as exc:
            await fail(exc)
            return True
        context.user_data.pop("rs_state", None)
        if MANAGER and bot.get("enabled"):
            await MANAGER.restart(bot["id"])
        await msg.reply_text(
            f"✅ Contact CS diatur ke {escape(bot['cs_url'])}.\n\n"
            + ("<b>Bot Anda sekarang AKTIF</b> dan siap menerima pembeli." if bot.get("enabled") else "Aktifkan bot dari panel untuk mulai menerima pembeli."),
            parse_mode="HTML", disable_web_page_preview=True,
        )
        await show_menu(msg, uid, edit=False)
        return True

    if kind == "margin":
        try:
            bot = await asyncio.to_thread(core.set_margin, uid, text)
        except ValueError as exc:
            await fail(exc)
            return True
        context.user_data.pop("rs_state", None)
        if MANAGER:
            MANAGER.invalidate(bot["id"])
        await msg.reply_text(f"✅ Margin diubah menjadi <b>{core.pct(bot['margin_percent'])}</b>.", parse_mode="HTML")
        await show_menu(msg, uid, edit=False)
        return True

    if kind == "wd_amount":
        bot = await asyncio.to_thread(db.get_reseller_by_owner, uid)
        try:
            amount = int(bot["margin_balance"]) if text.lower() == "semua" else core.parse_amount(text)
            if amount < core.MIN_WITHDRAW:
                raise ValueError(f"Minimum penarikan {core.rp(core.MIN_WITHDRAW)}.")
            if amount > int(bot["margin_balance"]):
                raise ValueError("Nominal melebihi saldo margin.")
        except ValueError as exc:
            await fail(exc)
            return True
        context.user_data["rs_state"] = {"type": "wd_provider", "amount": amount}
        await msg.reply_text(
            "🏦 Kirim <b>nama bank / e-wallet</b> tujuan.\nContoh: <code>BCA</code>, <code>BRI</code>, <code>DANA</code>, <code>OVO</code>",
            parse_mode="HTML", reply_markup=BACK,
        )
        return True

    if kind == "wd_provider":
        if not (2 <= len(text) <= 30):
            await fail("Nama bank/e-wallet tidak valid.")
            return True
        context.user_data["rs_state"] = {**state, "type": "wd_number", "provider": text}
        await msg.reply_text("🔢 Kirim <b>nomor rekening / e-wallet</b> (angka saja).", parse_mode="HTML", reply_markup=BACK)
        return True

    if kind == "wd_number":
        try:
            core.validate_account(state["provider"], text, "xx")
        except ValueError as exc:
            await fail(exc)
            return True
        context.user_data["rs_state"] = {**state, "type": "wd_name", "number": text}
        await msg.reply_text("📛 Kirim <b>nama pemilik</b> rekening / e-wallet.", parse_mode="HTML", reply_markup=BACK)
        return True

    if kind == "wd_name":
        try:
            provider, number, name = core.validate_account(state["provider"], state["number"], text)
        except ValueError as exc:
            await fail(exc)
            return True
        context.user_data.pop("rs_state", None)
        context.user_data["rs_wd_draft"] = {"amount": state["amount"], "provider": provider, "number": number, "name": name}
        await msg.reply_text(
            "📤 <b>KONFIRMASI WITHDRAW</b>\n\n"
            f"💰 Nominal: <b>{core.rp(state['amount'])}</b>\n"
            f"🏦 {escape(provider)} • <code>{escape(number)}</code>\n"
            f"📛 a.n. {escape(name)}\n\nPastikan data sudah benar.",
            parse_mode="HTML",
            reply_markup=_kb([[("✅ Kirim Permintaan", "rs:wd_confirm"), ("❌ Batal", "rs:menu")]]),
        )
        return True

    return False


# =========================================================
# PENGELOLA BOT RESELLER
# =========================================================

class ResellerManager:
    """Menjalankan banyak bot reseller (polling) dalam proses & event loop yang sama."""

    CACHE_TTL = 5.0

    def __init__(self, register_handlers):
        self._register = register_handlers
        self.apps = {}     # bot_id -> Application
        self.tokens = {}   # bot_id -> token yang sedang jalan
        self._cache = {}   # bot_id -> (waktu, row)
        self._lock = asyncio.Lock()

    # ---- cache baris reseller untuk gate per-update ----
    def invalidate(self, bot_id):
        self._cache.pop(bot_id, None)

    async def get_row(self, bot_id):
        hit = self._cache.get(bot_id)
        if hit and time.monotonic() - hit[0] < self.CACHE_TTL:
            return hit[1]
        row = await asyncio.to_thread(db.get_reseller_by_id, bot_id)
        self._cache[bot_id] = (time.monotonic(), row)
        return row

    def is_running(self, bot_id):
        return bot_id in self.apps

    def bot_for(self, bot_id):
        app = self.apps.get(bot_id)
        return app.bot if app else None

    # ---- siklus hidup ----
    async def _gate(self, update, context):
        bot_id = context.application.bot_data["reseller_id"]
        row = await self.get_row(bot_id)
        if not row or row.get("admin_blocked") or not row["enabled"] or not row.get("cs_url"):
            if update.callback_query:
                try:
                    await update.callback_query.answer("Bot sedang tidak aktif.", show_alert=True)
                except Exception:
                    pass
            elif update.effective_message:
                try:
                    await update.effective_message.reply_text("🚧 Bot ini sedang tidak aktif. Silakan coba lagi nanti.")
                except Exception:
                    pass
            raise ApplicationHandlerStop
        query = update.callback_query
        if query and (query.data == "reseller" or (query.data or "").startswith("rs:")):
            await query.answer("Menu ini hanya tersedia di bot utama.", show_alert=True)
            raise ApplicationHandlerStop
        CURRENT_RESELLER.set(row)
        user = update.effective_user
        if user:
            await asyncio.to_thread(db.register_reseller_buyer, bot_id, user.id)

    async def start_bot(self, row):
        async with self._lock:
            await self._stop_locked(row["id"])
            app = Application.builder().token(row["bot_token"]).build()
            app.bot_data["reseller_id"] = row["id"]
            app.add_handler(TypeHandler(Update, self._gate), group=-1)
            self._register(app)
            try:
                await app.initialize()
                await app.bot.delete_webhook()
                await app.start()
                await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
                try:
                    await app.bot.set_my_commands(self._commands())
                except Exception:
                    pass
            except Exception:
                logger.exception("Gagal menjalankan bot reseller id=%s", row["id"])
                try:
                    await app.shutdown()
                except Exception:
                    pass
                return False
            self.apps[row["id"]] = app
            self.tokens[row["id"]] = row["bot_token"]
            logger.info("Bot reseller berjalan: @%s", row.get("bot_username"))
            return True

    @staticmethod
    def _commands():
        from telegram import BotCommand
        return [
            BotCommand("start", "Mulai Bot"),
            BotCommand("layanan1", "Layanan Server 1"),
            BotCommand("layanan2", "Layanan Server 2"),
            BotCommand("layanan3", "Layanan Server 3"),
            BotCommand("deposit", "Deposit saldo"),
        ]

    async def _stop_locked(self, bot_id):
        app = self.apps.pop(bot_id, None)
        self.tokens.pop(bot_id, None)
        self.invalidate(bot_id)
        if not app:
            return
        for step in (app.updater.stop, app.stop, app.shutdown):
            try:
                await step()
            except Exception:
                logger.debug("stop step gagal", exc_info=True)

    async def stop_bot(self, bot_id):
        async with self._lock:
            await self._stop_locked(bot_id)

    async def restart(self, bot_id):
        row = await asyncio.to_thread(db.get_reseller_by_id, bot_id)
        if not row or row.get("admin_blocked") or not row["enabled"] or not row.get("cs_url"):
            await self.stop_bot(bot_id)
            return False
        return await self.start_bot(row)

    async def sync_loop(self, interval=20):
        """Samakan bot yang berjalan dengan database (juga perubahan dari azhura_web)."""
        while True:
            try:
                rows = {r["id"]: r for r in await asyncio.to_thread(db.list_runnable_resellers)}
                for bot_id in list(self.apps):
                    if bot_id not in rows or self.tokens.get(bot_id) != rows[bot_id]["bot_token"]:
                        await self.stop_bot(bot_id)
                for bot_id, row in rows.items():
                    if bot_id not in self.apps:
                        await self.start_bot(row)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("sync reseller gagal")
            await asyncio.sleep(interval)


def init_manager(register_handlers):
    global MANAGER
    MANAGER = ResellerManager(register_handlers)
    return MANAGER
