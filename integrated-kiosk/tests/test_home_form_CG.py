# ==============================================================================
# File:         test_home_form_CG.py
# Written by:   CG (Cristian Gonzalez)
# What it does: Tests for the home test form server: the payment, Monday.com
#               record and photo steps, and its safety rules.
# Team-written: All logic written by CG.
# Third-party:  pytest; Pillow; stripe (library only). Stripe and Monday.com
#               are replaced by stand-ins in these tests.
# ==============================================================================
import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import stripe
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'home_test_CG'))
import home_form_server_CG as home  # noqa: E402
import crm_photos_CG as crm_photos  # noqa: E402
import monday_service_CG as monday_service  # noqa: E402

REF = 'REG-HOME-TEST-0001'
FORM = dict(registrant='Jane Sample', license_plate='TEST-123', vehicle_year='2021', vehicle_make='Toyota',
            vehicle_model='Camry', vin='1HGCM82633A004352', confirmation_number=REF)
HEADERS = {'X-Kiosk-Payment': '1'}


def picture(width=300, height=200, color=(200, 30, 30), kind='JPEG'):
    buffer = io.BytesIO()
    Image.new('RGB', (width, height), color).save(buffer, kind)
    return buffer.getvalue()


class FakeStripe:  # the same idea as the kiosk's other payment tests: Stripe's state machine without a network
    def __init__(self, monkeypatch):
        self.intents, self.by_key = {}, {}
        monkeypatch.setattr(stripe.PaymentIntent, 'create', self.create)
        monkeypatch.setattr(stripe.PaymentIntent, 'retrieve', lambda iid, **kw: self.intents[iid])
        monkeypatch.setattr(stripe.PaymentIntent, 'capture', self.capture)
        monkeypatch.setattr(stripe.PaymentIntent, 'cancel', lambda iid, **kw: self.intents[iid])
        monkeypatch.setattr(stripe.PaymentIntent, 'modify', lambda iid, **kw: self.intents[iid])

    def create(self, **kw):
        if kw['idempotency_key'] in self.by_key:
            return self.intents[self.by_key[kw['idempotency_key']]]
        iid = 'pi_home_%d' % (len(self.intents) + 1)
        intent = stripe.PaymentIntent.construct_from(dict(id=iid, client_secret=iid + '_secret_fake', amount=kw['amount'], amount_received=0, currency=kw['currency'],
                                                          livemode=False, metadata=kw['metadata'], payment_method_types=kw['payment_method_types'], status='requires_payment_method'), 'sk_test_unit_fixture')
        self.intents[iid], self.by_key[kw['idempotency_key']] = intent, iid
        return intent

    def capture(self, iid, **kw):
        self.intents[iid].status, self.intents[iid].amount_received = 'succeeded', self.intents[iid].amount
        return self.intents[iid]

    def authorize(self, iid):  # what the reader or Stripe.js does once the card is accepted
        self.intents[iid].status = 'requires_capture'


@pytest.fixture
def home_test(tmp_path, monkeypatch):
    for name in ('KIOSK_PROBLEM_LOG', 'KIOSK_LICENSE_PHOTO_DIR', 'KIOSK_INSURANCE_PHOTO_DIR'):
        monkeypatch.setenv(name, 'restored-after-the-test')          # create_app changes these three, so the test puts them back afterwards
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_test_unit_fixture')
    monkeypatch.delenv('STRIPE_PUBLISHABLE_KEY', raising=False)
    monkeypatch.setenv('STRIPE_LOCATION_ID', 'tml_unit_fixture')  # [CG] the physical reader is the only way to pay
    monkeypatch.delenv('STRIPE_SIMULATED', raising=False)
    monkeypatch.setenv('MONDAY_LICENSE_PHOTO_COLUMN', 'files_license')
    monkeypatch.setenv('MONDAY_INSURANCE_PHOTO_COLUMN', 'files_insurance')
    monkeypatch.delenv('KIOSK_KEEP_LOCAL_PHOTOS', raising=False)
    fake = FakeStripe(monkeypatch)
    uploads, records = [], []
    monkeypatch.setattr(monday_service, 'upload_file', lambda item, column, name, data, setting: uploads.append((item, column, name, len(data))))
    monkeypatch.setattr(home, 'create_monday_record', lambda name, fields: (records.append((name, fields)), {'itemId': '777', 'warnings': []})[1])
    app = home.create_app(tmp_path / 'scratch')
    return SimpleNamespace(app=app, client=app.test_client(), stripe=fake, uploads=uploads, records=records, scratch=tmp_path / 'scratch', desk=app.extensions['home'].photos)


