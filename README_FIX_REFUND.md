# Fix: RumahOTP Cancel/Refund

Perbaikan pada alur `❌ Batal / Refund`:

1. Bot memanggil endpoint resmi RumahOTP `/v1/orders/set_status?order_id=...&status=cancel`.
2. Hasil pembatalan provider sekarang diperiksa.
3. Saldo user hanya dikembalikan jika RumahOTP mengonfirmasi status `cancel`/`canceled`.
4. Jika provider gagal menutup order, bot TIDAK melakukan refund lokal dan menampilkan tombol `🔄 Coba Batalkan Lagi`.
5. Respons provider dicatat di log Railway dengan prefix `[OTP CANCEL]` dan `[RUMAHOTP]` untuk debugging.

Ini mencegah kondisi: bot Telegram mengatakan refund sukses tetapi order RumahOTP masih WAITING.
