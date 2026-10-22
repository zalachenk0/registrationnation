"""The emailed TTT receipt: a one-page PDF without the signature block, sent once per payment. No real mail server is used."""
import io
import smtplib
import sqlite3
from datetime import date

import pytest
from pypdf import PdfReader
from pypdf.generic import ContentStream
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

import payment_service as payment
import receipt_documents
import receipt_email as mail
import scanner_api as api

FORM = dict(registrant='Jane Sample', license_plate='KGP-4821', vehicle_year='2014', vehicle_make='Toyota',
            vehicle_model='Rav4', vin='2T3WFREV2EW123456', confirmation_number='REG-MAIL-TEST-0001',
            receipt_email='jane.sample@example.com')
PAID = dict(id='pi_test_1', status='succeeded', amount=11425, testMode=True)
HEADERS = {'X-Kiosk-Payment': '1'}


def words(pdf_bytes, page=0):
    return ' '.join(PdfReader(io.BytesIO(pdf_bytes)).pages[page].extract_text().split())


# ---------- the PDF ----------

def test_email_copy_is_one_page_without_the_signature_block():
    pdf = receipt_documents.build_documents(FORM, PAID, today=date(2026, 10, 22), email_copy=True)
    reader = PdfReader(io.BytesIO(pdf))
    text = words(pdf)
    assert len(reader.pages) == 1
    assert not any(w in text for w in ('Signature', 'signing', 'By signing', 'corrections at no charge'))
    for kept in ('Jane Sample', 'KGP-4821', '2T3WFREV2EW123456', '114.25', 'Outstanding Balance', 'TEST PAYMENT - NO REAL MONEY',
                 'pi_test_1', 'Test receipt; no real money charged.', 'REG-MAIL-TEST-0001'):
        assert kept in text, kept
    assert '2 / 2' not in text and '1 / 2' not in text
    assert float(reader.pages[0].mediabox.bottom) == receipt_documents.EMAIL_COPY_BOTTOM      # cropped under the table


def test_the_signature_is_removed_not_just_covered_up():
    pdf = receipt_documents.build_documents(FORM, PAID, email_copy=True)
    raw = b''.join(ContentStream(PdfReader(io.BytesIO(pdf)).pages[0].get_contents(), PdfReader(io.BytesIO(pdf))).get_data() for _ in [0])
    assert b'Signature' not in raw and b'signing' not in raw                                     # not hidden in the file either


def test_the_printed_version_is_exactly_as_before():
    pdf = receipt_documents.build_documents(FORM, PAID, today=date(2026, 10, 22))
    reader = PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) == 2
    assert 'SAMPLE - NOT VALID' in words(pdf, 0) and '1 / 2' in words(pdf, 0)
    second = words(pdf, 1)
    assert 'Customer Signature' in second and 'By signing' in second and '2 / 2' in second     # the printed copy keeps its signature block
    assert float(reader.pages[1].mediabox.bottom) == 0


def test_only_the_signature_line_is_removed_and_the_rule_at_the_top_is_kept():
    template = PdfReader(receipt_documents.TEMPLATE)
    before = sum(1 for _, op in ContentStream(template.pages[0].get_contents(), template).operations if op == b'm')
    pdf = receipt_documents.build_documents(FORM, PAID, email_copy=True)
    reader = PdfReader(io.BytesIO(pdf))
    after = sum(1 for _, op in ContentStream(reader.pages[0].get_contents(), reader).operations if op == b'm')
    assert after == before - 1        # exactly one line gone. A careless "delete everything low" would have taken the top rule too


def test_a_template_without_a_signature_block_fails_loudly(tmp_path, monkeypatch):
    other = tmp_path / 'other.pdf'
    page = canvas.Canvas(str(other), pagesize=letter)
    page.drawString(72, 700, 'A different receipt with nothing near the bottom')
    page.showPage()
    page.save()
    monkeypatch.setattr(receipt_documents, 'TEMPLATE', other)
    with pytest.raises(ValueError, match='signature block was not found'):
        receipt_documents.build_documents(FORM, PAID, email_copy=True)                           # never email a receipt that still has it


# ---------- the mailer, with a fake mail server ----------

