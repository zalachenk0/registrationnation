"""Emails the customer their Texas Tag & Title receipt after a payment.

The email carries page 2 of the print-out (the TTT receipt) as a PDF, without the signature block.
It works with any ordinary mail login: Gmail, Google Workspace, or an email service. Only the host, port,
username and password in .env change. Those secrets stay on the server, the browser never sees them.

  - One email per payment. Once it has gone out the reference is recorded, so a retry or a double click can't send it twice.
  - A mail problem never blocks a payment or a print. The route just reports it and the screen shows it.
  - Only a payment that Stripe confirms can trigger an email (the route checks, same as printing).

Try it from this folder:
  python3 receipt_email.py check             does the login work? (sends nothing)
  python3 receipt_email.py test you@x.com    sends a sample TEST receipt to that address
"""
from __future__ import annotations

import contextlib
import hashlib
import os
from pathlib import Path
import re
import smtplib
import sqlite3
import ssl
import sys
import threading
import time
from email.message import EmailMessage
from email.utils import formataddr

from payment_service import clean_email
from printing import validate
from receipt_documents import build_documents

ROOT = Path(__file__).resolve().parent
GOOGLE_APP_PASSWORD = re.compile(r'[a-z]{4}( [a-z]{4}){3}')


class MailError(RuntimeError):
    """Sending failed. The message says what to check."""


class MailNotConfigured(MailError):
    """There is no mail login in .env yet."""


def settings():
    password = os.getenv('SMTP_PASSWORD', '').strip()
    if GOOGLE_APP_PASSWORD.fullmatch(password):      # Google shows an App Password as 4 groups of 4 with spaces. The spaces aren't part of it
        password = password.replace(' ', '')
    username = os.getenv('SMTP_USERNAME', '').strip()
    try:
        port = int(os.getenv('SMTP_PORT', '587'))
    except ValueError:
        port = 587
    return {'host': os.getenv('SMTP_HOST', '').strip() or 'smtp.gmail.com', 'port': port,
            'username': username, 'password': password,
            'sender': os.getenv('RECEIPT_FROM', '').strip() or username,
            'name': os.getenv('RECEIPT_FROM_NAME', '').strip() or 'Texas Tag & Title'}


def _login_help(exc):
    code = getattr(exc, 'smtp_code', 0)
    text = (getattr(exc, 'smtp_error', b'') or b'').decode('utf-8', 'replace').lower()
    if code == 534 or 'application-specific' in text:
        return ('Google wants an App Password, not the normal account password. Turn on 2-Step Verification for the account, '
                'create an App Password, and put it in SMTP_PASSWORD.')
    if code == 535:
        return ('The email server rejected the username or password. Check SMTP_USERNAME and SMTP_PASSWORD (for Google, an App Password). '
                'If App Passwords are not available for this Google Workspace account, ask the Workspace admin, '
                'or use another email service by changing SMTP_HOST and SMTP_PORT.')
    return 'The email server rejected the login. Check SMTP_USERNAME and SMTP_PASSWORD in .env.'


@contextlib.contextmanager
def _translated():
    """Turn the mail library's errors into messages a person can act on. Never includes the password."""
    try:
        yield
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError(_login_help(exc)) from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise MailError('The email server refused that address. Check the email on the Review step.') from exc
    except smtplib.SMTPSenderRefused as exc:
        raise MailError('The email server refused the sender address. RECEIPT_FROM must be the login account itself or an alias it owns.') from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError('Could not reach the email server. Check the internet connection and the SMTP settings in .env.') from exc


def _connect(cfg):
    context = ssl.create_default_context()
    if cfg['port'] == 465:
        server = smtplib.SMTP_SSL(cfg['host'], cfg['port'], timeout=20, context=context)
    else:
        server = smtplib.SMTP(cfg['host'], cfg['port'], timeout=20)
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
    server.login(cfg['username'], cfg['password'])
    return server


