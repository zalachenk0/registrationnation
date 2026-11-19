# ============================================================================
# File:         payment_service_CG.py
# Written by:   CG (Cristian Gonzalez)
# What it does: Creates, captures and verifies Stripe payments on the S700 card
#               reader, with a local ledger that prevents double charges. Also
#               runs the reader the server-driven way (Stripe carries the
#               payment to the reader, so no local network link is needed).
# Team-written: All logic written by CG (Cristian Gonzalez), except the lines
#               tagged [3RD PARTY].
# Third-party:  Stripe Python library; Stripe API (stripe.com/docs); SQLite
#               (Python standard library).
# ============================================================================
"""Adapted from registrationnation-main/integrated-kiosk/payment_service.py.

Stripe Payment Element sandbox integration. Persist references, not card/document data.
The local kiosk's inherited front-end login is not production authentication.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time

import stripe
from printing_BV import validate

AMOUNT_CENTS = 11425
# [CG] the S700 card reader is the kiosk's only way to pay. The kiosk has no keyboard for typing a card, so new payments
# are always card_present. 'card' stays accepted here only so a typed-card payment from before this change still
# matches the ledger (a reprint of an old receipt), never so a new one can be made
METHODS = {'card', 'card_present'}
# nothing has been authorized in these states, so a payment sitting in one of them can safely be replaced
UNPAID = {'requires_payment_method', 'requires_confirmation', 'requires_action'}


class PaymentError(RuntimeError):
    pass


_HTTP = None  # [CG] the one connection to Stripe this server keeps open (see _client)


def _client():
    global _HTTP
    key = os.getenv('STRIPE_SECRET_KEY', '').strip()
    if not key or 'xxxxx' in key or 'REPLACE' in key:
        raise PaymentError('Add your Stripe test secret key to .env, then restart the kiosk.')
    if not key.startswith('sk_test_'):
        raise PaymentError('This local kiosk supports Stripe test keys only. Live payments require a production deployment.')
    stripe.api_key = key  # [3RD PARTY: Stripe API]
    stripe.max_network_retries = 1
    # [CG] made once and kept, so each server thread reuses its open connection to Stripe. A new client on every call
    # (as before) meant a fresh DNS lookup and secure handshake for every Pay press and every once-a-second status check
    if _HTTP is None:
        _HTTP = stripe.RequestsClient(timeout=20)
    if stripe.default_http_client is not _HTTP:
        stripe.default_http_client = _HTTP


# a simple sanity check, not a full email validator. Anything that fails it is just not used for a receipt
EMAIL = re.compile(r'[^@\s,;<>()\[\]\\"]{1,64}@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')


def clean_email(value):
    email = value.strip() if isinstance(value, str) else ''
    return email if len(email) <= 254 and EMAIL.fullmatch(email) else None


# S700 reader settings from .env. This never raises, so a typo here shows up as a message on the card reader step.
# [CG] only the physical reader is used: STRIPE_SIMULATED is ignored, and 'simulated' is always False. The key stays in
# the answer because today's compiled kiosk form (dist/) reads it
def terminal_settings():
    location = os.getenv('STRIPE_LOCATION_ID', '').strip()
    if location.startswith('tml_'):
        return dict(enabled=True, simulated=False, location=location, problem='')
    problem = ('STRIPE_LOCATION_ID should look like tml_... (Stripe Dashboard > Terminal > Locations).' if location else
               'The card reader is not set up: add STRIPE_LOCATION_ID=tml_... to .env (Stripe Dashboard > Terminal > Locations).')
    return dict(enabled=False, simulated=False, location=None, problem=problem)


def configuration():
    """What the kiosk form needs to show the payment step. [CG] Never raises: the card reader is the only way to pay, so
    any setup problem (keys, location, reader id) is shown on the card reader step instead of breaking the page.
    terminal.enabled is always True so the form always opens the card reader step; serverReader says whether it can work."""
    reader = server_reader_settings()
    try:
        _client()
    except PaymentError as exc:
        reader = dict(enabled=False, simulated=False, problem=str(exc))
    location = os.getenv('STRIPE_LOCATION_ID', '').strip()
    return dict(amount=AMOUNT_CENTS, currency='usd', testMode=True, serverReader=reader,
                terminal=dict(enabled=True, simulated=False, location=location if location.startswith('tml_') else None,
                              problem=reader['problem']))


# ---- [CG] server-driven reader: the kiosk asks Stripe to put the payment on the reader, and Stripe talks to the reader over
# the internet. The kiosk and the reader never talk to each other directly, so the local DNS answers and the device-to-device
# traffic that campus Wi-Fi tends to block stop mattering. Approach follows Stripe's server-driven Terminal guide
# (docs.stripe.com/terminal/payments/setup-integration?terminal-sdk-platform=server-driven) ----
_readers = {}  # reader picked for each set of settings, so every status check doesn't list the readers again


def server_reader_settings():
    """Same never-raises promise as terminal_settings()."""
    reader = terminal_settings()
    wanted = os.getenv('STRIPE_READER_ID', '').strip()
    if not reader['enabled']:
        return dict(enabled=False, simulated=False, problem=reader['problem'])
    if wanted and not wanted.startswith('tmr_'):
        return dict(enabled=False, simulated=False,
                    problem='STRIPE_READER_ID should look like tmr_... (Stripe Dashboard > Terminal > Readers), or leave it out.')
    return dict(enabled=True, simulated=False, problem='')


def _is_simulated(reader):
    return str(reader.get('device_type') or '').startswith('simulated')


def _location_of(reader):
    place = reader.get('location')
    return place.get('id') if isinstance(place, dict) else place


def find_reader():
    """The reader id this kiosk charges on. Refuses to guess: a payment must never pop up on some other kiosk's reader.
    [CG] Only a physical reader is ever used. Stripe's simulated reader is skipped, even when one is still registered
    at the same location from earlier testing."""
    _client()
    settings = server_reader_settings()
    if not settings['enabled']:
        raise PaymentError(settings['problem'] or 'The card reader is not set up on this kiosk.')
    location = os.getenv('STRIPE_LOCATION_ID', '').strip()
    wanted = os.getenv('STRIPE_READER_ID', '').strip()
    key = (location, wanted)
    if key in _readers:
        return _readers[key]
    try:
        if wanted:
            reader = stripe.terminal.Reader.retrieve(wanted)  # [3RD PARTY: Stripe API]
            if _location_of(reader) != location:
                raise PaymentError('STRIPE_READER_ID is registered to a different location than STRIPE_LOCATION_ID. Fix .env.')
            if _is_simulated(reader):
                raise PaymentError('STRIPE_READER_ID is Stripe\'s simulated reader. This kiosk only uses the physical S700: '
                                   'put its tmr_... id in .env, or leave STRIPE_READER_ID out.')
        else:
            found = [r for r in stripe.terminal.Reader.list(location=location, limit=100).data  # [3RD PARTY: Stripe API]
                     if not _is_simulated(r)]
            if not found:
                raise PaymentError('No physical card reader is registered at this location. Run "reader_test_CG.py check" to see what Stripe has.')
            elif len(found) == 1:
                reader = found[0]
            else:
                online = [r for r in found if r.get('status') == 'online']
                if len(online) != 1:
                    raise PaymentError('More than one card reader is registered at this location. Put the one for this kiosk '
                                       'in .env as STRIPE_READER_ID=tmr_...')
                reader = online[0]
    except stripe.StripeError as exc:
        raise PaymentError('Stripe could not look up the card reader. Check the internet connection and retry.') from exc
    _readers[key] = reader.id
    return reader.id


def reader_working_on(intent_id):
    """True only when the server-driven reader is busy with this exact payment. Never raises: if Stripe can't be asked,
    the answer is False and the older behavior applies."""
    try:
        if not server_reader_settings()['enabled']:
            return False
        action = _action_for(stripe.terminal.Reader.retrieve(find_reader()), intent_id)  # [3RD PARTY: Stripe API]
        return bool(action and action.get('status') == 'in_progress')
    except Exception:
        return False


def _reader_problem(exc):
    """Turns Stripe's reader error codes into something a customer or operator can act on. Nothing is charged by a failed send."""
    code = getattr(exc, 'code', None) or ''
    if code == 'terminal_reader_busy':
        return 'The card reader is busy with something else. Touch Cancel on the reader (or press Cancel here), then press Pay again.'
    if code == 'terminal_reader_offline':
        return 'The card reader is offline. Check that it is on and connected to the internet, then press Pay again.'
    if code == 'terminal_reader_timeout':
        return 'The card reader did not answer in time. Press Pay again; this same payment is checked first, so it cannot charge twice.'
    return 'Stripe could not send the payment to the card reader. Press Pay again; nothing has been charged.'


