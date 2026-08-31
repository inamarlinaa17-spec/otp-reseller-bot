# AZHURA OTP V9 — RumahOTP Order Fix

- Create order memakai endpoint resmi RumahOTP `/api/v2/orders` dengan `number_id`, `provider_id`, `operator_id`.
- Route harga/operator yang dipilih diteruskan langsung dari quote.
- Operator `any` memakai `operator_id=1` sesuai dokumentasi RumahOTP.
- Endpoint pembelian TIDAK di-retry otomatis agar timeout tidak berisiko membuat order provider ganda.
- Timeout/error provider dicatat di Railway log dengan tag `[RUMAHOTP ORDER]`.
- `order_id`, nomor, dan `expired_at` disimpan setelah order berhasil.
