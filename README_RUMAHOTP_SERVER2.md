# Final 2-Server OTP build — Server 2 RumahOTP

- Server 1 = 5SIM.
- Server 2 = RumahOTP.
- Railway variable required: `RUMAHOTP_API_KEY`.
- RumahOTP API uses `x-apikey` against the official v2 services/countries/operators/orders endpoints and v1 status/cancel endpoints.
- Server 2 service resolution uses the real `service_code`/`service_name` mapping, so Google/Gmail/YouTube and Shopee no longer fall through to the first API service.
- Server 2 country selection shows flags, then a dedicated price/server screen. Every RumahOTP pricelist tier is shown, including zero-stock tiers with an explicit unavailable message.
- The displayed Server 2 price is calculated directly from the RumahOTP IDR price with `PROFIT_PERCENT` (default 7%) and rounded to the nearest Rp100, matching the existing reseller pricing rule.
- Selecting a live price tier stores the exact RumahOTP `provider_id`, `server_id`, `number_id`, and operator metadata before purchase, so the order uses the exact price/server the user selected.
