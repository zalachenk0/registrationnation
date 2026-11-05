# ============================================================================
# File:         delivery_service_BV.py
# Written by:   BV (Blas Vargas)
# What it does: Tracks print jobs in CUPS, queues security camera events and
#               emails the company receipt PDF.
# Team-written: Logic written by BV (Blas Vargas). Outside code is used only
#               through the third-party sources listed below.
# Third-party:  pypdf; pycups; python-dotenv; CUPS print system (lp command and
#               pycups); Gmail SMTP mail server.
# ============================================================================
"""Durable camera events, CUPS completion tracking, and company receipt email."""
from contextlib import contextmanager
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
import argparse
import hashlib
import io
import json
import os
import re
import smtplib
import sqlite3
import ssl
import threading
import time
import uuid

from printing_BV import create_pdf, document_data, PrintError
from security_client_BV_DG import capture_front


def company_receipt_pdf(printed_pdf):
    """Email only the populated TTT receipt (page 2), with no sticker or appendix."""
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(io.BytesIO(printed_pdf))
    if len(reader.pages) < 2:
        raise ValueError('The printed PDF has no TTT receipt on page 2.')
    writer = PdfWriter()
    writer.add_page(reader.pages[1])
    writer.add_metadata({'/Title': 'TTT receipt', '/Author': 'Registration Nation'})
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def valid_email(value):
    if not isinstance(value, str):
        raise ValueError('Enter one valid email address in the form.')
    address = value.strip()
    if len(address) > 254 or '\r' in address or '\n' in address or parseaddr(address)[1] != address or not re.fullmatch(r'[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+', address):
        raise ValueError('Enter one valid email address in the form.')
    return address


def receipt_recipient(payload, prepared):
    form_email = prepared.get('form_details', {}).get('personal', {}).get('email', '')
    checkout_email = payload.get('receipt_email', '')
    if not form_email and not checkout_email:
        return ''  # Older saved jobs have no destination; never use a fixed fallback.
    recipient = valid_email(form_email or checkout_email)
    if form_email and checkout_email and valid_email(checkout_email) != recipient:
        raise ValueError('The receipt email does not match the email entered in the form.')
    return recipient


def send_receipt(pdf, reference, recipient):
    attachment = company_receipt_pdf(pdf)
    sender = os.getenv('RECEIPT_SMTP_USERNAME', '').strip()
    recipient = valid_email(recipient)
    password = os.getenv('RECEIPT_SMTP_PASSWORD', '').replace(' ', '')
    if not all((sender, recipient, password)):
        raise ValueError('Configure RECEIPT_SMTP_USERNAME and RECEIPT_SMTP_PASSWORD on the kiosk Pi.')
    for address in (sender, recipient):
        if '\r' in address or '\n' in address or parseaddr(address)[1] != address or not re.fullmatch(r'[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+', address):
            raise ValueError('Use one valid sender and one valid receipt recipient.')
    message = EmailMessage()
    message['Subject'] = 'Your Registration Nation receipt'
    message['From'] = f'Registration Nation <{sender}>'
    message['To'] = recipient
    message['Message-ID'] = f'<receipt-{reference.lower()}@registrationnation.local>'
    message.set_content('Thank you for choosing Registration Nation. Your receipt is attached.\n\nRegistration Nation\nTexas Tag & Title\n')
    message.add_attachment(attachment, maintype='application', subtype='pdf', filename=f'TTT-Receipt-{reference}.pdf')
    with smtplib.SMTP('smtp.gmail.com', 587, timeout=20) as connection:
        connection.starttls(context=ssl.create_default_context())
        connection.login(sender, password)
        if connection.send_message(message):
            raise RuntimeError('Receipt recipient was refused.')


def cups_state(job_id):
    import cups
    return int(cups.Connection().getJobAttributes(job_id)['job-state'])


