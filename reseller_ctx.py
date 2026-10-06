"""Konteks reseller per-update (tanpa dependensi berat).

Setiap update yang masuk lewat bot reseller men-set CURRENT_RESELLER.
provider.hitung_harga_jual_idr membaca ini untuk menambahkan margin reseller,
dan database.create_pending_order memakainya untuk mencatat margin per order.
"""
import contextvars
from decimal import Decimal, ROUND_CEILING

CURRENT_RESELLER = contextvars.ContextVar("current_reseller", default=None)


def current_reseller():
    return CURRENT_RESELLER.get()


def add_margin(base_price, percent):
    """Harga jual reseller = harga bot utama + margin% (dibulatkan ke atas)."""
    base = int(base_price or 0)
    if base <= 0:
        return 0
    margin = (Decimal(base) * Decimal(str(percent)) / Decimal(100)).to_integral_value(rounding=ROUND_CEILING)
    return base + int(margin)


def split_margin(final_price, percent):
    """Kebalikan add_margin: kembalikan (harga_dasar, margin_rupiah)."""
    final = int(final_price or 0)
    if final <= 0:
        return 0, 0
    guess = int(Decimal(final) * Decimal(100) / (Decimal(100) + Decimal(str(percent))))
    for base in range(guess + 2, max(guess - 3, 0), -1):
        if add_margin(base, percent) == final:
            return base, final - base
    return guess, max(final - guess, 0)
