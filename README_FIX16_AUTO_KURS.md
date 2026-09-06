# FIX16 — Kurs USD/IDR Full Otomatis

- `KURS_DOLAR` tidak lagi dibaca dari Railway Variables.
- Saat startup, bot mengambil kurs USD/IDR dari Frankfurter API.
- Jika API gagal, bot memakai emergency fallback Rp18.000/USD.
- `PROFIT_PERCENT` tetap terpisah dan default 7%.
- Variabel `KURS_DOLAR` boleh dihapus dari Railway Variables.
- Fitur lain dari FIX15 tidak diubah.

## Railway

Setelah deploy FIX16, `KURS_DOLAR` di Variables sudah tidak diperlukan dan boleh dihapus.
Jangan menghapus `PROFIT_PERCENT` jika ingin tetap menggunakan margin 7%.
