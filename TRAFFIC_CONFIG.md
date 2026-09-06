# Traffic Channel Configuration — AZHURA

Channel traffic yang sudah dipasang sebagai default:

`@Tracif_NokosAzhura`

## Cara kerja
- Bot AZHURA utama mengirim notifikasi OTP ke channel tersebut.
- Tidak membutuhkan bot kedua.
- `TRAFFIC_BOT_TOKEN` boleh dikosongkan.
- Jika Railway memiliki variable `TRAFFIC_CHANNEL`, nilainya akan menjadi prioritas.
- Jika variable tersebut tidak ada, `traffic_config.py` menjadi fallback.

## Syarat Telegram
Bot AZHURA harus menjadi admin di channel dan memiliki izin **Post Messages**.
