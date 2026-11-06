# ============================================================================
# File:         test_terminal_payments_CG.py
# Written by:   CG (Cristian Gonzalez)
# What it does: Tests for the card reader (the only way to pay), the refused
#               typed card, and the Stripe receipt email.
# Team-written: Logic written by CG (Cristian Gonzalez). Outside code is used
#               only through the third-party sources listed below.
# Third-party:  pytest; Stripe Python library; Stripe, Monday.com and CUPS are
#               replaced by stand-ins in these tests.
# ============================================================================
"""S700 reader only (no typed card, no simulator): fake Stripe that behaves like Stripe's state machine. No account, key or network."""
import time

import pytest
import stripe

import payment_service_CG as payment
import scanner_api_BV_CG as api

FORM = dict(registrant='Jane Sample', license_plate='DEMO-123', vehicle_year='2021',
            vehicle_make='Example', vehicle_model='Sedan', vin='1HGCM82633A004352',
            confirmation_number='REG-TERMINAL-TEST-0001')
HEADERS = {'X-Kiosk-Payment': '1'}


class FakeStripe:
    """create / retrieve / cancel / capture with Stripe's idempotency-key behaviour."""

    def __init__(self, monkeypatch):
        self.intents, self.by_key = {}, {}
        self.created, self.canceled, self.captured = [], [], []
        self.modified, self.events, self.modify_error = [], [], None
        monkeypatch.setattr(stripe.PaymentIntent, 'create', self.create)
        monkeypatch.setattr(stripe.PaymentIntent, 'retrieve', lambda iid, **kw: self.intents[iid])
        monkeypatch.setattr(stripe.PaymentIntent, 'cancel', self.cancel)
        monkeypatch.setattr(stripe.PaymentIntent, 'capture', self.capture)
        monkeypatch.setattr(stripe.PaymentIntent, 'modify', self.modify)

    def create(self, **kw):
        self.created.append(kw)
        if kw['idempotency_key'] in self.by_key:
            return self.intents[self.by_key[kw['idempotency_key']]]
        iid = 'pi_fake_%d' % (len(self.intents) + 1)
        intent = stripe.PaymentIntent.construct_from(dict(
            id=iid, client_secret=iid + '_secret_fake', amount=kw['amount'], amount_received=0, currency=kw['currency'],
            livemode=False, metadata=kw['metadata'], payment_method_types=kw['payment_method_types'],
            status='requires_payment_method'), 'sk_test_unit_fixture')
        self.intents[iid] = intent
        self.by_key[kw['idempotency_key']] = iid
        return intent

    def cancel(self, iid, **kw):
        self.canceled.append(iid)
        self.intents[iid].status = 'canceled'
        return self.intents[iid]

    def modify(self, iid, **kw):
        self.events.append('modify')
        self.modified.append((iid, kw))
        if self.modify_error:
            raise self.modify_error
        if 'receipt_email' in kw:
            self.intents[iid]['receipt_email'] = kw['receipt_email']
        return self.intents[iid]

    def capture(self, iid, **kw):
        self.events.append('capture')
        self.captured.append(iid)
        self.intents[iid].status = 'succeeded'
        self.intents[iid].amount_received = self.intents[iid].amount
        return self.intents[iid]

    def authorize(self, iid):  # what the reader / Stripe.js does once the card is accepted
        self.intents[iid].status = 'requires_capture'


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_test_unit_fixture')
    monkeypatch.delenv('STRIPE_PUBLISHABLE_KEY', raising=False)
    monkeypatch.setenv('STRIPE_LOCATION_ID', 'tml_unit_fixture')
    monkeypatch.delenv('STRIPE_SIMULATED', raising=False)
    service = payment.PaymentService(tmp_path / 'payments.sqlite3')
    monkeypatch.setattr(api, 'payments', service)
    return service, FakeStripe(monkeypatch)


# ---------- settings ----------

