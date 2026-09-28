AZHURA WEB — UPDATE OTP DAN QRIS

Hanya unggah isi folder azhura_web/ ke folder azhura_web/ repository yang sudah ada.
JANGAN mengganti main.py, database.py, config.py, provider.py, rumahotp.py, atau premotp.py milik bot.

Perubahan:
- Resend OTP setelah kode pertama masuk: Server 2 RumahOTP dan Server 3 PremOTP. Server 1 5SIM menampilkan notifikasi tidak mendukung resend.
- Selesai: memanggil penyelesaian provider (Server 1/2) atau menutup lokal (Server 3), lalu mengubah status menjadi COMPLETED.
- Menampilkan kode OTP sebelumnya, nomor dan waktu kedaluwarsa jika provider menyediakan expired_at.
- QRIS Otomatis: tombol Cek Status membaca status dari database bersama yang diperbarui webhook/poller bot; tombol Batal mengecek status provider sebelum membatalkan invoice lokal.
- Midtrans disembunyikan dari tampilan deposit web; endpoint lama dibiarkan agar tidak merusak integrasi.

CATATAN PENTING:
- Deploy ini belum diuji dengan API dan database produksi; uji transaksi kecil dahulu.
- Refund otomatis untuk order web yang dibiarkan kedaluwarsa masih bergantung pada worker bot yang memproses order terkait. Tidak ditambahkan worker refund kedua di web karena risiko double refund.
- Pembatalan invoice QRIS otomatis di web mengubah status lokal dan tidak membatalkan QRIS di sisi provider. Jika pembayaran telah dikirim, segera hubungi admin.
- WEB_ORDER_ENABLED harus true untuk Resend/Selesai order dari web.
