# ============================================================================
# File:         test_security_photos_CG.py
# Written by:   CG (Cristian Gonzalez)
# What it does: Tests for the security Pi's three photos (license scanner, Pay,
#               print finished) reaching the renewal's Monday.com record.
# Team-written: Logic written by CG (Cristian Gonzalez). Outside code is used
#               only through the third-party sources listed below.
# Third-party:  pytest; the security Pi, its serial wire and Monday.com are
#               replaced by stand-ins in these tests.
# ============================================================================
"""Security photos on the Monday record. A stand-in security Pi answers on a stand-in serial wire the same way
photo_send_DG_CG.py on Diego's Pi does, and a stand-in Monday remembers every upload. Nothing real is contacted."""
import base64
import hashlib
import io
import os
import sqlite3
import sys
import threading
import time
from types import SimpleNamespace

import pytest

import monday_service_CG as monday_service
import scanner_api_BV_CG as api
import security_client_BV_DG as security_client
import security_photos_CG as sp
from delivery_service_BV import DeliveryService
from security_wire_BV_DG import decode, encode

TOKEN = 't' * 40
REF = 'REG-AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE'
OTHER = 'REG-11111111-2222-3333-4444-555555555555'
ITEM = '1234567890'
COLUMNS = {'MONDAY_SECURITY_LICENSE_COLUMN': 'files_license', 'MONDAY_SECURITY_PAY_COLUMN': 'files_pay', 'MONDAY_SECURITY_PRINT_COLUMN': 'files_print'}
CHUNK = 1152  # the piece size photo_send_DG_CG.py uses


