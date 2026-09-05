# AZHURA BOT NOKOS — V6 RumahOTP Cancel/Refund Fix

V6 memperbaiki masalah ketika user menekan **❌ Batal / Refund** tetapi bot sudah mengembalikan saldo sementara order di RumahOTP masih **WAITING**.

## Perubahan utama

1. Sebelum cancel, bot mengecek status order RumahOTP.
2. Bot meminta pembatalan melalui endpoint resmi:
   `/v1/orders/set_status?order_id=...&status=cancel`
3. Setelah request cancel, bot **tidak langsung refund**.
4. Bot melakukan verifikasi ulang melalui:
   `/v1/orders/get_status?order_id=...`
5. Verifikasi dilakukan beberapa kali dengan jeda pendek untuk memberi waktu propagasi status.
6. Refund saldo user hanya boleh terjadi jika RumahOTP benar-benar mengembalikan status `cancel`, `canceled`, atau `cancelled`.
7. Jika RumahOTP masih `WAITING`, `expiring`, error, atau status lain yang belum terminal, bot **tidak refund lokal** dan menampilkan tombol **🔄 Coba Batalkan Lagi**.
8. Jika order sudah `completed/received/done`, bot menolak pembatalan dan tidak mengembalikan saldo user.
9. Fitur **Admin → User → Transaksi → Batalkan & Refund** otomatis memakai logika V6 yang sama karena menggunakan adapter RumahOTP yang sama.
10. Refund lokal tetap idempotent melalui `refund_status=REFUNDED`, sehingga tidak bisa dikembalikan 2 kali.

## Tentang saldo admin RumahOTP

Cancel RumahOTP dilakukan terlebih dahulu. Setelah RumahOTP mengonfirmasi `cancel`, bot baru mengembalikan saldo user di database bot.

Endpoint status-change RumahOTP mendokumentasikan `status=cancel` sebagai aksi pembatalan. Perubahan saldo akun provider mengikuti sistem RumahOTP; V6 tidak menganggap refund lokal bot sebagai bukti bahwa saldo provider sudah kembali.

## Deploy Railway

Upload ZIP V6 ke GitHub, lalu deploy/redeploy service Railway yang sama. Environment variable lama tetap digunakan, termasuk `RUMAHOTP_API_KEY`.

## Log

Cari prefix berikut di Railway Deploy Logs:

- `[OTP CANCEL]`
- `[RUMAHOTP] cancel verify`
- `[RUMAHOTP] cancel confirmed`

Jika cancel belum dikonfirmasi provider, saldo user tidak disentuh.


## Resend OTP — Server 1 & Server 2

Tombol `🔁 Resend OTP` sekarang ditampilkan setelah OTP diterima untuk kedua server.

- **Server 2 — RumahOTP:** tombol benar-benar memanggil endpoint status `resend` provider.
- **Server 1 — 5SIM:** API activation resmi 5SIM saat ini tidak menyediakan endpoint untuk meminta resend pada order activation yang sama. Karena itu bot sengaja tidak memanggil `/reuse`: endpoint tersebut membuat/reuse activation baru, bukan resend OTP pada order yang sama dan dapat menimbulkan biaya provider baru.
- Jika Server 1 diklik, bot menampilkan pesan bahwa provider 5SIM tidak mendukung server-side resend.

Jika tujuan Anda adalah mengirim ulang kode dari aplikasi seperti WhatsApp/Telegram, request resend sebenarnya harus dipicu dari aplikasi/layanan tujuan; API 5SIM hanya menerima SMS yang masuk dan menyediakan endpoint untuk mengecek order tersebut.