def intent_only(payload):
    """True for {'payment_intent': 'pi_...'}. Today's compiled kiosk form only holds the payment's secret at the card step,
    not the form, so dist/stripe-terminal-server_CG.js asks by payment id instead"""
    return isinstance(payload, dict) and set(payload) == {'payment_intent'}


def _action_for(reader, intent_id):
    """The reader's current job, but only if that job is this payment. Anything else on the reader is not ours to read or touch."""
    action = reader.get('action') or {}
    if action.get('type') != 'process_payment_intent':
        return None
    target = (action.get('process_payment_intent') or {}).get('payment_intent')
    target = target.get('id') if isinstance(target, dict) else target
    return action if target == intent_id else None


# lets the browser's Terminal SDK connect to the S700. Only issued when a reader is set up in .env
def create_connection_token():
    _client()
    if not terminal_settings()['enabled']:
        raise PaymentError(terminal_settings()['problem'] or 'The card reader is not set up on this kiosk.')
    try:
        return stripe.terminal.ConnectionToken.create().secret  # [3RD PARTY: Stripe API]
    except stripe.StripeError as exc:
        raise PaymentError('Stripe could not start a card reader connection. Check the internet connection and retry.') from exc


class PaymentService:
    def __init__(self, database=None):
        self.database = Path(database or os.getenv('KIOSK_PAYMENT_DB') or
                             Path(__file__).parent / 'runtime/payments.sqlite3')
        self.lock = threading.RLock()

    def _db(self):
        self.database.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.database, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE IF NOT EXISTS payments (reference TEXT PRIMARY KEY, '
                   'fingerprint TEXT NOT NULL, intent_id TEXT, created REAL NOT NULL)')
        return db

    def _identity(self, payload):
        data = validate(payload)
        fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        return data['confirmation_number'], fingerprint

    def _check(self, intent, reference, fingerprint):
        methods = set(intent.payment_method_types or [])
        if (intent.amount != AMOUNT_CENTS or intent.currency != 'usd' or intent.livemode or
                intent.metadata.get('kiosk_reference') != reference or
                intent.metadata.get('kiosk_fingerprint') != fingerprint or
                not methods or not methods <= METHODS):
            raise PaymentError('Payment does not match this kiosk transaction. Ask the operator to review it.')
        return intent

    def _retrieve(self, row):
        if not row or not row['intent_id']:
            raise PaymentError('No payment exists for this transaction. Use Pay before printing.')
        intent = stripe.PaymentIntent.retrieve(row['intent_id'])  # [3RD PARTY: Stripe API]
        return self._check(intent, row['reference'], row['fingerprint'])

    # Stripe emails its receipt when a payment is captured, to the email that is on the payment at that moment.
    # Setting it here, not when the payment is created, means the email the customer ended up with is the one used,
    # and a changed email can never clash with Stripe's duplicate-request protection.
    # Best effort on purpose: a receipt problem must never stop a payment that was approved
    @staticmethod
    def _set_receipt_email(intent, payload):
        email = clean_email(payload.get('receipt_email')) if isinstance(payload, dict) else None
        if not email or intent.get('receipt_email') == email:
            return
        try:
            stripe.PaymentIntent.modify(intent.id, receipt_email=email,  # [3RD PARTY: Stripe API]
                                        idempotency_key='kiosk-receipt-' + intent.id + '-' + hashlib.sha256(email.encode()).hexdigest()[:12])
        except stripe.StripeError:
            pass

    @staticmethod
    def _method(intent):
        return 'terminal' if 'card_present' in (intent.payment_method_types or []) else 'card'

    @staticmethod
    def _create_key(mode, reference, replaced):
        # a replacement gets its own key, otherwise Stripe would hand back the old, canceled payment
        key = ('kiosk-terminal-create-' if mode == 'terminal' else 'kiosk-web-card-create-') + reference
        return key + '-after-' + replaced if replaced else key

    @staticmethod
    def _result(intent):
        return dict(id=intent.id, client_secret=intent.client_secret, status=intent.status,
                    amount=intent.amount, testMode=not intent.livemode, method=PaymentService._method(intent),
                    receiptEmail=intent.get('receipt_email'),
                    paymentIntent=dict(intent) if intent.status == 'requires_confirmation' else None)

    def create_payment_intent(self, payload, mode='terminal'):
        reference, fingerprint = self._identity(payload)
        # [CG] card reader only. A typed card can't be entered without a keyboard, so it's refused here and not just
        # hidden on the form: nothing can start one by mistake
        if mode != 'terminal':
            raise PaymentError('Typed card payments are turned off on this kiosk. Pay with the card reader.')
        _client()
        settings = terminal_settings()
        if not settings['enabled']:
            raise PaymentError(settings['problem'] or 'The card reader is not set up on this kiosk.')
        with self.lock:
            db = self._db()
            try:
                with db:
                    db.execute('INSERT OR IGNORE INTO payments VALUES (?, ?, NULL, ?)',
                               (reference, fingerprint, time.time()))
                row = db.execute('SELECT * FROM payments WHERE reference=?', (reference,)).fetchone()
                if row['fingerprint'] != fingerprint:
                    raise PaymentError('This payment is tied to the original vehicle and registrant. Restore those fields before retrying.')
                replaced = None
                if row['intent_id']:
                    current = self._retrieve(row)
                    if current.status != 'canceled':
                        # a reader payment where the card was read but never approved can't be resumed from the browser
                        # (the Terminal SDK has no way to pick it back up). Nothing was authorized, so start it fresh
                        stalled_reader = mode == 'terminal' and current.status == 'requires_confirmation'
                        # a payment on the method that was asked for, or one that already moved money, is simply resumed
                        if (self._method(current) == mode and not stalled_reader) or current.status not in UNPAID:
                            return self._result(current)
                        # [CG] ...and so is one the server-driven reader is in the middle of. Canceling it mid-approval could
                        # leave the customer's card held for a payment the kiosk then throws away
                        if self._method(current) == 'terminal' and reader_working_on(current.id):
                            return self._result(current)
                        # the other method was started but nothing was authorized, so it is safe to replace
                        stripe.PaymentIntent.cancel(current.id, idempotency_key='kiosk-cancel-' + current.id)  # [3RD PARTY: Stripe API]
                    replaced = current.id
                # Stripe may prune idempotency keys after 24h. Never recreate an
                # unresolved old request and risk a second charge.
                if time.time() - row['created'] > 23 * 3600:
                    raise PaymentError('An older payment request needs operator review in Stripe. Do not start another charge.')
                intent = stripe.PaymentIntent.create(  # [3RD PARTY: Stripe API]
                    amount=AMOUNT_CENTS, currency='usd',
                    payment_method_types=['card_present' if mode == 'terminal' else 'card'],
                    capture_method='manual',
                    metadata={'kiosk_reference': reference, 'kiosk_fingerprint': fingerprint},
                    idempotency_key=self._create_key(mode, reference, replaced))
                self._check(intent, reference, fingerprint)
                with db:
                    db.execute('UPDATE payments SET intent_id=? WHERE reference=?', (intent.id, reference))
                return self._result(intent)
            except stripe.StripeError as exc:
                raise PaymentError('Stripe could not confirm the payment state. Retry with this same reference; do not start another customer.') from exc
            finally:
                db.close()

    def capture_payment_intent(self, payload):
        reference, fingerprint = self._identity(payload)
        _client()
        with self.lock:
            db = self._db()
            try:
                row = db.execute('SELECT * FROM payments WHERE reference=?', (reference,)).fetchone()
                if row and row['fingerprint'] != fingerprint:
                    raise PaymentError('The form has changed since payment started. Restore the original information.')
                intent = self._retrieve(row)
                if intent.status == 'requires_capture':
                    self._set_receipt_email(intent, payload)
                    intent = stripe.PaymentIntent.capture(intent.id, idempotency_key='kiosk-capture-' + intent.id)  # [3RD PARTY: Stripe API]
                self._check(intent, reference, fingerprint)
                if intent.status != 'succeeded' or intent.amount_received != AMOUNT_CENTS:
                    raise PaymentError('Payment is not complete. Retry to check the same payment before printing.')
                return self._result(intent)
            except stripe.StripeError as exc:
                raise PaymentError('Stripe has not confirmed capture. Retry the same payment to check its status.') from exc
            finally:
                db.close()

    def verify_paid(self, payload):
        reference, fingerprint = self._identity(payload)
        _client()
        with self.lock:
            db = self._db()
            try:
                row = db.execute('SELECT * FROM payments WHERE reference=?', (reference,)).fetchone()
                if row and row['fingerprint'] != fingerprint:
                    raise PaymentError('This receipt does not match the paid transaction.')
                intent = self._retrieve(row)
                if intent.status != 'succeeded' or intent.amount_received != AMOUNT_CENTS:
                    raise PaymentError('Stripe has not confirmed payment. Printing is blocked until payment succeeds.')
                return {'id': intent.id, 'amount': intent.amount_received, 'testMode': not intent.livemode}
            except stripe.StripeError as exc:
                raise PaymentError('Could not verify payment with Stripe. Check the connection and retry; do not pay again.') from exc
            finally:
                db.close()

    # ---- [CG] server-driven reader steps. The browser calls start once, then checks status about every second until the
    # card is approved, then calls capture_payment_intent() exactly like before. Same ledger, same double-charge rules ----
    def _reader_answer(self, intent, state, message=''):
        # takes a Stripe payment, or the answer create_payment_intent() already built from one
        answer = self._result(intent) if isinstance(intent, stripe.PaymentIntent) else dict(intent)
        answer.update(reader=state, readerMessage=message)
        answer.pop('client_secret', None)  # server-driven never needs the browser to hold the payment's secret
        return answer

    def _ours(self, payload):
        """This transaction's reader payment, checked against the ledger and the form, plus the reader it belongs on.
        Asked by payment id instead, the payment must be one this kiosk's ledger started (never just any id)."""
        if intent_only(payload):
            intent_id = payload['payment_intent']
            if not isinstance(intent_id, str) or not intent_id.startswith('pi_'):  # the ledger lookup below is the real check
                raise PaymentError('That is not a payment this kiosk started.')
            reader_id = find_reader()
            db = self._db()
            try:
                row = db.execute('SELECT * FROM payments WHERE intent_id=?', (intent_id,)).fetchone()
            finally:
                db.close()
            if not row:
                raise PaymentError('That is not a payment this kiosk started.')
            return reader_id, row
        reference, fingerprint = self._identity(payload)
        reader_id = find_reader()
        db = self._db()
        try:
            row = db.execute('SELECT * FROM payments WHERE reference=?', (reference,)).fetchone()
        finally:
            db.close()
        if row and row['fingerprint'] != fingerprint:
            raise PaymentError('The form has changed since payment started. Restore the original information.')
        return reader_id, row

    def start_on_reader(self, payload):
        _client()
        with self.lock:
            try:
                reader_id, row = self._ours(payload)
                # pressing Pay again while the reader is already asking for this card must not send it a second time
                current = None
                if row and row['intent_id']:
                    current = self._retrieve(row)
                    if self._method(current) == 'terminal' and current.status in UNPAID:
                        action = _action_for(stripe.terminal.Reader.retrieve(reader_id), current.id)  # [3RD PARTY: Stripe API]
                        if action and action.get('status') == 'in_progress':
                            return self._reader_answer(current, 'waiting')
                if intent_only(payload):
                    # the built screen already made (or reused) the payment with create_intent; only send it.
                    # [CG] it was just looked up above, so it isn't asked for again: one less trip to Stripe per Pay
                    current = current or self._retrieve(row)
                    if self._method(current) != 'terminal':
                        raise PaymentError('This payment was not started for the card reader. Press Pay to start again.')
                    created = self._result(current)
                else:
                    # same create step as the browser-SDK reader: reuses this transaction's payment, or makes the one and only new one
                    created = self.create_payment_intent(payload, mode='terminal')  # already checked against this form inside
                if created['status'] in ('requires_capture', 'succeeded'):
                    return self._reader_answer(created, 'approved')
                if created['status'] == 'processing':  # Stripe is finishing an approval right now, so just keep checking
                    return self._reader_answer(created, 'waiting')
                if created['status'] != 'requires_payment_method':
                    raise PaymentError(f"Payment status: {created['status']}. Retry to check its status; do not start another charge.")
                try:
                    stripe.terminal.Reader.process_payment_intent(reader_id, payment_intent=created['id'])  # [3RD PARTY: Stripe API]
                except stripe.StripeError as exc:
                    raise PaymentError(_reader_problem(exc)) from exc
                return self._reader_answer(created, 'waiting')
            except stripe.StripeError as exc:
                raise PaymentError('Stripe could not confirm the payment state. Retry with this same reference; do not start another customer.') from exc

    def reader_status(self, payload):
        """waiting / approved / failed (declined or canceled on the reader, can be retried) / idle (not on the reader)."""
        _client()
        with self.lock:
            try:
                reader_id, row = self._ours(payload)
                intent = self._retrieve(row)
                if intent.status in ('requires_capture', 'succeeded'):
                    return self._reader_answer(intent, 'approved')
                if intent.status == 'canceled':
                    return self._reader_answer(intent, 'idle', 'This payment was canceled. Press Pay to start again.')
                action = _action_for(stripe.terminal.Reader.retrieve(reader_id), intent.id)  # [3RD PARTY: Stripe API]
                if action is None:
                    return self._reader_answer(intent, 'idle')
                if action.get('status') == 'failed':
                    reason = action.get('failure_message') or action.get('failure_code') or 'The card was not accepted.'
                    return self._reader_answer(intent, 'failed', reason)
                # 'succeeded' on the reader with the payment not caught up yet is still approval in progress, so keep waiting
                return self._reader_answer(intent, 'waiting')
            except stripe.StripeError as exc:
                raise PaymentError('Could not check the card reader with Stripe. Checking again is safe; it cannot charge twice.') from exc

    def cancel_on_reader(self, payload):
        """Clears the reader's screen if it is asking for this payment. The payment itself is left alone, so Pay can resend it."""
        _client()
        with self.lock:
            try:
                reader_id, row = self._ours(payload)
                if not row or not row['intent_id']:
                    return dict(reader='idle')
                intent = self._retrieve(row)
                if intent.status in UNPAID:
                    action = _action_for(stripe.terminal.Reader.retrieve(reader_id), intent.id)  # [3RD PARTY: Stripe API]
                    if action and action.get('status') == 'in_progress':
                        stripe.terminal.Reader.cancel_action(reader_id)  # [3RD PARTY: Stripe API]
                return self.reader_status(payload)
            except stripe.StripeError as exc:
                raise PaymentError('The card reader could not be canceled from here. Touch Cancel on the reader itself.') from exc