class FakeMail:
    def __init__(self, monkeypatch):
        self.calls, self.messages, self.connections = [], [], []
        self.connect_error = self.login_error = self.send_error = self.quit_error = None
        outer = self

        class Server:
            def __init__(self, host, port, timeout=None, context=None):
                if outer.connect_error:
                    raise outer.connect_error
                outer.connections.append((type(self).__name__, host, port))

            def ehlo(self):
                outer.calls.append('ehlo')

            def starttls(self, context=None):
                outer.calls.append('starttls')

            def login(self, user, password):
                outer.calls.append(('login', user, password))
                if outer.login_error:
                    raise outer.login_error

            def send_message(self, msg):
                if outer.send_error:
                    raise outer.send_error
                outer.messages.append(msg)
                outer.calls.append('send')

            def quit(self):
                outer.calls.append('quit')
                if outer.quit_error:
                    raise outer.quit_error

        class SMTP(Server):
            pass

        class SMTP_SSL(Server):
            pass

        monkeypatch.setattr(smtplib, 'SMTP', SMTP)
        monkeypatch.setattr(smtplib, 'SMTP_SSL', SMTP_SSL)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('SMTP_USERNAME', 'registrationnation@texastagandtitle.com')
    monkeypatch.setenv('SMTP_PASSWORD', 'abcd efgh ijkl mnop')
    for name in ('SMTP_HOST', 'SMTP_PORT', 'RECEIPT_FROM', 'RECEIPT_FROM_NAME'):
        monkeypatch.delenv(name, raising=False)
    return mail.ReceiptMailer(tmp_path / 'mail.sqlite3'), FakeMail(monkeypatch)


def attachment_pdf(message):
    parts = [p for p in message.iter_attachments()]
    assert len(parts) == 1 and parts[0].get_content_type() == 'application/pdf'
    return parts[0].get_content(), parts[0].get_filename()


def test_a_receipt_is_sent_with_the_one_page_pdf_attached(setup):
    mailer, smtp = setup
    result = mailer.send(FORM, PAID)
    assert result == {'to': 'jane.sample@example.com', 'already': False}
    assert smtp.connections == [('SMTP', 'smtp.gmail.com', 587)]
    assert smtp.calls == ['ehlo', 'starttls', 'ehlo', ('login', 'registrationnation@texastagandtitle.com', 'abcdefghijklmnop'), 'send', 'quit']
    msg = smtp.messages[0]
    assert msg['To'] == 'jane.sample@example.com' and 'registrationnation@texastagandtitle.com' in msg['From'] and 'Texas Tag & Title' in msg['From']
    assert 'receipt' in msg['Subject'] and 'TEST' in msg['Subject']
    body = msg.get_body(preferencelist=('plain',)).get_content()
    assert 'Jane Sample' in body and '$114.25' in body and 'REG-MAIL-TEST-0001' in body and 'TEST receipt' in body
    pdf, filename = attachment_pdf(msg)
    assert filename == 'Texas-Tag-and-Title-receipt.pdf' and len(PdfReader(io.BytesIO(pdf)).pages) == 1
    assert 'Jane Sample' in words(pdf) and 'signing' not in words(pdf)


def test_port_465_uses_a_secure_connection_from_the_start(setup, monkeypatch):
    mailer, smtp = setup
    monkeypatch.setenv('SMTP_PORT', '465')
    mailer.send(FORM, PAID)
    assert smtp.connections == [('SMTP_SSL', 'smtp.gmail.com', 465)] and 'starttls' not in smtp.calls


def test_another_mail_service_works_with_just_different_settings(setup, monkeypatch):
    mailer, smtp = setup
    monkeypatch.setenv('SMTP_HOST', 'smtp.example-mail.com')
    monkeypatch.setenv('SMTP_PORT', '2525')
    monkeypatch.setenv('RECEIPT_FROM', 'receipts@texastagandtitle.com')
    monkeypatch.setenv('RECEIPT_FROM_NAME', 'TTT Receipts')
    mailer.send(FORM, PAID)
    assert smtp.connections == [('SMTP', 'smtp.example-mail.com', 2525)]
    assert 'receipts@texastagandtitle.com' in smtp.messages[0]['From'] and 'TTT Receipts' in smtp.messages[0]['From']


def test_one_email_per_payment_however_often_the_screen_asks(setup):
    mailer, smtp = setup
    assert mailer.send(FORM, PAID)['already'] is False
    assert mailer.send(FORM, PAID) == {'to': 'jane.sample@example.com', 'already': True}
    assert len(smtp.messages) == 1 and len(smtp.connections) == 1
    assert mailer.send(dict(FORM, confirmation_number='REG-MAIL-TEST-0002'), PAID)['already'] is False      # a different payment is a different email
    assert len(smtp.messages) == 2


