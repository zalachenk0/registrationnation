"""PDF mock sticker + receipt, with idempotent submission to local CUPS."""
from __future__ import annotations
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


class PrintError(RuntimeError):
    pass


def clean(value):
    return ' '.join(str(value or '').split())[:100]


def validate(data):
    if not isinstance(data, dict):
        raise ValueError('Print data must be an object.')
    required = ('registrant', 'license_plate', 'vehicle_year', 'vehicle_make', 'vehicle_model', 'vin', 'confirmation_number')
    result = {key: clean(data.get(key)) for key in required}
    missing = [key for key in required if not result[key]]
    if missing:
        raise ValueError('Complete these fields before printing: ' + ', '.join(missing))
    if not re.fullmatch(r'[A-HJ-NPR-Z0-9]{17}', result['vin'].upper()):
        raise ValueError('Enter a complete 17-character VIN before printing.')
    if not re.fullmatch(r'[A-Za-z0-9-]{8,64}', result['confirmation_number']):
        raise ValueError('Invalid confirmation number.')
    return result


def create_pdf(data, payment=None):
    data = validate(data)
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setTitle('Demonstration registration sticker and receipt')
    pdf.setAuthor('Registration Nation kiosk prototype')
    navy, orange = HexColor('#1a3a5c'), HexColor('#e96225')
    def text(x, y, value, size=11, bold=False, width=480):
        font = 'Helvetica-Bold' if bold else 'Helvetica'
        while pdf.stringWidth(value, font, size) > width and size > 7:
            size -= .5
        pdf.setFont(font, size)
        pdf.drawString(x, y, value)
    today = datetime.now(ZoneInfo(os.getenv('KIOSK_TIMEZONE', 'America/Chicago'))).date()
    expiry = today + timedelta(days=365)
    pdf.setFillColor(navy)
    text(42, 747, 'REGISTRATION NATION', 20, True)
    text(42, 724, 'Vehicle renewal demonstration | Sticker and receipt', 11)
    pdf.setStrokeColor(orange)
    pdf.setLineWidth(3)
    pdf.line(42, 708, 570, 708)
    # Sticker section: deliberately marked mockup; not an official document.
    pdf.setDash(4, 3)
    pdf.setLineWidth(1)
    pdf.setStrokeColor(navy)
    pdf.roundRect(42, 460, 300, 222, 10)
    pdf.setDash()
    text(58, 657, 'MOCK RENEWAL STICKER', 15, True, 268)
    text(58, 637, 'DEMONSTRATION ONLY - NOT VALID', 10, True, 268)
    text(58, 596, data['license_plate'].upper(), 31, True, 268)
    text(58, 568, 'Example expiration: ' + expiry.strftime('%m/%d/%Y'), 12, True, 268)
    text(58, 543, ' '.join(data[k] for k in ('vehicle_year', 'vehicle_make', 'vehicle_model')), 11, width=268)
    text(58, 521, 'VIN ending: ' + data['vin'][-6:].upper(), 11, width=268)
    text(58, 490, 'No registration has been issued.', 10, width=268)
    text(363, 620, 'CUT-OUT SAMPLE', 12, True, 195)
    text(363, 598, 'Print at 100% on letter paper.', 10, width=195)
    text(363, 580, 'Sticker area: 4.17 x 3.08 inches.', 10, width=195)
    text(363, 557, 'Adhesive stock is optional.', 10, width=195)
    text(42, 413, 'STRIPE TEST PAYMENT RECEIPT' if payment else 'DEMONSTRATION RECEIPT', 16, True)
    pdf.setStrokeColor(HexColor('#d6dde4'))
    pdf.line(42, 400, 570, 400)
    rows = [('Customer', data['registrant']), ('Vehicle', ' '.join(data[k] for k in ('vehicle_year', 'vehicle_make', 'vehicle_model'))),
            ('License plate', data['license_plate']), ('VIN', data['vin'].upper()),
            ('Date', today.isoformat()), ('Reference', data['confirmation_number']),
            ('Example renewal fee', '$114.25'), ('Amount charged', '$114.25 TEST - NO REAL MONEY' if payment else '$0.00 - DEMO / NO PAYMENT PROCESSED')]
    for i, (label, value) in enumerate(rows):
        y = 376 - i * 27
        text(42, y, label, 10)
        text(190, y, value, 11, True, 380)
    if payment:
        text(42, 140, 'Stripe test payment: ' + payment['id'], 9)
    text(42, 115, 'This is a project mockup. It is not proof of registration or a real payment.', 10)
    text(42, 96, 'Verify information on screen before printing. No receipt email is sent.', 10)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def printer_status():
    lp = shutil.which('lp')
    return {'tools_installed': bool(lp), 'printer': os.getenv('KIOSK_PRINTER_NAME') or 'CUPS default',
            'message': 'Configure a CUPS queue and test with lpstat -p -d.' if lp else 'Install cups and cups-client on the Pi.'}


class PrintService:
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs = {}

    def submit(self, data, payment=None):
        data = validate(data)
        key = data['confirmation_number']
        fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        with self.lock:
            now = time.monotonic()
            self.jobs = {k: v for k, v in self.jobs.items() if now - v['time'] < 3600}
            previous = self.jobs.get(key)
            if previous:
                if previous['fingerprint'] != fingerprint:
                    raise ValueError('This reference was already used with different form data. Start a new customer.')
                if previous.get('unknown'):
                    raise PrintError('The previous print request timed out. Check the CUPS queue before starting a new customer; it may have been accepted.')
                return {'job': previous['job'], 'duplicate': True}
            lp = shutil.which('lp')
            if not lp:
                raise PrintError('Printing tools are missing. Install cups and cups-client and configure your printer.')
            cmd = [lp, '-t', 'Kiosk demonstration sticker and receipt', '-o', 'media=Letter', '-o', 'sides=one-sided']
            printer = os.getenv('KIOSK_PRINTER_NAME')
            if printer:
                cmd += ['-d', printer]
            try:
                completed = subprocess.run(cmd, input=create_pdf(data, payment=payment), capture_output=True, timeout=20, check=False,
                                           env=dict(os.environ, LC_ALL='C'))
            except subprocess.TimeoutExpired as exc:
                self.jobs[key] = {'fingerprint': fingerprint, 'time': now, 'unknown': True}
                raise PrintError('Printer submission timed out. Check the CUPS queue before retrying; the job may already be queued.') from exc
            except OSError as exc:
                raise PrintError('Could not contact CUPS. Check the printer setup.') from exc
            if completed.returncode:
                raise PrintError('CUPS rejected the job. Check lpstat -p -d, paper, and the selected queue.')
            job = completed.stdout.decode(errors='replace').strip()[:200] or 'Accepted by CUPS'
            self.jobs[key] = {'fingerprint': fingerprint, 'time': now, 'job': job}
            return {'job': job, 'duplicate': False}
