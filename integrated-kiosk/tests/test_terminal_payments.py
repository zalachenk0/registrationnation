"""S700 reader + typed-card backup: fake Stripe that behaves like Stripe's state machine. No account, key or network."""
import time

import pytest
import stripe

import payment_service as payment
import scanner_api as api

FORM = dict(registrant='Jane Sample', license_plate='DEMO-123', vehicle_year='2021',
            vehicle_make='Example', vehicle_model='Sedan', vin='1HGCM82633A004352',
            confirmation_number='REG-TERMINAL-TEST-0001')
HEADERS = {'X-Kiosk-Payment': '1'}


class FakeStripe:
    """create / retrieve / cancel / capture with Stripe's idempotency-key behaviour."""

    def __init__(self, monkeypatch):
        self.intents, self.by_key = {}, {}
        self.created, self.canceled, self.captured = [], [], []
        monkeypatch.setattr(stripe.PaymentIntent, 'create', self.create)
        monkeypatch.setattr(stripe.PaymentIntent, 'retrieve', lambda iid, **kw: self.intents[iid])
        monkeypatch.setattr(stripe.PaymentIntent, 'cancel', self.cancel)
        monkeypatch.setattr(stripe.PaymentIntent, 'capture', self.capture)

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

    def capture(self, iid, **kw):
        self.captured.append(iid)
        self.intents[iid].status = 'succeeded'
        self.intents[iid].amount_received = self.intents[iid].amount
        return self.intents[iid]

    def authorize(self, iid):  # what the reader / Stripe.js does once the card is accepted
        self.intents[iid].status = 'requires_capture'


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_test_unit_fixture')
    monkeypatch.setenv('STRIPE_PUBLISHABLE_KEY', 'pk_test_unit_fixture')
    monkeypatch.setenv('STRIPE_LOCATION_ID', 'tml_unit_fixture')
    monkeypatch.delenv('STRIPE_SIMULATED', raising=False)
    service = payment.PaymentService(tmp_path / 'payments.sqlite3')
    monkeypatch.setattr(api, 'payments', service)
    return service, FakeStripe(monkeypatch)


# ---------- settings ----------

def test_reader_settings_from_env(monkeypatch):
    monkeypatch.delenv('STRIPE_LOCATION_ID', raising=False); monkeypatch.delenv('STRIPE_SIMULATED', raising=False)
    assert payment.terminal_settings()['enabled'] is False and payment.terminal_settings()['problem'] == ''
    monkeypatch.setenv('STRIPE_LOCATION_ID', 'tml_abc123')
    assert payment.terminal_settings() == dict(enabled=True, simulated=False, location='tml_abc123', problem='')
    monkeypatch.setenv('STRIPE_LOCATION_ID', 'not-a-location')
    bad = payment.terminal_settings()
    assert bad['enabled'] is False and 'tml_' in bad['problem']          # typo is explained, never raised
    monkeypatch.setenv('STRIPE_SIMULATED', '1')
    assert payment.terminal_settings() == dict(enabled=True, simulated=True, location=None, problem='')   # simulator wins


def test_config_exposes_reader_but_never_secrets(env):
    config = api.app.test_client().get('/api/payment/config')
    assert config.json['terminal'] == dict(enabled=True, simulated=False, location='tml_unit_fixture', problem='')
    assert 'sk_test' not in config.text


def test_bad_reader_setting_does_not_break_typed_card(env, monkeypatch):
    payments, stripe_fake = env
    monkeypatch.setenv('STRIPE_LOCATION_ID', 'oops')
    assert payment.configuration()['amount'] == 11425                       # config still works
    assert payments.create_payment_intent(FORM)['method'] == 'card'         # typed card still works
    with pytest.raises(payment.PaymentError, match='not set up'):
        payments.create_payment_intent(FORM, mode='terminal')


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


# ---------- switching between reader and typed card (the double-charge danger zone) ----------

