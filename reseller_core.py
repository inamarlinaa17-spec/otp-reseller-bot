"""Logika bisnis fitur RESELLER (dipakai bersama oleh bot Telegram dan azhura_web)."""
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import database as db

MIN_MARGIN = 5.0
MAX_MARGIN = 100.0
MIN_WITHDRAW = int(os.getenv("RESELLER_MIN_WD", "5000"))

TOKEN_RE = re.compile(r"^\d{6,15}:[A-Za-z0-9_-]{30,60}$")
USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")
EWALLETS = {"dana", "ovo", "gopay", "go-pay", "shopeepay", "shopee pay", "linkaja", "link aja", "isaku", "sakuku"}


def rp(amount):
    return "Rp " + f"{int(amount or 0):,}".replace(",", ".")


def pct(value):
    return f"{float(value):g}%"


# ---------------- validasi input ----------------

def normalize_cs(raw):
    """@user / user / t.me/user / https://t.me/user -> https://t.me/user"""
    text = str(raw or "").strip()
    text = re.sub(r"^(https?://)?(www\.)?(t\.me|telegram\.me)/", "", text, flags=re.I)
    text = text.lstrip("@").split("?")[0].strip("/")
    if not USERNAME_RE.match(text):
        raise ValueError("Username Telegram tidak valid. Contoh: @username (5-32 karakter, huruf/angka/_).")
    return f"https://t.me/{text}"


def parse_margin(raw):
    text = str(raw or "").strip().replace("%", "").replace(",", ".")
    try:
        value = round(float(text), 2)
    except ValueError:
        raise ValueError("Margin harus berupa angka, contoh: 5 atau 7.5")
    if value < MIN_MARGIN:
        raise ValueError(f"Margin minimum {MIN_MARGIN:g}%.")
    if value > MAX_MARGIN:
        raise ValueError(f"Margin maksimum {MAX_MARGIN:g}%.")
    return value


def parse_amount(raw):
    digits = re.sub(r"[^\d]", "", str(raw or ""))
    if not digits:
        raise ValueError("Nominal tidak valid. Kirim angka saja, contoh: 50000")
    return int(digits)


def classify_method(provider_name):
    return "EWALLET" if str(provider_name or "").strip().lower() in EWALLETS else "BANK"


def validate_account(provider_name, account_number, account_name):
    provider_name = re.sub(r"\s+", " ", str(provider_name or "")).strip()
    account_number = re.sub(r"[\s-]", "", str(account_number or ""))
    account_name = re.sub(r"\s+", " ", str(account_name or "")).strip()
    if not (2 <= len(provider_name) <= 30):
        raise ValueError("Nama bank/e-wallet tidak valid. Contoh: BCA, BRI, DANA, OVO.")
    if not re.fullmatch(r"\d{5,24}", account_number):
        raise ValueError("Nomor rekening/e-wallet harus berupa angka (5-24 digit).")
    if not (2 <= len(account_name) <= 60):
        raise ValueError("Nama pemilik rekening tidak valid.")
    return provider_name, account_number, account_name


# ---------------- token bot ----------------

def fetch_bot_info(token):
    """Validasi token lewat Telegram getMe."""
    token = str(token or "").strip()
    if not TOKEN_RE.match(token):
        raise ValueError("Format token tidak valid. Salin ulang token dari @BotFather.")
    try:
        with urlopen(Request(f"https://api.telegram.org/bot{token}/getMe"), timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except HTTPError:
        raise ValueError("Token ditolak Telegram. Pastikan token benar dan belum di-revoke.")
    except (URLError, TimeoutError, OSError):
        raise ValueError("Gagal menghubungi Telegram. Coba lagi sebentar lagi.")
    result = data.get("result") or {}
    if not data.get("ok") or not result.get("is_bot"):
        raise ValueError("Token tidak valid.")
    main_token = os.getenv("BOT_TOKEN", "")
    if token == main_token or str(result.get("id")) == main_token.split(":", 1)[0]:
        raise ValueError("Token ini adalah token bot utama dan tidak boleh dipakai.")
    return {"id": int(result["id"]), "username": result.get("username") or "", "name": result.get("first_name") or ""}


# ---------------- operasi ----------------

def register_bot(owner_id, token):
    info = fetch_bot_info(token)
    return db.create_reseller(owner_id, token.strip(), info["id"], info["username"], info["name"], MIN_MARGIN)


def change_token(owner_id, token):
    if not db.get_reseller_by_owner(owner_id):
        raise ValueError("Anda belum punya bot reseller.")
    info = fetch_bot_info(token)
    return db.replace_reseller_token(owner_id, token.strip(), info["id"], info["username"], info["name"])


def set_cs(owner_id, raw):
    url = normalize_cs(raw)
    if not db.get_reseller_by_owner(owner_id):
        raise ValueError("Anda belum punya bot reseller.")
    return db.update_reseller(owner_id, cs_url=url)


def set_margin(owner_id, raw):
    value = parse_margin(raw)
    if not db.get_reseller_by_owner(owner_id):
        raise ValueError("Anda belum punya bot reseller.")
    return db.update_reseller(owner_id, margin_percent=value)


def set_enabled(owner_id, flag):
    row = db.get_reseller_by_owner(owner_id)
    if not row:
        raise ValueError("Anda belum punya bot reseller.")
    if flag and row.get("admin_blocked"):
        raise ValueError("Bot reseller sedang diblokir admin.")
    if flag and not row.get("cs_url"):
        raise ValueError("Atur Contact CS dahulu sebelum mengaktifkan bot.")
    return db.update_reseller(owner_id, enabled=bool(flag))


def request_withdrawal(owner_id, amount, provider_name, account_number, account_name):
    provider_name, account_number, account_name = validate_account(provider_name, account_number, account_name)
    amount = int(amount)
    if amount < MIN_WITHDRAW:
        raise ValueError(f"Minimum penarikan {rp(MIN_WITHDRAW)}.")
    return db.create_reseller_withdrawal(
        owner_id, amount, classify_method(provider_name), provider_name, account_number, account_name
    )


def status_label(row, running=True):
    """(teks status, aktif?)"""
    if row.get("admin_blocked"):
        return "⛔ Diblokir Admin", False
    if not row.get("cs_url"):
        return "⚠️ Belum Aktif (Contact CS belum diatur)", False
    if not row.get("enabled"):
        return "⛔ Dihentikan", False
    if not running:
        return "⏳ Sedang dimulai...", True
    return "Online", True


def panel_data(owner_id):
    row = db.get_reseller_by_owner(owner_id)
    if not row:
        return None
    stats = db.get_reseller_stats(row["id"])
    return {"bot": row, "stats": stats}
