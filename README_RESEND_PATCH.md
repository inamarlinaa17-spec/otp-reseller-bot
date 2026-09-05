# RESEND OTP PATCH

Perubahan:
1. Tombol `🔁 Resend OTP` ditampilkan setelah OTP diterima untuk Server 1 (5SIM) dan Server 2 (RumahOTP).
2. RumahOTP memakai endpoint `status=resend` yang sudah ada.
3. 5SIM diberi handler terpisah yang TIDAK melakukan pembelian/reuse order secara diam-diam.
4. 5SIM activation API resmi tidak menyediakan endpoint server-side resend untuk order activation yang sama, jadi klik tombol pada Server 1 akan memberi penjelasan di Telegram.
5. `otp_order_view` juga menampilkan tombol untuk kedua provider.
6. Syntax semua file Python sudah dicek.

Catatan penting:
`/reuse` 5SIM bukan resend OTP; itu membuat/reuse activation baru dan dapat menimbulkan transaksi provider baru. Karena itu tidak digunakan sebagai pengganti resend.
