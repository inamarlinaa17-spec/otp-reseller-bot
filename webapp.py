import hashlib
import hmac
import json
import os
import time
import uuid
from urllib.parse import parse_qsl

from flask import Blueprint, jsonify, request, render_template

from database import (
    get_balance,
    get_order,
    get_order_history,
    create_pending_order,
    save_provider_order,
    refund_order,
    mark_order_waiting_for_otp,
    get_user,
)

webapp_bp = Blueprint("az_webapp", __name__, template_folder="templates", static_folder="static", static_url_path="/static")

WEBAPP_MAX_AGE = int(os.getenv("WEBAPP_AUTH_MAX_AGE", "86400"))


def _main():
    # Lazy import avoids a circular import while main.py registers the blueprint.
    import main
    return main


def _verify_init_data(init_data: str):
    """Verify Telegram WebApp initData using BOT_TOKEN.

    The browser sends the signed initData in X-Telegram-Init-Data. In production
    the Telegram client supplies this automatically when the page is opened as
    a Telegram Mini App.
    """
    if not init_data:
        return None, "Telegram WebApp session tidak ditemukan."
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = pairs.pop("hash", "")
        auth_date = int(pairs.get("auth_date", "0"))
    except Exception:
        return None, "initData Telegram tidak valid."

    if not received_hash or not auth_date:
        return None, "initData Telegram tidak lengkap."
    if abs(int(time.time()) - auth_date) > WEBAPP_MAX_AGE:
        return None, "Sesi Telegram sudah kedaluwarsa. Buka Mini App lagi."

    main = _main()
    token = str(getattr(main, "BOT_TOKEN", "") or "").strip()
    if not token:
        return None, "BOT_TOKEN belum dikonfigurasi."

    data_check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        return None, "Signature Telegram tidak valid."

    try:
        user = json.loads(pairs.get("user", "{}"))
        telegram_id = int(user["id"])
    except Exception:
        return None, "Data user Telegram tidak valid."
    return {"telegram_id": telegram_id, "telegram_user": user}, None


def _auth():
    data = request.headers.get("X-Telegram-Init-Data", "")
    return _verify_init_data(data)


def _json_error(message, code=400):
    return jsonify({"ok": False, "error": message}), code


def _order_json(row):
    if not row:
        return None
    main = _main()
    provider = str(row.get("provider") or "").lower()
    status = str(row.get("status") or "").upper()
    has_otp = bool(main._order_has_received_otp(row))
    expired = provider in {"5sim", "rumahotp", "premotp"} and main._order_expired(row)
    history_completed = status == "COMPLETED" or (expired and has_otp)
    if history_completed:
        display_status = "COMPLETED"
    elif status == "SUCCESS" and has_otp:
        display_status = "OTP_RECEIVED"
    elif status == "REFUNDED":
        display_status = "REFUNDED"
    elif status == "PENDING":
        display_status = "WAITING_OTP"
    else:
        display_status = status or "UNKNOWN"
    return {
        "order_id": row.get("order_id"),
        "provider": provider,
        "server_name": main.OTP_SERVERS.get(provider, provider),
        "country": row.get("country_name") or row.get("country") or "-",
        "service": row.get("service_name") or row.get("service") or "-",
        "phone": row.get("phone") or "-",
        "price": int(row.get("sell_price") or 0),
        "provider_order_id": row.get("provider_order_id"),
        "status": display_status,
        "raw_status": status,
        "otp1": row.get("previous_otp_code") if main._is_real_otp_code(row.get("previous_otp_code")) else None,
        "otp2": row.get("otp_code") if main._is_real_otp_code(row.get("otp_code")) else None,
        "sms_text": row.get("sms_text") or "",
        "created_at": row.get("created_at"),
        "expired_at": row.get("expired_at"),
        "expired": bool(expired),
        "resend_available": bool(has_otp and not expired and status in {"SUCCESS", "PENDING"}),
    }


@webapp_bp.get("/app")
def web_app():
    return render_template("index.html")


@webapp_bp.get("/app/")
def web_app_slash():
    return render_template("index.html")


