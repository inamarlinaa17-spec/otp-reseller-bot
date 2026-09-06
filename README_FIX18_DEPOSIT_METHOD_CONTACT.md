# FIX18 — Deposit Method After Nominal + Dynamic Contact CS

Based on AZHURA FIX17.

## Perbaikan
- Setelah user klik Deposit, bot tetap meminta nominal terlebih dahulu.
- Setelah nominal valid, bot menampilkan pilihan:
  - ⚡ Pembayaran Otomatis (jalur payment gateway tetap tersembunyi dari user)
  - 📷 QRIS Manual
- Pilihan metode tidak lagi hilang setelah user memasukkan nominal.
- QRIS manual tetap menggunakan QRIS yang admin simpan dari Admin Panel → 📷 QRIS Manual.
- Deposit manual tetap memakai kode unik 3 digit dan konfirmasi admin sebelum saldo masuk.
- Contact CS tidak lagi hardcode `@AdminLu`.
- Contact CS membaca `ADMIN_USERNAME` dari Railway Variables, otomatis menghapus `@` jika ada, dan menyediakan tombol Chat Admin.
- Link konfirmasi QRIS manual ke admin juga memakai `ADMIN_USERNAME` yang sama.

## Railway
Pastikan:
`ADMIN_USERNAME=UsernameAdmin`

Tidak perlu menggunakan `@` di value.

## Fitur lama
Fitur FIX16/FIX17 lainnya dipertahankan.
