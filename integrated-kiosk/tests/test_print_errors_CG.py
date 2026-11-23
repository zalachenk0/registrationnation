# ============================================================================
# File:         test_print_errors_CG.py
# Written by:   CG (Cristian Gonzalez)
# What it does: Tests that a failed print always comes back to the screen as a
#               readable JSON error and lands in runtime/kiosk-problems.log
#               with only the reference and the error, no customer info.
# Team-written: Logic written by CG (Cristian Gonzalez). Outside code is used
#               only through the third-party sources listed below.
# Third-party:  pytest; Flask's test client; Stripe and CUPS are replaced by
#               stand-ins in these tests.
# ============================================================================
"""Nov 23: on the Pi the print screen flashed and went back to payment with no message. These pin down the server half."""
import pytest

import scanner_api_BV_CG as api
from payment_service_CG import PaymentError
from printing_BV import PrintError

PAYLOAD = {'registrant': 'Jane Sample', 'license_plate': 'ABC123', 'vehicle_year': '2019', 'vehicle_make': 'Honda',
           'vehicle_model': 'Accord', 'vin': '1HGCM82633A004352', 'confirmation_number': 'RN1-20261123-201500-A1B2C3',
           'receipt_email': 'jane@example.com'}


def log_lines(path):
    return path.read_text(encoding='utf-8').splitlines() if path.exists() else []


@pytest.fixture
def paid(monkeypatch):
    monkeypatch.setattr(api.payments, 'verify_paid', lambda payload: {'id': 'pi_test', 'amount': 11425, 'testMode': True})


def test_a_crash_while_making_the_receipt_is_json_and_logged(paid, monkeypatch, problem_log):
    def crash(payload, payment=None):
        raise OSError('No such file: assets/ttt-receipt.pdf')
    monkeypatch.setattr(api.delivery, 'submit', crash)
    reply = api.app.test_client().post('/api/print/registration', json=PAYLOAD)
    assert reply.status_code == 500 and reply.is_json
    assert 'OSError' in reply.json['error'] and 'Do not pay again' in reply.json['error']
    [line] = log_lines(problem_log)
    assert 'PRINT FAILED' in line and PAYLOAD['confirmation_number'] in line and 'ttt-receipt.pdf' in line
    assert 'Jane' not in line and 'jane@example.com' not in line and PAYLOAD['vin'] not in line  # reference and error only


@pytest.mark.parametrize('error, code', [(ValueError('The signature is missing or invalid.'), 422),
                                         (PrintError('CUPS rejected the job.'), 503),
                                         (PaymentError('Stripe has not confirmed payment.'), 409)])
def test_known_print_errors_keep_their_answer_and_are_logged(error, code, monkeypatch, problem_log):
    def refuse(payload, payment=None):
        raise error
    monkeypatch.setattr(api.payments, 'verify_paid', lambda payload: {'id': 'pi_test'})
    monkeypatch.setattr(api.delivery, 'submit', refuse)
    reply = api.app.test_client().post('/api/print/registration', json=PAYLOAD)
    assert reply.status_code == code and reply.json == {'error': str(error)}  # exactly what the screen got before
    [line] = log_lines(problem_log)
    assert 'PRINT FAILED' in line and type(error).__name__ in line and str(error) in line


def test_a_payment_problem_before_printing_is_logged_too(monkeypatch, problem_log):
    def unpaid(payload):
        raise PaymentError('No payment exists for this transaction. Use Pay before printing.')
    monkeypatch.setattr(api.payments, 'verify_paid', unpaid)
    reply = api.app.test_client().post('/api/print/registration', json=PAYLOAD)
    assert reply.status_code == 409 and 'No payment exists' in reply.json['error']
    assert 'PRINT FAILED' in log_lines(problem_log)[0]


def test_a_bad_body_is_logged_without_a_reference(monkeypatch, problem_log):
    reply = api.app.test_client().post('/api/print/registration', data='not json', content_type='text/plain')
    assert reply.is_json and reply.status_code in (409, 422)
    assert 'no reference' in log_lines(problem_log)[0]


def test_a_good_print_writes_nothing_to_the_log(paid, monkeypatch, problem_log):
    monkeypatch.setattr(api.delivery, 'submit', lambda payload, payment=None: {'success': True, 'state': 'queued'})
    reply = api.app.test_client().post('/api/print/registration', json=PAYLOAD)
    assert reply.status_code == 200 and reply.json['success']
    assert log_lines(problem_log) == []

