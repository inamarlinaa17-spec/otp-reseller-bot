# AZHURA Web Admin v2 — 28 September 2026

## Pemasangan
Unggah hanya isi folder `azhura_web/` dari ZIP ini ke folder `azhura_web/` pada repository GitHub yang sekarang. File bot root tidak termasuk dan tidak diubah. Pastikan `ADMIN_ID` sudah diatur di Railway service web dan database web sama dengan bot Telegram.

## Fitur tambahan
- Dashboard admin lama dipertahankan dan dipercantik. Kartu statistik bisa diklik; tambahan total seluruh waktu untuk pesanan dan deposit sukses, serta jumlah deposit pending.
- Data bot Telegram dan web muncul bersama bila kedua service menggunakan `DATABASE_URL` yang sama dan menyimpan transaksi ke tabel `orders` / `deposits` yang sama. Dashboard aktif melakukan refresh 12 detik selama tab terlihat. Bukan koneksi push real-time.
- Filter cepat status transaksi; tanggal harian dan semua waktu; ekspor CSV untuk hasil yang dimuat (maksimal 200 transaksi).
- Maintenance web: layar robot perbaikan memblokir UI pengguna biasa. Endpoint pembelian baru ditolak oleh server, sedangkan polling OTP, penyelesaian/refund, status deposit, dan callback tetap hidup. Admin tetap bisa masuk.
- Maintenance Server 1, 2, 3: pop-up sebelum masuk katalog, menawarkan server lain yang online. API pembelian baru server maintenance juga ditolak.
- Maintenance QRIS Manual dan QRIS Otomatis terpisah: pop-up metode alternatif dan API pembuatan invoice diblokir.

## Batasan
Maintenance web/server/pembayaran berlaku untuk pengguna **web** saja. Bot Telegram tetap berjalan seperti sebelumnya. Angka dashboard mengambil tabel bersama, tidak menghitung transaksi yang tidak tersimpan di database tersebut. Perlu pengujian staging dan transaksi uji dengan provider/payment gateway sebelum digunakan produksi. Tidak ada persetujuan deposit manual kedua di web untuk menghindari kredit saldo ganda.