def test_reader_settings_from_env(monkeypatch):
    monkeypatch.delenv('STRIPE_LOCATION_ID', raising=False); monkeypatch.delenv('STRIPE_SIMULATED', raising=False)
    off = payment.terminal_settings()
    assert off['enabled'] is False and 'STRIPE_LOCATION_ID' in off['problem']   # [CG] the reason is spelled out now
    monkeypatch.setenv('STRIPE_LOCATION_ID', 'tml_abc123')
    assert payment.terminal_settings() == dict(enabled=True, simulated=False, location='tml_abc123', problem='')
    monkeypatch.setenv('STRIPE_LOCATION_ID', 'not-a-location')
    bad = payment.terminal_settings()
    assert bad['enabled'] is False and 'tml_' in bad['problem']          # typo is explained, never raised


@pytest.mark.parametrize('value', ['1', 'true', 'on', 'yes'])
def test_stripe_simulated_is_ignored_the_physical_reader_is_always_used(monkeypatch, value):
    monkeypatch.setenv('STRIPE_SIMULATED', value)
    monkeypatch.setenv('STRIPE_LOCATION_ID', 'tml_abc123')
    assert payment.terminal_settings() == dict(enabled=True, simulated=False, location='tml_abc123', problem='')
    monkeypatch.delenv('STRIPE_LOCATION_ID')
    assert payment.terminal_settings()['enabled'] is False                # the simulator no longer turns the reader "on"


def test_config_exposes_reader_but_never_secrets(env):
    config = api.app.test_client().get('/api/payment/config')
    assert config.json['terminal'] == dict(enabled=True, simulated=False, location='tml_unit_fixture', problem='')
    assert 'sk_test' not in config.text and 'publishableKey' not in config.json


def test_a_bad_reader_setting_is_explained_and_nothing_can_be_charged(env, monkeypatch):
    payments, fake = env
    monkeypatch.setenv('STRIPE_LOCATION_ID', 'oops')
    config = payment.configuration()
    assert config['terminal']['enabled'] is True and 'tml_' in config['terminal']['problem']   # shown on the reader step
    for mode in ('terminal', 'card'):
        with pytest.raises(payment.PaymentError):
            payments.create_payment_intent(FORM, mode=mode)
    assert fake.created == []


def test_connection_token_needs_header_and_a_reader(env, monkeypatch):
    client = api.app.test_client()
    assert client.post('/api/payment/connection_token').status_code == 403
    monkeypatch.setattr(stripe.terminal.ConnectionToken, 'create', lambda **kw: stripe.terminal.ConnectionToken.construct_from(dict(secret='pst_test_fake'), 'k'))
    assert client.post('/api/payment/connection_token', headers=HEADERS).json == dict(secret='pst_test_fake')
    monkeypatch.delenv('STRIPE_LOCATION_ID')
    refused = client.post('/api/payment/connection_token', headers=HEADERS)
    assert refused.status_code == 409 and 'not set up' in refused.json['error']


# ---------- terminal payments ----------

def test_terminal_intent_is_card_present_manual_capture_and_prints(env):
    payments, fake = env
    intent = payments.create_payment_intent(FORM, mode='terminal')
    assert intent['method'] == 'terminal' and intent['status'] == 'requires_payment_method'
    kw = fake.created[0]
    assert kw['payment_method_types'] == ['card_present'] and kw['capture_method'] == 'manual' and kw['amount'] == 11425
    with pytest.raises(payment.PaymentError):
        payments.verify_paid(FORM)                                            # nothing paid yet: printing blocked
    fake.authorize(intent['id'])
    assert payments.capture_payment_intent(FORM)['status'] == 'succeeded'
    assert payments.verify_paid(FORM)['id'] == intent['id']                   # paid by reader: printing allowed
    assert fake.captured == [intent['id']]


def test_creating_the_same_terminal_payment_twice_reuses_it(env):
    payments, fake = env
    first = payments.create_payment_intent(FORM, mode='terminal')
    again = payments.create_payment_intent(FORM, mode='terminal')
    assert first['id'] == again['id'] and len(fake.intents) == 1 and not fake.canceled


