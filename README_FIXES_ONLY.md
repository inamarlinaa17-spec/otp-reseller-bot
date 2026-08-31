# V6 — ONLY REQUESTED FIXES

Berbasis V6 asli. Perubahan hanya pada alur:
- Menu Utama dari pesan order/hasil dikirim sebagai pesan baru sehingga pesan order sebelumnya tetap utuh.
- Cek OTP memiliki Kembali ke Order.
- Menu Utama tidak mengubah status order.
- Order hanya SUCCESS ketika provider mengembalikan OTP yang valid; status provider saja tidak cukup.
- Gagal cancel: provider tidak ditampilkan, detail pesanan ditampilkan, dan teks tunggu diubah menjadi 2 menit.

Tidak ada perubahan pada alur pembelian, pricing, katalog, atau provider selain yang diperlukan untuk poin di atas.
