from __future__ import annotations

# Stripe Terminal payment support: connection tokens, PaymentIntent
# creation, and capture.
#
# This module is intentionally the only place that imports the Stripe
# Python SDK and reads STRIPE_SECRET_KEY. Nothing else in the project
# needs to know how the charge actually happens.

import os

import stripe


# Stripe rejected the request or wasn't configured.
class PaymentError(RuntimeError):
    pass


MINIMUM_CHARGE_CENTS = 50  # Stripe's own minimum for a USD charge


def _client() -> None:
    # Points the stripe module at our secret key. Called before every
    # Stripe API call rather than once at import time, so a missing key
    # produces a clear error on first use instead of an import-time crash.
    secret_key = os.getenv("STRIPE_SECRET_KEY")
    if not secret_key:
        raise PaymentError(
            "STRIPE_SECRET_KEY is not set. Copy .env.example to .env and add your test key."
        )
    stripe.api_key = secret_key


def create_connection_token() -> str:
    # Returns a short-lived token the browser's Terminal SDK needs to
    # connect to a card reader. Must come from the server because it
    # requires the secret key.
    _client()
    try:
        token = stripe.terminal.ConnectionToken.create()
    except stripe.error.StripeError as exc:
        raise PaymentError(str(exc)) from exc
    return token.secret


def create_payment_intent(amount_cents: int, receipt_email: str | None = None) -> dict[str, object]:
    # Opens a charge for the given amount (in cents). Uses manual
    # capture, so no money moves until capture_payment_intent() is
    # called after the reader confirms the card was read.
    #
    # If receipt_email is given, Stripe automatically emails an official
    # payment receipt once the charge succeeds - separate from (and in
    # addition to) the printed sticker/receipt mockup.
    if not isinstance(amount_cents, int) or amount_cents < MINIMUM_CHARGE_CENTS:
        raise PaymentError(
            f"amount must be a whole number of cents, at least {MINIMUM_CHARGE_CENTS}"
        )

    _client()
    try:
        params = {
            "amount": amount_cents,
            "currency": "usd",
            "payment_method_types": ["card_present"],
            "capture_method": "manual",
        }
        if receipt_email:
            params["receipt_email"] = receipt_email

        intent = stripe.PaymentIntent.create(**params)
    except stripe.error.StripeError as exc:
        raise PaymentError(str(exc)) from exc

    return {
        "client_secret": intent.client_secret,
        "id": intent.id,
        "amount": amount_cents,
    }


def capture_payment_intent(payment_intent_id: str) -> dict[str, object]:
    # Actually takes the money, after the reader has confirmed the
    # card was read successfully.
    _client()
    try:
        intent = stripe.PaymentIntent.capture(payment_intent_id)
    except stripe.error.StripeError as exc:
        raise PaymentError(str(exc)) from exc

    return {"id": intent.id, "status": intent.status}
