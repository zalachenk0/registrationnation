# ============================================================================
# File:         test_server_reader_CG.py
# Written by:   CG (Cristian Gonzalez)
# What it does: Tests for the server-driven card reader (the kiosk asks Stripe,
#               Stripe tells the reader), in the kiosk and the home test form.
# Team-written: Logic written by CG (Cristian Gonzalez). Outside code is used
#               only through the third-party sources listed below.
# Third-party:  pytest; Stripe Python library; Stripe is replaced by a stand-in
#               that copies how Stripe's reader objects behave.
# ============================================================================
"""Server-driven S700: no account, key, reader or network. The fake keeps Stripe's rules: one job per reader,
a declined card leaves the payment unpaid and retryable, and nothing is charged until capture."""
import sys
from pathlib import Path

import pytest
import stripe

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'home_test_CG'))  # same as test_home_form_CG.py
import home_form_server_CG as home  # noqa: E402
import payment_service_CG as payment  # noqa: E402
import scanner_api_BV_CG as api  # noqa: E402
from test_terminal_payments_CG import FORM, HEADERS, FakeStripe  # noqa: E402 same fake payments as the browser-reader tests

LOCATION = 'tml_unit_fixture'


def stripe_error(code):
    return stripe.InvalidRequestError('reader said no', None, code=code)


class FakeReaders(FakeStripe):
    """Adds Stripe's reader objects: retrieve, list, create, process_payment_intent, cancel_action and the test-mode tap."""

    def __init__(self, monkeypatch):
        super().__init__(monkeypatch)
        self.readers, self.sent, self.cleared, self.taps, self.made = {}, [], [], [], []
        Reader = stripe.terminal.Reader
        monkeypatch.setattr(Reader, 'retrieve', lambda rid, **kw: self._reader(rid))
        monkeypatch.setattr(Reader, 'list', lambda **kw: stripe.ListObject.construct_from(
            dict(object='list', data=[self._reader(r) for r, v in self.readers.items() if v['location'] == kw.get('location')]), 'sk_test_unit_fixture'))
        monkeypatch.setattr(Reader, 'create', self.create_reader)
        monkeypatch.setattr(Reader, 'process_payment_intent', self.process)
        monkeypatch.setattr(Reader, 'cancel_action', self.cancel_action)
        monkeypatch.setattr(Reader.TestHelpers, 'present_payment_method', self.present)

    def add(self, rid, simulated=False, location=LOCATION, status='online'):
        self.readers[rid] = dict(id=rid, object='terminal.reader', location=location, status=status, action=None,
                                 device_type='simulated_stripe_s700' if simulated else 'stripe_s700')
        return rid

    def _reader(self, rid):
        if rid not in self.readers:
            raise stripe_error('resource_missing')
        return stripe.terminal.Reader.construct_from(dict(self.readers[rid]), 'sk_test_unit_fixture')

    def create_reader(self, **kw):
        self.made.append(kw)
        return self._reader(self.add('tmr_simulated_%d' % len(self.made), simulated=True, location=kw['location']))

    def process(self, rid, payment_intent, **kw):
        reader = self.readers[rid]
        if reader['status'] == 'offline':
            raise stripe_error('terminal_reader_offline')
        if (reader['action'] or {}).get('status') == 'in_progress':
            raise stripe_error('terminal_reader_busy')
        self.sent.append((rid, payment_intent))
        reader['action'] = dict(type='process_payment_intent', status='in_progress',
                                process_payment_intent=dict(payment_intent=payment_intent))
        return self._reader(rid)

    def cancel_action(self, rid, **kw):
        self.cleared.append(rid)
        self.readers[rid]['action'] = None
        return self._reader(rid)

    def present(self, rid, type=None, card_present=None, **kw):
        self.taps.append((rid, card_present['number']))
        action = self.readers[rid]['action']
        if card_present['number'] == '4242424242424242':
            action['status'] = 'succeeded'
            self.authorize(action['process_payment_intent']['payment_intent'])
        else:
            action.update(status='failed', failure_code='card_declined', failure_message='Your card was declined.')
        return self._reader(rid)

    def customer_taps(self, rid, approved=True):  # a real tap on a real reader, as far as the server can tell
        self.present(rid, card_present=dict(number='4242424242424242' if approved else '4000000000000002'))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_test_unit_fixture')
    monkeypatch.setenv('STRIPE_PUBLISHABLE_KEY', 'pk_test_unit_fixture')
    monkeypatch.setenv('STRIPE_LOCATION_ID', LOCATION)
    monkeypatch.delenv('STRIPE_SIMULATED', raising=False)
    monkeypatch.delenv('STRIPE_READER_ID', raising=False)
    monkeypatch.setattr(payment, '_readers', {})  # no reader remembered from another test
    service = payment.PaymentService(tmp_path / 'payments.sqlite3')
    monkeypatch.setattr(api, 'payments', service)
    fake = FakeReaders(monkeypatch)
    fake.add('tmr_kiosk')
    return service, fake


