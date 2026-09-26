# AZHURA ID — Web App terpisah dari bot produksi

**Jangan mengganti Railway service bot lama.** Semua file bot dari ZIP yang dikirim pengguna dipertahankan byte-for-byte. `azhura_web/` adalah service baru, bukan pengganti `main.py`.

## Yang tersedia
- PWA responsif menggunakan logo asli; Home, Order OTP, Riwayat, Profil.
- Telegram Login Widget dengan validasi HMAC; pengguna bot lama memakai `telegram_id` dan saldo yang sama.
- Login Google setelah menautkan akun Google dari sesi Telegram terverifikasi (memerlukan Google OAuth Client ID).
- Saldo dan riwayat langsung dari database yang sama; tampilan nomor dan OTP yang diperbarui setiap 10 detik.
- Katalog Server 3 Reguler/Plus dari modul PremOTP bot yang sama; negara, harga, stok. Harga menggunakan `hitung_harga_jual_idr` dari bot.
- Kode endpoint pembelian Server 3 tersedia tetapi **DINONAKTIFKAN secara default** (`WEB_ORDER_ENABLED=false`). Jangan aktifkan sebelum menguji dengan database staging dan API PremOTP yang sesuai, termasuk penanganan timeout, refund dan pembaruan OTP. Status order yang ambigu ditandai `REVIEW` untuk diperiksa admin; bukan refund otomatis.
- Server 1/2 serta deposit tetap membuka bot. Integrasi order langsung server 1/2, deposit web, push notification, dan APK native **belum tersedia**. Jangan menyebut ZIP ini sebagai produksi siap pakai untuk semua alur.

## Deploy GitHub + Railway
1. Buat repo GitHub **baru** dengan isi folder `otp-reseller-bot-main` (atau gunakan repo sama dengan service Railway **baru**). Bot lama tidak perlu dideploy ulang.
2. Railway > New Service > GitHub repo > Root Directory `azhura_web` jika isi `otp-reseller-bot-main` sudah berada di root repo; jika folder induk masih ada, gunakan `otp-reseller-bot-main/azhura_web`.
3. Build: `pip install -r requirements.txt`. Start: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 45`.
4. Variabel: `BOT_TOKEN`, `BOT_USERNAME`, `DATABASE_URL` (nilai **sama** dengan service bot), `WEB_SESSION_SECRET` (acak kuat minimal 32 karakter), `PREMOTP_API_KEY`, `PREMOTP_BASE_URL` (opsional), `WEB_ORDER_ENABLED=false`. Untuk Google: `GOOGLE_CLIENT_ID` (OAuth Web Client ID) dan tambahkan domain HTTPS aplikasi pada Google Cloud OAuth authorized JavaScript origins.
5. Atur `/setdomain` pada BotFather sesuai domain HTTPS web app. Buka domain dan uji login Telegram dengan akun yang sudah pernah `/start` bot.
6. Lakukan pengujian login, saldo, riwayat, katalog, Google linking, status nomor/OTP di database staging terlebih dahulu. Jangan uji transaksi berbayar pada saldo pelanggan produksi.

## Keamanan
- Jangan commit `.env`, token bot, kunci Google atau `DATABASE_URL`.
- Login Google hanya untuk akun yang sudah ditautkan lewat sesi Telegram, bukan membuat saldo kedua.
- Cookies Secure/HttpOnly/SameSite, CSRF token untuk operasi autentikasi setelah login, verifikasi hash Telegram dan Google token audience.
- Pembelian web default off karena endpoint order langsung butuh uji integrasi provider secara live dan rekonsiliasi kegagalan ambigu. Gunakan hanya setelah pengujian dan audit alur refund.
