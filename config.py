import os
from dotenv import load_dotenv


# =========================================================
# LOAD ENVIRONMENT VARIABLES
# =========================================================

load_dotenv()


# =========================================================
# TELEGRAM BOT
# =========================================================

BOT_TOKEN = os.getenv(
    "BOT_TOKEN",
    ""
).strip()


ADMIN_ID = os.getenv(
    "ADMIN_ID",
    ""
).strip()

ADMIN_USERNAME = os.getenv(
    "ADMIN_USERNAME",
    ""
).strip().lstrip("@")


# =========================================================
# DATABASE
# =========================================================

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    ""
).strip()


# =========================================================
# MIDTRANS CONFIGURATION
# PRODUCTION / LIVE
# =========================================================

MIDTRANS_SERVER_KEY = os.getenv(
    "MIDTRANS_SERVER_KEY",
    ""
).strip()


MIDTRANS_CLIENT_KEY = os.getenv(
    "MIDTRANS_CLIENT_KEY",
    ""
).strip()


MIDTRANS_MERCHANT_ID = os.getenv(
    "MIDTRANS_MERCHANT_ID",
    ""
).strip()


# =========================================================
# FORCE PRODUCTION MODE
# =========================================================

MIDTRANS_IS_PRODUCTION = True


# =========================================================
# MIDTRANS PRODUCTION URL
# =========================================================

MIDTRANS_API_URL = (
    "https://api.midtrans.com"
)


MIDTRANS_SNAP_URL = (
    "https://app.midtrans.com/snap"
)


# =========================================================
# 5SIM CONFIGURATION
# =========================================================

FIVESIM_API_KEY = os.getenv(
    "FIVESIM_API_KEY",
    ""
).strip()


# =========================================================
# KURS USD -> IDR OTOMATIS
# =========================================================
# Tidak membutuhkan KURS_DOLAR di Railway Variables.
# Bot mengambil kurs referensi USD/IDR terbaru saat startup.
# Jika API kurs sedang gagal, digunakan emergency fallback yang sengaja
# dibuat sedikit lebih tinggi agar perhitungan harga tidak terlalu rendah.
KURS_DOLAR_EMERGENCY = 18000.0


def _get_live_usd_idr():
    try:
        import requests

        response = requests.get(
            "https://api.frankfurter.dev/v2/rate/USD/IDR",
            timeout=8
        )
        response.raise_for_status()
        data = response.json()
        rate = float(data.get("rate") or 0)

        if rate > 0:
            print(f"[KURS] USD/IDR otomatis: Rp{rate:,.2f}")
            return rate
    except Exception as exc:
        print(f"[KURS] gagal mengambil kurs USD/IDR terbaru: {exc}")

    print(f"[KURS] memakai emergency fallback: Rp{KURS_DOLAR_EMERGENCY:,.2f}")
    return KURS_DOLAR_EMERGENCY


# Nilai ini adalah kurs dasar USD -> IDR.
# Margin reseller tetap dihitung terpisah oleh hitung_harga_jual().
KURS_DOLAR = _get_live_usd_idr()


# Margin reseller
PROFIT_PERCENT = float(
    os.getenv(
        "PROFIT_PERCENT",
        "7"
    )
)


# =========================================================
# RUMAHOTP CONFIGURATION
# =========================================================

RUMAHOTP_API_KEY = os.getenv(
    "RUMAHOTP_API_KEY",
    ""
).strip()


# =========================================================
# NUSAOTP CONFIGURATION (SERVER 3)
# =========================================================

NUSAOTP_API_KEY = os.getenv(
    "NUSAOTP_API_KEY",
    ""
).strip()

# Public Railway URL for the NusaOTP realtime OTP webhook.
# Prefer an explicit NUSAOTP_WEBHOOK_URL; otherwise derive it from
# Railway's public domain when available.
NUSAOTP_WEBHOOK_URL = os.getenv(
    "NUSAOTP_WEBHOOK_URL",
    ""
).strip()
if not NUSAOTP_WEBHOOK_URL:
    _railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
    if _railway_domain:
        if not _railway_domain.startswith("http://") and not _railway_domain.startswith("https://"):
            _railway_domain = "https://" + _railway_domain
        NUSAOTP_WEBHOOK_URL = _railway_domain.rstrip("/") + "/nusaotp/webhook"




# =========================================================
# TELEGRAM PROMO CHANNEL
# =========================================================
# Configure this in Railway Variables:
# PROMO_CHANNEL=@YourChannel
# or PROMO_CHANNEL=YourChannel
# or PROMO_CHANNEL=https://t.me/YourChannel
PROMO_CHANNEL = os.getenv(
    "PROMO_CHANNEL",
    ""
).strip()


# =========================================================
# TRAFFIC OTP NOTIFICATION
# =========================================================
# Optional Telegram channel/chat for automatic OTP traffic notifications.
# Example: @TRAFIC_OTP_AZHURA_ID or -1001234567890
try:
    from traffic_config import TRAFFIC_CHANNEL as TRAFFIC_CHANNEL_DEFAULT
    from traffic_config import TRAFFIC_BOT_TOKEN as TRAFFIC_BOT_TOKEN_DEFAULT
except ImportError:
    TRAFFIC_CHANNEL_DEFAULT = "@Tracif_NokosAzhura"
    TRAFFIC_BOT_TOKEN_DEFAULT = ""

TRAFFIC_CHANNEL = os.getenv(
    "TRAFFIC_CHANNEL",
    TRAFFIC_CHANNEL_DEFAULT
).strip()

# Optional separate bot token for the traffic notifier.
# If empty, the main AZHURA bot token is used.
TRAFFIC_BOT_TOKEN = os.getenv(
    "TRAFFIC_BOT_TOKEN",
    TRAFFIC_BOT_TOKEN_DEFAULT
).strip()



# =========================================================
# VALIDATION
# =========================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN belum diatur di environment."
    )


if not ADMIN_ID:
    raise RuntimeError(
        "ADMIN_ID belum diatur di environment."
    )


if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL belum diatur di environment."
    )


if not MIDTRANS_SERVER_KEY:
    raise RuntimeError(
        "MIDTRANS_SERVER_KEY belum diatur di environment."
    )


if not MIDTRANS_CLIENT_KEY:
    raise RuntimeError(
        "MIDTRANS_CLIENT_KEY belum diatur di environment."
    )


if not FIVESIM_API_KEY:
    raise RuntimeError(
        "FIVESIM_API_KEY belum diatur di environment."
    )


if not RUMAHOTP_API_KEY:
    raise RuntimeError(
        "RUMAHOTP_API_KEY belum diatur di environment."
    )



# =========================================================
# CONVERT ADMIN ID
# =========================================================

try:

    ADMIN_ID = int(ADMIN_ID)

except ValueError:

    raise RuntimeError(
        "ADMIN_ID harus berupa angka."
    )


# =========================================================
# PRODUCTION SAFETY CHECK
# =========================================================

if not MIDTRANS_IS_PRODUCTION:

    raise RuntimeError(
        "Midtrans harus berjalan dalam mode Production."
    )


if MIDTRANS_API_URL != "https://api.midtrans.com":

    raise RuntimeError(
        "MIDTRANS_API_URL bukan URL Production."
    )


if MIDTRANS_SNAP_URL != "https://app.midtrans.com/snap":

    raise RuntimeError(
        "MIDTRANS_SNAP_URL bukan URL Production."
    )


# =========================================================
# CONFIG LOADED
# =========================================================

print(
    "Midtrans mode: PRODUCTION / LIVE"
)
