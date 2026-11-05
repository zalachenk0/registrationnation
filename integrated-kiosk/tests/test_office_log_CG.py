# ============================================================================
# File:         test_office_log_CG.py
# Written by:   CG (Cristian Gonzalez)
# What it does: Tests that a Monday.com record problem reaches the office's
#               problem log (runtime/kiosk-problems.log) without changing what
#               the kiosk screen gets back.
# Team-written: Logic written by CG (Cristian Gonzalez). Outside code is used
#               only through the third-party sources listed below.
# Third-party:  pytest; Flask's test client; Monday.com is replaced by a
#               stand-in in these tests.
# ============================================================================
"""Rebuilt from the Oct 22 office-log tests for the renamed files. The receipt-email ones were retired with that code."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'home_test_CG'))  # same as test_home_form_CG.py
import crm_photos_CG as crm_photos  # noqa: E402
import home_form_server_CG as home  # noqa: E402
import monday_service_CG as monday_service  # noqa: E402
import scanner_api_BV_CG as api  # noqa: E402

RECORD = {'itemName': 'Sample, Jane - TEST-123',
          'fields': {'confirmationNumber': 'REG-OFFICE-LOG-0001', 'paymentIntentId': 'pi_office_log_1',
                     'fullName': 'Jane Sample', 'phone': '210-555-0100', 'licenseNumber': '12345678', 'status': 'Paid'}}


def lines(path):
    return path.read_text(encoding='utf-8').splitlines() if path.exists() else []


@pytest.fixture(params=['kiosk', 'home form'])
def client(request, tmp_path, monkeypatch, problem_log):
    """Every test runs against the kiosk's route and the home form's copy of it, so the two can't drift apart."""
    if request.param == 'kiosk':
        monkeypatch.setattr(api.photos, 'attach', lambda *a: None)
        return api.app.test_client(), api
    app = home.create_app(tmp_path / 'home')
    monkeypatch.setenv('KIOSK_PROBLEM_LOG', str(problem_log))  # create_app points the log at its scratch folder; point it back
    return app.test_client(), home


def test_a_monday_failure_is_logged_with_who_and_why_and_the_response_is_unchanged(client, problem_log, monkeypatch):
    test_client, module = client

    def refuse(name, fields):
        raise monday_service.MondayError('Not Authenticated (X) - check MONDAY_API_TOKEN', 401)
    monkeypatch.setattr(module, 'create_monday_record', refuse)
    reply = test_client.post('/api/monday/create_record', json=RECORD)
    assert reply.status_code == 401 and reply.json == {'error': 'Not Authenticated (X) - check MONDAY_API_TOKEN'}  # exactly what the page got before
    [line] = lines(problem_log)
    assert 'MONDAY NOT SAVED' in line and 'REG-OFFICE-LOG-0001' in line and 'Sample, Jane - TEST-123' in line
    assert 'pi_office_log_1' in line and 'Not Authenticated' in line and 'by hand' in line
    assert '12345678' not in line and '210-555-0100' not in line  # who and why only, not the customer's details


def test_skipped_fields_are_logged_but_a_clean_save_writes_nothing(client, problem_log, monkeypatch):
    test_client, module = client
    monkeypatch.setattr(module, 'create_monday_record', lambda name, fields: {'itemId': '7', 'warnings': ["phone: '12' doesn't look like a phone number"]})
    reply = test_client.post('/api/monday/create_record', json=RECORD)
    assert reply.status_code == 200 and reply.json == {'success': True, 'itemId': '7', 'warnings': ["phone: '12' doesn't look like a phone number"]}
    [line] = lines(problem_log)
    assert 'MONDAY SAVED, FIELDS SKIPPED' in line and "phone: '12'" in line and 'item 7' in line and 'REG-OFFICE-LOG-0001' in line
    problem_log.unlink()
    monkeypatch.setattr(module, 'create_monday_record', lambda name, fields: {'itemId': '8', 'warnings': []})
    assert test_client.post('/api/monday/create_record', json=RECORD).status_code == 200
    assert lines(problem_log) == []


@pytest.mark.parametrize('body', [{'itemName': 'x', 'fields': 'oops'}, {'fields': None}, {}, ['not', 'an', 'object'], 'just text'])
def test_odd_requests_get_a_clean_handled_reply_and_a_log_line_never_a_crash(client, body, problem_log, monkeypatch):
    test_client, module = client
    for name in ('MONDAY_API_TOKEN', 'MONDAY_BOARD_ID'):
        monkeypatch.delenv(name, raising=False)
    reply = test_client.post('/api/monday/create_record', json=body)
    assert reply.is_json and "isn't set up yet" in reply.json['error']  # a handled message, not an unhandled error page
    [line] = lines(problem_log)
    assert 'MONDAY NOT SAVED' in line and 'no reference' in line


def test_a_made_up_payment_id_or_reference_is_not_copied_into_the_log(client, problem_log, monkeypatch):
    test_client, module = client
    monkeypatch.setattr(module, 'create_monday_record', lambda *a: (_ for _ in ()).throw(monday_service.MondayError('no', 400)))
    weird = {'itemName': 'x', 'fields': {'confirmationNumber': '../../etc/passwd', 'paymentIntentId': 'not-a-payment\nFAKE LINE'}}
    test_client.post('/api/monday/create_record', json=weird)
    [line] = lines(problem_log)
    assert 'passwd' not in line and 'FAKE LINE' not in line and 'no reference' in line


def test_log_lines_are_one_line_each_dated_and_capped(problem_log):
    crm_photos.log_problem('KIND', 'first\nsecond   line\r\nthird ' + 'x' * 900)
    [line] = lines(problem_log)
    date, kind, detail = line.split('  ', 2)
    assert len(date) == 19 and date[4] == '-' and date[13] == ':'
    assert kind == 'KIND' and detail.startswith('first second line third ') and len(detail) == 500


def test_a_log_that_cannot_be_written_never_gets_in_a_customers_way(tmp_path, monkeypatch):
    blocker = tmp_path / 'a-file'
    blocker.write_text('x')
    monkeypatch.setenv('KIOSK_PROBLEM_LOG', str(blocker / 'sub' / 'kiosk-problems.log'))  # a folder can't be made inside a file
    crm_photos.log_problem('KIND', 'detail')  # must simply not raise

    def refuse(name, fields):
        raise monday_service.MondayError('Monday is down', 503)
    monkeypatch.setattr(api, 'create_monday_record', refuse)
    reply = api.app.test_client().post('/api/monday/create_record', json=RECORD)
    assert reply.status_code == 503 and reply.json == {'error': 'Monday is down'}