def visit(t, with_photos=True):
    if with_photos:
        r = t.client.post('/api/home/photos', data={'license_front': (io.BytesIO(picture()), 'front.jpg'), 'insurance': (io.BytesIO(picture(color=(30, 30, 200))), 'card.jpg')}, headers=HEADERS)
        assert r.status_code == 200 and r.get_json()['saved'] == ['license', 'insurance']
    r = t.client.post('/api/payment/create_intent', json={**FORM, 'payment_mode': 'terminal'}, headers=HEADERS)
    assert r.status_code == 200 and r.get_json()['status'] == 'requires_payment_method'
    t.stripe.authorize(r.get_json()['id'])
    r = t.client.post('/api/payment/capture_intent', json=FORM, headers=HEADERS)
    assert r.get_json()['status'] == 'succeeded'
    return r.get_json()


def save_record(t, intent):
    return t.client.post('/api/monday/create_record', json={'itemName': 'Sample, Jane - TEST-123', 'fields': {'confirmationNumber': REF, 'paymentIntentId': intent['id']}})


def test_the_page_is_served_and_nothing_is_cached(home_test):
    r = home_test.client.get('/')
    assert r.status_code == 200 and b'Home test form' in r.data and r.headers['Cache-Control'] == 'no-store'


def test_the_whole_visit_without_camera_scanner_or_printer(home_test):
    intent = visit(home_test)
    assert save_record(home_test, intent).get_json()['success'] is True
    assert home_test.records and home_test.records[0][1]['confirmationNumber'] == REF
    assert home_test.uploads == []                                    # photos go after the answer, in the background
    home_test.desk.process_once()
    assert sorted(u[1] for u in home_test.uploads) == ['files_insurance', 'files_license'] and {u[0] for u in home_test.uploads} == {'777'}
    raw = home_test.client.get('/api/home/photo_status/' + REF).get_data(as_text=True)
    assert str(home_test.scratch) not in raw and 'path' not in raw and '.jpg' not in raw          # the browser is never told where files are kept
    status = home_test.client.get('/api/home/photo_status/' + REF).get_json()['photos']
    assert {p['kind']: (p['state'], p['onKiosk']) for p in status} == {'license': ('uploaded', False), 'insurance': ('uploaded', False)}   # uploaded, and the local copies are gone
    assert not list((home_test.scratch / 'license_front').glob('*')) and not list((home_test.scratch / 'insurance_front').glob('*'))


def test_photos_taken_after_payment_started_do_not_join_that_payment(home_test):
    intent = visit(home_test, with_photos=False)
    home_test.client.post('/api/home/photos', data={'license_front': (io.BytesIO(picture()), 'late.jpg')}, headers=HEADERS)
    save_record(home_test, intent)
    home_test.desk.process_once()
    assert home_test.uploads == []                                    # same rule as the kiosk: photos are linked when payment starts


def test_a_visit_with_no_photos_still_saves_the_record(home_test):
    intent = visit(home_test, with_photos=False)
    assert save_record(home_test, intent).get_json()['success'] is True
    assert home_test.client.get('/api/home/photo_status/' + REF).get_json()['photos'] == []


def test_the_keep_setting_keeps_the_copies(home_test, monkeypatch):
    monkeypatch.setenv('KIOSK_KEEP_LOCAL_PHOTOS', '1')
    intent = visit(home_test)
    save_record(home_test, intent)
    home_test.desk.process_once()
    status = home_test.client.get('/api/home/photo_status/' + REF).get_json()['photos']
    assert all(p['onKiosk'] for p in status) and len(list((home_test.scratch / 'license_front').glob('*.jpg'))) == 1


