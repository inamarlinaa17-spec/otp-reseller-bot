import requests
import os
from config import KURS_DOLAR, PROFIT_PERCENT

BASE_URL = "https://5sim.net/v1"

def get_headers():
    api_key = os.getenv("FIVESIM_API_KEY")
    if not api_key:
        return {}
    return {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}

def get_balance():
    """Cek saldo 5sim dalam $"""
    try:
        r = requests.get(f"{BASE_URL}/user/profile", headers=get_headers(), timeout=15)
        r.raise_for_status()
        return r.json().get("balance", 0)
    except:
        return 0

def get_all_countries():
    """Ambil semua negara yang support di 5sim"""
    try:
        r = requests.get(f"{BASE_URL}/guest/countries", headers=get_headers(), timeout=15)
        r.raise_for_status()
        return r.json() # format: {"0": "Indonesia", "1": "USA", ...}
    except:
        return {}

def get_all_products():
    """Ambil semua layanan/produk yang ada di 5sim"""
    try:
        r = requests.get(f"{BASE_URL}/guest/products", headers=get_headers(), timeout=15)
        r.raise_for_status()
        return r.json() # format: {"whatsapp": "WhatsApp", "tg": "Telegram", ...}
    except:
        return {}

def get_prices(country="0"):
    """Ambil semua harga di 1 negara. Return: {product: {operator: {cost, count}}}"""
    try:
        r = requests.get(f"{BASE_URL}/guest/prices", headers=get_headers(), timeout=15)
        r.raise_for_status()
        data = r.json()
        return data.get(country, {})
    except:
        return {}

def hitung_harga_jual(harga_dolar):
    """Hitung harga jual = modal + profit 20%"""
    if harga_dolar == 0:
        return 0
    harga_modal_rp = harga_dolar * KURS_DOLAR
    keuntungan = harga_modal_rp * (PROFIT_PERCENT / 100)
    harga_jual = harga_modal_rp + keuntungan
    # Bulatin ke kelipatan 100 biar rapi
    return int(round(harga_jual / 100) * 100)

def buy_number(country="0", product="whatsapp", operator="any"):
    """Beli nomor. operator='any' = ambil yg termurah/stock paling banyak"""
    try:
        r = requests.get(f"{BASE_URL}/user/buy/activation/{country}/{product}/{operator}", headers=get_headers(), timeout=20)
        r.raise_for_status()
        return r.json() # isi: id, phone, country, operator, product
    except Exception as e:
        return {"response": "ERROR", "message": str(e)}

def get_sms(order_id):
    """Ambil kode OTP dari order. Cek berulang"""
    try:
        r = requests.get(f"{BASE_URL}/user/check/{order_id}", headers=get_headers(), timeout=15)
        r.raise_for_status()
        return r.json() # isi: status, sms: [{code, text}]
    except Exception as e:
        return {"response": "ERROR", "message": str(e)}

def cancel_number(order_id):
    """Batalin order dan refund ke saldo 5sim"""
    try:
        r = requests.get(f"{BASE_URL}/user/cancel/{order_id}", headers=get_headers(), timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"response": "ERROR", "message": str(e)}