@webapp_bp.get("/api/webapp/me")
def api_me():
    auth, error = _auth()
    if error:
        return _json_error(error, 401)
    tid = auth["telegram_id"]
    user = get_user(tid)
    if not user:
        return _json_error("Akun Telegram belum terdaftar di bot.", 403)
    return jsonify({
        "ok": True,
        "user": {
            "telegram_id": tid,
            "username": user.get("username") or auth["telegram_user"].get("username") or "-",
            "first_name": user.get("first_name") or auth["telegram_user"].get("first_name") or "User",
            "balance": int(user.get("balance") or 0),
        },
    })


@webapp_bp.get("/api/webapp/orders")
def api_orders():
    auth, error = _auth()
    if error:
        return _json_error(error, 401)
    try:
        limit = max(1, min(100, int(request.args.get("limit", 50))))
    except Exception:
        limit = 50
    rows = get_order_history(auth["telegram_id"], limit=limit, offset=0)
    return jsonify({"ok": True, "orders": [_order_json(r) for r in rows]})


@webapp_bp.get("/api/webapp/orders/<order_id>")
def api_order_detail(order_id):
    auth, error = _auth()
    if error:
        return _json_error(error, 401)
    row = get_order(order_id)
    if not row or int(row.get("telegram_id")) != auth["telegram_id"]:
        return _json_error("Order tidak ditemukan.", 404)
    return jsonify({"ok": True, "order": _order_json(row)})


@webapp_bp.get("/api/webapp/catalog/services")
def api_services():
    auth, error = _auth()
    if error:
        return _json_error(error, 401)
    main = _main()
    provider = str(request.args.get("provider", "5sim")).lower()
    try:
        if provider == "premotp":
            data = main.get_premotp_services("regular") or []
            rows = []
            iterable = data.items() if isinstance(data, dict) else enumerate(data)
            for key, item in iterable:
                if isinstance(item, dict):
                    code = str(item.get("service_key") or item.get("key") or item.get("id") or key)
                    name = str(item.get("name") or item.get("service_name") or code)
                else:
                    code, name = str(key), str(item)
                rows.append({"code": code, "name": name})
        elif provider == "rumahotp":
            rows = []
            for item in (main.get_rumahotp_services() or []):
                if not isinstance(item, dict):
                    continue
                code = str(item.get("service_code") or item.get("code") or item.get("key") or item.get("service") or item.get("id") or "")
                name = str(item.get("service_name") or item.get("name") or code)
                if code:
                    rows.append({"code": code, "name": name})
        else:
            rows = [{"code": code, "name": label} for code, label in main.OTP_SERVICES]
        return jsonify({"ok": True, "services": rows})
    except Exception as exc:
        return _json_error(str(exc), 502)


@webapp_bp.get("/api/webapp/catalog/countries")
def api_countries():
    auth, error = _auth()
    if error:
        return _json_error(error, 401)
    main = _main()
    provider = str(request.args.get("provider", "5sim")).lower()
    service = str(request.args.get("service", "")).strip()
    if not service:
        return _json_error("service wajib diisi")
    try:
        if provider == "premotp":
            data = main.get_premotp_countries(service, "regular") or []
            rows = []
            iterable = data.items() if isinstance(data, dict) else enumerate(data)
            for key, item in iterable:
                if isinstance(item, dict):
                    code = str(item.get("country_key") or item.get("key") or item.get("code") or key)
                    name = str(item.get("name") or item.get("country_name") or code)
                else:
                    code, name = str(key), str(item)
                rows.append({"code": code, "name": name, "flag": main.country_flag(name)})
        elif provider == "rumahotp":
            data = main.get_rumahotp_countries(service) or []
            rows = []
            for item in data:
                if isinstance(item, dict):
                    code = str(item.get("code") or item.get("country") or item.get("key") or item.get("id") or "")
                    name = str(item.get("name") or item.get("country_name") or code)
                else:
                    code, name = str(item), str(item)
                if code:
                    rows.append({"code": code, "name": name, "flag": main.country_flag(name)})
        else:
            data = main.get_all_countries() or {}
            rows = []
            if isinstance(data, dict):
                iterable = data.items()
            else:
                iterable = enumerate(data)
            for key, item in iterable:
                if isinstance(item, dict):
                    code = str(item.get("code") or item.get("country") or item.get("id") or key)
                    name = str(item.get("name") or item.get("country_name") or code)
                else:
                    code, name = str(key), str(item)
                rows.append({"code": code, "name": name, "flag": main.country_flag(name)})
        return jsonify({"ok": True, "countries": rows})
    except Exception as exc:
        return _json_error(str(exc), 502)


