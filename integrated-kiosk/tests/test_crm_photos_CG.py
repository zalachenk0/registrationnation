# ============================================================================
# File:         test_crm_photos_CG.py
# Written by:   CG (Cristian Gonzalez)
# What it does: Tests for the license (front and back) and insurance photos on
#               the Monday.com record.
# Team-written: Logic written by CG (Cristian Gonzalez). Outside code is used
#               only through the third-party sources listed below.
# Third-party:  OpenCV; NumPy; pytest; Requests; Stripe, Monday.com and CUPS are
#               replaced by stand-ins in these tests.
# ============================================================================
"""License-front, license-back and insurance photos on the Monday.com record. A stand-in Monday is used, nothing real is contacted."""
import io
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
import requests

import crm_photos_CG as crm_photos
import monday_service_CG as monday_service
import scanner_api_BV_CG as api

HEADERS = {'X-Kiosk-Payment': '1'}
REF = 'REG-AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE'
ITEM = '1234567890'


def make_photo(path, width=800, height=500, noise=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.RandomState(7)
    image = rng.randint(0, 255, (height, width, 3), np.uint8) if noise else np.full((height, width, 3), 180, np.uint8)
    cv2.imwrite(str(path), image)
    return path


class FakeMonday:
    """Stands in for monday.com's file endpoint and remembers every upload."""

    def __init__(self, monkeypatch):
        self.uploads, self.error, self.network_down, self.fail_column = [], None, False, None
        outer = self

        class Response:
            status_code = 200

            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

        def post(url, headers=None, data=None, files=None, json=None, timeout=None):
            if outer.network_down:
                raise requests.exceptions.ConnectionError('network is down')
            if outer.error or (outer.fail_column and f'column_id: "{outer.fail_column}"' in data['query']):
                return Response(outer.error or {'errors': [{'message': 'Column type is not valid for file upload'}]})
            name, content, mime = files['variables[file]']
            outer.uploads.append(dict(url=url, headers=headers, query=data['query'], filename=name, content=content, mime=mime, timeout=timeout))
            return Response({'data': {'add_file_to_column': {'id': str(len(outer.uploads))}}})

        monkeypatch.setattr(monday_service.requests, 'post', post)


@pytest.fixture
def kit(tmp_path, monkeypatch):
    monkeypatch.setenv('MONDAY_API_TOKEN', 'secret-token')
    monkeypatch.setenv('MONDAY_BOARD_ID', '555')
    monkeypatch.setenv('MONDAY_LICENSE_PHOTO_COLUMN', 'files_license')
    monkeypatch.setenv('MONDAY_INSURANCE_PHOTO_COLUMN', 'files_insurance')
    monkeypatch.setenv('KIOSK_INSURANCE_PHOTO_DIR', str(tmp_path / 'insurance_front'))
    monkeypatch.setenv('KIOSK_LICENSE_PHOTO_DIR', str(tmp_path / 'license_front'))
    monkeypatch.setenv('KIOSK_LICENSE_BACK_PHOTO_DIR', str(tmp_path / 'license_back'))
    monkeypatch.delenv('MONDAY_LICENSE_BACK_PHOTO_COLUMN', raising=False)   # like a Pi that hasn't had the back column added yet
    monkeypatch.delenv('KIOSK_KEEP_LOCAL_PHOTOS', raising=False)
    monkeypatch.delenv('MONDAY_API_VERSION', raising=False)
    desk = crm_photos.PhotoDesk(tmp_path)
    return SimpleNamespace(desk=desk, monday=FakeMonday(monkeypatch), tmp=tmp_path, monkeypatch=monkeypatch)


def one_customer(kit, name='a', fingerprint=None):
    """What the kiosk does while one customer scans: license front, license back, insurance card."""
    front = make_photo(kit.tmp / 'license_front' / f'front-{name}.jpg')
    kit.desk.license_taken(front)
    kit.desk.license_scanned(fingerprint or {'DAQ': f'license-{name}'})
    kit.desk.save_insurance(np.full((300, 500, 3), 90, np.uint8))
    return front


def uploaded(kit):
    return [(u['filename'], u['query']) for u in kit.monday.uploads]


def rows(kit):
    connection = sqlite3.connect(kit.desk.database)
    connection.row_factory = sqlite3.Row
    return {r['kind']: dict(r) for r in connection.execute('SELECT * FROM crm_photos ORDER BY kind')}


# ---------- the normal run ----------

def test_a_customers_two_photos_reach_the_right_item_and_columns(kit):
    one_customer(kit)
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    kit.desk.process_once()
    sent = {u['filename']: u['query'] for u in kit.monday.uploads}
    assert len(sent) == 2
    license_query = next(q for n, q in sent.items() if n.startswith('license-front'))
    insurance_query = next(q for n, q in sent.items() if n.startswith('insurance-card'))
    assert f'item_id: {ITEM}' in license_query and 'column_id: "files_license"' in license_query and 'add_file_to_column' in license_query
    assert f'item_id: {ITEM}' in insurance_query and 'column_id: "files_insurance"' in insurance_query
    assert all(u['url'] == 'https://api.monday.com/v2/file' and u['mime'] == 'image/jpeg' for u in kit.monday.uploads)
    assert all(u['headers'] == {'Authorization': 'secret-token'} for u in kit.monday.uploads)      # no json content type: it is a multipart form
    assert {r['state'] for r in rows(kit).values()} == {'uploaded'}


def test_photos_are_sent_as_a_readable_jpeg_of_sensible_size(kit):
    big = make_photo(kit.tmp / 'license_front' / 'big.jpg', 3000, 2000, noise=True)
    kit.desk.license_taken(big)
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    kit.desk.process_once()
    image = cv2.imdecode(np.frombuffer(kit.monday.uploads[0]['content'], np.uint8), cv2.IMREAD_COLOR)
    assert max(image.shape[:2]) == 2000 and image.shape[1] / image.shape[0] == pytest.approx(1.5, abs=0.01)   # shrunk, same shape
    small = make_photo(kit.tmp / 'license_front' / 'small.jpg', 640, 400)
    assert cv2.imdecode(np.frombuffer(crm_photos.prepare(small), np.uint8), cv2.IMREAD_COLOR).shape[:2] == (400, 640)   # small ones are left alone


def test_a_finished_upload_is_never_repeated(kit):
    one_customer(kit)
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    kit.desk.process_once()
    kit.desk.process_once()
    kit.desk.attach(REF, '999')           # a second record for the same payment must not move or repeat anything
    kit.desk.process_once()
    assert len(kit.monday.uploads) == 2


def test_nothing_is_uploaded_until_the_monday_record_exists(kit):
    one_customer(kit)
    kit.desk.bind(REF)
    kit.desk.process_once()
    assert kit.monday.uploads == []
    kit.desk.attach(REF, ITEM)
    kit.desk.process_once()
    assert len(kit.monday.uploads) == 2


# ---------- keeping one customer's photos off the next customer's record ----------

def test_an_abandoned_customers_photos_never_reach_the_next_customer(kit):
    one_customer(kit, 'a')                                         # customer A scans everything, never pays
    front_b = make_photo(kit.tmp / 'license_front' / 'front-b.jpg')
    kit.desk.license_taken(front_b)                                # customer B arrives minutes later
    kit.desk.license_scanned({'DAQ': 'license-b'})                 # a different license: a different person
    kit.desk.bind(REF)
    assert set(rows(kit)) == {'license'} and rows(kit)['license']['path'] == str(front_b)    # A's insurance photo was dropped
    log = (kit.tmp / 'kiosk-problems.log').read_text()
    assert 'MONDAY PHOTOS MISSING' in log and 'insurance-card' in log                          # and the office is told B has none


def test_the_next_customer_after_a_long_gap_starts_clean(kit, monkeypatch):
    clock = {'now': 1_000_000.0}
    monkeypatch.setattr(crm_photos, 'time', SimpleNamespace(time=lambda: clock['now']))
    one_customer(kit, 'a')
    clock['now'] += 11 * 60                                        # eleven minutes pass: nobody is still at the kiosk
    front_b = make_photo(kit.tmp / 'license_front' / 'front-b.jpg')
    kit.desk.license_taken(front_b)
    kit.desk.bind(REF)
    assert set(rows(kit)) == {'license'} and rows(kit)['license']['path'] == str(front_b)


def test_retaking_a_photo_or_going_back_a_step_keeps_the_insurance_photo(kit):
    one_customer(kit, 'a', fingerprint={'DAQ': 'same-card'})
    retake = make_photo(kit.tmp / 'license_front' / 'retake.jpg')
    kit.desk.license_taken(retake)                                 # the customer went back and took the front photo again
    kit.desk.license_scanned({'DAQ': 'same-card'})                 # the same license again
    kit.desk.bind(REF)
    assert set(rows(kit)) == {'license', 'insurance'} and rows(kit)['license']['path'] == str(retake)


def test_photos_older_than_the_limit_are_never_attached(kit, monkeypatch):
    clock = {'now': 5_000_000.0}
    monkeypatch.setattr(crm_photos, 'time', SimpleNamespace(time=lambda: clock['now']))
    one_customer(kit)
    clock['now'] += 46 * 60
    kit.desk.bind(REF)
    assert rows(kit) == {}


def test_a_payment_takes_the_photos_and_leaves_none_for_the_next_customer(kit):
    one_customer(kit, 'a')
    kit.desk.bind(REF)
    kit.desk.bind('REG-SECOND')
    assert {r for r in rows(kit)} == {'license', 'insurance'}     # the next payment got nothing
    assert all(r['reference'] == REF for r in rows(kit).values())


def test_the_same_payment_again_keeps_its_first_link_and_does_not_wipe_the_next_customer(kit):
    one_customer(kit, 'a')
    kit.desk.bind(REF)
    first = rows(kit)
    next_front = make_photo(kit.tmp / 'license_front' / 'next.jpg')
    kit.desk.license_taken(next_front)                             # the next customer has started
    kit.desk.bind(REF)                                             # a late retry for the first payment (e.g. switching to the typed card)
    assert rows(kit) == first
    kit.desk.bind('REG-NEXT')
    connection = sqlite3.connect(kit.desk.database)
    assert connection.execute("SELECT path FROM crm_photos WHERE reference='REG-NEXT'").fetchone()[0] == str(next_front)


def test_a_reference_that_is_not_a_plain_reference_is_ignored(kit):
    one_customer(kit)
    for bad in (None, '', 123, "x'; DROP TABLE crm_photos;--", '../../etc/passwd', 'a' * 200):
        kit.desk.bind(bad)
        kit.desk.attach(bad, ITEM)
    assert rows(kit) == {} and len(kit.desk.current) == 2          # nothing was linked and nothing was thrown away


# ---------- settings ----------

def test_with_no_photo_columns_set_nothing_is_uploaded_and_nothing_goes_wrong(kit, monkeypatch):
    monkeypatch.delenv('MONDAY_LICENSE_PHOTO_COLUMN')
    monkeypatch.delenv('MONDAY_INSURANCE_PHOTO_COLUMN')
    one_customer(kit)
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    kit.desk.process_once()
    assert kit.monday.uploads == [] and {r['state'] for r in rows(kit).values()} == {'skipped'}
    assert not (kit.tmp / 'kiosk-problems.log').exists()             # the feature is simply off, so the office isn't bothered


def test_with_only_one_column_set_only_that_photo_goes(kit, monkeypatch):
    monkeypatch.delenv('MONDAY_INSURANCE_PHOTO_COLUMN')
    one_customer(kit)
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    kit.desk.process_once()
    assert [n.split('-')[0] for n, _ in uploaded(kit)] == ['license']
    assert rows(kit)['insurance']['state'] == 'skipped'


# ---------- when something goes wrong ----------

def test_a_refused_photo_is_retried_later_and_then_succeeds(kit, monkeypatch):
    clock = {'now': 9_000_000.0}
    monkeypatch.setattr(crm_photos, 'time', SimpleNamespace(time=lambda: clock['now']))
    one_customer(kit)
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    kit.monday.error = {'errors': [{'message': 'Column type is not valid for file upload'}]}
    kit.desk.process_once()
    assert kit.monday.uploads == [] and {r['state'] for r in rows(kit).values()} == {'waiting'}
    kit.monday.error = None
    kit.desk.process_once()
    assert kit.monday.uploads == []                                # not yet: it waits before trying again
    clock['now'] += 61
    kit.desk.process_once()
    assert len(kit.monday.uploads) == 2 and {r['state'] for r in rows(kit).values()} == {'uploaded'}


def test_giving_up_tells_the_office_which_payment_item_and_where_the_photo_is_saved(kit, monkeypatch):
    clock = {'now': 2_000_000.0}
    monkeypatch.setattr(crm_photos, 'time', SimpleNamespace(time=lambda: clock['now']))
    front = one_customer(kit)
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    kit.monday.error = {'errors': [{'message': 'Not Authenticated', 'extensions': {'code': 'UserUnauthorizedException'}}]}
    for _ in range(crm_photos.MAX_ATTEMPTS):
        kit.desk.process_once()
        clock['now'] += 1000
    assert {r['state'] for r in rows(kit).values()} == {'failed'}
    lines = (kit.tmp / 'kiosk-problems.log').read_text().splitlines()
    assert len(lines) == 2 and all('MONDAY PHOTO NOT ATTACHED' in l and REF in l and ITEM in l and 'check MONDAY_API_TOKEN' in l for l in lines)
    assert str(front) in ''.join(lines) and 'secret-token' not in ''.join(lines)
    kit.desk.process_once()                                        # and it stops trying
    assert kit.monday.uploads == []


def test_no_internet_never_crashes_and_the_photo_goes_later(kit, monkeypatch):
    clock = {'now': 3_000_000.0}
    monkeypatch.setattr(crm_photos, 'time', SimpleNamespace(time=lambda: clock['now']))
    one_customer(kit)
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    kit.monday.network_down = True
    kit.desk.process_once()
    kit.monday.network_down = False
    clock['now'] += 120
    kit.desk.process_once()
    assert len(kit.monday.uploads) == 2


def test_a_photo_file_that_has_gone_missing_is_handled(kit):
    front = one_customer(kit)
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    front.unlink()
    kit.desk.process_once()
    assert [n.split('-')[0] for n, _ in uploaded(kit)] == ['insurance']      # the other photo still goes
    assert rows(kit)['license']['state'] == 'waiting' and 'missing' in rows(kit)['license']['error']


def test_unfinished_uploads_carry_on_after_a_restart(kit):
    one_customer(kit)
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    restarted = crm_photos.PhotoDesk(kit.tmp)                       # the kiosk was switched off before the worker ran
    restarted.process_once()
    assert len(kit.monday.uploads) == 2


def test_the_background_worker_uploads_by_itself_and_survives_an_error(kit, monkeypatch):
    desk = kit.desk
    real = desk.process_once
    calls = {'n': 0}

    def flaky():
        calls['n'] += 1
        if calls['n'] == 1:
            raise RuntimeError('boom')
        real()
    monkeypatch.setattr(desk, 'process_once', flaky)
    one_customer(kit)
    desk.bind(REF)
    desk.start()
    desk.attach(REF, ITEM)                                          # wakes the worker
    import time as real_time
    for _ in range(80):
        if len(kit.monday.uploads) == 2:
            break
        real_time.sleep(0.1)
    desk.stop.set()
    desk.wake.set()
    assert len(kit.monday.uploads) == 2 and calls['n'] >= 2
    assert 'MONDAY PHOTO WORKER' in (kit.tmp / 'kiosk-problems.log').read_text()


# ---------- the upload request itself ----------

def test_ids_that_could_change_the_request_never_reach_monday(kit):
    with pytest.raises(monday_service.MondayError):
        monday_service.upload_file('12 } }; mutation { x', 'files_license', 'a.jpg', b'x')
    with pytest.raises(monday_service.MondayError, match='column id'):
        monday_service.upload_file(ITEM, 'files" ) { id } } #', 'a.jpg', b'x')
    with pytest.raises(monday_service.MondayError):
        monday_service.upload_file(ITEM, '', 'a.jpg', b'x')
    assert kit.monday.uploads == []


def test_a_column_that_is_not_a_files_column_gets_the_right_hint(kit):
    kit.monday.error = {'errors': [{'message': 'Column type is not supported'}]}
    with pytest.raises(monday_service.MondayError) as caught:
        monday_service.upload_file(ITEM, 'text_abc', 'a.jpg', b'x', 'MONDAY_LICENSE_PHOTO_COLUMN')
    assert 'MONDAY_LICENSE_PHOTO_COLUMN is the id of a Files column' in str(caught.value) and 'COLUMN_MAP' not in str(caught.value)


def test_not_being_set_up_for_monday_is_a_clear_error_and_no_request(kit, monkeypatch):
    monkeypatch.delenv('MONDAY_API_TOKEN')
    with pytest.raises(monday_service.MondayError, match='MONDAY_API_TOKEN'):
        monday_service.upload_file(ITEM, 'files_license', 'a.jpg', b'x')
    assert kit.monday.uploads == []


def test_the_pinned_api_version_is_sent_when_one_is_set(kit, monkeypatch):
    monkeypatch.setenv('MONDAY_API_VERSION', '2025-01')
    monday_service.upload_file(ITEM, 'files_license', 'a.jpg', b'x')
    assert kit.monday.uploads[0]['headers']['API-Version'] == '2025-01'


def test_photo_test_command_uploads_a_sample_picture(kit, capsys):
    assert monday_service._cli(['x', 'photo-test', ITEM, 'files_license']) == 0
    assert kit.monday.uploads[0]['filename'] == 'kiosk-photo-test.jpg' and 'Worked' in capsys.readouterr().out
    kit.monday.error = {'errors': [{'message': 'Not Authenticated'}]}
    assert monday_service._cli(['x', 'photo-test', ITEM, 'files_license']) == 1


# ---------- the whole route sequence, as the kiosk screen calls it ----------

@pytest.fixture
def route(kit, monkeypatch):
    monkeypatch.setattr(api, 'photos', kit.desk)
    monkeypatch.setenv('KIOSK_LICENSE_PHOTO_DIR', str(kit.tmp / 'license_front'))
    image = np.full((600, 900, 3), 140, np.uint8)
    monkeypatch.setattr(api.camera, 'get', lambda preview=False, timeout=10: image)
    monkeypatch.setattr(api, 'scan_license', lambda img: {'success': True, 'fields': {'DAQ': 'license-route'}})
    monkeypatch.setattr(api, 'scan_insurance', lambda img: {'success': True, 'fields': {'insurance_company': 'ACME'}})
    monkeypatch.setattr(api.delivery, 'enqueue_capture', lambda event, key=None: {'queued': True})
    monkeypatch.setattr(api.payments, 'create_payment_intent', lambda payload, mode='card': {'id': 'pi_route', 'client_secret': 's'})
    monkeypatch.setattr(api, 'create_monday_record', lambda name, fields: {'itemId': ITEM, 'warnings': []})
    return api.app.test_client(), kit


def run_the_steps(client):
    assert client.post('/api/camera/license-front').json == {'saved': True}
    assert client.post('/api/camera/scan/license').status_code == 200
    assert client.post('/api/camera/scan/insurance').status_code == 200
    intent = client.post('/api/payment/create_intent', json={'confirmation_number': REF, 'payment_mode': 'terminal'}, headers=HEADERS)
    assert intent.status_code == 200 and intent.json['id'] == 'pi_route'
    return client.post('/api/monday/create_record', json={'itemName': 'Doe, Jane - ABC123', 'fields': {'confirmationNumber': REF, 'paymentIntentId': 'pi_route'}})


def test_the_whole_visit_ends_with_both_photos_on_the_monday_item(route):
    client, kit = route
    reply = run_the_steps(client)
    assert reply.status_code == 200 and reply.json == {'success': True, 'itemId': ITEM, 'warnings': []}   # the screen gets the same answer as before
    assert kit.monday.uploads == []                                  # the photos go after the answer, in the background
    assert len(list((kit.tmp / 'license_front').glob('*.jpg'))) == 1 and len(list((kit.tmp / 'insurance_front').glob('*.jpg'))) == 1   # saved on the kiosk until then
    kit.desk.process_once()
    assert sorted(n.split('-')[0] for n, _ in uploaded(kit)) == ['insurance', 'license']
    assert all(f'item_id: {ITEM}' in q for _, q in uploaded(kit))
    assert len(list((kit.tmp / 'license_front').glob('*.jpg'))) == 0 and len(list((kit.tmp / 'insurance_front').glob('*.jpg'))) == 0   # both are on Monday, so neither is kept on the kiosk


def test_a_scan_that_read_nothing_does_not_replace_a_good_insurance_photo(route, monkeypatch):
    client, kit = route
    client.post('/api/camera/license-front')
    assert client.post('/api/camera/scan/insurance').status_code == 200
    good = kit.desk.current['insurance'][0]
    monkeypatch.setattr(api, 'scan_insurance', lambda img: {'success': False, 'fields': {}})
    assert client.post('/api/camera/scan/insurance').status_code == 200
    assert kit.desk.current['insurance'][0] == good
    monkeypatch.setattr(api, 'scan_insurance', lambda img: (_ for _ in ()).throw(ValueError('Could not read this card.')))
    assert client.post('/api/camera/scan/insurance').status_code == 422      # and a failed scan still gives the screen its normal error
    assert kit.desk.current['insurance'][0] == good


def test_a_photo_problem_never_breaks_scanning_paying_or_the_monday_record(route, monkeypatch):
    client, kit = route

    def broken(*args, **kwargs):
        raise RuntimeError('disk full')
    for name in ('license_taken', 'license_scanned', 'save_license_back', 'save_insurance', 'bind', 'attach'):
        monkeypatch.setattr(kit.desk, name, broken)
    reply = run_the_steps(client)
    assert reply.status_code == 200 and reply.json['itemId'] == ITEM


def test_the_browser_cannot_point_the_upload_at_any_other_file(route):
    client, kit = route
    client.post('/api/camera/license-front')
    client.post('/api/payment/create_intent', json={'confirmation_number': REF}, headers=HEADERS)
    forged = {'confirmationNumber': REF, 'licensePhoto': '/etc/passwd', 'insurancePhoto': '../../secret.jpg', 'path': '/etc/shadow'}
    client.post('/api/monday/create_record', json={'itemName': 'x', 'fields': forged})
    kit.desk.process_once()
    assert len(kit.monday.uploads) == 1 and kit.monday.uploads[0]['content'][:2] == b'\xff\xd8'      # only the photo the kiosk itself saved
    assert b'passwd' not in kit.monday.uploads[0]['content']


def test_a_monday_error_still_reaches_the_screen_exactly_as_before(route, monkeypatch):
    client, kit = route

    def refuse(name, fields):
        raise monday_service.MondayError('Not Authenticated - check MONDAY_API_TOKEN', 502)
    monkeypatch.setattr(api, 'create_monday_record', refuse)
    reply = client.post('/api/monday/create_record', json={'itemName': 'x', 'fields': {'confirmationNumber': REF}})
    assert reply.status_code == 502 and 'check MONDAY_API_TOKEN' in reply.json['error']
    kit.desk.process_once()
    assert kit.monday.uploads == []


def test_odd_monday_requests_are_still_handled(route, monkeypatch):
    client, kit = route
    for body in ({}, {'itemName': 'x', 'fields': 'oops'}, {'fields': None}, {'itemName': 'x', 'fields': {'confirmationNumber': ['a']}}):
        assert client.post('/api/monday/create_record', json=body).status_code == 200


# ---------- the real request on the wire ----------

def test_the_real_request_has_the_shape_mondays_documentation_shows(tmp_path, monkeypatch):
    """No stand-in for the web library here: a tiny local web server receives the actual multipart form."""
    import email
    import email.policy
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            message = email.message_from_bytes(b'Content-Type: ' + self.headers['Content-Type'].encode() + b'\r\n\r\n' + body, policy=email.policy.HTTP)
            seen['content_type'] = message.get_content_type()
            seen['authorization'] = self.headers['Authorization']
            seen['path'] = self.path
            seen['parts'] = {part.get_param('name', header='content-disposition'): (part.get_filename(), part.get_content_type(), part.get_payload(decode=True))
                             for part in message.iter_parts()}
            reply = b'{"data": {"add_file_to_column": {"id": "77"}}}'
            self.send_response(200)
            self.send_header('Content-Length', str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *args):
            pass

    server = HTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv('MONDAY_API_TOKEN', 'secret-token')
        monkeypatch.setenv('MONDAY_BOARD_ID', '555')
        monkeypatch.setattr(monday_service, 'MONDAY_FILE_API_URL', f'http://127.0.0.1:{server.server_port}/v2/file')
        monday_service.upload_file(ITEM, 'files_license', 'license-front-abc.jpg', b'\xff\xd8JPEGBYTES\xff\xd9')
    finally:
        server.shutdown()
    assert seen['content_type'] == 'multipart/form-data' and seen['path'] == '/v2/file' and seen['authorization'] == 'secret-token'
    assert set(seen['parts']) == {'query', 'variables[file]'}                                    # exactly the two fields in monday's own curl example
    assert b'add_file_to_column' in seen['parts']['query'][2] and f'item_id: {ITEM}'.encode() in seen['parts']['query'][2]
    name, mime, content = seen['parts']['variables[file]']
    assert (name, mime, content) == ('license-front-abc.jpg', 'image/jpeg', b'\xff\xd8JPEGBYTES\xff\xd9')


# ---------- deleting the kiosk's copy once monday has the photo ----------

def kiosk_photos(kit):
    return sorted(p.name for folder in ('license_front', 'insurance_front') for p in (kit.tmp / folder).glob('*') if p.is_file())


def test_photos_are_deleted_from_the_kiosk_once_monday_has_them(kit):
    one_customer(kit)
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    assert len(kiosk_photos(kit)) == 2
    kit.desk.process_once()
    assert len(kit.monday.uploads) == 2 and kiosk_photos(kit) == []
    assert {r['state'] for r in rows(kit).values()} == {'uploaded'}


def test_nothing_is_deleted_before_monday_confirms(kit):
    one_customer(kit)
    kit.desk.bind(REF)
    assert len(kiosk_photos(kit)) == 2
    kit.desk.process_once()                                          # there is no monday row yet, so nothing uploads or goes
    assert len(kiosk_photos(kit)) == 2


def test_a_photo_that_monday_refused_is_kept_even_after_giving_up(kit, monkeypatch):
    clock = {'now': 4_000_000.0}
    monkeypatch.setattr(crm_photos, 'time', SimpleNamespace(time=lambda: clock['now']))
    one_customer(kit)
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    kit.monday.error = {'errors': [{'message': 'Not Authenticated'}]}
    for _ in range(crm_photos.MAX_ATTEMPTS):
        kit.desk.process_once()
        clock['now'] += 1000
    assert {r['state'] for r in rows(kit).values()} == {'failed'} and len(kiosk_photos(kit)) == 2      # still there for the office to attach by hand


def test_when_only_one_photo_reaches_monday_only_that_one_is_deleted(kit):
    one_customer(kit)
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    kit.monday.fail_column = 'files_insurance'
    kit.desk.process_once()
    assert [n.split('-')[0] for n, _ in uploaded(kit)] == ['license']
    left = kiosk_photos(kit)
    assert len(left) == 1 and (kit.tmp / 'insurance_front' / left[0]).exists()           # the insurance photo is still on the kiosk


def test_photos_with_no_column_set_are_kept_because_they_went_nowhere(kit, monkeypatch):
    monkeypatch.delenv('MONDAY_LICENSE_PHOTO_COLUMN')
    monkeypatch.delenv('MONDAY_INSURANCE_PHOTO_COLUMN')
    one_customer(kit)
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    kit.desk.process_once()
    assert kit.monday.uploads == [] and len(kiosk_photos(kit)) == 2


@pytest.mark.parametrize('value', ['1', 'true', 'YES', 'on'])
def test_the_keep_setting_keeps_every_copy(kit, monkeypatch, value):
    monkeypatch.setenv('KIOSK_KEEP_LOCAL_PHOTOS', value)
    one_customer(kit)
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    kit.desk.process_once()
    assert len(kit.monday.uploads) == 2 and len(kiosk_photos(kit)) == 2


def test_a_file_outside_the_photo_folders_is_never_deleted(kit):
    one_customer(kit)
    kit.desk.bind(REF)
    outside = make_photo(kit.tmp / 'precious' / 'family-photo.jpg')
    with sqlite3.connect(kit.desk.database) as db:                    # as if the database had been tampered with
        db.execute("UPDATE crm_photos SET path = ? WHERE kind = 'license'", (str(outside),))
    kit.desk.attach(REF, ITEM)
    kit.desk.process_once()
    assert outside.exists()
    assert 'PHOTO NOT DELETED' in (kit.tmp / 'kiosk-problems.log').read_text() and 'outside the photo folders' in (kit.tmp / 'kiosk-problems.log').read_text()


def test_a_link_inside_the_photo_folder_that_points_elsewhere_is_not_followed(kit):
    one_customer(kit)
    kit.desk.bind(REF)
    outside = make_photo(kit.tmp / 'precious' / 'family-photo.jpg')
    link = kit.tmp / 'license_front' / 'sneaky.jpg'
    link.symlink_to(outside)
    with sqlite3.connect(kit.desk.database) as db:
        db.execute("UPDATE crm_photos SET path = ? WHERE kind = 'license'", (str(link),))
    kit.desk.attach(REF, ITEM)
    kit.desk.process_once()
    assert outside.exists() and link.is_symlink()


def test_a_delete_that_fails_is_logged_and_the_upload_stays_done(kit, monkeypatch):
    one_customer(kit)
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    real_unlink = crm_photos.Path.unlink

    def refuse(self, *args, **kwargs):
        raise PermissionError('read-only file system')
    monkeypatch.setattr(crm_photos.Path, 'unlink', refuse)
    kit.desk.process_once()
    monkeypatch.setattr(crm_photos.Path, 'unlink', real_unlink)
    assert {r['state'] for r in rows(kit).values()} == {'uploaded'} and len(kit.monday.uploads) == 2       # never uploaded twice because of a failed delete
    log = (kit.tmp / 'kiosk-problems.log').read_text()
    assert log.count('PHOTO NOT DELETED') == 2 and 'reached Monday but could not be deleted' in log
    kit.desk.process_once()
    assert len(kit.monday.uploads) == 2


def test_a_photo_that_is_already_gone_is_not_an_error(kit):
    one_customer(kit)
    kit.desk.bind(REF)
    row = {'kind': 'license', 'reference': REF, 'path': str(kit.tmp / 'license_front' / 'never-existed.jpg')}
    kit.desk._delete_local_copy(row)
    assert not (kit.tmp / 'kiosk-problems.log').exists()


# ---------- [CG] the back of the license ----------

@pytest.fixture
def back(kit, monkeypatch):
    monkeypatch.setenv('MONDAY_LICENSE_BACK_PHOTO_COLUMN', 'files_license_back')
    return kit


def back_picture(shade=60):
    return np.full((400, 640, 3), shade, np.uint8)


def whole_customer(kit, name='a'):
    """Front photo, then the barcode on the back, then the insurance card: the order the kiosk screen goes in."""
    front = make_photo(kit.tmp / 'license_front' / f'front-{name}.jpg')
    kit.desk.license_taken(front)
    kit.desk.license_scanned({'DAQ': f'license-{name}'})
    back_path = kit.desk.save_license_back(back_picture())
    kit.desk.save_insurance(np.full((300, 500, 3), 90, np.uint8))
    return front, back_path


def test_the_back_of_the_license_goes_to_its_own_column_and_then_leaves_the_kiosk(back):
    _, back_path = whole_customer(back)
    assert back_path.parent == back.tmp / 'license_back' and back_path.suffix == '.jpg'
    back.desk.bind(REF)
    back.desk.attach(REF, ITEM)
    back.desk.process_once()
    sent = {n.rsplit('-', 1)[0]: q for n, q in uploaded(back)}
    assert set(sent) == {'license-front', 'license-back', 'insurance-card'}
    assert 'column_id: "files_license_back"' in sent['license-back'] and f'item_id: {ITEM}' in sent['license-back']
    assert 'files_license_back' not in sent['license-front'] + sent['insurance-card']          # each photo in its own column
    assert {r['state'] for r in rows(back).values()} == {'uploaded'}
    assert not back_path.exists() and not list((back.tmp / 'license_back').glob('*'))       # deleted once Monday has it, like the others


def test_with_the_back_column_not_set_yet_the_back_photo_is_skipped_quietly(kit):
    whole_customer(kit)
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    kit.desk.process_once()
    assert sorted(n.split('-')[0] for n, _ in uploaded(kit)) == ['insurance', 'license'] and 'license-back' not in str(uploaded(kit))
    assert rows(kit)['license_back']['state'] == 'skipped'
    assert not (kit.tmp / 'kiosk-problems.log').exists()                    # nothing for the office to worry about


def test_a_new_front_photo_drops_a_back_photo_left_by_someone_who_walked_away(back):
    whole_customer(back, 'a')                                               # customer A scans everything, never pays
    front_b = make_photo(back.tmp / 'license_front' / 'front-b.jpg')
    back.desk.license_taken(front_b)                                        # B takes a front photo; B's barcode then won't read, so B types it in
    back.desk.bind(REF)
    assert 'license_back' not in rows(back)                                 # A's back photo never reaches B's record
    log = (back.tmp / 'kiosk-problems.log').read_text()
    assert 'MONDAY PHOTOS MISSING' in log and 'license-back' in log         # and the office is told B's is missing


def test_a_different_license_card_drops_the_back_photo(back):
    whole_customer(back, 'a')
    back.desk.license_scanned({'DAQ': 'someone-else'})                      # a different card was read
    assert 'license_back' not in back.desk.current


def test_scanning_the_back_again_keeps_only_the_newest_picture(back):
    whole_customer(back)
    second = back.desk.save_license_back(back_picture(200))
    back.desk.bind(REF)
    assert rows(back)['license_back']['path'] == str(second)


def test_the_same_license_read_again_keeps_the_back_photo(back):
    _, back_path = whole_customer(back, 'a')
    back.desk.license_scanned({'DAQ': 'license-a'})                         # the same card again
    back.desk.bind(REF)
    assert rows(back)['license_back']['path'] == str(back_path)


def test_the_whole_visit_ends_with_the_back_photo_on_the_monday_item_too(route, monkeypatch):
    client, kit = route
    monkeypatch.setenv('MONDAY_LICENSE_BACK_PHOTO_COLUMN', 'files_license_back')
    assert run_the_steps(client).status_code == 200
    assert len(list((kit.tmp / 'license_back').glob('*.jpg'))) == 1         # the barcode scan's picture was kept
    kit.desk.process_once()
    assert sorted(n.rsplit('-', 1)[0] for n, _ in uploaded(kit)) == ['insurance-card', 'license-back', 'license-front']
    assert all(f'item_id: {ITEM}' in q for _, q in uploaded(kit))
    assert not list((kit.tmp / 'license_back').glob('*.jpg'))


def test_a_barcode_that_was_not_read_keeps_no_back_photo(route, monkeypatch):
    client, kit = route
    client.post('/api/camera/license-front')
    monkeypatch.setattr(api, 'scan_license', lambda img: {'success': False, 'fields': {}})
    assert client.post('/api/camera/scan/license').status_code == 200
    monkeypatch.setattr(api, 'scan_license', lambda img: (_ for _ in ()).throw(ValueError('Barcode not readable.')))
    assert client.post('/api/camera/scan/license').status_code == 422     # the screen still gets its normal error
    assert 'license_back' not in kit.desk.current and not list((kit.tmp / 'license_back').glob('*'))   # a picture that might not be the back is never kept


def test_the_tests_never_put_back_photos_in_the_real_runtime_folder():
    real = (Path(crm_photos.__file__).resolve().parent / 'runtime').resolve()
    assert not Path(os.environ['KIOSK_LICENSE_BACK_PHOTO_DIR']).resolve().is_relative_to(real)   # conftest.py points it at a throwaway folder