class DeliveryService:
    def __init__(self, root, printer):
        self.root = Path(root)
        self.directory = self.root / 'runtime' / 'deliveries'
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.database = self.directory / 'delivery.sqlite3'
        self.printer = printer
        self.stop = threading.Event()
        self.submit_lock = threading.Lock()
        with self.db() as connection:
            connection.executescript('''
              CREATE TABLE IF NOT EXISTS cameras (key TEXT PRIMARY KEY, request_id TEXT NOT NULL,
                event TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL, error TEXT NOT NULL DEFAULT '');
              CREATE TABLE IF NOT EXISTS documents (reference TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                pdf TEXT NOT NULL, job TEXT NOT NULL DEFAULT '', job_id INTEGER,
                state TEXT NOT NULL, email_state TEXT NOT NULL DEFAULT 'waiting',
                created REAL NOT NULL, error TEXT NOT NULL DEFAULT '');
            ''')
            columns = {row['name'] for row in connection.execute('PRAGMA table_info(documents)')}
            if 'recipient' not in columns:
                connection.execute("ALTER TABLE documents ADD COLUMN recipient TEXT NOT NULL DEFAULT ''")
            # Never repeat a physical action after an uncertain crash/SMTP acknowledgement.
            connection.execute("UPDATE cameras SET state='uncertain',error='Capture interrupted; inspect the camera Pi before retrying.' WHERE state='sending'")
            connection.execute("UPDATE documents SET state='unknown',error='Submission interrupted; inspect CUPS before retrying.' WHERE state='submitting'")
            connection.execute("UPDATE documents SET email_state='uncertain',error='Email interrupted; inspect Sent mail before retrying.' WHERE email_state='sending'")
        os.chmod(self.database, 0o600)

    @contextmanager
    def db(self):
        with sqlite3.connect(self.database, timeout=15) as connection:
            connection.row_factory = sqlite3.Row
            yield connection

    def enqueue_capture(self, event, key=None):
        if event not in {'license_scan', 'insurance_scan', 'payment_click', 'print_completed'}:
            raise ValueError('Unknown security event.')
        key = key or 'license:' + uuid.uuid4().hex
        with self.db() as connection:
            connection.execute('INSERT OR IGNORE INTO cameras (key,request_id,event,state,created) VALUES (?,?,?,?,?)',
                               (key, uuid.uuid4().hex, event, 'pending', time.time()))
            row = connection.execute('SELECT state FROM cameras WHERE key=?', (key,)).fetchone()
        return dict(enabled=True, queued=True, state=row['state'])

    def submit(self, payload, payment):
        data = document_data(payload)
        recipient = receipt_recipient(payload, data)
        reference = data['confirmation_number']
        fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        with self.submit_lock:
            with self.db() as connection:
                previous = connection.execute('SELECT * FROM documents WHERE reference=?', (reference,)).fetchone()
            if previous:
                if previous['recipient'] and recipient != previous['recipient']:
                    raise ValueError('This receipt already belongs to a different form email. Do not reuse its reference.')
                if previous['fingerprint'] != fingerprint:
                    raise ValueError('This reference already has different form data. Check the existing job before retrying.')
                if previous['state'] in {'unknown', 'error', 'cancelled', 'aborted'}:
                    raise PrintError(previous['error'] or 'The print job needs operator attention. Do not pay again.')
                return self.status(reference, duplicate=True)
            pdf = create_pdf(data, payment=payment)
            path = self.directory / (reference + '-' + uuid.uuid4().hex[:8] + '.pdf')
            with path.open('xb') as output:
                os.chmod(path, 0o600)
                output.write(pdf)
            with self.db() as connection:
                connection.execute('INSERT INTO documents (reference,fingerprint,pdf,state,created,recipient) VALUES (?,?,?,?,?,?)',
                                   (reference, fingerprint, str(path), 'submitting', time.time(), recipient))
            try:
                result = self.printer.submit(data, payment=payment, document_bytes=pdf)
            except PrintError:
                if not self.printer.jobs.get(reference, {}).get('unknown'):
                    with self.db() as connection:
                        connection.execute('DELETE FROM documents WHERE reference=?', (reference,))
                    path.unlink(missing_ok=True)
                    raise
                with self.db() as connection:
                    connection.execute("UPDATE documents SET state='unknown',error='Print submission timed out. Check CUPS before retrying; do not pay again.' WHERE reference=?", (reference,))
                raise
            except Exception:
                with self.db() as connection:
                    connection.execute("UPDATE documents SET state='unknown',error='Print submission failed or timed out. Check CUPS before retrying; do not pay again.' WHERE reference=?", (reference,))
                raise
            match = re.search(r'\brequest id is (\S+)-(\d+)\b', result['job'])
            with self.db() as connection:
                connection.execute("UPDATE documents SET job=?,job_id=?,state=?,error=? WHERE reference=?",
                                   (result['job'], int(match[2]) if match else None, 'queued' if match else 'unknown', '' if match else 'Could not identify the CUPS job. Inspect the queue.', reference))
            return self.status(reference)

    def status(self, reference, duplicate=False):
        with self.db() as connection:
            row = connection.execute('SELECT * FROM documents WHERE reference=?', (reference,)).fetchone()
            camera = connection.execute('SELECT state FROM cameras WHERE key=?', ('print:' + reference,)).fetchone()
        if not row:
            raise ValueError('Print reference was not found.')
        return dict(success=True, reference=reference, job=row['job'], state=row['state'], duplicate=duplicate,
                    email_state=row['email_state'], recipient=row['recipient'], camera_state=camera['state'] if camera else 'waiting', error=row['error'])

    def monitor_once(self):
        with self.db() as connection:
            jobs = connection.execute("SELECT reference,job_id FROM documents WHERE state='queued'").fetchall()
        for row in jobs:
            try:
                state = cups_state(row['job_id'])
            except Exception:
                continue  # No evidence of completion: never infer it from an empty queue.
            if state == 9:
                self.enqueue_capture('print_completed', 'print:' + row['reference'])
                with self.db() as connection:
                    connection.execute("UPDATE documents SET state='completed',email_state='pending' WHERE reference=? AND state='queued'", (row['reference'],))
            elif state in {7, 8}:
                with self.db() as connection:
                    connection.execute('UPDATE documents SET state=?,error=? WHERE reference=?',
                                       ('cancelled' if state == 7 else 'aborted', 'Printing stopped. Check the printer and CUPS; do not pay again.', row['reference']))

    def camera_once(self):
        with self.db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute("SELECT * FROM cameras WHERE state='pending' ORDER BY created LIMIT 1").fetchone()
            if row:
                connection.execute("UPDATE cameras SET state='sending' WHERE key=?", (row['key'],))
        if not row:
            return
        try:
            result = capture_front(row['event'], row['request_id'])
            state = 'disabled' if not result.get('enabled') else 'saved' if result.get('saved') else 'failed'
            error = '' if state in {'saved', 'disabled'} else 'Camera Pi could not save the photo.'
        except Exception:
            state, error = 'uncertain', 'Camera request failed or timed out; inspect the camera Pi before retrying.'
        with self.db() as connection:
            connection.execute('UPDATE cameras SET state=?,error=? WHERE key=?', (state, error, row['key']))

    def email_once(self):
        with self.db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute("SELECT * FROM documents WHERE email_state='pending' ORDER BY created LIMIT 1").fetchone()
            if row:
                connection.execute("UPDATE documents SET email_state='sending' WHERE reference=?", (row['reference'],))
        if not row:
            return
        try:
            send_receipt(Path(row['pdf']).read_bytes(), row['reference'], row['recipient'])
            state, error = 'sent', ''
        except Exception:
            state, error = 'failed', 'Company receipt email failed. Check Gmail settings and Sent mail before retrying email only.'
        with self.db() as connection:
            connection.execute('UPDATE documents SET email_state=?,error=? WHERE reference=?', (state, error, row['reference']))

    def start(self):
        for operation in (self.monitor_once, self.camera_once, self.email_once):
            def run(action=operation):
                while not self.stop.wait(0.75):
                    try:
                        action()
                    except Exception:
                        # Never expose receipt contents, SMTP passwords, or tokens in logs.
                        import logging
                        logging.getLogger(__name__).warning('Delivery worker needs attention: %s', action.__name__)
            threading.Thread(target=run, daemon=True, name=operation.__name__).start()