@webapp_bp.get("/api/webapp/catalog/offers")
def api_offers():
    auth, error = _auth()
    if error:
        return _json_error(error, 401)
    main = _main()
    provider = str(request.args.get("provider", "5sim")).lower()
    service = str(request.args.get("service", "")).strip()
    country = str(request.args.get("country", "")).strip()
    if not service or not country:
        return _json_error("service dan country wajib diisi")
    try:
        rows = []
        if provider == "5sim":
            offers = main.get_price_options(country, main.canonical_5sim_service(service)) or []
            for item in sorted(offers, key=lambda x: float(x.get("cost") or 0)):
                stock = int(item.get("stock") or 0)
                cost = float(item.get("cost") or 0)
                if stock <= 0 or cost <= 0:
                    continue
                rows.append({
                    "provider": provider,
                    "country": country,
                    "service": main.canonical_5sim_service(service),
                    "provider_cost": cost,
                    "sell_price": int(main.hitung_harga_jual(cost)),
                    "stock": stock,
                    "meta": {"operators": item.get("operators") or []},
                })
        elif provider == "rumahotp":
            quotes = main.get_rumahotp_quotes_for_country(country, service) or []
            for item in quotes:
                cost = float(item.get("cost_idr") or item.get("price_idr") or 0)
                stock = int(item.get("stock") or 0)
                if cost <= 0 or stock <= 0:
                    continue
                rows.append({
                    "provider": provider,
                    "country": str(item.get("country") or country),
                    "service": str(item.get("service") or service),
                    "provider_cost": cost,
                    "sell_price": int(main.hitung_harga_jual_idr(cost)),
                    "stock": stock,
                    "meta": {"pool": item.get("pool"), "operator": item.get("provider_operator") or item.get("operator")},
                })
            rows.sort(key=lambda x: (x["sell_price"], -x["stock"]))
        elif provider == "premotp":
            offers = main.get_premotp_offers(service, country, "regular") or []
            iterable = offers.items() if isinstance(offers, dict) else enumerate(offers)
            for key, item in iterable:
                if not isinstance(item, dict):
                    continue
                offer_id = str(item.get("offer_id") or item.get("id") or item.get("key") or key)
                cost = float(item.get("price") or item.get("price_idr") or item.get("cost_idr") or item.get("amount") or 0)
                stock = int(item.get("stock") or item.get("available") or 1)
                if cost <= 0:
                    continue
                rows.append({
                    "provider": provider,
                    "country": country,
                    "service": service,
                    "provider_cost": cost,
                    "sell_price": int(main.hitung_harga_jual_idr(cost)),
                    "stock": max(stock, 1),
                    "meta": {"offer_id": offer_id, "order_type": "regular", "cost_idr": cost},
                })
            rows.sort(key=lambda x: x["sell_price"])
        else:
            return _json_error("Provider tidak didukung")
        return jsonify({"ok": True, "offers": rows})
    except Exception as exc:
        return _json_error(str(exc), 502)