def only_intent(fake):
    assert len(fake.intents) == 1
    return next(iter(fake.intents.values()))


# ---------- settings ----------

def test_server_reader_needs_a_location_even_when_simulated(monkeypatch):
    monkeypatch.setenv('STRIPE_SIMULATED', '1'); monkeypatch.delenv('STRIPE_LOCATION_ID', raising=False)
    monkeypatch.delenv('STRIPE_READER_ID', raising=False)
    settings = payment.server_reader_settings()
    assert settings['enabled'] is False and 'STRIPE_LOCATION_ID' in settings['problem']
    assert payment.terminal_settings()['enabled'] is True  # the browser-direct simulator still works without one
    monkeypatch.setenv('STRIPE_LOCATION_ID', LOCATION)
    assert payment.server_reader_settings() == dict(enabled=True, simulated=True, problem='')


def test_a_mistyped_reader_id_turns_the_server_reader_off_with_a_reason(monkeypatch):
    monkeypatch.delenv('STRIPE_SIMULATED', raising=False); monkeypatch.setenv('STRIPE_LOCATION_ID', LOCATION)
    monkeypatch.setenv('STRIPE_READER_ID', 'kiosk-reader')
    settings = payment.server_reader_settings()
    assert settings['enabled'] is False and 'tmr_' in settings['problem']


def test_config_tells_the_page_about_the_server_reader_but_no_ids_or_secrets(env, monkeypatch):
    monkeypatch.setenv('STRIPE_READER_ID', 'tmr_kiosk')
    config = payment.configuration()
    assert config['serverReader'] == dict(enabled=True, simulated=False, problem='')
    assert 'tmr_kiosk' not in str(config) and 'sk_test' not in str(config)


# ---------- which reader ----------

def test_the_one_real_reader_at_the_location_is_used(env):
    assert payment.find_reader() == 'tmr_kiosk'


def test_simulated_mode_makes_stripes_pretend_s700_once_and_then_reuses_it(env, monkeypatch):
    service, fake = env
    monkeypatch.setenv('STRIPE_SIMULATED', '1')
    first = payment.find_reader()
    assert fake.made == [dict(registration_code='simulated-s700', location=LOCATION, label='Registration Nation simulated S700',
                              idempotency_key='kiosk-simulated-reader-' + LOCATION)]
    monkeypatch.setattr(payment, '_readers', {})  # even after a restart it finds the one it made instead of making another
    assert payment.find_reader() == first and len(fake.made) == 1


def test_real_mode_never_uses_a_simulated_reader(env, monkeypatch):
    service, fake = env
    del fake.readers['tmr_kiosk']; fake.add('tmr_pretend', simulated=True)
    with pytest.raises(payment.PaymentError, match='No card reader is registered'):
        payment.find_reader()


def test_two_online_readers_at_one_location_are_never_guessed_between(env):
    service, fake = env
    fake.add('tmr_other_kiosk')
    with pytest.raises(payment.PaymentError, match='STRIPE_READER_ID'):
        payment.find_reader()


def test_with_two_readers_the_only_online_one_is_used(env):
    service, fake = env
    fake.add('tmr_spare', status='offline')
    assert payment.find_reader() == 'tmr_kiosk'


def test_a_reader_id_from_another_location_is_refused(env, monkeypatch):
    service, fake = env
    fake.add('tmr_elsewhere', location='tml_other_store')
    monkeypatch.setenv('STRIPE_READER_ID', 'tmr_elsewhere')
    with pytest.raises(payment.PaymentError, match='different location'):
        payment.find_reader()


def test_a_reader_id_that_does_not_match_the_simulated_setting_is_refused(env, monkeypatch):
    service, fake = env
    fake.add('tmr_pretend', simulated=True)
    monkeypatch.setenv('STRIPE_READER_ID', 'tmr_pretend')
    with pytest.raises(payment.PaymentError, match='simulated'):
        payment.find_reader()


# ---------- paying ----------

def test_start_puts_one_card_present_manual_capture_payment_on_the_reader(env):
    service, fake = env
    answer = service.start_on_reader(FORM)
    intent = only_intent(fake)
    assert fake.sent == [('tmr_kiosk', intent.id)]
    assert fake.created[0]['payment_method_types'] == ['card_present'] and fake.created[0]['capture_method'] == 'manual'
    assert answer['reader'] == 'waiting' and answer['id'] == intent.id
    assert 'client_secret' not in answer  # the browser never needs the payment's secret in this setup


def test_pressing_pay_again_while_the_reader_waits_never_sends_it_twice(env):
    service, fake = env
    service.start_on_reader(FORM)
    again = service.start_on_reader(FORM)
    assert again['reader'] == 'waiting'
    assert len(fake.sent) == 1 and len(fake.intents) == 1