# ---------- [CG] the typed card is gone ----------

def old_typed_card_payment(payments, fake, status='requires_payment_method'):
    """A typed-card payment left in the ledger from before the card reader became the only way to pay."""
    reference, fingerprint = payments._identity(FORM)
    intent = fake.create(amount=11425, currency='usd', payment_method_types=['card'], capture_method='manual',
                         metadata={'kiosk_reference': reference, 'kiosk_fingerprint': fingerprint},
                         idempotency_key='kiosk-web-card-create-' + reference)
    fake.created.clear()
    intent.status = status
    db = payments._db()
    with db:
        db.execute('INSERT INTO payments VALUES (?, ?, ?, ?)', (reference, fingerprint, intent.id, time.time()))
    db.close()
    return intent


@pytest.mark.parametrize('mode', ['card', 'typed', '', None, 'TERMINAL'])
def test_anything_but_the_card_reader_is_refused_and_nothing_is_created(env, mode):
    payments, fake = env
    with pytest.raises(payment.PaymentError, match='turned off'):
        payments.create_payment_intent(FORM, mode=mode)
    assert fake.created == []
    db = payments._db()
    assert db.execute('SELECT count(*) FROM payments').fetchone()[0] == 0   # not even a ledger row
    db.close()


def test_create_intent_route_is_card_reader_only(env):
    payments, fake = env
    client = api.app.test_client()
    refused = client.post('/api/payment/create_intent', json=dict(FORM, payment_mode='card'), headers=HEADERS)
    assert refused.status_code == 409 and 'turned off' in refused.json['error'] and fake.created == []
    assert client.post('/api/payment/create_intent', json=dict(FORM, payment_mode='terminal'), headers=HEADERS).json['method'] == 'terminal'
    assert client.post('/api/payment/create_intent', json=FORM, headers=HEADERS).json['method'] == 'terminal'   # no mode = the reader
    assert len(fake.created) == 1 and fake.created[0]['payment_method_types'] == ['card_present']


def test_an_old_unpaid_typed_card_payment_is_replaced_by_a_reader_payment(env):
    payments, fake = env
    old = old_typed_card_payment(payments, fake)
    reader = payments.create_payment_intent(FORM)
    assert fake.canceled == [old.id] and reader['id'] != old.id and reader['method'] == 'terminal'
    assert fake.created[0]['payment_method_types'] == ['card_present']
    assert fake.created[0]['idempotency_key'].endswith('-after-' + old.id)
    assert payments.create_payment_intent(FORM)['id'] == reader['id']      # and the ledger now follows the new one


def test_an_old_typed_card_payment_that_was_already_approved_is_finished_not_replaced(env):
    payments, fake = env
    old = old_typed_card_payment(payments, fake, status='requires_capture')
    resumed = payments.create_payment_intent(FORM)
    assert resumed['id'] == old.id and resumed['status'] == 'requires_capture'
    assert not fake.canceled and fake.created == []                          # no cancel, no second payment
    assert payments.capture_payment_intent(FORM)['status'] == 'succeeded' and fake.captured == [old.id]
    assert payments.verify_paid(FORM)['id'] == old.id                        # an old paid receipt can still be reprinted


def test_authorized_payment_is_never_replaced_it_is_finished(env):
    payments, fake = env
    terminal = payments.create_payment_intent(FORM, mode='terminal')
    fake.authorize(terminal['id'])                                            # reader took the card
    resumed = payments.create_payment_intent(FORM, mode='terminal')           # Pay pressed again
    assert resumed['id'] == terminal['id'] and resumed['status'] == 'requires_capture'
    assert not fake.canceled and len(fake.created) == 1                       # no cancel, no second payment
    assert payments.capture_payment_intent(FORM)['status'] == 'succeeded'
    assert fake.captured == [terminal['id']]