def _message(cfg, to, data, payment, pdf):
    test = payment.get('testMode', True)
    msg = EmailMessage()
    msg['From'] = formataddr((cfg['name'], cfg['sender']))
    msg['To'] = to
    msg['Subject'] = 'Your Texas Tag & Title receipt' + (' (TEST: no real money charged)' if test else '')
    lines = [f"Hello {data['registrant']},", '',
             'Thank you for renewing your vehicle registration with Texas Tag & Title. Your receipt is attached as a PDF.', '',
             f"Amount paid: ${payment['amount'] / 100:,.2f}", f"Reference: {data['confirmation_number']}"]
    if test:
        lines += ['', 'This is a TEST receipt from a demonstration kiosk. No real money was charged.']
    msg.set_content('\n'.join(lines) + '\n')
    msg.add_attachment(pdf, maintype='application', subtype='pdf', filename='Texas-Tag-and-Title-receipt.pdf')
    return msg


def _deliver(cfg, to, data, payment, pdf):
    msg = _message(cfg, to, data, payment, pdf)
    with _translated():
        server = _connect(cfg)
        try:
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except (smtplib.SMTPException, OSError):
                pass                                  # the message already went. A messy goodbye is not a failure


class ReceiptMailer:
    def __init__(self, database=None):
        self.database = Path(database or os.getenv('KIOSK_PAYMENT_DB') or ROOT / 'runtime/payments.sqlite3')
        self.lock = threading.Lock()

    def _db(self):
        self.database.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.database, timeout=30)
        # only a fingerprint of the address is kept, not the address itself
        db.execute('CREATE TABLE IF NOT EXISTS receipt_emails (reference TEXT PRIMARY KEY, email_hash TEXT NOT NULL, sent REAL NOT NULL)')
        return db

    def send(self, payload, payment):
        """payment is the verified payment from Stripe (the caller has already checked it)."""
        data = validate(payload)
        email = clean_email(payload.get('receipt_email'))
        if not email:
            raise ValueError('No valid email address was entered, so no receipt email was sent.')
        cfg = settings()
        if not cfg['username'] or not cfg['password']:
            raise MailNotConfigured('Receipt email is not set up on this kiosk yet. Add SMTP_USERNAME and SMTP_PASSWORD to .env.')
        reference = data['confirmation_number']
        with self.lock:
            db = self._db()
            try:
                if db.execute('SELECT 1 FROM receipt_emails WHERE reference=?', (reference,)).fetchone():
                    return {'to': email, 'already': True}          # one receipt per payment, however many times the screen asks
                pdf = build_documents(data, payment=payment, email_copy=True)
                _deliver(cfg, email, data, payment, pdf)
                with db:
                    db.execute('INSERT INTO receipt_emails VALUES (?,?,?)',
                               (reference, hashlib.sha256(email.lower().encode()).hexdigest(), time.time()))
                return {'to': email, 'already': False}
            finally:
                db.close()


def _cli(argv):
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    if len(argv) < 2 or argv[1] not in ('check', 'test'):
        print(__doc__)
        return 2
    cfg = settings()
    if not cfg['username'] or not cfg['password']:
        print('Not set up yet: add SMTP_USERNAME and SMTP_PASSWORD to .env (see S700_AND_MONDAY.md).')
        return 1
    where = f"{cfg['username']} through {cfg['host']}:{cfg['port']}"
    try:
        if argv[1] == 'check':
            with _translated():
                _connect(cfg).quit()
            print(f'OK: logged in as {where}. Nothing was sent.')
        else:
            to = clean_email(argv[2] if len(argv) > 2 else None)
            if not to:
                print('Usage: python3 receipt_email.py test someone@example.com')
                return 2
            data = {'registrant': 'Jane Sample', 'license_plate': 'KGP-4821', 'vehicle_year': '2014', 'vehicle_make': 'Toyota',
                    'vehicle_model': 'Rav4', 'vin': '2T3WFREV2EW123456', 'confirmation_number': 'REG-SAMPLE-TEST-0001'}
            payment = {'id': 'pi_sample_test', 'amount': 11425, 'testMode': True}
            _deliver(cfg, to, data, payment, build_documents(data, payment=payment, email_copy=True))
            print(f'Sent a sample TEST receipt to {to} from {where}. Check that inbox, and the spam folder.')
    except MailError as exc:
        print('Failed:', exc)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(_cli(sys.argv))