def jpeg(seed, size=3000):
    return b'\xff\xd8' + hashlib.sha256(seed.encode()).digest() * (size // 32) + b'\xff\xd9'


class FakeSecurityPi:
    """The security Pi's end of the serial wire: answers 'photo_chunk' requests like photo_send_DG_CG.py does."""

    def __init__(self):
        self.photos, self.incoming, self.outgoing = {}, bytearray(), bytearray()
        self.requests, self.opened, self.silent, self.corrupt, self.held_lock = [], 0, False, False, []

    def __call__(self):  # stands in for security_photos_CG._open_port
        self.opened += 1
        self.held_lock.append(security_client.SERIAL_LOCK.locked())
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def reset_input_buffer(self):
        self.outgoing.clear()

    def write(self, data):
        self.incoming += data
        while b'\n' in self.incoming:
            line, _, rest = bytes(self.incoming).partition(b'\n')
            self.incoming = bytearray(rest)
            request = decode(line, TOKEN)
            self.requests.append(request)
            if self.silent:
                continue
            data = self.photos.get(request['photo'])
            if data is None:
                answer = {'id': request['id'], 'photo': request['photo'], 'error': 'not_found'}
            else:
                piece = data[request['offset']:request['offset'] + CHUNK]
                if self.corrupt:
                    piece = piece[:-1] + bytes([piece[-1] ^ 1])  # one wrong byte, and the jpeg start still looks right
                answer = {'id': request['id'], 'photo': request['photo'], 'offset': request['offset'], 'total': len(data),
                          'sha256': hashlib.sha256(data).hexdigest(), 'data': base64.b64encode(piece).decode()}
            frame = encode(answer, TOKEN)
            assert len(frame) <= 2048, 'a real answer must fit in one serial frame'
            self.outgoing += frame

    def read(self, n=1):
        if not self.outgoing:
            return b''
        byte = bytes(self.outgoing[:1])
        del self.outgoing[:1]
        return byte


@pytest.fixture
def kit(tmp_path, monkeypatch):
    monkeypatch.setenv('SECURITY_TRANSPORT', 'serial')
    monkeypatch.setenv('SECURITY_SHARED_TOKEN', TOKEN)
    for name, value in COLUMNS.items():
        monkeypatch.setenv(name, value)
    pi = FakeSecurityPi()
    monkeypatch.setattr(sp, '_open_port', pi)
    monkeypatch.setattr(sp, 'PIECE_SECONDS', 0.3)
    uploads = []
    monkeypatch.setattr(monday_service, 'upload_file', lambda item, column, name, data, setting: uploads.append(SimpleNamespace(item=item, column=column, name=name, data=data)))
    delivery = DeliveryService(tmp_path, None)  # BV's real camera queue, in a scratch folder. Its workers are never started
    desk = sp.SecurityPhotos(tmp_path, camera_queue=delivery.database)
    return SimpleNamespace(tmp=tmp_path, pi=pi, uploads=uploads, delivery=delivery, desk=desk)


def queued(kit, event, key, state='saved', created=None, photo=True):
    """Puts a capture in the kiosk's camera queue the way delivery_service_BV.py does, and its photo on the security Pi."""
    kit.delivery.enqueue_capture(event, key)
    with kit.delivery.db() as db:
        if created is not None:
            db.execute('UPDATE cameras SET created=? WHERE key=?', (created, key))
        db.execute('UPDATE cameras SET state=? WHERE key=?', (state, key))
        request_id = db.execute('SELECT request_id FROM cameras WHERE key=?', (key,)).fetchone()['request_id']
    if photo:
        kit.pi.photos[request_id] = jpeg(key)
    return request_id


def rows(kit):
    with kit.desk._db() as db:
        return [dict(r) for r in db.execute('SELECT * FROM security_photos ORDER BY id')]


def run(kit, times=1):
    for _ in range(times):
        with kit.desk._db() as db:
            db.execute('UPDATE security_photos SET next_try = 0')
        kit.desk.process_once()


def by_column(kit):
    return {u.column: u for u in kit.uploads}


# ---------- which photos belong to which renewal ----------

def test_the_three_photos_reach_the_right_item_and_columns_exactly_as_saved(kit):
    scan = queued(kit, 'license_scan', 'license:a')
    pay = queued(kit, 'payment_click', f'pay:{REF}:first-attempt')
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    printed = queued(kit, 'print_completed', 'print:' + REF)
    run(kit)
    got = by_column(kit)
    assert set(got) == {'files_license', 'files_pay', 'files_print'}
    assert {u.item for u in kit.uploads} == {ITEM}
    assert got['files_license'].data == kit.pi.photos[scan]
    assert got['files_pay'].data == kit.pi.photos[pay]
    assert got['files_print'].data == kit.pi.photos[printed]
    assert got['files_pay'].name.startswith('security-pay-') and got['files_print'].name.startswith('security-print-done-')
    assert {r['state'] for r in rows(kit)} == {'uploaded'}


def test_nothing_is_fetched_until_the_monday_record_exists(kit):
    queued(kit, 'payment_click', f'pay:{REF}:first-attempt')
    kit.desk.bind(REF)
    run(kit)
    assert kit.pi.requests == [] and kit.uploads == []


def test_every_pay_attempt_is_kept_so_a_decline_and_its_retry_both_show(kit):
    first = queued(kit, 'payment_click', f'pay:{REF}:try-1')
    kit.desk.bind(REF)
    second = queued(kit, 'payment_click', f'pay:{REF}:try-2')
    kit.desk.bind(REF)  # scanner_api calls bind on every attempt
    kit.desk.attach(REF, ITEM)
    run(kit)
    assert sorted(u.data for u in kit.uploads if u.column == 'files_pay') == sorted([kit.pi.photos[first], kit.pi.photos[second]])


def test_another_renewals_pay_photo_is_never_taken(kit):
    queued(kit, 'payment_click', f'pay:{OTHER}:first-attempt')
    mine = queued(kit, 'payment_click', f'pay:{REF}:first-attempt')
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    run(kit)
    assert [u.data for u in kit.uploads if u.column == 'files_pay'] == [kit.pi.photos[mine]]


def test_a_reference_with_an_underscore_only_matches_itself(kit):
    queued(kit, 'payment_click', 'pay:REGxA:first-attempt')  # '_' is a wildcard in SQL LIKE, so this would sneak in with LIKE
    kit.desk.bind('REG_A')
    assert [r for r in rows(kit) if r['event'] == 'payment_click'] == []


def test_a_customer_who_left_without_paying_never_lands_on_the_next_customers_record(kit):
    now = time.time()
    left = queued(kit, 'license_scan', 'license:left', created=now - 20 * 60)
    mine = queued(kit, 'license_scan', 'license:mine', created=now - 60)
    retake = queued(kit, 'license_scan', 'license:retake', created=now - 30)
    kit.desk.bind(REF)
    ids = {r['request_id'] for r in rows(kit) if r['event'] == 'license_scan'}
    assert ids == {mine, retake} and left not in ids


def test_the_previous_customers_scanner_photo_is_not_reused(kit):
    queued(kit, 'license_scan', 'license:first-customer')
    kit.desk.bind(OTHER)
    time.sleep(0.01)
    kit.desk.bind(REF)  # the next customer never opened the scanner
    assert [r for r in rows(kit) if r['reference'] == REF and r['event'] == 'license_scan'] == []


def test_a_missing_scanner_photo_is_logged_for_the_office(kit, problem_log):
    kit.desk.bind(REF)
    assert 'SECURITY PHOTO MISSING' in problem_log.read_text() and REF in problem_log.read_text()


def test_scanner_photos_older_than_the_limit_are_never_attached(kit):
    queued(kit, 'license_scan', 'license:old', created=time.time() - (sp.MAX_AGE_MINUTES + 1) * 60)
    kit.desk.bind(REF)
    assert [r for r in rows(kit) if r['event'] == 'license_scan'] == []


def test_a_reference_that_is_not_a_plain_reference_is_ignored(kit):
    for bad in (None, '', "x' OR 1=1 --", '../../etc', 'a' * 81):
        kit.desk.bind(bad)
        kit.desk.attach(bad, ITEM)
    assert rows(kit) == []


def test_an_item_id_that_is_not_a_number_is_ignored(kit):
    kit.desk.bind(REF)
    kit.desk.attach(REF, '123) { delete }')
    assert {r['item_id'] for r in rows(kit)} == {''}


# ---------- waiting for the security Pi and the printer ----------

def test_a_photo_the_security_pi_has_not_taken_yet_is_waited_for(kit):
    pay = queued(kit, 'payment_click', f'pay:{REF}:first-attempt', state='pending')
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    run(kit)
    assert kit.uploads == [] and all(r['attempts'] == 0 for r in rows(kit))
    with kit.delivery.db() as db:
        db.execute("UPDATE cameras SET state='saved'")
    run(kit)
    assert by_column(kit)['files_pay'].data == kit.pi.photos[pay]


def test_the_print_photo_is_picked_up_after_the_monday_record(kit):
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    run(kit)
    assert 'files_print' not in by_column(kit) and kit.pi.requests == []
    printed = queued(kit, 'print_completed', 'print:' + REF)
    run(kit)
    assert by_column(kit)['files_print'].data == kit.pi.photos[printed]


def test_a_print_that_never_finishes_stops_waiting_and_tells_the_office(kit, problem_log):
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    with kit.desk._db() as db:
        db.execute("UPDATE security_photos SET taken=? WHERE event='print_completed'", (time.time() - (sp.PRINT_WAIT_HOURS * 3600 + 5),))
    run(kit)
    assert [r['state'] for r in rows(kit) if r['event'] == 'print_completed'] == ['skipped']
    assert 'never reported the job finished' in problem_log.read_text()


def test_a_photo_the_security_pi_failed_to_take_is_skipped_and_logged(kit, problem_log):
    queued(kit, 'payment_click', f'pay:{REF}:first-attempt', state='failed', photo=False)
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    run(kit)
    assert [r['state'] for r in rows(kit) if r['event'] == 'payment_click'] == ['skipped']
    assert 'did not save the Pay photo' in problem_log.read_text()
    assert kit.pi.requests == []


def test_an_uncertain_capture_is_still_tried_because_it_may_be_saved(kit):
    pay = queued(kit, 'payment_click', f'pay:{REF}:first-attempt', state='uncertain')
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    run(kit)
    assert by_column(kit)['files_pay'].data == kit.pi.photos[pay]


# ---------- settings ----------

def test_with_no_columns_set_nothing_is_asked_of_the_security_pi(kit, monkeypatch):
    for name in COLUMNS:
        monkeypatch.delenv(name)
    queued(kit, 'license_scan', 'license:a')
    queued(kit, 'payment_click', f'pay:{REF}:first-attempt')
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    queued(kit, 'print_completed', 'print:' + REF)
    run(kit)
    assert kit.pi.requests == [] and kit.uploads == [] and {r['state'] for r in rows(kit)} == {'skipped'}


def test_the_env_template_lists_every_monday_photo_column_the_code_reads():
    """Every photo column setting the kiosk reads must be in .env.example, or whoever sets up a Pi won't know it exists."""
    import crm_photos_CG as crm_photos
    template = (sp.Path(sp.__file__).parent / '.env.example').read_text()
    settings = set(crm_photos.SETTINGS.values()) | {setting for setting, _, _ in sp.EVENTS.values()}
    missing = sorted(name for name in settings if f'\n{name}=' not in template)
    assert missing == [], 'add these to .env.example: ' + ', '.join(missing)


def test_with_only_the_pay_column_set_only_that_photo_goes(kit, monkeypatch):
    monkeypatch.delenv('MONDAY_SECURITY_LICENSE_COLUMN')
    monkeypatch.delenv('MONDAY_SECURITY_PRINT_COLUMN')
    queued(kit, 'license_scan', 'license:a')
    queued(kit, 'payment_click', f'pay:{REF}:first-attempt')
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    queued(kit, 'print_completed', 'print:' + REF)
    run(kit)
    assert set(by_column(kit)) == {'files_pay'}


# ---------- the serial wire ----------

def test_a_big_photo_comes_over_in_turns_and_lets_captures_in_between(kit):
    key = f'pay:{REF}:first-attempt'
    pay = queued(kit, 'payment_click', key)
    kit.pi.photos[pay] = jpeg('big', size=CHUNK * 20)  # 20+ pieces: three turns of 8
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    run(kit)
    assert by_column(kit)['files_pay'].data == kit.pi.photos[pay]
    assert kit.pi.opened >= 3, 'the wire must be let go between turns'
    assert all(kit.pi.held_lock), 'the wire is only opened while holding the shared lock'
    assert not security_client.SERIAL_LOCK.locked()


def test_a_capture_waits_for_the_shared_lock_instead_of_failing(monkeypatch):
    monkeypatch.setenv('SECURITY_SHARED_TOKEN', TOKEN)
    seen = []

    class Port(FakeSecurityPi):
        def write(self, data):
            seen.append(security_client.SERIAL_LOCK.locked())
            request = decode(data.strip(), TOKEN)
            self.outgoing += encode({'id': request['id'], 'saved': True}, TOKEN)

    # a stand-in pyserial, so this runs on machines without it (the laptop's venv doesn't have it)
    monkeypatch.setitem(sys.modules, 'serial', SimpleNamespace(Serial=lambda *a, **k: Port()))
    assert security_client.capture_serial('payment_click', 'a' * 32)['saved'] is True
    assert seen == [True], 'capture_serial must hold SERIAL_LOCK while it uses the wire'


def test_a_photo_the_security_pi_does_not_have_is_retried_then_reported(kit, problem_log):
    queued(kit, 'payment_click', f'pay:{REF}:first-attempt', photo=False)
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    run(kit, times=sp.MAX_ATTEMPTS)
    pay = [r for r in rows(kit) if r['event'] == 'payment_click'][0]
    assert pay['state'] == 'failed' and pay['attempts'] == sp.MAX_ATTEMPTS
    assert 'SECURITY PHOTO NOT ATTACHED' in problem_log.read_text() and pay['request_id'] in problem_log.read_text()


def test_a_damaged_photo_is_never_uploaded(kit):
    queued(kit, 'payment_click', f'pay:{REF}:first-attempt')
    kit.pi.corrupt = True
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    run(kit)
    assert kit.uploads == []
    assert [r['attempts'] for r in rows(kit) if r['event'] == 'payment_click'] == [1]


def test_a_silent_security_pi_times_out_and_is_tried_again_later(kit):
    pay = queued(kit, 'payment_click', f'pay:{REF}:first-attempt')
    kit.pi.silent = True
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    run(kit)
    assert kit.uploads == [] and not security_client.SERIAL_LOCK.locked()
    kit.pi.silent = False
    run(kit)
    assert by_column(kit)['files_pay'].data == kit.pi.photos[pay]


def test_a_late_answer_to_an_earlier_question_is_ignored(kit):
    pay = queued(kit, 'payment_click', f'pay:{REF}:first-attempt')
    stale = {'id': 'f' * 32, 'photo': pay, 'offset': 0, 'total': 4, 'sha256': hashlib.sha256(b'\xff\xd8xx').hexdigest(),
             'data': base64.b64encode(b'\xff\xd8xx').decode()}
    original = kit.pi.write

    def write_with_stale_answer_first(data):
        kit.pi.outgoing += encode(stale, TOKEN)
        original(data)

    kit.pi.write = write_with_stale_answer_first
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    run(kit)
    assert by_column(kit)['files_pay'].data == kit.pi.photos[pay]


def test_answers_signed_with_another_token_are_ignored(kit, monkeypatch):
    queued(kit, 'payment_click', f'pay:{REF}:first-attempt')
    monkeypatch.setenv('SECURITY_SHARED_TOKEN', 'x' * 40)  # the kiosk and the security Pi no longer match
    kit.pi.write = lambda data: kit.pi.outgoing.extend(encode({'id': 'a', 'photo': 'b', 'error': 'not_found'}, TOKEN))
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    run(kit)
    assert kit.uploads == [] and [r['attempts'] for r in rows(kit) if r['event'] == 'payment_click'] == [1]


def test_without_a_token_nothing_is_sent(kit, monkeypatch):
    monkeypatch.setenv('SECURITY_SHARED_TOKEN', 'short')
    with pytest.raises(sp.SecurityPhotoError):
        sp.fetch_photo('a' * 32)
    assert kit.pi.requests == []


def test_a_capture_id_that_is_not_a_capture_id_is_never_sent(kit):
    for bad in ('../x', 'A' * 32, 'a' * 31, None):
        with pytest.raises(sp.SecurityPhotoError):
            sp.fetch_photo(bad)
    assert kit.pi.requests == []


# ---------- HTTP instead of the serial wire ----------

class FakeOpener:
    def __init__(self, body, digest=None, error=None):
        self.body, self.digest, self.error, self.requests = body, digest, error, []

    def open(self, request, timeout=None):
        self.requests.append(request)
        if self.error:
            raise self.error
        outer = self

        class Response(io.BytesIO):
            headers = {'X-Photo-SHA256': outer.digest or hashlib.sha256(outer.body).hexdigest()}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False
        return Response(self.body)


def test_over_http_the_photo_comes_from_the_photo_route_with_the_token(monkeypatch):
    monkeypatch.setenv('SECURITY_TRANSPORT', 'http')
    monkeypatch.setenv('SECURITY_PI_URL', 'http://192.168.1.50:5001/')
    monkeypatch.setenv('SECURITY_SHARED_TOKEN', TOKEN)
    opener = FakeOpener(jpeg('http'))
    monkeypatch.setattr(sp, 'build_opener', lambda *handlers: opener)
    assert sp.fetch_photo('a' * 32) == jpeg('http')
    request = opener.requests[0]
    assert request.full_url == 'http://192.168.1.50:5001/photo/' + 'a' * 32
    assert request.get_header('Authorization') == 'Bearer ' + TOKEN and request.get_method() == 'POST'


def test_over_http_a_damaged_photo_is_refused(monkeypatch):
    monkeypatch.setenv('SECURITY_TRANSPORT', 'http')
    monkeypatch.setenv('SECURITY_PI_URL', 'http://192.168.1.50:5001')
    monkeypatch.setenv('SECURITY_SHARED_TOKEN', TOKEN)
    monkeypatch.setattr(sp, 'build_opener', lambda *handlers: FakeOpener(jpeg('http'), digest='0' * 64))
    with pytest.raises(sp.SecurityPhotoError):
        sp.fetch_photo('a' * 32)


def test_over_http_something_that_is_not_a_jpeg_is_refused(monkeypatch):
    monkeypatch.setenv('SECURITY_TRANSPORT', 'http')
    monkeypatch.setenv('SECURITY_PI_URL', 'http://192.168.1.50:5001')
    monkeypatch.setenv('SECURITY_SHARED_TOKEN', TOKEN)
    monkeypatch.setattr(sp, 'build_opener', lambda *handlers: FakeOpener(b'<html>login</html>'))
    with pytest.raises(sp.SecurityPhotoError):
        sp.fetch_photo('a' * 32)


# ---------- restarts, Monday problems and the background worker ----------

def test_a_monday_refusal_is_retried_and_the_photo_goes_later(kit, monkeypatch):
    pay = queued(kit, 'payment_click', f'pay:{REF}:first-attempt')
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    good = monday_service.upload_file

    def refuse(*args):
        raise monday_service.MondayError('Monday is down')
    monkeypatch.setattr(monday_service, 'upload_file', refuse)
    run(kit)
    assert [r['attempts'] for r in rows(kit) if r['event'] == 'payment_click'] == [1]
    monkeypatch.setattr(monday_service, 'upload_file', good)
    run(kit)
    assert by_column(kit)['files_pay'].data == kit.pi.photos[pay]


def test_a_finished_upload_is_never_repeated(kit):
    queued(kit, 'payment_click', f'pay:{REF}:first-attempt')
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    run(kit, times=3)
    assert len([u for u in kit.uploads if u.column == 'files_pay']) == 1


def test_unfinished_work_carries_on_after_a_restart(kit):
    pay = queued(kit, 'payment_click', f'pay:{REF}:first-attempt')
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    restarted = sp.SecurityPhotos(kit.tmp, camera_queue=kit.delivery.database)
    restarted.process_once()
    assert by_column(kit)['files_pay'].data == kit.pi.photos[pay]


def test_the_background_worker_uploads_by_itself(kit):
    pay = queued(kit, 'payment_click', f'pay:{REF}:first-attempt')
    kit.desk.bind(REF)
    kit.desk.start()
    try:
        kit.desk.attach(REF, ITEM)  # wakes the worker
        deadline = time.time() + 5
        while 'files_pay' not in by_column(kit) and time.time() < deadline:
            time.sleep(0.05)
    finally:
        kit.desk.stop.set()
        kit.desk.wake.set()
    assert by_column(kit)['files_pay'].data == kit.pi.photos[pay]


def test_the_kiosk_never_saves_a_security_photo_to_its_own_disk(kit):
    queued(kit, 'license_scan', 'license:a')
    queued(kit, 'payment_click', f'pay:{REF}:first-attempt')
    kit.desk.bind(REF)
    kit.desk.attach(REF, ITEM)
    run(kit)
    assert len(kit.uploads) == 2
    assert not [p for p in kit.tmp.rglob('*') if p.suffix.lower() in {'.jpg', '.jpeg'}]


# ---------- the real routes, as the kiosk screen calls them ----------

def test_the_whole_visit_ends_with_the_security_photos_on_the_monday_item(kit, monkeypatch):
    monkeypatch.setattr(api, 'delivery', kit.delivery)
    monkeypatch.setattr(api, 'security_photos', kit.desk)
    monkeypatch.setattr(api.photos, 'bind', lambda reference: None)
    monkeypatch.setattr(api.photos, 'attach', lambda reference, item: None)
    monkeypatch.setattr(api.payments, 'create_payment_intent', lambda payload, mode='terminal': {'id': 'pi_route', 'client_secret': 's'})
    monkeypatch.setattr(api, 'create_monday_record', lambda name, fields: {'itemId': ITEM, 'warnings': []})
    client = api.app.test_client()
    assert client.post('/api/security/front-capture', headers={'X-Kiosk-Security': '1'}).status_code == 200
    assert client.post('/api/payment/create_intent', json={'confirmation_number': REF, 'payment_mode': 'terminal', 'security_event_id': 'tap-1'},
                       headers={'X-Kiosk-Payment': '1'}).status_code == 200
    assert client.post('/api/monday/create_record', json={'itemName': 'Doe, Jane', 'fields': {'confirmationNumber': REF}}).status_code == 200
    kit.delivery.enqueue_capture('print_completed', 'print:' + REF)  # what the CUPS monitor does once the job finishes
    with kit.delivery.db() as db:
        db.execute("UPDATE cameras SET state='saved'")  # what the camera worker does once the security Pi answers
        for row in db.execute('SELECT request_id, key FROM cameras'):
            kit.pi.photos[row['request_id']] = jpeg(row['key'])
    run(kit)
    assert set(by_column(kit)) == {'files_license', 'files_pay', 'files_print'}
    assert by_column(kit)['files_pay'].data == jpeg(f'pay:{REF}:tap-1')


def test_a_security_photo_problem_never_breaks_paying_or_the_monday_record(kit, monkeypatch):
    def broken(*args):
        raise RuntimeError('disk full')
    monkeypatch.setattr(api, 'security_photos', SimpleNamespace(bind=broken, attach=broken))
    monkeypatch.setattr(api.photos, 'bind', lambda reference: None)
    monkeypatch.setattr(api.photos, 'attach', lambda reference, item: None)
    monkeypatch.setattr(api.delivery, 'enqueue_capture', lambda event, key=None: {'queued': True})
    monkeypatch.setattr(api.payments, 'create_payment_intent', lambda payload, mode='terminal': {'id': 'pi_route', 'client_secret': 's'})
    monkeypatch.setattr(api, 'create_monday_record', lambda name, fields: {'itemId': ITEM, 'warnings': []})
    client = api.app.test_client()
    assert client.post('/api/payment/create_intent', json={'confirmation_number': REF, 'payment_mode': 'terminal'},
                       headers={'X-Kiosk-Payment': '1'}).status_code == 200
    reply = client.post('/api/monday/create_record', json={'itemName': 'Doe, Jane', 'fields': {'confirmationNumber': REF}})
    assert reply.status_code == 200 and reply.json['itemId'] == ITEM
