# AZHURA Server 4 — NomorOTP

Server 4 is added without replacing or changing the existing Server 1 (5SIM), Server 2 (RumahOTP), or Server 3 (PremOTP) flow.

## Railway Variables

Add the same variables to **both** the Railway service running the Telegram bot and the Railway service running `Azhura_web`:

```text
NOMOROTP_API_KEY=YOUR_NOMOROTP_API_KEY
NOMOROTP_BASE_URL=https://api.nomorotp.id/
NOMOROTP_PLUS_SERVER=plus
NOMOROTP_EXPRESS_SERVER=sh
```

`NOMOROTP_EXPRESS_SERVER=sh` follows the currently public NomorOTP API documentation, where `sh` is the documented SMSHub provider code. The AZHURA UI labels that route **Server Express**. If NomorOTP gives your account a different API server code for its Express route, change only this Railway variable; no source-code change is needed.

## Flow

### Telegram

`Order OTP` →

```text
Server 1        Server 3
Server 2        Server 4 — NomorOTP
```

Server 4 →

```text
🐷 Server Plus
⚡ Server Express
```

Then:

`Layanan → Negara → Operator/Stock/Harga → Order`

Popular services are placed first and Indonesia is placed first in the country list.

### Web AZHURA

The same 2×2 server arrangement is used. Server 4 opens Plus/Express, then live service → country → operator/stock/price.

## Provider API flow

The adapter uses the documented NomorOTP SMS-Activate-compatible actions:

- `getServices`
- `getCountries`
- `getAvailability`
- `getNumber`
- `getStatus`
- `setStatus` status `3` for resend
- `setStatus` status `6` for finish
- `cancelActivation` for cancellation/refund

The API key is kept server-side and is never exposed to the web browser.
