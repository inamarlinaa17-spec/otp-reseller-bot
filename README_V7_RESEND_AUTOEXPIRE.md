# AZHURA V7 — Resend OTP + Auto Expire

Perubahan utama:

1. Pilihan server saat `ORDER OTP` dibuat seperti menu referensi:
   - Server 1 = seluruh layanan/nomor/negara 5SIM.
   - Server 2 = seluruh layanan/nomor/negara RumahOTP.
   Katalog harga, negara, dan susunan langkah setelah server dipilih tidak diubah.

2. RumahOTP:
   - Tombol `🔁 Resend OTP` tersedia sejak order aktif dibuat.
   - Tombol tetap tersedia setelah OTP pertama diterima.
   - Resend memanggil `set_status?status=resend` sesuai API RumahOTP.
   - Resend ditolak setelah masa aktif habis.

3. Masa aktif:
   - RumahOTP menggunakan `expired_at` dari provider.
   - Jika provider tidak mengembalikan expiry (fallback 5SIM), bot memakai 20 menit dari waktu order.

4. Auto expire/refund:
   - Worker berjalan setiap 30 detik.
   - Hanya order lokal `PENDING` yang sudah melewati expiry yang diperiksa.
   - Bot mengecek status provider sebelum refund.
   - Jika provider masih aktif, bot mencoba membatalkan provider.
   - Refund lokal hanya dilakukan setelah provider mengonfirmasi status terminal cancel/expired.
   - Refund database idempotent sehingga tidak bisa refund order yang sama dua kali.
   - User menerima notifikasi otomatis setelah refund.

5. Cancel manual tetap menggunakan verifikasi provider sebelum refund.

Validasi:
- `main.py`, `provider.py`, `rumahotp.py`, dan `database.py` lolos `py_compile`.
