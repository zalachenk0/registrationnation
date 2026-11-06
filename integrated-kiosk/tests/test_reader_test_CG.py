# ============================================================================
# File:         test_reader_test_CG.py
# Written by:   CG (Cristian Gonzalez)
# What it does: Tests for the reader test tool.
# Team-written: Logic written by CG (Cristian Gonzalez). Outside code is used
#               only through the third-party sources listed below.
# Third-party:  pytest; Stripe, Monday.com and CUPS are replaced by stand-ins in
#               these tests.
# ============================================================================
"""reader_test_CG.py, the tool that checks the S700 without the kiosk screen. A stand-in Stripe is used, nothing real is contacted."""
from types import SimpleNamespace

import pytest

import reader_test_CG as reader_test


class Obj(dict):
    __getattr__ = dict.get          # attribute access like Stripe's objects: None when something is missing


class FakeStripe:
    class StripeError(Exception):
        user_message = None

    def __init__(self):
        self.api_key = None
        self.log = []
        self.locations = [Obj(id='tml_1', display_name='Texas Tag and Title')]
        self.readers = [Obj(id='tmr_1', label='Kiosk S700', device_type='stripe_s700', status='online', ip_address='192.168.1.50', location='tml_1')]
        self.polls = [(None, 'requires_payment_method'), (None, 'requires_capture')]    # what each look at the reader and the payment shows
        self.poll = 0
        self.busy = False
        self.register_error = None
        fake = self

        class Location:
            @staticmethod
            def list(limit=20):
                return Obj(data=fake.locations)

        class Reader:
            @staticmethod
            def list(location=None, limit=20):
                return Obj(data=[r for r in fake.readers if location is None or r.location == location])

            @staticmethod
            def retrieve(reader_id):
                action = fake.polls[min(fake.poll, len(fake.polls) - 1)][0]

                class Live(Obj):
                    def process_payment_intent(self, **params):
                        if fake.busy:
                            raise fake.StripeError('The reader is busy with another action.')
                        fake.log.append(('send', reader_id, params))

                    def cancel_action(self):
                        fake.log.append(('cancel_action', reader_id))
                return Live(id=reader_id, action=action)

            @staticmethod
            def create(**params):
                if fake.register_error:
                    raise fake.register_error
                fake.log.append(('register', params))
                return Obj(id='tmr_new', label=params['label'], status='online')

        class PaymentIntent:
            @staticmethod
            def create(**params):
                fake.log.append(('create', params))
                return Obj(id='pi_test_1', status='requires_payment_method')

            @staticmethod
            def retrieve(intent_id, expand=None):
                if expand:
                    card = Obj(brand='visa', last4='4242', read_method='contactless_emv')
                    return Obj(id=intent_id, latest_charge=Obj(payment_method_details=Obj(card_present=card)))
                status = fake.polls[min(fake.poll, len(fake.polls) - 1)][1]
                fake.poll += 1
                return Obj(id=intent_id, status=status)

            @staticmethod
            def capture(intent_id):
                fake.log.append(('capture', intent_id))

            @staticmethod
            def cancel(intent_id):
                fake.log.append(('cancel', intent_id))

        self.terminal = SimpleNamespace(Location=Location, Reader=Reader)
        self.PaymentIntent = PaymentIntent


@pytest.fixture
def fake(monkeypatch):
    stripe = FakeStripe()
    monkeypatch.setattr(reader_test, 'stripe', stripe)
    monkeypatch.setattr('dotenv.load_dotenv', lambda *a, **k: None)          # a real .env must never leak into a test
    monkeypatch.setattr(reader_test.time, 'sleep', lambda seconds: None)
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_test_abc123')
    monkeypatch.setenv('STRIPE_LOCATION_ID', 'tml_1')
    monkeypatch.delenv('STRIPE_SIMULATED', raising=False)
    monkeypatch.delenv('STRIPE_READER_ID', raising=False)
    return stripe


def run(*args):
    return reader_test.main(['reader_test_CG.py', *args])


def steps(fake):
    return [entry[0] for entry in fake.log]


# ---------- safety ----------

@pytest.mark.parametrize('command', [['check'], ['pay'], ['register', 'abc-def']])
def test_a_live_key_is_refused_before_anything_is_sent_to_stripe(fake, monkeypatch, capsys, command):
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_live_abc123')
    assert run(*command) == 1
    assert 'TEST key' in capsys.readouterr().out and fake.log == [] and fake.api_key is None