def test_approved_card_then_capture_then_printing_is_allowed(env):
    service, fake = env
    service.start_on_reader(FORM)
    assert service.reader_status(FORM)['reader'] == 'waiting'
    fake.customer_taps('tmr_kiosk')
    assert service.reader_status(FORM)['reader'] == 'approved'
    assert service.capture_payment_intent(FORM)['status'] == 'succeeded'
    assert service.verify_paid(FORM)['amount'] == payment.AMOUNT_CENTS


def test_nothing_is_captured_while_the_reader_is_still_waiting(env):
    service, fake = env
    service.start_on_reader(FORM)
    with pytest.raises(payment.PaymentError, match='not complete'):
        service.capture_payment_intent(FORM)
    assert fake.captured == []
    with pytest.raises(payment.PaymentError):
        service.verify_paid(FORM)


def test_a_declined_card_can_be_retried_on_the_same_payment_without_a_second_charge(env):
    service, fake = env
    service.start_on_reader(FORM)
    fake.customer_taps('tmr_kiosk', approved=False)
    declined = service.reader_status(FORM)
    assert declined['reader'] == 'failed' and declined['readerMessage'] == 'Your card was declined.'
    with pytest.raises(payment.PaymentError):
        service.verify_paid(FORM)  # a decline never unlocks printing
    service.start_on_reader(FORM)
    intent = only_intent(fake)  # the same payment went back to the reader, no new one
    assert fake.sent == [('tmr_kiosk', intent.id)] * 2
    fake.customer_taps('tmr_kiosk')
    assert service.capture_payment_intent(FORM)['status'] == 'succeeded' and fake.captured == [intent.id]


def test_an_already_approved_payment_is_not_sent_to_the_reader_again(env):
    service, fake = env
    service.start_on_reader(FORM)
    fake.customer_taps('tmr_kiosk')
    assert service.start_on_reader(FORM)['reader'] == 'approved'
    assert len(fake.sent) == 1


def test_a_busy_reader_gives_a_clear_message_and_nothing_is_charged(env):
    service, fake = env
    fake.readers['tmr_kiosk']['action'] = dict(type='process_payment_intent', status='in_progress',
                                               process_payment_intent=dict(payment_intent='pi_someone_else'))
    with pytest.raises(payment.PaymentError, match='busy'):
        service.start_on_reader(FORM)
    assert fake.captured == []


def test_an_offline_reader_says_so(env):
    service, fake = env
    fake.readers['tmr_kiosk']['status'] = 'offline'
    with pytest.raises(payment.PaymentError, match='offline'):
        service.start_on_reader(FORM)


def test_status_ignores_a_job_on_the_reader_that_is_not_this_payment(env):
    service, fake = env
    service.start_on_reader(FORM)
    fake.readers['tmr_kiosk']['action']['process_payment_intent']['payment_intent'] = 'pi_someone_else'
    assert service.reader_status(FORM)['reader'] == 'idle'


def test_a_changed_form_is_refused_by_every_reader_step(env):
    service, fake = env
    service.start_on_reader(FORM)
    changed = dict(FORM, vin='2HGCM82633A004352')
    for step in (service.start_on_reader, service.reader_status, service.cancel_on_reader):
        with pytest.raises(payment.PaymentError, match='form has changed'):
            step(changed)
    assert len(fake.sent) == 1


# ---------- cancel ----------

def test_cancel_clears_the_reader_but_leaves_the_payment_so_pay_can_resend_it(env):
    service, fake = env
    service.start_on_reader(FORM)
    assert service.cancel_on_reader(FORM)['reader'] == 'idle'
    assert fake.cleared == ['tmr_kiosk'] and fake.canceled == []
    service.start_on_reader(FORM)
    assert len(fake.intents) == 1 and len(fake.sent) == 2


def test_cancel_never_touches_another_payment_on_the_reader(env):
    service, fake = env
    service.start_on_reader(FORM)
    fake.readers['tmr_kiosk']['action']['process_payment_intent']['payment_intent'] = 'pi_someone_else'
    service.cancel_on_reader(FORM)
    assert fake.cleared == []


def test_cancel_after_approval_changes_nothing(env):
    service, fake = env
    service.start_on_reader(FORM)
    fake.customer_taps('tmr_kiosk')
    assert service.cancel_on_reader(FORM)['reader'] == 'approved'
    assert fake.cleared == [] and fake.canceled == []


# ---------- simulated tap ----------

def test_a_pretend_tap_is_refused_for_a_real_reader(env):
    service, fake = env
    service.start_on_reader(FORM)
    with pytest.raises(payment.PaymentError, match='simulated reader'):
        service.simulate_tap(FORM, '4242424242424242')
    assert fake.taps == []


