# Admin Cancel & Refund Order Provider

Fitur ini menambahkan pembatalan order provider dari ADMIN PANEL.

Alur:
1. Admin buka `👥 Users`.
2. Pilih user.
3. Klik `📦 Transaksi`.
4. Pada order berstatus `PENDING`, akan muncul tombol `🛑 Batalkan & Refund <ORDER_ID>`.
5. Bot mengirim cancel ke provider (RumahOTP atau 5SIM).
6. Refund hanya dilakukan jika provider mengonfirmasi cancellation.
7. User menerima notifikasi refund dengan tombol `🚀 Order OTP Sekarang`.

Jika provider menolak/gagal cancel, saldo tidak dikembalikan dan tersedia tombol `🔄 Coba Lagi`.

Fitur ini memakai API key provider yang sudah ada di Railway. Tidak perlu menambah environment variable baru.
