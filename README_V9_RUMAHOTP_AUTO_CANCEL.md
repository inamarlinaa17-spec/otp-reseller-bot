# AZHURA V9 — RumahOTP Auto Cancel/Reconciliation

## Perubahan utama
- `set_status?status=cancel` hanya dianggap perintah/acknowledgement.
- Bot wajib memverifikasi `/v1/orders/get_status` sampai status `canceled`.
- Tidak memakai perubahan saldo akun RumahOTP sebagai bukti cancel karena saldo adalah aggregate account dan dapat berubah asynchronous/karena order lain.
- Jika cancel belum terkonfirmasi, permintaan disimpan di database (`cancel_requested_at`) dan worker otomatis mencoba lagi setiap 15 detik.
- Refund saldo user hanya dilakukan setelah RumahOTP benar-benar mengembalikan status `canceled`.
- Auto-expire juga masuk queue yang sama, sehingga admin tidak perlu membatalkan manual.
- Rate limiter tetap menjaga request RumahOTP <= 5 request/10 detik.

## Penting
RumahOTP mendokumentasikan `cancel`, `done`, dan `resend` pada endpoint `set_status`. Status aktual order diverifikasi melalui `get_status`.
