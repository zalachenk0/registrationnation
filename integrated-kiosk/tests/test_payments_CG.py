# ============================================================================
# File:         test_payments_CG.py
# Written by:   CG (Cristian Gonzalez)
# What it does: Tests for the payment ledger and Stripe handling.
# Team-written: Logic written by CG (Cristian Gonzalez). Outside code is used
#               only through the third-party sources listed below.
# Third-party:  pytest; pypdf; Stripe Python library; Stripe, Monday.com and
#               CUPS are replaced by stand-ins in these tests.
# ============================================================================
"""Payment boundary tests: fake Stripe responses, no account/key/network required."""
import io
import subprocess
import time
from unittest.mock import Mock

import pytest
from pypdf import PdfReader
import stripe

import scanner_api_BV_CG as api
import payment_service_CG as payment
from printing_BV import PrintService

FORM = dict(registrant='Jane Sample', license_plate='DEMO-123', vehicle_year='2021',
            vehicle_make='Example', vehicle_model='Sedan', vin='1HGCM82633A004352',
            confirmation_number='REG-TEST-PAYMENT-0001')
HEADERS = {'X-Kiosk-Payment': '1'}


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_test_unit_fixture')
    monkeypatch.setenv('STRIPE_PUBLISHABLE_KEY', 'pk_test_unit_fixture')
    service = payment.PaymentService(tmp_path / 'payments.sqlite3')
    monkeypatch.setattr(api, 'payments', service)
    reference, fingerprint = service._identity(FORM)
    intent = stripe.PaymentIntent.construct_from(dict(id='pi_test_fixture', client_secret='pi_test_fixture_secret_fake',
        amount=11425, amount_received=0, currency='usd', livemode=False,
        metadata={'kiosk_reference': reference, 'kiosk_fingerprint': fingerprint},
        payment_method_types=['card'], status='requires_payment_method'), 'sk_test_unit_fixture')
    create = Mock(return_value=intent)
    retrieve = Mock(return_value=intent)
    def capture(*args, **kwargs):
        intent.status = 'succeeded'
        intent.amount_received = 11425
        return intent
    captured = Mock(side_effect=capture)
    monkeypatch.setattr(stripe.PaymentIntent, 'create', create)
    monkeypatch.setattr(stripe.PaymentIntent, 'retrieve', retrieve)
    monkeypatch.setattr(stripe.PaymentIntent, 'capture', captured)
    return service, intent, create, retrieve, captured


def test_fixed_amount_same_intent_survives_restart(service):
    payments, intent, create, _, _ = service
    assert payments.create_payment_intent(dict(FORM, amount=1))['id'] == intent.id
    restarted = payment.PaymentService(payments.database)
    assert restarted.create_payment_intent(FORM)['id'] == intent.id
    assert create.call_count == 1
    assert create.call_args.kwargs['amount'] == 11425
    assert create.call_args.kwargs['capture_method'] == 'manual'
    assert create.call_args.kwargs['payment_method_types'] == ['card']
    assert 'receipt_email' not in create.call_args.kwargs
    with pytest.raises(payment.PaymentError, match='original'):
        payments.create_payment_intent(dict(FORM, vin='1HGCM82633A004353'))


def test_capture_retries_after_lost_response_do_not_capture_twice(service):
    payments, intent, _, _, capture = service
    payments.create_payment_intent(FORM)
    intent.status = 'requires_capture'
    def lost_response(*args, **kwargs):
        intent.status, intent.amount_received = 'succeeded', 11425
        raise stripe.APIConnectionError('simulated response lost')
    capture.side_effect = lost_response
    with pytest.raises(payment.PaymentError, match='not confirmed capture'):
        payments.capture_payment_intent(FORM)
    assert payment.PaymentService(payments.database).capture_payment_intent(FORM)['status'] == 'succeeded'
    assert capture.call_count == 1


def test_confirmation_recovery_returns_same_sdk_intent(service):
    payments, intent, create, _, _ = service
    payments.create_payment_intent(FORM)
    intent.status = 'requires_confirmation'
    recovered = payments.create_payment_intent(FORM)
    assert recovered['paymentIntent']['id'] == intent.id
    assert recovered['paymentIntent']['client_secret'] == intent.client_secret
    assert create.call_count == 1


def test_create_retry_uses_same_idempotency_key_and_old_unknown_is_blocked(service):
    payments, intent, create, _, _ = service
    create.side_effect = [stripe.APIConnectionError('lost'), intent]
    with pytest.raises(payment.PaymentError):
        payments.create_payment_intent(FORM)
    payments.create_payment_intent(FORM)
    assert create.call_args_list[0].kwargs == create.call_args_list[1].kwargs
    db = payments._db()
    with db:
        db.execute('UPDATE payments SET intent_id=NULL, created=?', (time.time() - 25 * 3600,))
    db.close()
    with pytest.raises(payment.PaymentError, match='older payment'):
        payments.create_payment_intent(FORM)
    assert create.call_count == 2


