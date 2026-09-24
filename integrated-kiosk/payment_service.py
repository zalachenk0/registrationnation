from __future__ import annotations # makes it so that script runs commands when script gets there rather than instantly

# stripe Terminal payment backend 
# imports the Stripe Python SDK and reads STRIPE_SECRET_KEY.

import os

import stripe

class PaymentError(RuntimeError): # if stripe rejects the request or if it wasn't configured
    pass


MINIMUM_CHARGE_CENTS = 50  # stripe's minimum for a charge


def _client() -> None:
    secret_key = os.getenv("STRIPE_SECRET_KEY")     # points the stripe module to our secret key
    if not secret_key:                             
        raise PaymentError(
            "STRIPE_SECRET_KEY is not set. Copy .env.example to .env and add your test key."
        )
    stripe.api_key = secret_key


def create_connection_token() -> str: # creates temporary token to connect card reader to SDK
    _client()
    try:
        token = stripe.terminal.ConnectionToken.create()
    except stripe.error.StripeError as exc:
        raise PaymentError(str(exc)) from exc
    return token.secret


def create_payment_intent(amount_cents: int, receipt_email: str | None = None) -> dict[str, object]:     # Opens a charge for the given amount (in cents).
    if not isinstance(amount_cents, int) or amount_cents < MINIMUM_CHARGE_CENTS:
        raise PaymentError(
            f"amount must be a whole number of cents, at least {MINIMUM_CHARGE_CENTS}"
        )

    _client()
    try:
        params = {                                    # sets parameters for stripe
            "amount": amount_cents,
            "currency": "usd",
            "payment_method_types": ["card_present"],
            "capture_method": "manual",
        }
        if receipt_email:
            params["receipt_email"] = receipt_email    # if receipt_email is passed from reg form stripe will automatically
        intent = stripe.PaymentIntent.create(**params) # emails a separate receipt when payment is processed
    except stripe.error.StripeError as exc:
        raise PaymentError(str(exc)) from exc

    return {
        "client_secret": intent.client_secret,
        "id": intent.id,
        "amount": amount_cents,
    }


def capture_payment_intent(payment_intent_id: str) -> dict[str, object]: # takes the money after the reader confirms the card was read successfully
    _client()
    try:
        intent = stripe.PaymentIntent.capture(payment_intent_id)
    except stripe.error.StripeError as exc:
        raise PaymentError(str(exc)) from exc

    return {"id": intent.id, "status": intent.status}
