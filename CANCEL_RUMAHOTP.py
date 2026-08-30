#!/usr/bin/env python3
"""
CANCEL_RUMAHOTP.py
Alat standalone untuk mencoba membatalkan order RumahOTP
tanpa mengubah source code bot/GitHub.

Tidak melakukan refund saldo AZHURA.
Hanya:
1. Cek status order
2. Request cancel
3. Cek ulang status beberapa kali
"""

import getpass
import json
import time
import urllib.parse
import urllib.request
import urllib.error

BASE = "https://www.rumahotp.io/api/v1/orders"

def request(endpoint, api_key, params):
    url = endpoint + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            "x-apikey": api_key,
            "Accept": "application/json",
            "User-Agent": "AZHURA-RumahOTP-Cancel-Tool/1.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, {"raw": raw}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {"raw": raw}
        return e.code, body
    except Exception as e:
        return None, {"error": str(e)}

def show(title, status, data):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)
    print("HTTP:", status)
    print(json.dumps(data, indent=2, ensure_ascii=False))

def main():
    print("=" * 60)
    print("   RUMAHOTP - CANCEL ORDER TOOL")
    print("=" * 60)
    print("Tool ini TIDAK mengubah saldo AZHURA.")
    print("API key hanya dipakai selama program berjalan.\n")

    order_id = input("Order ID RumahOTP [RO0047768972]: ").strip()
    if not order_id:
        order_id = "RO0047768972"

    api_key = getpass.getpass("API Key RumahOTP: ").strip()
    if not api_key:
        print("API key kosong. Dibatalkan.")
        return

    # 1. Cek status awal
    http, data = request(
        BASE + "/get_status",
        api_key,
        {"order_id": order_id},
    )
    show("STATUS AWAL", http, data)

    answer = input("\nLanjut kirim CANCEL untuk order ini? (y/N): ").strip().lower()
    if answer != "y":
        print("Dibatalkan. Tidak ada perubahan pada order.")
        return

    # 2. Request cancel
    http, data = request(
        BASE + "/set_status",
        api_key,
        {"order_id": order_id, "status": "cancel"},
    )
    show("RESPONS CANCEL", http, data)

    # 3. Verifikasi status provider beberapa kali
    print("\nMemverifikasi status RumahOTP...")
    for attempt in range(1, 6):
        time.sleep(2)

        http, data = request(
            BASE + "/get_status",
            api_key,
            {"order_id": order_id},
        )
        show(f"VERIFIKASI #{attempt}", http, data)

        text = json.dumps(data, ensure_ascii=False).lower()

        if any(x in text for x in ('"status": "canceled"', '"status":"canceled"',
                                   '"status": "cancelled"', '"status":"cancelled"',
                                   '"status": "cancel"', '"status":"cancel"')):
            print("\n✅ ORDER TERKONFIRMASI DIBATALKAN DI RUMAHOTP.")
            print("Saldo AZHURA TIDAK disentuh oleh tool ini.")
            return

        if '"status": "completed"' in text or '"status":"completed"' in text:
            print("\n⚠️ ORDER SUDAH COMPLETED. Jangan lakukan refund lagi.")
            return

        if '"status": "received"' in text or '"status":"received"' in text:
            print("\n⚠️ ORDER SUDAH RECEIVED. Jangan lakukan refund lagi.")
            return

    print("\n⚠️ Setelah 5 kali pengecekan, RumahOTP belum mengonfirmasi status canceled.")
    print("JANGAN melakukan refund kedua kali di AZHURA.")
    print("Simpan output di atas untuk pemeriksaan RumahOTP.")

if __name__ == "__main__":
    main()
