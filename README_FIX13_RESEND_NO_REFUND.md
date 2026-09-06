# FIX13 — Resend OTP: no refund after first OTP

Perubahan utama:
- Setelah OTP pertama diterima, order dianggap **tidak refundable**.
- Saat user menekan **Resend OTP**, bot tidak lagi menampilkan `❌ Batal / Refund`.
- Bot menampilkan `⏳ MENUNGGU SMS OTP...` tanpa tombol refund selama menunggu OTP baru.
- Saat OTP baru benar-benar diterima, tombol kembali menjadi `🔁 Resend OTP` dan `✅ Pesanan Selesai`.
- Callback refund juga diperketat: jika `otp_code` atau `previous_otp_code` berisi OTP valid, refund ditolak.
- Detail order yang masih PENDING setelah resend juga tidak menampilkan tombol refund.
- RumahOTP resend tetap menggunakan endpoint resmi `set_status=status=resend` dan hanya dianggap berhasil jika API mengembalikan success.
