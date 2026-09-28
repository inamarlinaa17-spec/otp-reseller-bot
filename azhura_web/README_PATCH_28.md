# AZHURA Web — Patch 28 September

**Unggah hanya isi folder `azhura_web/` ke folder GitHub `azhura_web/`.** Jangan menimpa file bot di root.

Perubahan: tombol invoice manual menggunakan event listener, bukan inline onclick yang rusak; QRIS hilang setelah konfirmasi atau pembatalan; cek duplikasi kode unik; prioritas layanan populer mengikuti screenshot Telegram; pencarian tetap meliputi seluruh katalog API; status OTP dan timer 120 detik; tombol pembatalan web hanya muncul aktif setelah 120 detik, dan saldo hanya direfund setelah provider mengonfirmasi pembatalan.

**Batasan:** kesetaraan seluruh status expired, resend dan seluruh variasi provider dengan bot Telegram belum teruji pada API produksi. Jangan buka untuk seluruh pelanggan sebelum uji satu order per provider dan pengujian refund di database staging. Tidak ada file bot Telegram yang diubah.

Pengaturan Railway web tetap Root `/`, build `pip install -r azhura_web/requirements.txt`, start `gunicorn azhura_web.app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 45`.