def test_the_address_itself_is_not_stored_only_a_fingerprint(setup, tmp_path):
    mailer, _ = setup
    mailer.send(FORM, PAID)
    row = sqlite3.connect(tmp_path / 'mail.sqlite3').execute('SELECT reference, email_hash FROM receipt_emails').fetchone()
    assert row[0] == 'REG-MAIL-TEST-0001' and len(row[1]) == 64 and '@' not in row[1]
    assert b'jane.sample' not in (tmp_path / 'mail.sqlite3').read_bytes()


def test_no_login_in_env_means_a_clear_not_set_up_message_and_nothing_sent(setup, monkeypatch):
    mailer, smtp = setup
    monkeypatch.setenv('SMTP_PASSWORD', '')
    with pytest.raises(mail.MailNotConfigured, match='SMTP_USERNAME and SMTP_PASSWORD'):
        mailer.send(FORM, PAID)
    assert smtp.connections == []


@pytest.mark.parametrize('bad', [None, '', 'not-an-email', 'two@@example.com', 'a b@example.com'])
def test_a_missing_or_bad_address_sends_nothing(setup, bad):
    mailer, smtp = setup
    with pytest.raises(ValueError, match='No valid email address'):
        mailer.send(dict(FORM, receipt_email=bad), PAID)
    assert smtp.connections == []


def test_incomplete_form_data_sends_nothing(setup):
    mailer, smtp = setup
    with pytest.raises(ValueError):
        mailer.send(dict(FORM, vin='123'), PAID)
    assert smtp.connections == []


def test_a_value_too_long_for_the_receipt_is_refused_not_cut_off(setup):
    mailer, smtp = setup
    with pytest.raises(ValueError, match='too long'):
        mailer.send(dict(FORM, license_plate='W' * 60), PAID)      # a plate that can't fit its box
    assert smtp.messages == []


@pytest.mark.parametrize('code, text, expect', [
    (534, b'5.7.9 Application-specific password required', 'App Password'),
    (535, b'5.7.8 Username and Password not accepted', 'Workspace'),
    (454, b'4.7.0 something else', 'rejected the login'),
])
def test_login_problems_say_what_to_check_and_never_show_the_password(setup, code, text, expect):
    mailer, smtp = setup
    smtp.login_error = smtplib.SMTPAuthenticationError(code, text)
    with pytest.raises(mail.MailError) as caught:
        mailer.send(FORM, PAID)
    assert expect in str(caught.value) and 'abcdefghijklmnop' not in str(caught.value) and 'abcd efgh' not in str(caught.value)
    assert smtp.messages == []


def test_after_a_failure_a_retry_sends_and_is_recorded_only_then(setup, tmp_path):
    mailer, smtp = setup
    smtp.login_error = smtplib.SMTPAuthenticationError(535, b'nope')
    with pytest.raises(mail.MailError):
        mailer.send(FORM, PAID)
    assert sqlite3.connect(tmp_path / 'mail.sqlite3').execute('SELECT COUNT(*) FROM receipt_emails').fetchone()[0] == 0   # a failure is not recorded as sent
    smtp.login_error = None
    assert mailer.send(FORM, PAID)['already'] is False and len(smtp.messages) == 1


@pytest.mark.parametrize('attr, error, expect', [
    ('send_error', smtplib.SMTPRecipientsRefused({'jane.sample@example.com': (550, b'no such user')}), 'refused that address'),
    ('send_error', smtplib.SMTPSenderRefused(553, b'not allowed', 'x@y.com'), 'RECEIPT_FROM'),
    ('send_error', smtplib.SMTPServerDisconnected('gone'), 'Could not reach'),
    ('connect_error', OSError('network is down'), 'Could not reach'),
    ('connect_error', TimeoutError('timed out'), 'Could not reach'),
])
def test_other_mail_problems_become_plain_messages(setup, attr, error, expect):
    mailer, smtp = setup
    setattr(smtp, attr, error)
    with pytest.raises(mail.MailError, match=expect):
        mailer.send(FORM, PAID)


def test_a_messy_goodbye_after_the_message_went_is_not_a_failure(setup):
    mailer, smtp = setup
    smtp.quit_error = smtplib.SMTPServerDisconnected('closed')
    assert mailer.send(FORM, PAID)['already'] is False and len(smtp.messages) == 1


