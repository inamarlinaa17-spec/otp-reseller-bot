AZHURA WEB QRIS MANUAL + SERVER 1/2/3

Tidak ada perubahan pada main.py, database.py, config.py atau kode bot Telegram.
Web membuat invoice QRIS MANUAL di tabel deposits yang sama, dengan admin Rp150 dan kode unik Rp50–150.
Gambar QRIS diambil dari bot_settings qris_manual_file_id melalui API Telegram tanpa mengekspos BOT_TOKEN.
Tombol Saya Sudah Bayar menyimpan konfirmasi web secara terpisah; TIDAK menambah saldo.
Admin tetap memverifikasi dan menyetujui deposit melalui alur bot Telegram yang sudah ada.
Syarat dan ketentuan, profil, CS, katalog dan riwayat tetap tampil di web.
Web: root /; build pip install -r azhura_web/requirements.txt; start gunicorn azhura_web.app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 45
Bot lama: root /; build pip install -r requirements.txt; start python main.py
Variables web: BOT_TOKEN, BOT_USERNAME, DATABASE_URL, WEB_SESSION_SECRET, ADMIN_USERNAME, provider keys, GOOGLE_CLIENT_ID, WEB_ORDER_ENABLED.
Belum diuji dengan API provider atau database produksi. Lakukan tes nominal kecil sebelum rilis.