def test_a_missing_key_says_where_to_put_it(fake, monkeypatch, capsys):
    monkeypatch.delenv('STRIPE_SECRET_KEY')
    assert run('check') == 1 and 'STRIPE_SECRET_KEY is not set' in capsys.readouterr().out


def test_the_secret_key_is_never_printed(fake, capsys):
    run('check')
    run('pay')
    assert 'sk_test_abc123' not in capsys.readouterr().out


# ---------- check ----------

def test_check_reports_ready_when_a_reader_is_online_at_the_kiosks_location(fake, capsys):
    assert run('check') == 0
    out = capsys.readouterr().out
    assert 'READY' in out and 'tmr_1' in out and 'Kiosk S700' in out and 'the kiosk uses this one' in out and fake.api_key == 'sk_test_abc123'


def test_check_says_stripe_simulated_is_no_longer_used(fake, monkeypatch, capsys):
    monkeypatch.setenv('STRIPE_SIMULATED', '1')
    assert run('check') == 0                                  # [CG] the physical reader is still found and ready
    out = capsys.readouterr().out
    assert 'no longer used' in out and 'physical S700' in out and 'READY' in out


def test_check_never_counts_stripes_simulated_reader_as_ready(fake, capsys):
    fake.readers = [Obj(id='tmr_sim', label='Pretend', device_type='simulated_stripe_s700', status='online', ip_address=None, location='tml_1')]
    assert run('check') == 1
    out = capsys.readouterr().out
    assert 'kiosk ignores it' in out and 'NOT READY' in out


def test_pay_never_uses_the_simulated_reader_even_if_it_is_listed_first(fake, capsys):
    fake.readers.insert(0, Obj(id='tmr_sim', label='Pretend', device_type='simulated_stripe_s700', status='online', ip_address=None, location='tml_1'))
    assert run('pay') == 0
    assert [entry[1] for entry in fake.log if entry[0] == 'send'] == ['tmr_1']


def test_pay_uses_the_reader_named_in_stripe_reader_id(fake, monkeypatch, capsys):
    fake.readers.append(Obj(id='tmr_2', label='Spare S700', device_type='stripe_s700', status='online', ip_address=None, location='tml_1'))
    monkeypatch.setenv('STRIPE_READER_ID', 'tmr_2')
    assert run('pay') == 0
    assert [entry[1] for entry in fake.log if entry[0] == 'send'] == ['tmr_2']
    monkeypatch.setenv('STRIPE_READER_ID', 'tmr_gone')
    fake.log.clear()
    assert run('pay') == 1 and 'with id tmr_gone' in capsys.readouterr().out and fake.log == []


def test_check_spots_an_offline_reader(fake, capsys):
    fake.readers[0]['status'] = 'offline'
    assert run('check') == 1
    out = capsys.readouterr().out
    assert 'NOT READY' in out and 'power' in out


def test_check_spots_a_reader_in_a_different_location(fake, capsys):
    fake.readers[0]['location'] = 'tml_other'
    assert run('check') == 1 and 'Wrong location' in capsys.readouterr().out


def test_check_spots_a_location_id_that_does_not_exist_on_this_account(fake, monkeypatch, capsys):
    monkeypatch.setenv('STRIPE_LOCATION_ID', 'tml_typo')
    run('check')
    assert 'has no such location' in capsys.readouterr().out


def test_check_with_nothing_set_up_yet_points_to_the_next_step(fake, monkeypatch, capsys):
    monkeypatch.delenv('STRIPE_LOCATION_ID')
    fake.locations, fake.readers = [], []
    run('check')
    out = capsys.readouterr().out
    assert "can't take payments" in out and 'Create one in the Dashboard' in out and 'register' in out


# ---------- register ----------

def test_register_adds_the_reader_at_the_kiosks_location(fake, capsys):
    assert run('register', 'sepia-cat-ocean', 'Front', 'desk') == 0
    assert fake.log == [('register', {'registration_code': 'sepia-cat-ocean', 'label': 'Front desk', 'location': 'tml_1'})]
    assert 'tmr_new' in capsys.readouterr().out


