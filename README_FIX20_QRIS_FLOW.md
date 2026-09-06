# AZHURA FIX20 — QRIS Manual Flow Cleanup

Based on FIX19.

Changes:
- `❌ Batal` on a QRIS manual invoice now deletes the QRIS photo message and immediately sends a fresh Menu Utama with working buttons.
- After `✅ Saya Sudah Bayar`, the QRIS invoice photo is deleted and the user receives only `💬 Buka Chat Admin`.
- `🏠 Menu Utama` is intentionally not shown at the confirmation stage.
- After admin approves or rejects the manual deposit, the user notification now includes `🏠 Menu Utama`.
- Existing OTP/server/provider/traffic/maintenance/history/auto-kurs/payment features are preserved.