@pytest.mark.parametrize('files,message', [({}, 'at least one'), ({'insurance': (io.BytesIO(b'not a picture'), 'x.jpg')}, 'not a readable picture'),
                                           ({'insurance': (io.BytesIO(picture(10, 10)), 'tiny.jpg')}, '32 pixels')])
def test_bad_uploads_are_refused_with_a_plain_message(home_test, files, message):
    r = home_test.client.post('/api/home/photos', data=files, headers=HEADERS)
    assert r.status_code == 422 and message in r.get_json()['error']
    assert not list((home_test.scratch / 'insurance_front').glob('*'))


def test_png_pictures_are_saved_as_jpegs_like_the_camera_photos(home_test):
    r = home_test.client.post('/api/home/photos', data={'license_front': (io.BytesIO(picture(kind='PNG')), 'front.png')}, headers=HEADERS)
    assert r.status_code == 200
    saved = list((home_test.scratch / 'license_front').glob('*'))
    assert len(saved) == 1 and saved[0].suffix == '.jpg' and saved[0].read_bytes()[:2] == b'\xff\xd8'


def test_it_only_answers_on_this_computer(home_test):
    assert home_test.client.get('/', headers={'Host': '192.168.1.50:5055'}).status_code == 403
    assert home_test.client.get('/api/payment/config', headers={'Host': 'example.com'}).status_code == 403
    assert home_test.client.get('/', headers={'Host': 'localhost:5055'}).status_code == 200


def test_the_setup_check_never_reveals_keys_or_paths(home_test, monkeypatch):
    monkeypatch.setenv('MONDAY_API_TOKEN', 'monday-secret-token-value')
    monkeypatch.setenv('MONDAY_BOARD_ID', '123456')
    body = home_test.client.get('/api/home/settings').get_data(as_text=True)
    assert 'monday-secret-token-value' not in body and 'sk_test_unit_fixture' not in body and 'pk_test_unit_fixture' not in body and str(home_test.scratch) not in body
    data = home_test.client.get('/api/home/settings').get_json()
    assert data['stripeKeys'] and data['monday'] and data['licenseColumn'] and data['insuranceColumn'] and data['reader'] == 'real' and data['liveKeyFound'] is False


def test_a_live_stripe_key_is_flagged_and_payments_refused(home_test, monkeypatch):
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_live_not_a_real_key')
    assert home_test.client.get('/api/home/settings').get_json()['liveKeyFound'] is True
    r = home_test.client.post('/api/payment/create_intent', json={**FORM, 'payment_mode': 'terminal'}, headers=HEADERS)
    assert r.status_code == 409 and 'test' in r.get_json()['error'].lower()
    assert not home_test.stripe.intents


def test_the_home_form_is_card_reader_only_too(home_test, monkeypatch):
    """[CG] same rules as the kiosk: no typed card, no simulator, and the page doesn't load Stripe.js at all."""
    r = home_test.client.post('/api/payment/create_intent', json={**FORM, 'payment_mode': 'card'}, headers=HEADERS)
    assert r.status_code == 409 and 'turned off' in r.get_json()['error'] and not home_test.stripe.intents
    r = home_test.client.post('/api/payment/create_intent', json=FORM, headers=HEADERS)  # no mode at all: the reader, as at the kiosk
    assert r.status_code == 200 and r.get_json()['method'] == 'terminal'
    assert home_test.client.post('/api/payment/reader_simulate_tap', json=FORM, headers=HEADERS).status_code in (404, 405)
    monkeypatch.setenv('STRIPE_SIMULATED', '1')
    assert home_test.client.get('/api/home/settings').get_json()['reader'] == 'real'
    page = home_test.client.get('/').get_data(as_text=True)
    assert 'js.stripe.com/v3' not in page and 'Typed card' not in page and 'simCard' not in page and 'Pretend' not in page


