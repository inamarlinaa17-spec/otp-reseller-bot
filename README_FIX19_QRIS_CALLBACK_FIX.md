# FIX19 — QRIS Manual Callback / Button Fix

Based on FIX18.

## Perbaikan utama
- Memperbaiki error Telegram `There is no text in the message to edit`.
- Invoice QRIS manual dikirim sebagai foto, sehingga tombol `Saya Sudah Bayar` dan `Batal` sekarang mengedit **caption foto**, bukan mencoba mengedit text message.
- Jika edit caption gagal, bot mengirim pesan status baru sebagai fallback.
- Setelah `Saya Sudah Bayar`, user mendapat tombol **Buka Chat Admin** dengan nominal/deposit yang sama.
- Notifikasi admin menampilkan ID deposit, nominal saldo, nominal transfer, dan kode unik dari **deposit yang sama**.
- Approve/reject admin diproses dengan DB di background thread agar callback Telegram tidak mudah stuck karena operasi database.
- Approve/reject dibuat lebih tahan error dan tetap memberi feedback jika edit pesan admin gagal.
- Reject mengirim detail transaksi yang sama ke user.

## Catatan kode unik
Kode unik berbeda jika dibuat untuk deposit yang berbeda. Jadi screenshot dengan `DEP-...` berbeda memang dapat memiliki kode unik berbeda. Untuk satu deposit yang sama, kode unik pada invoice user dan notifikasi admin diambil dari row database yang sama dan harus sama persis.

## Fitur lain
Tidak mengubah fitur OTP, Server 1/2, resend, refund provider, traffic channel, maintenance, history, auto kurs FIX16, dan margin.