def test_a_real_payment_is_not_labelled_test(setup):
    mailer, smtp = setup
    mailer.send(FORM, dict(PAID, testMode=False))
    msg = smtp.messages[0]
    assert '(TEST' not in msg['Subject'] and 'TEST receipt' not in msg.get_body(preferencelist=('plain',)).get_content()
    assert 'PAYMENT RECEIPT' in words(attachment_pdf(msg)[0])


def test_the_customers_name_never_goes_into_a_header(setup):
    mailer, smtp = setup
    mailer.send(dict(FORM, registrant='Jane\r\nBcc: attacker@example.com'), PAID)
    msg = smtp.messages[0]
    assert msg['Bcc'] is None and 'attacker' not in str(msg['To']) + str(msg['From']) + str(msg['Subject'])


# ---------- the command-line helper ----------

def test_check_command_logs_in_and_sends_nothing(setup, capsys):
    _, smtp = setup
    assert mail._cli(['x', 'check']) == 0
    assert 'logged in' in capsys.readouterr().out and smtp.messages == [] and smtp.calls[-1] == 'quit'


def test_test_command_sends_a_sample_to_the_given_address(setup, capsys):
    _, smtp = setup
    assert mail._cli(['x', 'test', 'me@example.com']) == 0
    assert smtp.messages[0]['To'] == 'me@example.com' and 'Sent a sample' in capsys.readouterr().out
    assert len(PdfReader(io.BytesIO(attachment_pdf(smtp.messages[0])[0])).pages) == 1


def test_commands_explain_a_missing_login_or_a_bad_login(setup, monkeypatch, capsys):
    _, smtp = setup
    smtp.login_error = smtplib.SMTPAuthenticationError(534, b'5.7.9 Application-specific password required')
    assert mail._cli(['x', 'check']) == 1 and 'App Password' in capsys.readouterr().out
    monkeypatch.setenv('SMTP_PASSWORD', '')
    assert mail._cli(['x', 'check']) == 1 and 'Not set up yet' in capsys.readouterr().out


# ---------- the route ----------

@pytest.fixture
def route(setup, monkeypatch):
    mailer, smtp = setup
    monkeypatch.setattr(api, 'mailer', mailer)
    monkeypatch.setattr(api.payments, 'verify_paid', lambda payload: PAID)
    return api.app.test_client(), smtp


def test_route_needs_the_kiosk_header(route):
    client, smtp = route
    assert client.post('/api/payment/email_receipt', json=FORM).status_code == 403
    assert smtp.connections == []


def test_route_refuses_when_the_payment_is_not_confirmed(route, monkeypatch):
    client, smtp = route

    def unpaid(payload):
        raise payment.PaymentError('Payment is not complete.')
    monkeypatch.setattr(api.payments, 'verify_paid', unpaid)
    reply = client.post('/api/payment/email_receipt', json=FORM, headers=HEADERS)
    assert reply.status_code == 409 and smtp.connections == []                   # no confirmed payment, no email


def test_route_sends_once_then_reports_already_sent(route):
    client, smtp = route
    first = client.post('/api/payment/email_receipt', json=FORM, headers=HEADERS)
    assert first.status_code == 200 and first.json == {'success': True, 'to': 'jane.sample@example.com', 'already': False}
    again = client.post('/api/payment/email_receipt', json=FORM, headers=HEADERS)
    assert again.status_code == 200 and again.json['already'] is True and len(smtp.messages) == 1


def test_route_reports_each_kind_of_problem_with_the_right_status(route, monkeypatch):
    client, smtp = route
    assert client.post('/api/payment/email_receipt', json=dict(FORM, receipt_email='nope'), headers=HEADERS).status_code == 422
    assert client.post('/api/payment/email_receipt', data='not json', headers=HEADERS).status_code == 422
    smtp.login_error = smtplib.SMTPAuthenticationError(535, b'x')
    bad = client.post('/api/payment/email_receipt', json=FORM, headers=HEADERS)
    assert bad.status_code == 502 and 'rejected the username or password' in bad.json['error']
    monkeypatch.setenv('SMTP_PASSWORD', '')
    off = client.post('/api/payment/email_receipt', json=FORM, headers=HEADERS)
    assert off.status_code == 503 and off.json['notConfigured'] is True
