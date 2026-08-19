import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = os.getenv("ADMIN_ID", "").strip()

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN belum diatur.")

if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID belum diatur.")

ADMIN_ID = int(ADMIN_ID)
