# AZHURA OTP V7

Perbaikan alur OTP tanpa mengubah environment variables Railway.

- Cek OTP tidak lagi menandai order SUCCESS hanya karena status provider; SUCCESS hanya saat kode OTP benar-benar diterima.
- Tombol setelah Cek OTP menjadi `Kembali ke Order`, bukan hanya Menu Utama.
- RumahOTP menampilkan waktu Expired jika API memberikannya.
- RumahOTP memiliki tombol Resend OTP selama order belum expired (termasuk setelah OTP pertama diterima, selama provider masih mengizinkan).
- Order Terakhir tetap ditampilkan di Menu Utama selama belum REFUNDED.
- Order yang dibatalkan/refund dihapus dari shortcut Order Terakhir.
- Refund menampilkan nama layanan, negara, dan nomor order yang dibatalkan.
- `Order Lagi` setelah refund kembali ke halaman harga untuk server/layanan/negara/operator yang sama.
- Database otomatis menambah kolom operator, expires_at, dan phone pada startup.

Catatan: RumahOTP mendokumentasikan status `cancel`, `done`, dan `resend` pada endpoint set_status. Expired time berasal dari field `expired_at` API RumahOTP.
