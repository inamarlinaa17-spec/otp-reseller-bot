# AZHURA FIX17 — Deposit Otomatis + QRIS Manual

Berbasis `AZHURA_FIX16_FULL_AUTO_KURS`.

## Fitur

- Menu Deposit sekarang memilih:
  - `⚡ Pembayaran Otomatis` — flow existing menggunakan Midtrans di belakang layar. Nama provider tidak ditampilkan ke user.
  - `📷 QRIS Manual` — memakai QRIS statis yang diunggah admin, cocok untuk QRIS DANA Bisnis.
- QRIS manual menambahkan kode unik 3 digit ke nominal transfer.
  - Contoh saldo Rp10.000 → user diminta transfer Rp10.347.
  - Yang masuk ke saldo user tetap Rp10.000; kode unik tidak dikreditkan.
- User menekan `Saya Sudah Bayar` setelah transfer.
- Bot otomatis mengirim permintaan konfirmasi ke admin dengan detail nominal dan kode unik.
- Admin mendapat tombol `Terima & Tambah Saldo` / `Tolak`.
- Setelah admin menyetujui, saldo user otomatis bertambah dan user menerima notifikasi.
- User juga mendapat tombol `Buka Chat Admin` untuk membuka DM admin.
- Admin dapat mengganti QRIS langsung dari panel admin → `📷 QRIS Manual` → kirim foto QRIS baru.
- QRIS disimpan sebagai Telegram `file_id` di tabel `bot_settings`, sehingga tidak perlu upload gambar ke Railway Variable.
- Migrasi database otomatis menambahkan kolom metode pembayaran/kode unik pada tabel `deposits`.
- Bonus deposit existing 10% untuk nominal Rp100.000+ tetap berlaku pada QRIS manual.

## Railway Variable tambahan

Tambahkan:

```text
ADMIN_USERNAME=username_admin_tanpa_@
```

Contoh jika username admin adalah `@AdminLu`, isi:

```text
ADMIN_USERNAME=AdminLu
```

Variable ini dipakai untuk membuat tombol DM admin dengan teks konfirmasi yang sudah terisi. Jika kosong, bot tetap menyediakan link Telegram berbasis `ADMIN_ID`.

## Setup QRIS DANA Bisnis

1. Deploy ZIP ini.
2. Buka bot sebagai admin.
3. Masuk `Admin Panel`.
4. Tekan `📷 QRIS Manual`.
5. Kirim foto QRIS DANA Bisnis.
6. Bot menjawab bahwa QRIS berhasil disimpan.

Jangan mengunggah QRIS milik orang lain; gunakan QRIS merchant yang memang menjadi milik/admin bisnis.

## Catatan keamanan

Konfirmasi QRIS manual **tidak otomatis dianggap lunas hanya karena user menekan tombol**. Admin tetap harus mengecek mutasi/pembayaran pada QRIS dan baru menekan `Terima & Tambah Saldo`.