def test_unpaid_reader_payment_is_replaced_by_typed_card(env):
    payments, fake = env
    terminal = payments.create_payment_intent(FORM, mode='terminal')
    card = payments.create_payment_intent(FORM, mode='card')
    assert fake.canceled == [terminal['id']]                                  # the reader's payment was canceled first
    assert card['id'] != terminal['id'] and card['method'] == 'card'
    assert fake.created[1]['payment_method_types'] == ['card']
    assert fake.created[1]['idempotency_key'].endswith('-after-' + terminal['id'])
    assert fake.intents[terminal['id']].status == 'canceled'
    assert payments.create_payment_intent(FORM, mode='card')['id'] == card['id']   # and the ledger now follows the new one


def test_switching_back_and_forth_never_reuses_an_idempotency_key(env):
    payments, fake = env
    a = payments.create_payment_intent(FORM, mode='terminal')
    b = payments.create_payment_intent(FORM, mode='card')
    c = payments.create_payment_intent(FORM, mode='terminal')
    assert len({a['id'], b['id'], c['id']}) == 3 and c['method'] == 'terminal'
    keys = [kw['idempotency_key'] for kw in fake.created]
    assert len(keys) == len(set(keys)) == 3
    assert fake.canceled == [a['id'], b['id']]
    assert [i.status for i in fake.intents.values()] == ['canceled', 'canceled', 'requires_payment_method']


def test_authorized_payment_is_never_replaced_it_is_finished(env):
    payments, fake = env
    terminal = payments.create_payment_intent(FORM, mode='terminal')
    fake.authorize(terminal['id'])                                            # reader took the card
    resumed = payments.create_payment_intent(FORM, mode='card')               # operator then clicks "typed card instead"
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


def test_typed_card_payment_awaiting_confirmation_is_still_resumed(env):
    payments, fake = env
    first = payments.create_payment_intent(FORM, mode='card')
    fake.intents[first['id']].status = 'requires_confirmation'
    assert payments.create_payment_intent(FORM, mode='card')['id'] == first['id'] and not fake.canceled   # Stripe.js can finish this one


def test_paid_payment_cannot_be_switched_or_recharged(env):
    payments, fake = env
    terminal = payments.create_payment_intent(FORM, mode='terminal')
    fake.authorize(terminal['id']); payments.capture_payment_intent(FORM)
    for mode in ('card', 'terminal'):
        assert payments.create_payment_intent(FORM, mode=mode)['status'] == 'succeeded'
    assert len(fake.created) == 1 and not fake.canceled and fake.captured == [terminal['id']]


def test_processing_payment_blocks_a_switch(env):
    payments, fake = env
    terminal = payments.create_payment_intent(FORM, mode='terminal')
    fake.intents[terminal['id']].status = 'processing'
    assert payments.create_payment_intent(FORM, mode='card')['id'] == terminal['id']
    assert not fake.canceled and len(fake.created) == 1


def test_switch_interrupted_after_cancel_resumes_with_the_same_key(env):
    payments, fake = env
    terminal = payments.create_payment_intent(FORM, mode='terminal')
    real_create = fake.create
    calls = {'n': 0}
    def flaky(**kw):
        calls['n'] += 1
        if calls['n'] == 1:
            raise stripe.APIConnectionError('lost')
        return real_create(**kw)
    stripe.PaymentIntent.create = flaky
    with pytest.raises(payment.PaymentError):
        payments.create_payment_intent(FORM, mode='card')                     # canceled the old one, then the network died
    assert fake.intents[terminal['id']].status == 'canceled'
    card = payments.create_payment_intent(FORM, mode='card')                  # retry
    assert fake.canceled == [terminal['id']]                                  # NOT canceled a second time
    assert card['method'] == 'card' and len(fake.intents) == 2


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
        payments.create_payment_intent(FORM, mode='card')


def test_create_intent_route_passes_the_payment_mode(env):
    payments, fake = env
    client = api.app.test_client()
    assert client.post('/api/payment/create_intent', json=dict(FORM, payment_mode='terminal'), headers=HEADERS).json['method'] == 'terminal'
    assert client.post('/api/payment/create_intent', json=dict(FORM, payment_mode='card'), headers=HEADERS).json['method'] == 'card'
    assert client.post('/api/payment/create_intent', json=FORM, headers=HEADERS).json['method'] == 'card'   # default stays typed card
    assert len(fake.created) == 2 and fake.canceled == ['pi_fake_1']          # terminal -> card swapped; the last call just resumed