@pytest.mark.parametrize('field,value', [('amount', 1), ('currency', 'eur'), ('livemode', True),
    ('metadata', {}), ('payment_method_types', ['us_bank_account']), ('payment_method_types', ['card', 'us_bank_account'])])
def test_reject_foreign_or_tampered_intents(service, field, value):
    payments, intent, _, _, _ = service
    payments.create_payment_intent(FORM)
    setattr(intent, field, value)
    with pytest.raises(payment.PaymentError, match='does not match'):
        payments.verify_paid(FORM)


def test_unpaid_and_processing_never_print_or_capture(service, monkeypatch):
    payments, intent, _, _, capture = service
    submit = Mock()
    monkeypatch.setattr(api.printer, 'submit', submit)
    client = api.app.test_client()
    assert client.post('/api/print/registration', json=FORM).status_code == 409
    payments.create_payment_intent(FORM)
    for status in ('requires_payment_method', 'requires_confirmation', 'processing', 'canceled'):
        intent.status = status
        assert client.post('/api/print/registration', json=FORM).status_code == 409
        assert client.post('/api/payment/capture_intent', json=FORM, headers=HEADERS).status_code == 409
    submit.assert_not_called()
    capture.assert_not_called()


def test_paid_print_retry_and_receipt(service, monkeypatch):
    payments, intent, create, _, capture = service
    monkeypatch.setattr(api, 'printer', PrintService())
    monkeypatch.setattr('printing_BV.shutil.which', lambda _: '/usr/bin/lp')
    run = Mock(side_effect=[subprocess.CompletedProcess(['lp'], 1, b'', b'offline'),
                           subprocess.CompletedProcess(['lp'], 0, b'accepted', b'')])
    monkeypatch.setattr('printing_BV.subprocess.run', run)
    client = api.app.test_client()
    assert client.post('/api/payment/create_intent', json=FORM, headers=HEADERS).status_code == 200
    intent.status = 'requires_capture'
    assert client.post('/api/payment/capture_intent', json=FORM, headers=HEADERS).json['status'] == 'succeeded'
    assert client.post('/api/print/registration', json=FORM).status_code == 503
    assert client.post('/api/payment/create_intent', json=FORM, headers=HEADERS).json['status'] == 'succeeded'
    assert client.post('/api/print/registration', json=FORM).status_code == 200
    assert client.post('/api/print/registration', json=FORM).json['duplicate']
    assert create.call_count == capture.call_count == 1
    assert run.call_count == 2
    text = PdfReader(io.BytesIO(run.call_args.kwargs['input'])).pages[0].extract_text()
    assert 'STRIPE TEST PAYMENT RECEIPT' in text and '$114.25 TEST' in text
    assert intent.id in text and 'MOCK RENEWAL STICKER' in text
    assert client.post('/api/print/registration', json=dict(FORM, license_plate='OTHER')).status_code == 409


def test_payment_api_validation_and_secret_handling(service, monkeypatch):
    client = api.app.test_client()
    assert client.post('/api/payment/create_intent', json=FORM).status_code == 403
    assert client.post('/api/payment/create_intent', json=FORM, headers=dict(HEADERS, Origin='https://other.example')).status_code == 403
    for payload in ([], {}, None, {'paymentIntentId': 'pi_foreign'}):
        assert client.post('/api/payment/capture_intent', json=payload, headers=HEADERS).status_code == 422
    assert client.get('/.env').status_code == 404
    assert client.get('/runtime/payments.sqlite3').status_code == 404
    config = client.get('/api/payment/config')
    assert config.json['testMode'] and config.json['publishableKey'] == 'pk_test_unit_fixture'
    assert 'sk_test' not in config.text
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_live_not_a_real_key')
    assert client.get('/api/payment/config').status_code == 409
    monkeypatch.delenv('STRIPE_SECRET_KEY')
    assert client.get('/api/payment/config').status_code == 409


def test_publishable_key_required_and_reader_settings_ignored(service, monkeypatch):
    monkeypatch.setenv('STRIPE_SIMULATED', 'false')
    monkeypatch.delenv('STRIPE_LOCATION_ID', raising=False)
    assert payment.configuration()['amount'] == 11425
    monkeypatch.delenv('STRIPE_PUBLISHABLE_KEY')
    with pytest.raises(payment.PaymentError, match='STRIPE_PUBLISHABLE_KEY'):
        payment.configuration()
    monkeypatch.setenv('STRIPE_PUBLISHABLE_KEY', 'pk_live_not_real')
    with pytest.raises(payment.PaymentError):
        payment.configuration()
