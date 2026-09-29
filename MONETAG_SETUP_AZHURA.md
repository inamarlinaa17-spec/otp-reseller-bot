# AZHURA Monetag — setup sebelum aktif

Ini menambahkan tombol **Nonton Iklan** ke bot Telegram dan Telegram Mini App di web. Sistem order/deposit tetap menggunakan database lama. Jangan aktifkan tanpa tes transaksi di database staging.

## Railway BOT Telegram
Tambahkan `AZHURA_WEB_URL=https://azhura-bot-nokos.up.railway.app` (ganti dengan domain web aktif Anda). Update `main.py` saja pada service bot. Jika memakai Watch Paths, perubahan `main.py` harus memicu deploy bot.

## Railway WEB
Tambahkan `MONETAG_ZONE_ID` (main SDK zone ID Rewarded Interstitial dari dashboard Monetag), `MONETAG_POSTBACK_SECRET` (random 32+ karakter, rahasiakan), `MONETAG_REWARD_SHARE=0.2`, `MONETAG_USD_IDR=16000`. Pastikan `DATABASE_URL`, `BOT_TOKEN`, `WEB_SESSION_SECRET` sama dengan sebelumnya. Web service deploy folder `azhura_web` saja.

## Monetag dashboard
Daftarkan URL Telegram Mini App `https://DOMAIN-WEB/rewards` dan aktifkan **Rewarded Interstitial** serta server-side postbacks. Set postback URL:

`https://DOMAIN-WEB/api/rewards/postback?token=SECRET&ymid={ymid}&zone={zone_id}&event={event_type}&value={reward_event_type}&price={estimated_price}`

Ganti DOMAIN-WEB dan SECRET dengan nilai Railway. JANGAN bagikan URL rahasia ini. Jika layanan menyediakan opsi signature postback native, gunakan juga validasi signature sesuai dokumentasi Monetag.

## Reward
Reward dihitung `floor(estimated_price USD * MONETAG_USD_IDR * MONETAG_REWARD_SHARE)` dan hanya dikreditkan untuk `impression` berstatus `valued`. Harga adalah estimasi Monetag, bukan jaminan pendapatan final. Jika hasil < Rp1, tidak ada kredit. Jika user hanya membuka/menutup iklan atau SDK gagal, saldo tidak berubah. Semua reward masuk ke tabel `users.balance` yang sama dengan order bot/web, dicatat di `ledger` dengan tipe `MONETAG_REWARD`.

Sistem menolak postback tanpa secret, zona salah, ymid tidak dikenal, tayangan non-valued, dan duplikat. Tidak ada batas harian dari aplikasi; Monetag tetap menentukan ketersediaan iklan. Uji tayangan nyata dan periksa ledger sebelum mengaktifkan untuk seluruh user.

**Catatan:** halaman iklan harus dibuka dari tombol Telegram Mini App pada bot (bukan browser biasa), supaya initData Telegram bisa diverifikasi.
