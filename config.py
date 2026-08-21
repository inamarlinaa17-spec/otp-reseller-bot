import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = os.getenv("ADMIN_ID", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# MIDTRANS CONFIG
MIDTRANS_SERVER_KEY = os.getenv("MIDTRANS_SERVER_KEY", "").strip()
MIDTRANS_CLIENT_KEY = os.getenv("MIDTRANS_CLIENT_KEY", "").strip()
MIDTRANS_MERCHANT_ID = os.getenv("MIDTRANS_MERCHANT_ID", "").strip()
MIDTRANS_IS_PRODUCTION = os.getenv("MIDTRANS_IS_PRODUCTION", "false").lower() == "true"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN belum diatur.")

if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID belum diatur.")

if not MIDTRANS_SERVER_KEY:
    raise RuntimeError("MIDTRANS_SERVER_KEY belum diatur.")

if not MIDTRANS_CLIENT_KEY:
    raise RuntimeError("MIDTRANS_CLIENT_KEY belum diatur.")

ADMIN_ID = int(ADMIN_ID)

# URL Midtrans otomatis sesuai mode
if not MIDTRANS_IS_PRODUCTION:
    MIDTRANS_API_URL = "https://api.sandbox.midtrans.com"
    MIDTRANS_SNAP_URL = "https://app.sandbox.midtrans.com/snap"
else:
    MIDTRANS_API_URL = "https://api.midtrans.com"
    MIDTRANS_SNAP_URL = "https://app.midtrans.com/snap"