@webapp_bp.post("/api/webapp/orders")
def api_create_order():
    auth, error = _auth()
    if error:
        return _json_error(error, 401)
    main = _main()
    payload = request.get_json(silent=True) or {}
    provider = str(payload.get("provider") or "").lower()
    country = str(payload.get("country") or "").strip()
    service = str(payload.get("service") or "").strip()
    offer = payload.get("offer") or {}
    if provider not in {"5sim", "rumahotp", "premotp"} or not country or not service:
        return _json_error("Data order tidak lengkap.")

    sell_price = int(offer.get("sell_price") or 0)
    provider_cost = float(offer.get("provider_cost") or 0)
    meta = offer.get("meta") or {}
    if sell_price <= 0:
        return _json_error("Harga order tidak valid.")

    tid = auth["telegram_id"]
    if get_balance(tid) < sell_price:
        return _json_error("Saldo tidak cukup.", 402)

    order_id = "OTP-" + uuid.uuid4().hex[:12].upper()
    try:
        balance_after = create_pending_order(tid, order_id, country, service, sell_price, provider)
        # Keep labels identical to the Telegram flow.
        main._save_order_labels(order_id, service, country)
        result = None
        provider_order_id = None
        phone = None
        expired_at = None

        if provider == "5sim":
            operators = meta.get("operators") or ["any"]
            result = main.buy_number_any_operator(country, main.canonical_5sim_service(service), operators)
            provider_order_id = result.get("id") if result else None
            phone = result.get("phone") if result else None
            expired_at = result.get("expired_at") if result else None
        elif provider == "rumahotp":
            route = meta.get("pool")
            result = main.buy_rumahotp_number(country, service, meta.get("operator") or "any", route)
            provider_order_id = (result.get("order_id") or result.get("id")) if result else None
            phone = (result.get("phone") or result.get("number")) if result else None
            expired_at = result.get("expired_at") if result else None
        else:
            result = main.create_premotp_order(order_id, service, country, str(meta.get("offer_id") or ""), str(meta.get("order_type") or "regular"))
            provider_order_id = str((result or {}).get("id") or "").strip() or None
            phone = (result or {}).get("phone_number") or (result or {}).get("phone") or (result or {}).get("number")
            expired_at = (result or {}).get("expired_at") or (result or {}).get("expires_at") or (result or {}).get("expires")

        if not provider_order_id or not phone:
            refund_order(order_id, f"Pembelian {provider} dari Web App gagal")
            return _json_error("Nomor tidak tersedia. Saldo dikembalikan.", 502)

        if provider == "premotp":
            provider_cost_rp = int(round(provider_cost))
        elif provider == "rumahotp":
            provider_cost_rp = int(round(provider_cost))
        else:
            provider_cost_rp = int(round(provider_cost * float(main.KURS_DOLAR)))
        save_provider_order(order_id, provider_order_id, provider_cost_rp, phone, expired_at)
        row = get_order(order_id)
        return jsonify({"ok": True, "balance": balance_after, "order": _order_json(row)})
    except Exception as exc:
        try:
            refund_order(order_id, f"Web order error: {exc}")
        except Exception:
            pass
        return _json_error(str(exc), 502)


@webapp_bp.post("/api/webapp/orders/<order_id>/resend")
def api_resend(order_id):
    auth, error = _auth()
    if error:
        return _json_error(error, 401)
    row = get_order(order_id)
    if not row or int(row.get("telegram_id")) != auth["telegram_id"]:
        return _json_error("Order tidak ditemukan.", 404)
    main = _main()
    provider = str(row.get("provider") or "").lower()
    if not main._order_has_received_otp(row):
        return _json_error("Resend hanya tersedia setelah OTP pertama diterima.")
    if main._order_expired(row):
        return _json_error("Order sudah expired. Resend OTP ditutup.", 409)
    provider_order_id = row.get("provider_order_id")
    if not provider_order_id:
        return _json_error("Provider order ID tidak ditemukan.")
    try:
        if provider == "premotp":
            result = main.resend_premotp_order(provider_order_id)
        elif provider == "rumahotp":
            result = main.resend_rumahotp_otp(provider_order_id)
        elif provider == "5sim":
            result = main.resend_5sim_otp(provider_order_id)
        else:
            return _json_error("Provider tidak didukung.")
        if isinstance(result, dict) and result.get("response") == "ERROR":
            return _json_error(str(result.get("error") or "Provider menolak resend."), 409)
        changed = mark_order_waiting_for_otp(order_id)
        if not changed:
            return _json_error("Order belum dapat masuk mode menunggu OTP baru.", 409)
        return jsonify({"ok": True, "message": "Permintaan resend dikirim. Menunggu OTP baru..."})
    except Exception as exc:
        return _json_error(str(exc), 502)
