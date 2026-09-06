# FIX25 — Server Maintenance + QRIS Manual Approval Message

Perubahan hanya pada fitur yang diminta:
- Popup maintenance QRIS Manual dipastikan tampil saat metode dimatikan.
- Pesan `KONFIRMASI TERKIRIM` QRIS Manual disimpan message ID-nya dan saat admin menyetujui akan diedit menjadi `DEPOSIT MANUAL DISETUJUI` dengan tombol Menu Utama.
- Ditambahkan menu admin `🖥 Server Maintenance` dengan toggle terpisah Server 1 dan Server 2.
- Saat user memilih server yang sedang maintenance, muncul popup pemberitahuan dan user tidak masuk ke katalog layanan server tersebut.
- Database menambahkan kolom `user_message_id` pada tabel deposits untuk mendukung pengeditan pesan konfirmasi.

Fitur lain tidak diubah.
