import os
from dotenv import load_dotenv

load_dotenv()

# =========================================================
# TELEGRAM BOT
# =========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = os.getenv("ADMIN_ID", "").strip()

# =========================================================
# DATABASE
# =========================================================
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# =========================================================
# MIDTRANS CONFIGURATION - PRODUCTION / LIVE
# =========================================================
MIDTRANS_SERVER_KEY = os.getenv("MIDTRANS_SERVER_KEY", "").strip()
MIDTRANS_CLIENT_KEY = os.getenv("MIDTRANS_CLIENT_KEY", "").strip()
MIDTRANS_MERCHANT_ID = os.getenv("MIDTRANS_MERCHANT_ID", "").strip()

MIDTRANS_IS_PRODUCTION = True
MIDTRANS_API_URL = "https://api.midtrans.com"
MIDTRANS_SNAP_URL = "https://app.midtrans.com/snap"

# =========================================================
# 5SIM CONFIGURATION - OTP PROVIDER
# =========================================================
FIVESIM_API_KEY = os.getenv("FIVESIM_API_KEY", "").strip() # <- TAMBAH INI DI RAILWAY

KURS_DOLAR = 17650        # Kurs $1 = Rp. Update manual
PROFIT_PERCENT = 20       # Keuntungan lu 20% dari modal
MARKUP_RIBUAN = 100       # Pembulatan harga ke kelipatan 100
COUNTRY_ID = "0"          # 0 = Indonesia

# =========================================================
# VALIDATION
# =========================================================
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN belum diatur di environment.")

if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID belum diatur di environment.")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL belum diatur di environment.")

if not MIDTRANS_SERVER_KEY:
    raise RuntimeError("MIDTRANS_SERVER_KEY belum diatur di environment.")

if not MIDTRANS_CLIENT_KEY:
    raise RuntimeError("MIDTRANS_CLIENT_KEY belum diatur di environment.")

if not FIVESIM_API_KEY: # <- TAMBAH VALIDASI INI
    raise RuntimeError("FIVESIM_API_KEY belum diatur di Railway Variables.")

# =========================================================
# CONVERT ADMIN ID
# =========================================================
try:
    ADMIN_ID = int(ADMIN_ID)
except ValueError:
    raise RuntimeError("ADMIN_ID harus berupa angka.")

# =========================================================
# PRODUCTION SAFETY CHECK
# =========================================================
if not MIDTRANS_IS_PRODUCTION:
    raise RuntimeError("Midtrans harus berjalan dalam mode Production.")

if MIDTRANS_API_URL != "https://api.midtrans.com":
    raise RuntimeError("MIDTRANS_API_URL bukan URL Production.")

if MIDTRANS_SNAP_URL != "https://app.midtrans.com/snap":
    raise RuntimeError("MIDTRANS_SNAP_URL bukan URL Production.")

# =========================================================
# CONFIG LOADED
# =========================================================
print("Config loaded: MIDTRANS PRODUCTION + 5SIM ACTIVE")
