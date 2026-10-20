"""Adapted from registrationnation-main/integrated-kiosk/payment_service.py.

Stripe Payment Element sandbox integration. Persist references, not card/document data.
The local kiosk's inherited front-end login is not production authentication.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time

import stripe
from printing import validate

AMOUNT_CENTS = 11425
METHODS = {'card', 'card_present'}  # typed card (Payment Element) and the S700 reader (Terminal)
# nothing has been authorized in these states, so a payment sitting in one of them can safely be replaced
UNPAID = {'requires_payment_method', 'requires_confirmation', 'requires_action'}


class PaymentError(RuntimeError):
    pass


def _client():
    key = os.getenv('STRIPE_SECRET_KEY', '').strip()
    if not key or 'xxxxx' in key or 'REPLACE' in key:
        raise PaymentError('Add your Stripe test secret key to .env, then restart the kiosk.')
    if not key.startswith('sk_test_'):
        raise PaymentError('This local kiosk supports Stripe test keys only. Live payments require a production deployment.')
    stripe.api_key = key
    stripe.max_network_retries = 1
    stripe.default_http_client = stripe.RequestsClient(timeout=20)


# S700 reader settings from .env. This never raises, so a typo here can't break the typed-card backup
def terminal_settings():
    simulated = os.getenv('STRIPE_SIMULATED', '').strip().lower() in {'1', 'true', 'yes', 'on'}
    location = os.getenv('STRIPE_LOCATION_ID', '').strip()
    if simulated:
        return dict(enabled=True, simulated=True, location=None, problem='')
    if location.startswith('tml_'):
        return dict(enabled=True, simulated=False, location=location, problem='')
    problem = 'STRIPE_LOCATION_ID should look like tml_... (Stripe Dashboard > Terminal > Locations).' if location else ''
    return dict(enabled=False, simulated=False, location=None, problem=problem)


def configuration():
    _client()
    public_key = os.getenv('STRIPE_PUBLISHABLE_KEY', '').strip()
    if not public_key.startswith('pk_test_') or 'REPLACE' in public_key:
        raise PaymentError('Add STRIPE_PUBLISHABLE_KEY=pk_test_... to .env from the same Stripe test account as your secret key, then restart.')
    return dict(publishableKey=public_key, amount=AMOUNT_CENTS, currency='usd', testMode=True, terminal=terminal_settings())


# lets the browser's Terminal SDK connect to the S700. Only issued when a reader is set up in .env
def create_connection_token():
    _client()
    if not terminal_settings()['enabled']:
        raise PaymentError('The card reader is not set up on this kiosk. Use the typed-card option.')
    try:
        return stripe.terminal.ConnectionToken.create().secret
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
        intent = stripe.PaymentIntent.retrieve(row['intent_id'])
        return self._check(intent, row['reference'], row['fingerprint'])

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
                    paymentIntent=dict(intent) if intent.status == 'requires_confirmation' else None)

    def create_payment_intent(self, payload, mode='card'):
        reference, fingerprint = self._identity(payload)
        settings = configuration()['terminal']
        mode = 'terminal' if mode == 'terminal' else 'card'
        if mode == 'terminal' and not settings['enabled']:
            raise PaymentError('The card reader is not set up on this kiosk. Use the typed-card option.')
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
                        # the other method was started but nothing was authorized, so it is safe to replace
                        stripe.PaymentIntent.cancel(current.id, idempotency_key='kiosk-cancel-' + current.id)
                    replaced = current.id
                # Stripe may prune idempotency keys after 24h. Never recreate an
                # unresolved old request and risk a second charge.
                if time.time() - row['created'] > 23 * 3600:
                    raise PaymentError('An older payment request needs operator review in Stripe. Do not start another charge.')
                intent = stripe.PaymentIntent.create(
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
                    intent = stripe.PaymentIntent.capture(intent.id, idempotency_key='kiosk-capture-' + intent.id)
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
