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


# Kurs internal USD -> IDR
KURS_DOLAR = float(
    os.getenv(
        "KURS_DOLAR",
        "17649.80"
    )
)


# Margin reseller
PROFIT_PERCENT = float(
    os.getenv(
        "PROFIT_PERCENT",
        "7"
    )
)


# =========================================================
# SMSPOOL CONFIGURATION
# =========================================================

SMSPOOL_API_KEY = os.getenv(
    "SMSPOOL_API_KEY",
    ""
).strip()




# =========================================================
# SMS-MAN CONFIGURATION
# =========================================================

SMSMAN_API_KEY = os.getenv(
    "SMSMAN_API_KEY",
    ""
).strip()


# =========================================================
# RUMAHOTP CONFIGURATION
# =========================================================

RUMAHOTP_API_KEY = os.getenv(
    "RUMAHOTP_API_KEY",
    ""
).strip()


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


# SMS-Man control API reports prices in its account currency;
# the default installation uses RUB and converts to USD for
# comparison with 5SIM/SMSPool. Change this in Railway if
# your SMS-Man account uses a different price basis.
SMSMAN_RUB_TO_USD = float(
    os.getenv(
        "SMSMAN_RUB_TO_USD",
        "0.0125"
    )
)


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


if not SMSPOOL_API_KEY:
    raise RuntimeError(
        "SMSPOOL_API_KEY belum diatur di environment."
    )

if not SMSMAN_API_KEY:
    print("[SMSMAN] SMSMAN_API_KEY belum diatur; Server 3 dan aggregator tidak akan memiliki stok SMS-Man.")


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
