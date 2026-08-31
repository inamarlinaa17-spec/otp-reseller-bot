# V14 Flow Fix

Perubahan utama:

- ORDER BERHASIL hanya menampilkan `Cek OTP` dan `Batal / Refund`.
- Halaman Cek OTP yang belum menerima SMS hanya menampilkan `Cek OTP Lagi` dan `Kembali`.
- Menu `Cek OTP` selalu melakukan query provider terbaru.
- Setelah OTP diterima, status order menjadi `SUCCESS`; refund diblokir di backend.
- Setelah OTP diterima tersedia `Cek OTP`, `Resend OTP`, dan `Kembali`.
- `Batal / Refund` tidak muncul lagi setelah OTP diterima.
- Waktu tunggu pembatalan adalah 2 menit dan pembulatan tampilan tidak lagi menghasilkan `0 menit` saat masih terkunci.
- Request cancel ke provider dikunci atomik di database (`cancel_requested`) sehingga double-click/retry Telegram hanya mengubah UI dan request provider maksimal satu kali.
- Error PostgreSQL `COALESCE bigint/text` untuk `expires_at` tetap ditangani dengan migrasi ke TEXT.

Catatan provider Resend:
- RumahOTP memakai `set_status` dengan `status=repeat` melalui adapter RumahOTP.
- 5SIM tidak memiliki endpoint resend OTP yang didokumentasikan untuk order aktivasi yang sudah selesai; bot menampilkan pesan tidak didukung, tanpa memalsukan keberhasilan.
