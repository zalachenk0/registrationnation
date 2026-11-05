# ============================================================================
# File:         reader_test_CG.py
# Written by:   CG (Cristian Gonzalez)
# What it does: Command line tool that checks the Stripe card reader and sends a
#               test payment to it, without the kiosk screen.
# Team-written: All logic written by CG (Cristian Gonzalez), except the lines
#               tagged [3RD PARTY].
# Third-party:  Stripe Python library; python-dotenv; Stripe API (Terminal
#               readers and PaymentIntents, stripe.com/docs/terminal).
# ============================================================================
"""Checks the Stripe card reader (S700) without the kiosk screen, so a problem can be pinned on the reader or on the kiosk.

Run from this folder (the same folder as .env):
  python3 reader_test_CG.py check                shows your Stripe setup, locations and readers, and what the kiosk will use
  python3 reader_test_CG.py register CODE        registers a new reader using the code shown on its screen
  python3 reader_test_CG.py pay [DOLLARS]        sends a test payment to the reader, then you tap the Stripe TEST card on it

It talks to Stripe from this computer only. The reader receives the payment from Stripe over the internet, so it does not
need to be on the same network as this computer (the kiosk screen's own reader connection does, see READER_TEST_TODAY.md).
Test mode only: it refuses to run with a live key, and the payment is captured in test mode so no real money can move.
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

import stripe

ROOT = Path(__file__).resolve().parent
POLL_SECONDS = 2  # how often to ask stripe what the reader is doing
WAIT_SECONDS = 120  # how long to wait for someone to tap the card before giving up


class ToolError(Exception):
    """A problem to explain to the person at the keyboard, in plain words."""


def setup() -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    if not key:
        raise ToolError("STRIPE_SECRET_KEY is not set. Put your Stripe TEST secret key in .env (Dashboard > Developers > API keys, with Test mode on).")
    if not key.startswith(("sk_test_", "rk_test_")):
        raise ToolError("This tool only works with a Stripe TEST key (it starts with sk_test_). Turn on Test mode in the Dashboard and use that key.")
    stripe.api_key = key


def configured_location() -> str:
    return os.getenv("STRIPE_LOCATION_ID", "").strip()


def simulated_is_on() -> bool:
    return os.getenv("STRIPE_SIMULATED", "").strip().lower() in {"1", "true", "yes", "on"}


def say(text: str = "") -> None:
    print(text)


def dollars(cents: int) -> str:
    return f"${cents / 100:,.2f}"


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------

def check() -> int:
    setup()
    say("\nStripe account: TEST mode key found.")
    location_id = configured_location()
    say("\nWhat the kiosk is set to do (from .env):")
    if simulated_is_on():
        say("  STRIPE_SIMULATED is ON, so the kiosk uses Stripe's FAKE reader and ignores STRIPE_LOCATION_ID.")
        say("  Set STRIPE_SIMULATED=0 (or delete the line) to use the real S700.")
    elif location_id:
        say(f"  The kiosk will look for a real reader at location {location_id}.")
    else:
        say("  Neither STRIPE_SIMULATED nor STRIPE_LOCATION_ID is set, so the kiosk uses the typed-card form only.")

    locations = stripe.terminal.Location.list(limit=20).data  # [3RD PARTY: Stripe API]
    say("\nLocations on this Stripe account (Dashboard > Terminal > Locations):")
    if not locations:
        say("  none. Create one in the Dashboard first, then put its id (tml_...) in STRIPE_LOCATION_ID.")
    for location in locations:
        mark = "   <-- the kiosk uses this one" if location.id == location_id else ""
        say(f"  {location.id}   {location.display_name}{mark}")
    if location_id and location_id not in [location.id for location in locations]:
        say(f"\n  PROBLEM: STRIPE_LOCATION_ID is {location_id}, but this account has no such location. Check for a typo, or a key from a different account.")

    readers = stripe.terminal.Reader.list(limit=20).data  # [3RD PARTY: Stripe API]
    say("\nReaders on this Stripe account (Dashboard > Terminal > Readers):")
    if not readers:
        say("  none yet. Register the S700 with:  python3 reader_test_CG.py register CODE")
    online_here = []
    for reader in readers:
        here = reader.location == location_id if location_id else False
        if reader.status == "online" and here:
            online_here.append(reader)
        label = reader.label or "(no name)"
        say(f"  {reader.id}   {label:<22} {reader.device_type:<22} {reader.status:<8} ip {reader.ip_address or '?':<15}"
            + ("   <-- at the kiosk's location" if here else ""))
    say()
    if online_here:
        say(f"READY: {len(online_here)} reader(s) online at the kiosk's location. Next:  python3 reader_test_CG.py pay")
        return 0
    if readers and location_id:
        say("NOT READY: no reader is both ONLINE and at STRIPE_LOCATION_ID.")
        say("  Offline: check the reader's power and Wi-Fi/Ethernet, and give it a minute after it starts.")
        say("  Wrong location: re-register it under the right location, or change STRIPE_LOCATION_ID.")
    return 1


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------

def register(code: str, label: str) -> int:
    setup()
    location_id = configured_location()
    if not location_id:
        raise ToolError("Set STRIPE_LOCATION_ID in .env first (Dashboard > Terminal > Locations), so Stripe knows where this reader lives.")
    try:
        reader = stripe.terminal.Reader.create(registration_code=code.strip(), label=label, location=location_id)  # [3RD PARTY: Stripe API]
    except stripe.StripeError as exc:
        raise ToolError(f"Stripe would not register the reader: {exc.user_message or exc}\n"
                        "  Check the code is the one currently on the reader's screen (they expire), and that the reader is online.") from exc
    say(f"\nRegistered: {reader.id} ({reader.label}) at {location_id}. Status: {reader.status}.")
    say("Run  python3 reader_test_CG.py check  to confirm it shows as online.\n")
    return 0


# ---------------------------------------------------------------------------
# pay
# ---------------------------------------------------------------------------

def pick_reader() -> stripe.terminal.Reader:  # [3RD PARTY: Stripe API]
    location_id = configured_location()
    if not location_id:
        raise ToolError("Set STRIPE_LOCATION_ID in .env first. Run  python3 reader_test_CG.py check  to see your locations.")
    readers = stripe.terminal.Reader.list(location=location_id, limit=20).data  # [3RD PARTY: Stripe API]
    online = [reader for reader in readers if reader.status == "online"]
    if not online:
        raise ToolError(f"No reader is online at {location_id}. Run  python3 reader_test_CG.py check  to see what Stripe can see.")
    return online[0]


def card_description(intent_id: str) -> str:
    try:
        intent = stripe.PaymentIntent.retrieve(intent_id, expand=["latest_charge"])  # [3RD PARTY: Stripe API]
        card = intent.latest_charge.payment_method_details.card_present
        method = {"contact_emv": "chip (inserted)", "contactless_emv": "contactless (tapped)",
                  "magnetic_stripe_track2": "magnetic stripe (swiped)"}.get(card.read_method, card.read_method or "unknown method")
        return f"{(card.brand or 'card').title()} ending {card.last4}, read as {method}"
    except Exception:  # the details are a nice extra, never a reason to call a good payment a failure
        return "card details not available"


def wait_for_card(reader_id: str, intent_id: str):
    deadline = time.time() + WAIT_SECONDS
    while time.time() < deadline:
        action = stripe.terminal.Reader.retrieve(reader_id).get("action") or {}  # [3RD PARTY: Stripe API]
        if action.get("status") == "failed":
            return "failed", action.get("failure_message") or action.get("failure_code") or "the reader reported a failure"
        status = stripe.PaymentIntent.retrieve(intent_id).status  # [3RD PARTY: Stripe API]
        if status == "requires_capture":
            return "ok", ""
        if status in ("canceled", "succeeded"):
            return status, ""
        print(".", end="", flush=True)
        time.sleep(POLL_SECONDS)
    return "timeout", f"no card was read within {WAIT_SECONDS} seconds"


def pay(amount: str) -> int:
    setup()
    try:
        cents = round(float(amount) * 100)
    except ValueError as exc:
        raise ToolError(f"'{amount}' is not an amount. Example:  python3 reader_test_CG.py pay 1.00") from exc
    if not 50 <= cents <= 100_000:
        raise ToolError("Use an amount between $0.50 (Stripe's minimum) and $1,000.00 for a test.")
    reader = pick_reader()
    say(f"\nUsing reader {reader.id} ({reader.label or 'no name'}).")
    intent = stripe.PaymentIntent.create(  # [3RD PARTY: Stripe API]
        amount=cents, currency="usd", payment_method_types=["card_present"], capture_method="manual",
        description="Registration Nation reader test", metadata={"kiosk_test": "reader_test_CG.py"},
        idempotency_key="reader-test-" + uuid.uuid4().hex,
    )
    try:
        stripe.terminal.Reader.retrieve(reader.id).process_payment_intent(payment_intent=intent.id)  # [3RD PARTY: Stripe API]
    except stripe.StripeError as exc:
        stripe.PaymentIntent.cancel(intent.id)  # [3RD PARTY: Stripe API]
        raise ToolError(f"Stripe could not send the payment to the reader: {exc.user_message or exc}\n"
                        "  If it says the reader is busy, wait a few seconds, or touch the reader's screen to cancel what it is showing.") from exc
    say(f"Look at the reader: it should now ask for a payment of {dollars(cents)}.")
    say("Tap, insert or swipe the Stripe TEST card on the reader now (Ctrl+C cancels).")
    print("Waiting", end="", flush=True)
    try:
        outcome, detail = wait_for_card(reader.id, intent.id)
    except KeyboardInterrupt:
        outcome, detail = "cancelled", "you pressed Ctrl+C"
    say()
    if outcome != "ok":
        try:
            stripe.terminal.Reader.retrieve(reader.id).cancel_action()  # [3RD PARTY: Stripe API]
        except stripe.StripeError:
            pass
        try:
            stripe.PaymentIntent.cancel(intent.id)  # [3RD PARTY: Stripe API]
        except stripe.StripeError:
            pass
        say(f"NOT PAID ({outcome}): {detail}.")
        say("The reader's screen has been cleared and the test payment was cancelled, so nothing was charged.\n")
        return 1
    say(f"The reader accepted the card: {card_description(intent.id)}.")
    stripe.PaymentIntent.capture(intent.id)  # [3RD PARTY: Stripe API]
    say(f"PAID in test mode: {dollars(cents)}, payment {intent.id}. Find it in Dashboard > Payments (with Test mode on).")
    say("The S700 and the test card both work. Next, try the real kiosk screen.\n")
    return 0


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else ""
    try:
        if command == "check":
            return check()
        if command == "register" and len(argv) >= 3:
            return register(argv[2], " ".join(argv[3:]) or "Kiosk reader")
        if command == "pay":
            return pay(argv[2] if len(argv) > 2 else "1.00")
    except ToolError as exc:
        say(f"\nProblem: {exc}\n")
        return 1
    except stripe.StripeError as exc:
        say(f"\nStripe said: {exc.user_message or exc}\n")
        return 1
    say(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
