"""Menu SMM PANEL untuk bot Telegram (callback `smm_*`) + panel admin `admin_smm`.

Alur (mengikuti alur resmi SIMURU): platform -> jenis -> daftar layanan -> detail harga ->
input link/target -> input jumlah -> konfirmasi -> order -> cek status.
Seluruh logika bisnis ada di smm.py (dipakai juga oleh web).
"""
import os
import uuid
import asyncio
import logging
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import smm
from database import get_balance, is_maintenance_enabled
from reseller_ctx import current_reseller

logger = logging.getLogger("smm_bot")

PAGE = 8
HIST_PAGE = 5
SYNC_INTERVAL = 120

STATUS_LABEL = {
    "CREATING": "⏳ Membuat", "PENDING": "🕐 Menunggu", "PROCESSING": "🔄 Diproses",
    "COMPLETED": "✅ Selesai", "PARTIAL": "🟠 Parsial", "CANCELED": "❌ Dibatalkan",
    "REFUNDED": "↩️ Refund", "FAILED": "❌ Gagal", "REVIEW": "🛠 Dicek admin",
}


def _admin_id():
    try:
        return int(os.getenv("ADMIN_ID", "0"))
    except ValueError:
        return 0


def _kb(rows):
    return InlineKeyboardMarkup(rows)


def _btn(text, data):
    return InlineKeyboardButton(text, callback_data=data)


def _n(v):
    try:
        return f"{int(v):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "-"


async def _show(query, text, markup):
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup,
                                      disable_web_page_preview=True)
    except Exception as exc:  # pesan sama / tidak bisa diedit -> kirim baru
        if "not modified" in str(exc).lower():
            return
        try:
            await query.message.reply_text(text, parse_mode="HTML", reply_markup=markup,
                                           disable_web_page_preview=True)
        except Exception:
            logger.exception("smm: gagal menampilkan pesan")


def _service_detail_text(s):
    pub = smm.public_service(s)
    lines = [
        "🚀 <b>DETAIL LAYANAN SMM</b>\n",
        f"📌 <b>{escape(pub['title'])}</b>",
        f"🆔 ID layanan: <code>{pub['id']}</code>",
        f"📱 Platform: {escape(str(pub['platform'] or '-'))} • {escape(str(pub['kind'] or '-'))}",
        f"💰 Harga: <b>{smm.rupiah(pub['price'])}</b> / {_n(pub['price_per'])}",
        f"📊 Min: <b>{_n(pub['min'])}</b> • Maks: <b>{_n(pub['max'])}</b>",
        f"♻️ Refill: {'Ya' if pub['refill'] else 'Tidak'}",
    ]
    if pub.get("start_minutes") is not None:
        lines.append(f"⏱ Mulai: ± {escape(str(pub['start_minutes']))} menit")
    if pub.get("speed_per_day") is not None:
        lines.append(f"⚡ Kecepatan: ± {escape(str(pub['speed_per_day']))} / hari")
    if pub.get("target_type"):
        lines.append(f"🎯 Target: {escape(str(pub['target_type']))}")
    if pub.get("note"):
        lines.append(f"\n📝 {escape(str(pub['note'])[:400])}")
    return "\n".join(lines)


# =========================================================
# CALLBACK ROUTER
# =========================================================

