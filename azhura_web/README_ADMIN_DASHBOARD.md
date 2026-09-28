# AZHURA Web — Admin Control Center

## Cara pasang
Unggah **isi folder `azhura_web` dari ZIP** ke folder `azhura_web` yang sama di GitHub. Jangan hapus folder root atau menimpa file bot. Railway web otomatis membangun ulang jika Watch Paths `/azhura_web/**` aktif.

## Penting: variabel Railway web
Tambahkan `ADMIN_ID` pada service web `joyful-education` dengan **Telegram ID admin yang sama seperti bot utama**. Jangan masukkan username atau bot token. Jika `ADMIN_ID` belum diisi, panel admin tidak dapat diakses (fail-closed). `BOT_TOKEN`, `DATABASE_URL`, dan `WEB_SESSION_SECRET` tetap memakai konfigurasi lama.

## Cara masuk
Login ke website memakai akun Telegram admin yang sesuai `ADMIN_ID`. Tombol **Buka Dashboard Admin AZHURA** muncul di atas header. Atau buka `/admin` setelah login.

## Fungsi
- Dashboard: total pengguna, pesanan harian, deposit sukses, transaksi pending.
- Pesanan: filter tanggal/status, cari ID pesanan / Telegram ID / layanan, unduh CSV.
- Deposit: filter tanggal/status, cari ID deposit / pengguna, unduh CSV. **Persetujuan QRIS manual tetap lewat bot Telegram** untuk mencegah kredit saldo ganda.
- Pengguna: cari Telegram ID, username atau nama; lihat saldo dan tanggal terdaftar.
- Maintenance: web keseluruhan, atau masing-masing Server 1, 2, 3. Status disimpan di tabel `bot_settings` menggunakan kunci berawalan `azhura_web_`, tanpa mengubah kunci maintenance bot Telegram.

## Batasan yang disengaja
- Maintenance web hanya menolak **pembuatan order/invoice baru melalui web**. Login, status deposit, polling OTP, resend, finish, cancel/refund, dan callback pembayaran tidak diblokir. Bot Telegram **tidak** ikut maintenance. Untuk mematikan server di bot, gunakan pengaturan admin bot yang sudah ada.
- Riwayat transaksi hanya baca; tidak mengubah status transaksi atau saldo.
- Tabel maksimal 200 transaksi per pencarian (pengguna 100) agar tidak memperlambat database. Filter tanggal menggunakan tanggal yang tersimpan pada `created_at`; jam ditampilkan WIB.
- Statistik dan maintenance menggunakan database PostgreSQL yang sama; butuh tabel `users`, `orders`, `deposits`, dan `bot_settings` yang sudah ada.
- Uji dahulu di staging sebelum memakai transaksi produksi. Tidak ada pengujian pembayaran provider secara langsung dalam paket ini.