def test_a_pretend_tap_only_takes_stripes_test_cards_and_only_after_pay(env, monkeypatch):
    service, fake = env
    monkeypatch.setenv('STRIPE_SIMULATED', '1')
    service.create_payment_intent(FORM, mode='terminal')  # a payment exists, but nothing was sent to the reader yet
    with pytest.raises(payment.PaymentError, match='Press Pay first'):
        service.simulate_tap(FORM, '4242424242424242')
    service.start_on_reader(FORM)
    with pytest.raises(payment.PaymentError, match='test cards'):
        service.simulate_tap(FORM, '5555555555554444')
    assert fake.taps == []
    assert service.simulate_tap(FORM, '4242 4242 4242 4242')['reader'] == 'approved'


def test_simulated_decline_then_approval(env, monkeypatch):
    service, fake = env
    monkeypatch.setenv('STRIPE_SIMULATED', '1')
    service.start_on_reader(FORM)
    assert service.simulate_tap(FORM, '4000000000000002')['reader'] == 'failed'
    service.start_on_reader(FORM)
    assert service.simulate_tap(FORM, '4242424242424242')['reader'] == 'approved'
    assert service.capture_payment_intent(FORM)['status'] == 'succeeded' and len(fake.intents) == 1


# ---------- switching to the typed card ----------

def test_an_unpaid_server_reader_payment_can_still_switch_to_the_typed_card(env):
    service, fake = env
    service.start_on_reader(FORM)
    fake.readers['tmr_kiosk']['action'] = None  # customer canceled on the reader first
    typed = service.create_payment_intent(FORM, mode='card')
    assert typed['method'] == 'card' and len(fake.canceled) == 1


# ---------- kiosk routes ----------

def test_reader_routes_need_the_kiosk_header(env):
    client = api.app.test_client()
    for route in ('reader_start', 'reader_status', 'reader_cancel', 'reader_simulate_tap'):
        assert client.post('/api/payment/' + route, json=FORM).status_code == 403


def test_reader_start_route_does_the_same_kiosk_side_steps_as_create_intent(env, monkeypatch):
    service, fake = env
    steps = []
    monkeypatch.setattr(api.delivery, 'enqueue_capture', lambda *a: steps.append(('security photo',) + a))
    monkeypatch.setattr(api.photos, 'bind', lambda ref: steps.append(('photos', ref)))
    client = api.app.test_client()
    answer = client.post('/api/payment/reader_start', json=dict(FORM, security_event_id='evt-1'), headers=HEADERS)
    assert answer.status_code == 200 and answer.get_json()['reader'] == 'waiting'
    assert steps == [('security photo', 'payment_click', 'pay:' + FORM['confirmation_number'] + ':evt-1'),
                     ('photos', FORM['confirmation_number'])]


def test_reader_routes_walk_a_whole_payment(env, monkeypatch):
    service, fake = env
    monkeypatch.setenv('STRIPE_SIMULATED', '1')
    client = api.app.test_client()
    post = lambda route, body=FORM: client.post('/api/payment/' + route, json=body, headers=HEADERS)
    assert post('reader_start').get_json()['reader'] == 'waiting'
    assert post('reader_simulate_tap', dict(FORM, test_card='4242424242424242')).get_json()['reader'] == 'approved'
    assert post('reader_status').get_json()['reader'] == 'approved'
    assert post('capture_intent').get_json()['status'] == 'succeeded'


def test_reader_route_errors_come_back_as_messages_not_crashes(env):
    service, fake = env
    fake.readers['tmr_kiosk']['status'] = 'offline'
    answer = api.app.test_client().post('/api/payment/reader_start', json=FORM, headers=HEADERS)
    assert answer.status_code == 409 and 'offline' in answer.get_json()['error']


# ---------- home test form ----------

def test_home_form_runs_the_server_driven_reader_too(env, tmp_path, monkeypatch):
    service, fake = env
    monkeypatch.setenv('STRIPE_SIMULATED', '1')
    app = home.create_app(tmp_path / 'home')
    client = app.test_client()
    post = lambda route, body=FORM: client.post('/api/payment/' + route, json=body, headers=HEADERS)
    settings = client.get('/api/home/settings').get_json()
    assert settings['serverReader'] is True and settings['serverReaderProblem'] == ''
    assert post('reader_start').get_json()['reader'] == 'waiting'
    assert post('reader_simulate_tap', dict(FORM, test_card='4000000000000002')).get_json()['reader'] == 'failed'
    assert post('reader_start').get_json()['reader'] == 'waiting'
    assert post('reader_simulate_tap', dict(FORM, test_card='4242424242424242')).get_json()['reader'] == 'approved'
    assert post('capture_intent').get_json()['status'] == 'succeeded'
    assert len(fake.intents) == 1
