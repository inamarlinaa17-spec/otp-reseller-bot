# AZHURA BOT NOKOS — V6 RumahOTP Cancel/Refund Fix

V6 memperbaiki masalah ketika user menekan **❌ Batal / Refund** tetapi bot sudah mengembalikan saldo sementara order di RumahOTP masih **WAITING**.

## Perubahan utama

1. Sebelum cancel, bot mengecek status order RumahOTP.
2. Bot meminta pembatalan melalui endpoint resmi:
   `/v1/orders/set_status?order_id=...&status=cancel`
3. Setelah request cancel, bot **tidak langsung refund**.
4. Bot melakukan verifikasi ulang melalui:
   `/v1/orders/get_status?order_id=...`
5. Verifikasi dilakukan bertahap (maksimal 3 kali, dengan jeda 2/3/4 detik) agar memberi waktu propagasi status sekaligus tidak membanjiri API RumahOTP yang membatasi 5 request per 10 detik.
6. Refund saldo user hanya boleh terjadi jika RumahOTP benar-benar mengembalikan status `cancel`, `canceled`, atau `cancelled`.
7. Jika RumahOTP masih `WAITING`, `expiring`, error, atau status lain yang belum terminal, bot **tidak refund lokal** dan menampilkan tombol **🔄 Coba Batalkan Lagi**.
8. Jika order sudah `completed/received/done`, bot menolak pembatalan dan tidak mengembalikan saldo user.
9. Fitur **Admin → User → Transaksi → Batalkan & Refund** otomatis memakai logika V6 yang sama karena menggunakan adapter RumahOTP yang sama.
10. Refund lokal tetap idempotent melalui `refund_status=REFUNDED`, sehingga tidak bisa dikembalikan 2 kali.

## Tentang saldo admin RumahOTP

Cancel RumahOTP dilakukan terlebih dahulu. Setelah RumahOTP mengonfirmasi `cancel`, bot baru mengembalikan saldo user di database bot.

Endpoint status-change RumahOTP mendokumentasikan `status=cancel` sebagai aksi pembatalan. Response `success=true` dari endpoint tersebut hanya dianggap sebagai penerimaan perintah; bot tetap memanggil `get_status` dan baru mengembalikan saldo user setelah status provider benar-benar `cancel/canceled/cancelled`. Perubahan saldo akun provider tetap dilakukan oleh sistem RumahOTP.

## Deploy Railway

Upload ZIP V6 ke GitHub, lalu deploy/redeploy service Railway yang sama. Environment variable lama tetap digunakan, termasuk `RUMAHOTP_API_KEY`.

## Log

Cari prefix berikut di Railway Deploy Logs:

- `[OTP CANCEL]`
- `[RUMAHOTP] cancel verify`
- `[RUMAHOTP] cancel confirmed`

Jika cancel belum dikonfirmasi provider, saldo user tidak disentuh.