async def handle_callback(query, context):
    data = query.data
    user_id = query.from_user.id

    if current_reseller() is not None:
        await query.answer("Menu SMM hanya tersedia di bot utama.", show_alert=True)
        return
    if not smm.is_configured():
        await _show(query, "🚀 <b>SMM PANEL</b>\n\nLayanan SMM belum diaktifkan. Hubungi admin.",
                    _kb([[_btn("⬅️ Menu Utama", "user_home")]]))
        return

    try:
        if data == "smm_home":
            await _platforms(query, context)
        elif data.startswith("smm_p:"):
            await _kinds(query, context, int(data.split(":")[1]))
        elif data.startswith("smm_k:"):
            await _list_services(query, context, int(data.split(":")[1]), 0)
        elif data.startswith("smm_l:"):
            await _list_services(query, context, None, int(data.split(":")[1]))
        elif data.startswith("smm_s:"):
            await _service_detail(query, context, data.split(":")[1])
        elif data.startswith("smm_o:"):
            await _start_order(query, context, data.split(":")[1])
        elif data.startswith("smm_c:"):
            await _confirm(query, context, user_id, data.split(":")[1])
        elif data == "smm_cancel":
            context.user_data.pop("smm_state", None)
            await _platforms(query, context)
        elif data.startswith("smm_h:"):
            await _history(query, user_id, int(data.split(":")[1]))
        elif data.startswith("smm_st:"):
            await _status(query, user_id, data.split(":", 1)[1])
    except smm.SmmError as exc:
        await _show(query, f"❌ {escape(str(exc))}", _kb([[_btn("⬅️ SMM PANEL", "smm_home")]]))
    except Exception:
        logger.exception("smm callback error data=%s", data)
        await _show(query, "❌ Terjadi kesalahan. Silakan coba lagi.", _kb([[_btn("⬅️ SMM PANEL", "smm_home")]]))


# =========================================================
# BROWSE
# =========================================================

