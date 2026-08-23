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
#
# Bot ini dikunci menggunakan Midtrans Production.
# Tidak menggunakan Sandbox.
#

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
        "20"
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
