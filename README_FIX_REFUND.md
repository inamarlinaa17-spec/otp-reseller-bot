# Fix RumahOTP Cancel / Refund

## Masalah yang ditangani

Kasus yang ingin dicegah:

1. User menekan `❌ Batal / Refund`.
2. Saldo user dikembalikan oleh bot.
3. Tetapi order RumahOTP masih `waiting/running` sehingga saldo/provider order di akun admin RumahOTP belum kembali.

## Perubahan

- Saat user membatalkan order Server 2, bot lebih dulu memanggil:
  `/v1/orders/set_status?order_id=...&status=cancel`
- Bot kemudian mengecek `/v1/orders/get_status`.
- Refund lokal hanya boleh dilakukan jika provider mengonfirmasi status `cancel`, `canceled`, atau `cancelled`.
- Verifikasi diperpanjang menjadi beberapa percobaan agar propagasi status provider tidak terlalu cepat dianggap gagal.
- Ditambahkan **background reconciliation setiap 45 detik** untuk kasus lama yang sudah `REFUNDED` di database AZHURA tetapi order RumahOTP masih aktif. Reconciliation hanya mencoba membatalkan order provider dan **tidak menambah saldo user**.
- Di panel admin, order RumahOTP yang sudah `REFUNDED` lokal mendapatkan tombol `🔄 Sinkronkan RumahOTP` untuk recovery manual.
- Recovery admin tidak melakukan refund kedua kali.

## Resend OTP

RumahOTP mendukung `status=resend` melalui endpoint `set_status`, sehingga fitur Resend OTP tetap berjalan.

## Deployment

Ganti source GitHub dengan file hasil patch, commit, lalu biarkan Railway melakukan redeploy.

Setelah deploy, cek Railway Logs. Prefix penting:

- `[RUMAHOTP]`
- `[OTP CANCEL]`
- `[RUMAHOTP RECONCILE]`
- `[ADMIN CANCEL]`

Target akhirnya: **saldo user tidak boleh direfund sebelum cancel provider terkonfirmasi, dan kasus refund lama yang terlanjur tidak sinkron akan dicoba diperbaiki otomatis.**


## Fix database `expired_at` (Railway)

This version also fixes a PostgreSQL schema mismatch found in Railway:
`psycopg.errors.DatatypeMismatch: COALESCE types bigint and text cannot be matched`.

Some existing databases had `orders.expired_at` as `BIGINT`, while the bot/provider data is handled as text. On startup, the migration now normalizes the existing column to `TEXT` using `expired_at::text`.

No user balance or order balance is changed by this migration.
