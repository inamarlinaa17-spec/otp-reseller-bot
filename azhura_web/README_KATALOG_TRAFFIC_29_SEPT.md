# Patch katalog dan Live Traffic (hanya azhura_web)

Perbaikan: Server 1 sekarang membaca negara dari cabang `product -> country` pada endpoint 5SIM guest/prices?product=...; Server 2 membaca `service_code` dan `service_name` dari API RumahOTP v2. Nama operator RumahOTP ditampilkan bila tersedia dalam baris pricelist; jika tidak, label Pilihan harga dipertahankan. Endpoint Live Traffic membaca pesanan Telegram + web dari database yang sama, polling 15 detik, hanya saat pengguna login, menyamarkan nama dan tidak pernah menampilkan nomor telepon atau OTP. Notifikasi tidak menampilkan order lama saat pertama login.

## Railway web Variables
- `FIVESIM_API_KEY`: **tambahkan**, isi token 5SIM protocol dari akun 5SIM. Katalog guest bisa dimuat tanpa key, tetapi pembelian dan pengecekan OTP butuh key.
- `RUMAHOTP_API_KEY`: sudah terlihat di screenshot web; pastikan nilainya valid dan akun API aktif.
- `DATABASE_URL`: harus menunjuk ke database PostgreSQL **yang sama** dengan bot Telegram supaya trafik kedua platform tampil.
- `PROFIT_PERCENT`: **opsional**, default web 7%; isi sama seperti bot bila margin di bot bukan 7%.
- `TRAFFIC_CHANNEL`, `TRAFFIC_BOT_TOKEN`, `MIDTRANS_*`: tidak diperlukan untuk popup web ini. Jangan menyalin rahasia tambahan tanpa kebutuhan.

Tidak mengubah file bot Telegram, alur pembelian, deposit, refund, maintenance, dashboard admin, atau Server 3. Uji katalog dan satu pembelian nominal kecil setelah deploy; provider langsung tidak dapat diuji tanpa kredensial Anda. Notifikasi Live Traffic bergantung pada order yang tersimpan di tabel orders, dan mungkin tidak muncul jika data provider atau timestamp tidak tersedia.
