# Fitur RESELLER / Whitelabel

## Alur user
1. Bot utama → tombol **🤝 RESELLER** → info → **➕ Buat Bot Baru** → kirim token dari @BotFather.
2. Bot dibuat tapi **belum aktif** sampai **Contact CS** (username Telegram milik user sendiri) diisi.
3. Setelah CS diisi bot langsung jalan; panel menampilkan status Online, margin, statistik, saldo margin.
4. Menu panel: Ubah Margin, Restart Bot, Withdraw, Riwayat WD, Contact CS, Ganti Token, Hentikan/Aktifkan Bot.
5. Panel yang sama ada di azhura_web (tile **RESELLER**).

## Aturan harga & keuntungan
- Harga jual di bot reseller = harga bot utama + margin% (dibulatkan ke atas). Margin min 5%, maks 100%.
- Margin dikreditkan ke **saldo margin** reseller sekali saat OTP pertama masuk (aman dari double-credit saat resend).
- Order yang direfund setelah margin masuk → margin ditarik kembali otomatis.
- Pembeli memakai saldo yang sama (satu `telegram_id`), deposit via QRIS/Midtrans otomatis atau QRIS manual. QRIS manual di bot reseller tetap dikonfirmasi admin lewat bot utama (notifikasi masuk ke admin, saldo & pesan hasil dikirim lewat bot reseller si pembeli).
- Saldo Gratis, Iklan, dan Referral disembunyikan di bot reseller.

## Withdraw
- Minimum `RESELLER_MIN_WD` (default Rp5.000). Saldo margin langsung dipotong saat pengajuan.
- Admin dapat pesan dengan tombol **✅ Sudah Ditransfer** / **❌ Tolak** (tolak = saldo dikembalikan). Transfer dilakukan manual.

## Teknis
- Tabel baru (otomatis dibuat saat start): `reseller_bots`, `reseller_buyers`, `reseller_ledger`, `reseller_withdrawals`; kolom baru `orders.reseller_*`, `deposits.reseller_bot_id`.
- Semua bot reseller berjalan dalam **proses bot utama** (polling) dan disinkronkan dari database tiap 20 detik, jadi perubahan dari web ikut berlaku.
- File baru: `reseller.py`, `reseller_core.py`, `reseller_ctx.py`.