def test_the_photo_status_route_ignores_odd_references(home_test):
    assert home_test.client.get('/api/home/photo_status/..%2f..%2fetc').status_code == 404
    assert home_test.client.get('/api/home/photo_status/' + 'x' * 200).status_code == 404


def test_everything_the_test_form_writes_stays_in_its_own_folder(home_test):
    visit(home_test)
    for name in ('payments.sqlite3', 'photos.sqlite3'):
        assert (home_test.scratch / name).exists()
    import os
    for name in ('KIOSK_PROBLEM_LOG', 'KIOSK_LICENSE_PHOTO_DIR', 'KIOSK_INSURANCE_PHOTO_DIR'):
        assert Path(os.environ[name]).resolve().is_relative_to(home_test.scratch.resolve())


def test_monday_trouble_is_reported_not_hidden(home_test, monkeypatch):
    intent = visit(home_test)

    def refuse(name, fields):
        raise monday_service.MondayError('Not Authenticated', 401)
    monkeypatch.setattr(home, 'create_monday_record', refuse)
    r = save_record(home_test, intent)
    assert r.status_code == 401 and r.get_json()['error'] == 'Not Authenticated'
    home_test.desk.process_once()
    assert home_test.uploads == []                                    # no record, so no photo is sent and none is deleted
    assert len(list((home_test.scratch / 'license_front').glob('*.jpg'))) == 1


def test_a_refused_photo_shows_why_counts_down_and_keeps_the_copy_until_it_gives_up(home_test, monkeypatch):
    intent = visit(home_test)
    save_record(home_test, intent)
    leaked_place = str(home_test.scratch / 'license_front' / 'secret-name.jpg')

    def refuse(item, column, name, data, setting):
        if column == 'files_license':  # like a wrong column id in .env: only the license photo is refused
            raise monday_service.MondayError(f'Column not found ({leaked_place})', 400)
        home_test.uploads.append((item, column, name, len(data)))
    monkeypatch.setattr(monday_service, 'upload_file', refuse)
    import sqlite3
    waits = []
    for attempt in range(1, crm_photos.MAX_ATTEMPTS + 1):
        home_test.desk.process_once()
        raw = home_test.client.get('/api/home/photo_status/' + REF).get_data(as_text=True)
        assert str(home_test.scratch) not in raw and 'secret-name' not in raw          # the reason is shown, the file's location never is
        rows = {p['kind']: p for p in home_test.client.get('/api/home/photo_status/' + REF).get_json()['photos']}
        lic = rows['license']
        assert lic['attempts'] == attempt and 'Column not found' in lic['note'] and lic['onKiosk'] is True   # copy kept on every try
        if attempt < crm_photos.MAX_ATTEMPTS:
            assert lic['state'] == 'waiting'
            waits.append(lic['retryIn'])
            with sqlite3.connect(home_test.desk.database) as db:                     # skip the wait instead of sleeping through it
                db.execute("UPDATE crm_photos SET next_try = 0 WHERE kind = 'license'")
        else:
            assert lic['state'] == 'failed' and lic['retryIn'] is None
    assert [round(w / 60) for w in waits] == [1, 2, 4, 8]                             # about 15 minutes in total before it gives up
    assert rows['insurance']['state'] == 'uploaded' and rows['insurance']['onKiosk'] is False   # each photo is judged on its own
    assert len(list((home_test.scratch / 'license_front').glob('*.jpg'))) == 1
    assert 'MONDAY PHOTO NOT ATTACHED' in (home_test.scratch / 'kiosk-problems.log').read_text()


def test_the_page_keeps_watching_long_enough_to_see_a_photo_give_up():
    import re
    page = (ROOT / 'home_test_CG' / 'home_form_CG.html').read_text(encoding='utf-8')
    minutes = re.search(r'const WATCH_FOR_MS = (\d+) \* 60 \* 1000;', page)
    assert minutes and int(minutes.group(1)) >= 16                                    # 1 + 2 + 4 + 8 minutes of waits, plus the worker's 15 s naps
    assert 'WATCH_FOR_MS) clearInterval' in page and '<th>Why</th>' in page and 'p.note' in page and 'textContent = text' in page