def test_reader_payment_stalled_after_the_card_was_read_restarts_fresh(env):
    payments, fake = env
    first = payments.create_payment_intent(FORM, mode='terminal')
    fake.intents[first['id']].status = 'requires_confirmation'                # card read, never approved: the browser can't resume this
    second = payments.create_payment_intent(FORM, mode='terminal')
    assert fake.canceled == [first['id']] and second['id'] != first['id'] and second['status'] == 'requires_payment_method'
    assert len(fake.created) == 2 and fake.created[1]['idempotency_key'].endswith('-after-' + first['id'])


def test_paid_payment_cannot_be_recharged(env):
    payments, fake = env
    terminal = payments.create_payment_intent(FORM, mode='terminal')
    fake.authorize(terminal['id']); payments.capture_payment_intent(FORM)
    for _ in range(2):
        assert payments.create_payment_intent(FORM, mode='terminal')['status'] == 'succeeded'
    assert len(fake.created) == 1 and not fake.canceled and fake.captured == [terminal['id']]


def test_a_processing_payment_is_never_replaced(env):
    payments, fake = env
    old = old_typed_card_payment(payments, fake, status='processing')       # money may be moving: leave it alone
    assert payments.create_payment_intent(FORM, mode='terminal')['id'] == old.id
    assert not fake.canceled and fake.created == []


def test_replacement_interrupted_after_cancel_resumes_with_the_same_key(env):
    payments, fake = env
    old = old_typed_card_payment(payments, fake)
    real_create = fake.create
    calls = {'n': 0}
    asked = []
    def flaky(**kw):
        asked.append(kw['idempotency_key'])
        calls['n'] += 1
        if calls['n'] == 1:
            raise stripe.APIConnectionError('lost')
        return real_create(**kw)
    stripe.PaymentIntent.create = flaky
    with pytest.raises(payment.PaymentError):
        payments.create_payment_intent(FORM, mode='terminal')                 # canceled the old one, then the network died
    assert fake.intents[old.id].status == 'canceled'
    reader = payments.create_payment_intent(FORM, mode='terminal')            # retry
    assert fake.canceled == [old.id]                                          # NOT canceled a second time
    assert reader['method'] == 'terminal' and len(fake.intents) == 2
    assert len(asked) == 2 and asked[0] == asked[1]                           # the retry reused the same key


def test_externally_canceled_payment_is_simply_replaced(env):
    payments, fake = env
    first = payments.create_payment_intent(FORM, mode='terminal')
    fake.intents[first['id']].status = 'canceled'                             # e.g. canceled in the Stripe Dashboard
    second = payments.create_payment_intent(FORM, mode='terminal')
    assert second['id'] != first['id'] and second['status'] == 'requires_payment_method'


def test_old_ledger_rows_are_still_refused_after_24h(env):
    payments, fake = env
    payments.create_payment_intent(FORM, mode='terminal')
    db = payments._db()
    with db:
        db.execute('UPDATE payments SET created=?', (time.time() - 25 * 3600,))
    db.close()
    fake.intents[next(iter(fake.intents))].status = 'canceled'
    with pytest.raises(payment.PaymentError, match='older payment'):
        payments.create_payment_intent(FORM, mode='terminal')


# ---------- receipt email ----------

EMAILS = ['jane.sample@example.com', 'a@b.co']


def test_receipt_email_is_set_just_before_capture(env):
    payments, fake = env
    intent = payments.create_payment_intent(FORM, mode='terminal')
    fake.authorize(intent['id'])
    result = payments.capture_payment_intent(dict(FORM, receipt_email='  Jane.Sample@Example.com '))
    assert fake.events == ['modify', 'capture']                              # email first, then the money, in that order
    assert fake.modified[0][0] == intent['id'] and fake.modified[0][1]['receipt_email'] == 'Jane.Sample@Example.com'   # trimmed
    assert result['receiptEmail'] == 'Jane.Sample@Example.com' and result['status'] == 'succeeded'


