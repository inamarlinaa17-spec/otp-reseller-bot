PERBAIKAN CRASH WEB: Import web_servers di azhura_web/app.py diubah menjadi from azhura_web.web_servers import ... agar cocok dengan Gunicorn `gunicorn azhura_web.app:app` dari root repository. Sebelumnya Python mencari web_servers di root dan dapat menimbulkan ModuleNotFoundError.

RAILWAY WEB: Root Directory / ; Build Command pip install -r azhura_web/requirements.txt ; Start Command gunicorn azhura_web.app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 45
VARIABLES WAJIB WEB: BOT_TOKEN, DATABASE_URL, WEB_SESSION_SECRET. BOT_USERNAME untuk login Telegram. Provider keys untuk pembelian. WEB_ORDER_ENABLED=true hanya setelah pengujian.
RAILWAY BOT: Root Directory / ; Build Command pip install -r requirements.txt ; Start Command python main.py ; pertahankan variabel lama.
ZIP ini memperbaiki import yang teridentifikasi dari inspeksi kode. Tidak ada pengujian Railway atau transaksi live.