if __name__ == '__main__':
    from dotenv import load_dotenv
    from printing_BV import PrintService
    root = Path(__file__).resolve().parent
    load_dotenv(root / '.env')
    parser = argparse.ArgumentParser(description='Inspect delivery status or retry ONLY a failed company receipt email.')
    parser.add_argument('command', choices=['status', 'retry-email'])
    parser.add_argument('reference')
    args = parser.parse_args()
    # CLI inspection must not recover/reset actions owned by the running service.
    database = root / 'runtime/deliveries/delivery.sqlite3'
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute('SELECT reference,state,email_state,job,error FROM documents WHERE reference=?', (args.reference,)).fetchone()
        if not row:
            raise SystemExit('Reference not found.')
        if args.command == 'retry-email':
            if row['state'] != 'completed' or row['email_state'] not in {'failed', 'uncertain'}:
                raise SystemExit('Only failed/uncertain email for a completed job can be retried.')
            answer = input('Check Gmail Sent mail first. Retry may duplicate an email. Type RETRY to continue: ')
            if answer == 'RETRY':
                connection.execute("UPDATE documents SET email_state='pending',error='' WHERE reference=?", (args.reference,))
                print('Email-only retry queued. Keep the kiosk backend running. No new print or charge.')
        else:
            print(json.dumps(dict(row), indent=2))
