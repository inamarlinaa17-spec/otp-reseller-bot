# FIX12 — Traffic OTP Notification + Detailed Order History

Perubahan ini dibuat di atas FIX11 dan fokus pada dua permintaan:

1. Notifikasi OTP otomatis ke channel/chat Telegram traffic.
2. Histori order admin dan user menampilkan detail nomor, OTP, negara, layanan, harga, status, waktu transaksi, expired, dan ID order.

## Traffic notification

Konfigurasi Railway Variables:

- `TRAFFIC_CHANNEL=@Tracif_NokosAzhura` atau ID channel seperti `-1001234567890`

Jika Railway Variable `TRAFFIC_CHANNEL` tidak diisi, project otomatis memakai nilai pada `traffic_config.py`: `@Tracif_NokosAzhura`.
- `TRAFFIC_BOT_TOKEN=` opsional. Tidak perlu diisi untuk channel ini; jika kosong, `BOT_TOKEN` utama dipakai.

Bot yang digunakan harus memiliki izin mengirim pesan ke channel/chat tersebut.

Notifikasi dikirim otomatis ketika OTP yang benar-benar valid diterima. Notifikasi tidak mengubah status order, saldo, resend, refund, atau completion.

Format notifikasi mengikuti pola `CODE RECEIVED 2.0` dan menyertakan ID, user yang dimasking, code, nomor yang dimasking, harga, message_text, layanan, dan negara.

## History

- User: histori order tetap detail seperti screenshot referensi: layanan, negara, nomor, OTP, harga/status, waktu transaksi, expired, dan order ID.
- Admin: menu Orders sekarang juga menampilkan 10 order terbaru dengan detail yang sama serta Telegram ID user.
- Detail per-user admin juga ditambah OTP, waktu transaksi, dan expired.

Perubahan ini tidak mengubah provider order flow, auto OTP, resend, refund, maintenance, atau aturan no-refund setelah OTP diterima.