def test_the_email_is_not_sent_when_the_payment_is_created(env):
    payments, fake = env
    payments.create_payment_intent(dict(FORM, receipt_email='jane.sample@example.com'), mode='terminal')
    assert 'receipt_email' not in fake.created[0]                            # so a changed email can never clash with Stripe's duplicate protection


@pytest.mark.parametrize('bad', [None, '', 'not-an-email', 'two@@example.com', 'sp ace@example.com', 'x@example', 123])
def test_a_missing_or_bad_email_never_blocks_the_payment(env, bad):
    payments, fake = env
    intent = payments.create_payment_intent(FORM, mode='terminal')
    fake.authorize(intent['id'])
    result = payments.capture_payment_intent(dict(FORM, receipt_email=bad) if bad is not None else FORM)
    assert result['status'] == 'succeeded' and result['receiptEmail'] is None
    assert fake.events == ['capture']                                        # no attempt to attach a bad address


def test_a_stripe_problem_while_attaching_the_email_never_blocks_the_payment(env):
    payments, fake = env
    intent = payments.create_payment_intent(FORM, mode='terminal')
    fake.authorize(intent['id'])
    fake.modify_error = stripe.APIConnectionError('lost')
    result = payments.capture_payment_intent(dict(FORM, receipt_email=EMAILS[0]))
    assert result['status'] == 'succeeded' and fake.captured == [intent['id']]   # the customer is still charged exactly once
    assert result['receiptEmail'] is None                                       # and the screen will say no receipt could be set up


def test_an_email_changed_after_the_first_attempt_is_the_one_used(env):
    payments, fake = env
    intent = payments.create_payment_intent(dict(FORM, receipt_email='old@example.com'), mode='terminal')
    fake.intents[intent['id']]['receipt_email'] = 'old@example.com'          # an earlier attempt had set the old address
    fake.authorize(intent['id'])
    payments.capture_payment_intent(dict(FORM, receipt_email='new@example.com'))
    assert fake.modified[0][1]['receipt_email'] == 'new@example.com'


def test_no_update_when_stripe_already_has_the_same_email(env):
    payments, fake = env
    intent = payments.create_payment_intent(FORM, mode='terminal')
    fake.intents[intent['id']]['receipt_email'] = EMAILS[0]
    fake.authorize(intent['id'])
    payments.capture_payment_intent(dict(FORM, receipt_email=EMAILS[0]))
    assert fake.modified == [] and fake.events == ['capture']


def test_an_already_paid_payment_is_not_touched_again(env):
    payments, fake = env
    intent = payments.create_payment_intent(FORM, mode='terminal')
    fake.authorize(intent['id'])
    payments.capture_payment_intent(dict(FORM, receipt_email=EMAILS[0]))
    again = payments.capture_payment_intent(dict(FORM, receipt_email='other@example.com'))    # e.g. the print retry
    assert fake.events == ['modify', 'capture'] and again['status'] == 'succeeded' and again['receiptEmail'] == EMAILS[0]


def test_editing_the_email_never_trips_the_form_changed_check_and_printing_still_works(env):
    payments, fake = env
    intent = payments.create_payment_intent(dict(FORM, receipt_email='one@example.com'), mode='terminal')
    fake.authorize(intent['id'])
    payments.capture_payment_intent(dict(FORM, receipt_email='two@example.com'))        # a different email than at creation: fine
    assert payments.verify_paid(dict(FORM, receipt_email='three@example.com'))['id'] == intent['id']


def test_the_capture_route_passes_the_email_through(env):
    payments, fake = env
    client = api.app.test_client()
    intent = client.post('/api/payment/create_intent', json=dict(FORM, payment_mode='terminal'), headers=HEADERS).json
    fake.authorize(intent['id'])
    reply = client.post('/api/payment/capture_intent', json=dict(FORM, receipt_email=EMAILS[0]), headers=HEADERS)
    assert reply.status_code == 200 and reply.json['receiptEmail'] == EMAILS[0] and fake.events == ['modify', 'capture']