async def _platforms(query, context):
    plats = await asyncio.to_thread(smm.get_platforms)
    context.user_data["smm_plats"] = [p["platform"] for p in plats]
    rows, row = [], []
    for i, p in enumerate(plats):
        row.append(_btn(f"{p['platform']} ({p.get('total', '?')})", f"smm_p:{i}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([_btn("📋 Riwayat SMM", "smm_h:0")])
    rows.append([_btn("⬅️ Menu Utama", "user_home")])
    bal = await asyncio.to_thread(get_balance, query.from_user.id)
    await _show(query,
                "🚀 <b>SMM PANEL</b>\n\nFollowers, Likes, Views & lainnya untuk sosial media kamu.\n"
                f"💳 Saldo: <b>{smm.rupiah(bal)}</b>\n\nPilih platform:", _kb(rows))


async def _kinds(query, context, idx):
    plats = context.user_data.get("smm_plats") or []
    if idx >= len(plats):
        await _platforms(query, context); return
    platform = plats[idx]
    kinds = await asyncio.to_thread(smm.get_kinds, platform)
    context.user_data["smm_ctx"] = {"platform": platform, "kinds": kinds}
    rows, row = [], []
    for j, k in enumerate(kinds):
        row.append(_btn(str(k)[:28], f"smm_k:{j}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([_btn("⬅️ Platform", "smm_home")])
    await _show(query, f"🚀 <b>{escape(platform)}</b>\n\nPilih jenis layanan:", _kb(rows))


async def _list_services(query, context, kind_idx, page):
    ctx = context.user_data.get("smm_ctx") or {}
    if kind_idx is not None:
        kinds = ctx.get("kinds") or []
        if kind_idx >= len(kinds):
            await _platforms(query, context); return
        ctx["kind"] = kinds[kind_idx]
        svcs = await asyncio.to_thread(smm.get_services, ctx["platform"], ctx["kind"], None, 200)
        svcs = sorted(svcs, key=lambda s: (s.get("price") or 0))
        ctx["svcs"] = svcs
        context.user_data["smm_ctx"] = ctx
    svcs = ctx.get("svcs")
    if svcs is None:
        await _platforms(query, context); return
    total_pages = max(1, (len(svcs) + PAGE - 1) // PAGE)
    page = max(0, min(page, total_pages - 1))
    rows = []
    for s in svcs[page * PAGE:(page + 1) * PAGE]:
        price = smm.sell_price_per_1000(s.get("price") or 0)
        rows.append([_btn(f"{smm.service_title(s)[:34]} • {smm.rupiah(price)}/1K", f"smm_s:{s['id']}")])
    nav = []
    if page > 0:
        nav.append(_btn("◀️", f"smm_l:{page - 1}"))
    nav.append(_btn(f"{page + 1}/{total_pages}", "smm_home"))
    if page < total_pages - 1:
        nav.append(_btn("▶️", f"smm_l:{page + 1}"))
    rows.append(nav)
    rows.append([_btn("⬅️ Jenis", f"smm_p:{(context.user_data.get('smm_plats') or []).index(ctx['platform'])}")
                 if ctx.get("platform") in (context.user_data.get("smm_plats") or []) else _btn("⬅️ Platform", "smm_home")])
    head = f"🚀 <b>{escape(str(ctx.get('platform')))} • {escape(str(ctx.get('kind')))}</b>\n"
    if not svcs:
        await _show(query, head + "\nBelum ada layanan untuk kategori ini.", _kb(rows[-1:]))
        return
    await _show(query, head + f"\n{len(svcs)} layanan • harga per 1.000 (urut termurah).\nPilih layanan:", _kb(rows))


def _lookup(context, sid):
    for s in (context.user_data.get("smm_ctx") or {}).get("svcs") or []:
        if str(s.get("id")) == str(sid):
            return s
    return None


async def _service_detail(query, context, sid):
    s = _lookup(context, sid)
    if not s:
        raise smm.SmmError("Daftar layanan kedaluwarsa. Buka ulang menu SMM PANEL.")
    await _show(query, _service_detail_text(s),
                _kb([[_btn("🛒 Order Sekarang", f"smm_o:{sid}")], [_btn("⬅️ Daftar Layanan", "smm_l:0")]]))


# =========================================================
# ORDER (input teks via text_handler)
# =========================================================

async def _start_order(query, context, sid):
    s = _lookup(context, sid)
    if not s:
        raise smm.SmmError("Daftar layanan kedaluwarsa. Buka ulang menu SMM PANEL.")
    context.user_data["smm_state"] = {"step": "target", "sid": str(sid)}
    hint = {"profile": "username/link profil", "post": "link postingan", "comment": "link komentar",
            "story": "link story", "live": "link live", "track": "link lagu", "app": "link aplikasi",
            "keyword": "kata kunci", "website": "link website", "tg_channel": "link channel Telegram",
            "tg_post": "link postingan Telegram"}.get(str(s.get("target_type") or ""), "link / username target")
    await _show(query,
                f"🛒 <b>{escape(smm.service_title(s))}</b>\n\n🔗 Kirim <b>{hint}</b> sebagai pesan teks.\n"
                "Pastikan akun/postingan tidak private.",
                _kb([[_btn("❌ Batal", "smm_cancel")]]))


async def handle_text(update, context):
    """Return True bila pesan dipakai oleh alur SMM."""
    st = context.user_data.get("smm_state")
    if not st or not update.message or not update.message.text:
        return False
    text = update.message.text.strip()
    cancel = _kb([[_btn("❌ Batal", "smm_cancel")]])
    s = _lookup(context, st["sid"])
    if not s:
        context.user_data.pop("smm_state", None)
        await update.message.reply_text("Sesi pesanan kedaluwarsa. Buka ulang menu SMM PANEL.")
        return True

    if st["step"] == "target":
        if not text or len(text) > 1024:
            await update.message.reply_text("❌ Target tidak valid (maks. 1024 karakter). Kirim ulang.", reply_markup=cancel)
            return True
        st["target"] = text
        if smm.needs_custom_comments(s):
            st["step"] = "comments"
            await update.message.reply_text("💬 Kirim komentar, <b>satu komentar per baris</b>. Jumlah = jumlah baris.",
                                            parse_mode="HTML", reply_markup=cancel)
        else:
            st["step"] = "qty"
            await update.message.reply_text(
                f"🔢 Kirim jumlah (min {_n(s.get('min'))}, maks {_n(s.get('max'))}).",
                reply_markup=cancel)
        return True

    if st["step"] in ("comments", "qty"):
        try:
            if st["step"] == "comments":
                st["comments"] = text
                target, qty, comments = smm.validate_input(s, st["target"], 0, text)
            else:
                digits = text.replace(".", "").replace(",", "").replace(" ", "")
                target, qty, comments = smm.validate_input(s, st["target"], digits, None)
        except smm.SmmError as exc:
            await update.message.reply_text(f"❌ {escape(str(exc))}\nKirim ulang.", parse_mode="HTML", reply_markup=cancel)
            return True
        cost, sell = smm.calc_prices(s.get("price") or 0, qty)
        bal = await asyncio.to_thread(get_balance, update.effective_user.id)
        token = uuid.uuid4().hex[:12]
        st.update({"step": "confirm", "qty": qty, "price": sell, "token": token})
        rows = []
        if bal >= sell:
            rows.append([_btn("✅ Konfirmasi & Order", f"smm_c:{token}")])
        else:
            rows.append([_btn("💳 Deposit", "user_deposit")])
        rows.append([_btn("❌ Batal", "smm_cancel")])
        await update.message.reply_text(
            "🧾 <b>KONFIRMASI PESANAN</b>\n\n"
            f"📌 {escape(smm.service_title(s))}\n"
            f"🔗 Target: <code>{escape(target[:200])}</code>\n"
            f"🔢 Jumlah: <b>{_n(qty)}</b>\n"
            f"💰 Total: <b>{smm.rupiah(sell)}</b>\n"
            f"💳 Saldo: <b>{smm.rupiah(bal)}</b>\n\n"
            + ("Tekan konfirmasi untuk memesan." if bal >= sell else "⚠️ Saldo tidak cukup. Silakan deposit dulu."),
            parse_mode="HTML", reply_markup=_kb(rows))
        return True

    return False


async def _confirm(query, context, user_id, token):
    st = context.user_data.get("smm_state")
    if not st or st.get("step") != "confirm" or st.get("token") != token:
        raise smm.SmmError("Konfirmasi sudah dipakai atau kedaluwarsa.")
    if is_maintenance_enabled() and user_id != _admin_id():
        await query.answer("🛠 Maintenance aktif. Pemesanan sementara ditutup.", show_alert=True)
        return
    context.user_data.pop("smm_state", None)  # sekali pakai -> cegah order ganda
    s = _lookup(context, st["sid"])
    if not s:
        raise smm.SmmError("Daftar layanan kedaluwarsa. Buka ulang menu SMM PANEL.")
    # validasi harga terbaru langsung dari SIMURU
    fresh = await asyncio.to_thread(smm.find_fresh_service, s["id"], s.get("platform"), s.get("kind"),
                                    smm.service_title(s))
    if fresh:
        s = fresh
    await _show(query, "⏳ Memproses pesanan...", None)
    comments = st.get("comments")
    order = await asyncio.to_thread(
        smm.create_order, user_id, s, st["target"], st["qty"], comments, "BOT",
        f"bot-{user_id}-{token}", st["price"])
    await _show(query, _order_text(order, header="✅ <b>PESANAN DIBUAT</b>"),
                _kb([[_btn("🔄 Cek Status", f"smm_st:{order['local_id']}")],
                     [_btn("🚀 SMM PANEL", "smm_home"), _btn("⬅️ Menu Utama", "user_home")]]))


def _order_text(o, header="🧾 <b>DETAIL PESANAN SMM</b>"):
    lines = [
        header + "\n",
        f"🆔 ID: <code>{escape(str(o['local_id']))}</code>",
        f"🔖 ID Provider: <code>{escape(str(o.get('provider_order_id') or '-'))}</code>",
        f"📌 {escape(str(o.get('service_name') or '-'))}",
        f"🔗 <code>{escape(str(o.get('target') or '-')[:200])}</code>",
        f"🔢 Jumlah: <b>{_n(o.get('quantity'))}</b>",
        f"💰 Harga: <b>{smm.rupiah(o.get('sell_price'))}</b>",
        f"📊 Status: <b>{STATUS_LABEL.get(o.get('status'), o.get('status'))}</b>",
    ]
    if o.get("start_count") is not None:
        lines.append(f"▶️ Start count: {_n(o['start_count'])}")
    if o.get("remains") is not None:
        lines.append(f"⏳ Sisa: {_n(o['remains'])}")
    if int(o.get("refunded_amount") or 0) > 0:
        lines.append(f"↩️ Dikembalikan ke saldo: <b>{smm.rupiah(o['refunded_amount'])}</b>")
    return "\n".join(lines)


# =========================================================
# RIWAYAT & STATUS USER
# =========================================================

async def _history(query, user_id, page):
    total = await asyncio.to_thread(smm.count_user_orders, user_id)
    pages = max(1, (total + HIST_PAGE - 1) // HIST_PAGE)
    page = max(0, min(page, pages - 1))
    rows = await asyncio.to_thread(smm.list_user_orders, user_id, HIST_PAGE, page * HIST_PAGE)
    kb = []
    lines = ["📋 <b>RIWAYAT SMM</b>\n"]
    if not rows:
        lines.append("Belum ada pesanan SMM.")
    for o in rows:
        lines.append(f"• <code>{escape(o['local_id'])}</code> — {escape(str(o['service_name'])[:40])}\n"
                     f"  {_n(o['quantity'])}x • {smm.rupiah(o['sell_price'])} • "
                     f"{STATUS_LABEL.get(o['status'], o['status'])}")
        kb.append([_btn(f"🔎 {o['local_id']}", f"smm_st:{o['local_id']}")])
    nav = []
    if page > 0:
        nav.append(_btn("◀️", f"smm_h:{page - 1}"))
    if page < pages - 1:
        nav.append(_btn("▶️", f"smm_h:{page + 1}"))
    if nav:
        kb.append(nav)
    kb.append([_btn("⬅️ SMM PANEL", "smm_home")])
    await _show(query, "\n".join(lines), _kb(kb))


async def _status(query, user_id, local_id):
    o = await asyncio.to_thread(smm.get_smm_order, local_id, user_id)
    if not o:
        raise smm.SmmError("Pesanan tidak ditemukan.")
    o = await asyncio.to_thread(smm.sync_order, local_id)
    await _show(query, _order_text(o),
                _kb([[_btn("🔄 Cek Status", f"smm_st:{local_id}")], [_btn("📋 Riwayat", "smm_h:0")]]))


# =========================================================
# ADMIN
# =========================================================

async def admin_view(query, page=0):
    per = 8
    summ = await asyncio.to_thread(smm.admin_summary)
    rows = await asyncio.to_thread(smm.list_all_orders, per, page * per)
    pages = max(1, (int(summ["total"]) + per - 1) // per)
    lines = ["🚀 <b>SMM ORDERS (ADMIN)</b>\n",
             f"Total {summ['total']} • Aktif {summ['active']} • Selesai {summ['completed']} • "
             f"Gagal/Batal {summ['failed']} • Review {summ['review']}",
             f"💰 Omzet: <b>{smm.rupiah(summ['gross'])}</b> • Profit ± <b>{smm.rupiah(summ['profit'])}</b>\n"]
    for o in rows:
        lines.append(f"• <code>{escape(o['local_id'])}</code> | {escape(str(o.get('username') or o['telegram_id']))} "
                     f"[{escape(str(o['source']))}]\n  {escape(str(o['service_name'])[:40])} • {_n(o['quantity'])}x • "
                     f"{smm.rupiah(o['sell_price'])} • <b>{STATUS_LABEL.get(o['status'], o['status'])}</b>")
    if not rows:
        lines.append("Belum ada pesanan SMM.")
    nav = []
    if page > 0:
        nav.append(_btn("◀️", f"admin_smm_page:{page - 1}"))
    if page < pages - 1:
        nav.append(_btn("▶️", f"admin_smm_page:{page + 1}"))
    kb = ([nav] if nav else []) + [[_btn("⬅️ Admin Panel", "admin_home")]]
    await _show(query, "\n".join(lines), _kb(kb))


# =========================================================
# BACKGROUND SYNC (status + notifikasi completed)
# =========================================================

async def sync_loop():
    await asyncio.sleep(30)
    while True:
        try:
            if smm.is_configured():
                await asyncio.to_thread(smm.sync_active_orders)
        except Exception:
            logger.exception("smm sync_loop error")
        await asyncio.sleep(SYNC_INTERVAL)
