"""The office problem log: backend problems the customer never sees are written down for whoever looks after the kiosk."""
import pytest

import monday_service
import scanner_api as api

HEADERS = {'X-Kiosk-Payment': '1'}
RECORD = {'itemName': 'Doe, Jane - KGP-4821', 'fields': {'paymentIntentId': 'pi_test_1', 'fullName': 'Jane Doe'}}


def lines(path):
    return path.read_text(encoding='utf-8').splitlines() if path.exists() else []


def test_a_monday_failure_is_logged_with_who_and_why_and_the_response_is_unchanged(problem_log, monkeypatch):
    def refuse(name, fields):
        raise monday_service.MondayError('Not Authenticated (X) - check MONDAY_API_TOKEN', 401)
    monkeypatch.setattr(api, 'create_monday_record', refuse)
    reply = api.app.test_client().post('/api/monday/create_record', json=RECORD)
    assert reply.status_code == 401 and 'Not Authenticated' in reply.json['error']          # exactly what the page got before
    [line] = lines(problem_log)
    assert 'MONDAY NOT SAVED' in line and 'Doe, Jane - KGP-4821' in line and 'payment pi_test_1' in line and 'check MONDAY_API_TOKEN' in line


def test_skipped_fields_are_logged_but_a_clean_save_writes_nothing(problem_log, monkeypatch):
    monkeypatch.setattr(api, 'create_monday_record', lambda name, fields: {'itemId': '7', 'warnings': ["phone: '12' doesn't look like a phone number"]})
    reply = api.app.test_client().post('/api/monday/create_record', json=RECORD)
    assert reply.status_code == 200 and reply.json == {'success': True, 'itemId': '7', 'warnings': ["phone: '12' doesn't look like a phone number"]}
    assert 'MONDAY SAVED, FIELDS SKIPPED' in lines(problem_log)[0] and "phone: '12'" in lines(problem_log)[0]
    problem_log.unlink()
    monkeypatch.setattr(api, 'create_monday_record', lambda name, fields: {'itemId': '8', 'warnings': []})
    assert api.app.test_client().post('/api/monday/create_record', json=RECORD).status_code == 200
    assert lines(problem_log) == []                                                         # a normal day leaves the log empty


@pytest.mark.parametrize('body', [{'itemName': 'x', 'fields': 'oops'}, {'fields': None}, {}])
def test_odd_requests_get_a_clean_handled_reply_and_a_log_line_never_a_crash(body, problem_log, monkeypatch):
    for name in ('MONDAY_API_TOKEN', 'MONDAY_BOARD_ID'):
        monkeypatch.delenv(name, raising=False)
    reply = api.app.test_client().post('/api/monday/create_record', json=body)
    assert reply.is_json and "isn't set up yet" in reply.json['error']                      # a handled message, not an unhandled error page
    assert 'MONDAY NOT SAVED' in lines(problem_log)[0]


def test_log_lines_are_one_line_each_dated_and_capped(problem_log):
    api.log_problem('KIND', 'first\nsecond   line\r\nthird ' + 'x' * 900)
    [line] = lines(problem_log)
    assert line[:4].isdigit() and '  KIND  first second line third x' in line and len(line) < 560


def test_a_log_that_cannot_be_written_never_gets_in_a_customers_way(tmp_path, monkeypatch):
    blocker = tmp_path / 'a-file'
    blocker.write_text('x')
    monkeypatch.setattr(api, 'PROBLEM_LOG', blocker / 'sub' / 'kiosk-problems.log')          # a folder can't be made inside a file
    api.log_problem('KIND', 'detail')                                                        # must simply not raise


def test_receipt_email_problems_are_logged_with_the_reference_and_never_the_password(problem_log, monkeypatch):
    import receipt_email as mail
    monkeypatch.setenv('SMTP_USERNAME', 'registrationnation@texastagandtitle.com')
    monkeypatch.setenv('SMTP_PASSWORD', 'abcd efgh ijkl mnop')
    paid = dict(id='pi_x', status='succeeded', amount=11425, testMode=True)
    monkeypatch.setattr(api.payments, 'verify_paid', lambda payload: paid)

    def broken(payload, payment):
        raise mail.MailError('The email server rejected the username or password.')
    monkeypatch.setattr(api.mailer, 'send', broken)
    form = dict(registrant='Jane Sample', license_plate='KGP-4821', vehicle_year='2014', vehicle_make='Toyota', vehicle_model='Rav4',
                vin='2T3WFREV2EW123456', confirmation_number='REG-LOG-TEST-0001', receipt_email='jane@example.com')
    assert api.app.test_client().post('/api/payment/email_receipt', json=form, headers=HEADERS).status_code == 502
    [line] = lines(problem_log)
    assert 'RECEIPT EMAIL NOT SENT' in line and 'REG-LOG-TEST-0001' in line and 'rejected the username or password' in line
    assert 'abcdefghijklmnop' not in line and 'abcd efgh' not in line and 'jane@example.com' not in line


def test_an_unpaid_attempt_is_not_logged_as_an_email_problem(problem_log, monkeypatch):
    import payment_service as payment

    def unpaid(payload):
        raise payment.PaymentError('Payment is not complete.')
    monkeypatch.setattr(api.payments, 'verify_paid', unpaid)
    assert api.app.test_client().post('/api/payment/email_receipt', json={'confirmation_number': 'REG-X'}, headers=HEADERS).status_code == 409
    assert lines(problem_log) == []
