# stripe terminal payment backend
# only file that touches the stripe SDK and our secret key

from __future__ import annotations  # runs type hints when needed instead of when program is opened

import os

import stripe


class PaymentError(RuntimeError):  # stripe rejects transaction or we're not set up right
    pass


MINIMUM_CHARGE_CENTS = 50  # minimum charge for stripe


def _client() -> None:
    secret_key = os.getenv("STRIPE_SECRET_KEY")  # grabs the key from .env
    if not secret_key:
        raise PaymentError(
            "STRIPE_SECRET_KEY is not set. Copy .env.example to .env and add your test key."
        )
    stripe.api_key = secret_key


def create_connection_token() -> str:  # gives the SDK a token so it can connect to the reader
    _client()
    try:
        token = stripe.terminal.ConnectionToken.create()
    except stripe.error.StripeError as exc:
        raise PaymentError(str(exc)) from exc
    return token.secret


def create_payment_intent(amount_cents: int, receipt_email: str | None = None) -> dict[str, object]:  # opens a charge
    if not isinstance(amount_cents, int) or amount_cents < MINIMUM_CHARGE_CENTS:
        raise PaymentError(
            f"amount must be a whole number of cents, at least {MINIMUM_CHARGE_CENTS}"
        )

    _client()
    try:
        params = {  # sets the parameters for stripe
            "amount": amount_cents,
            "currency": "usd",
            "payment_method_types": ["card_present"],
            "capture_method": "manual",
        }
        if receipt_email:
            params["receipt_email"] = receipt_email  # stripe emails a receipt if we give it one
        intent = stripe.PaymentIntent.create(**params)
    except stripe.error.StripeError as exc:
        raise PaymentError(str(exc)) from exc

    return {
        "client_secret": intent.client_secret,
        "id": intent.id,
        "amount": amount_cents,
    }


def capture_payment_intent(payment_intent_id: str) -> dict[str, object]:  # takes the money once transactions approved
    _client()
    try:
        intent = stripe.PaymentIntent.capture(payment_intent_id)
    except stripe.error.StripeError as exc:
        raise PaymentError(str(exc)) from exc

    return {"id": intent.id, "status": intent.status}
