# stripe terminal payment backend
# only file that touches the stripe SDK and our secret key

from __future__ import annotations  # runs type hints when needed instead of when program is opened

import os

# [STRIPE'S SDK] - the official Stripe Python package (pip install stripe)
import stripe


# [OUR OWN DESIGN] - Stripe doesn't define this class; a custom exception
# type is just a normal Python pattern for telling our own errors apart
class PaymentError(RuntimeError):  # stripe rejects transaction or we're not set up right
    pass


MINIMUM_CHARGE_CENTS = 50  # [STRIPE'S RULE] minimum charge for stripe ($0.50 USD, not ours)


# [OUR OWN DESIGN] - Stripe's docs just show stripe.api_key = "..." set once
# at the top of a file; wrapping it in a function that runs before every
# call (instead of once at import time) was our choice, so a missing key
# fails with a clear message on first use instead of crashing on import
def _client() -> None:
    secret_key = os.getenv("STRIPE_SECRET_KEY")  # grabs the key from .env
    if not secret_key:
        raise PaymentError(
            "STRIPE_SECRET_KEY is not set. Copy .env.example to .env and add your test key."
        )
    stripe.api_key = secret_key  # [STRIPE'S SDK] - this is exactly how Stripe's docs say to set the key


# [STRIPE'S SDK] - stripe.terminal.ConnectionToken.create() is Stripe's
# documented call, straight out of their Terminal integration docs
def create_connection_token() -> str:  # gives the SDK a token so it can connect to the reader
    _client()
    try:
        token = stripe.terminal.ConnectionToken.create()
    except stripe.error.StripeError as exc:
        raise PaymentError(str(exc)) from exc
    return token.secret


def create_payment_intent(amount_cents: int, receipt_email: str | None = None) -> dict[str, object]:  # opens a charge
    # [OUR OWN DESIGN] - this validation check isn't from Stripe; added so
    # a bad amount fails with our own clear message instead of Stripe's
    if not isinstance(amount_cents, int) or amount_cents < MINIMUM_CHARGE_CENTS:
        raise PaymentError(
            f"amount must be a whole number of cents, at least {MINIMUM_CHARGE_CENTS}"
        )

    _client()
    try:
        # [STRIPE'S SDK] - stripe.PaymentIntent.create() with these exact
        # parameter names is Stripe's documented call. payment_method_types:
        # ["card_present"] and capture_method: "manual" specifically are
        # Stripe's documented combination for a Terminal/card-present flow
        params = {  # sets the parameters for stripe
            "amount": amount_cents,
            "currency": "usd",
            "payment_method_types": ["card_present"],
            "capture_method": "manual",
        }
        if receipt_email:
            # [STRIPE'S SDK] - receipt_email is a documented PaymentIntent
            # field; only adding it when we actually have one is our own choice
            params["receipt_email"] = receipt_email  # stripe emails a receipt if we give it one
        intent = stripe.PaymentIntent.create(**params)
    except stripe.error.StripeError as exc:
        raise PaymentError(str(exc)) from exc

    # [OUR OWN DESIGN] - this return shape (a plain dict with just these
    # three fields) is ours; Stripe's intent object has many more fields
    # we don't bother returning to the frontend
    return {
        "client_secret": intent.client_secret,
        "id": intent.id,
        "amount": amount_cents,
    }


# [STRIPE'S SDK] - stripe.PaymentIntent.capture() is Stripe's documented
# call for finishing a manual-capture charge
def capture_payment_intent(payment_intent_id: str) -> dict[str, object]:  # takes the money once transactions approved
    _client()
    try:
        intent = stripe.PaymentIntent.capture(payment_intent_id)
    except stripe.error.StripeError as exc:
        raise PaymentError(str(exc)) from exc

    return {"id": intent.id, "status": intent.status}
