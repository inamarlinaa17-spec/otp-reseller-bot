# User Refund + Order History Update

- User-facing refund flow now shows a short countdown before cancellation is requested.
- User balance is NOT refunded unless the upstream order cancellation is confirmed.
- If the provider order ID is missing, the bot refuses to refund locally and asks the user to retry later.
- Provider names are hidden from user-facing refund error messages.
- Order success details show service, country, price, transaction time, and first-order expiration time.
- Order history stores and displays phone number and OTP code, plus transaction/expiration times.
- Original expiration is preserved; resend does not overwrite the first-order expiration.
- RumahOTP cancellation verification is limited to three status checks after the cancel request to respect its documented API rate limit.