def test_register_needs_a_location_first(fake, monkeypatch, capsys):
    monkeypatch.delenv('STRIPE_LOCATION_ID')
    assert run('register', 'abc') == 1 and 'STRIPE_LOCATION_ID' in capsys.readouterr().out and fake.log == []


def test_register_explains_a_code_stripe_rejects(fake, capsys):
    error = fake.StripeError('bad code')
    error.user_message = 'The registration code is invalid or expired.'
    fake.register_error = error
    assert run('register', 'nope') == 1
    out = capsys.readouterr().out
    assert 'invalid or expired' in out and 'currently on the reader' in out


# ---------- pay ----------

def test_a_test_payment_goes_to_the_reader_is_tapped_and_is_captured(fake, capsys):
    assert run('pay', '114.25') == 0
    create = next(e for e in fake.log if e[0] == 'create')[1]
    assert create['amount'] == 11425 and create['payment_method_types'] == ['card_present'] and create['capture_method'] == 'manual'
    assert steps(fake) == ['create', 'send', 'capture'] and fake.log[1][2] == {'payment_intent': 'pi_test_1'}
    out = capsys.readouterr().out
    assert 'PAID in test mode: $114.25' in out and 'Visa ending 4242' in out and 'contactless' in out


def test_the_default_amount_is_one_dollar(fake):
    run('pay')
    assert next(e for e in fake.log if e[0] == 'create')[1]['amount'] == 100


@pytest.mark.parametrize('amount', ['abc', '0.10', '5000', '-3'])
def test_silly_amounts_are_refused_before_anything_is_created(fake, capsys, amount):
    assert run('pay', amount) == 1 and fake.log == []


def test_no_online_reader_stops_before_a_payment_is_even_created(fake, capsys):
    fake.readers[0]['status'] = 'offline'
    assert run('pay') == 1 and fake.log == [] and 'No physical reader is online' in capsys.readouterr().out


def test_a_declined_card_is_explained_and_nothing_is_left_hanging(fake, capsys):
    fake.polls = [(None, 'requires_payment_method'), (Obj(status='failed', failure_message='The card was declined.'), 'requires_payment_method')]
    assert run('pay') == 1
    assert 'declined' in capsys.readouterr().out
    assert 'capture' not in steps(fake) and 'cancel_action' in steps(fake) and 'cancel' in steps(fake)


def test_nobody_tapping_gives_up_cleanly_and_clears_the_reader(fake, monkeypatch, capsys):
    monkeypatch.setattr(reader_test, 'WAIT_SECONDS', 0.05)
    fake.polls = [(None, 'requires_payment_method')]
    assert run('pay') == 1
    assert 'timeout' in capsys.readouterr().out and 'capture' not in steps(fake) and steps(fake)[-2:] == ['cancel_action', 'cancel']


def test_pressing_ctrl_c_cancels_everything_it_started(fake, monkeypatch, capsys):
    real = fake.PaymentIntent.retrieve
    state = {'n': 0}

    def interrupted(intent_id, expand=None):
        state['n'] += 1
        if state['n'] == 2:
            raise KeyboardInterrupt
        return real(intent_id, expand)
    monkeypatch.setattr(fake.PaymentIntent, 'retrieve', staticmethod(interrupted))
    fake.polls = [(None, 'requires_payment_method')]
    assert run('pay') == 1
    assert 'Ctrl+C' in capsys.readouterr().out and 'capture' not in steps(fake) and steps(fake)[-2:] == ['cancel_action', 'cancel']


def test_a_busy_reader_is_explained_and_the_unused_payment_is_cancelled(fake, capsys):
    fake.busy = True
    assert run('pay') == 1
    assert 'busy' in capsys.readouterr().out and steps(fake) == ['create', 'cancel']


def test_a_problem_reading_the_card_details_never_turns_a_good_payment_into_a_failure(fake, monkeypatch, capsys):
    real = fake.PaymentIntent.retrieve

    def no_details(intent_id, expand=None):
        if expand:
            raise RuntimeError('no charge details')
        return real(intent_id, expand)
    monkeypatch.setattr(fake.PaymentIntent, 'retrieve', staticmethod(no_details))
    assert run('pay') == 0 and 'PAID in test mode' in capsys.readouterr().out


def test_unknown_commands_print_help(fake, capsys):
    assert run('dance') == 2 and 'python3 reader_test_CG.py check' in capsys.readouterr().out
