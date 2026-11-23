# ============================================================================
# File:         printing_BV.py
# Written by:   BV (Blas Vargas)
# What it does: Builds the print job (sticker and receipt PDF) and sends it to
#               CUPS once.
# Team-written: Logic written by BV (Blas Vargas). Outside code is used only
#               through the third-party sources listed below.
# Third-party:  ReportLab; pypdf; CUPS (lp command).
# ============================================================================
"""PDF mock sticker + receipt, with idempotent submission to local CUPS."""
from __future__ import annotations
import base64
import binascii
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
    from receipt_documents_BV import build_documents
    from pypdf.errors import PdfReadError
    try:
        return build_documents(document_data(data), payment=payment)
    except (OSError, PdfReadError) as exc:
        raise PrintError('The TTT receipt template is missing or unreadable. Reinstall the print update before retrying.') from exc


def document_data(data):
    from form_documents_BV import parse_details
    result = validate(data)
    if data.get('form_details') is not None:
        result['form_details'] = parse_details(data['form_details'])
    initials = data.get('initials')
    if initials is not None:
        if not isinstance(initials, str) or not re.fullmatch(r'[A-Z]{1,8}', initials):
            raise ValueError('Enter valid customer initials.')
        result['initials'] = initials
    signature = data.get('signature')  # [SL] finger signature from the review step, a PNG data URL
    if signature is not None:
        result['signature'] = signature_base64(signature)
    return result


SIGNATURE_PREFIX = 'data:image/png;base64,'
SIGNATURE_MAX_BYTES = 500_000


def signature_base64(signature):
    """[SL] Check the drawn signature is a real, reasonably sized PNG; return its base64 part."""
    if (not isinstance(signature, str) or not signature.startswith(SIGNATURE_PREFIX)
            or len(signature) > len(SIGNATURE_PREFIX) + SIGNATURE_MAX_BYTES * 4 // 3 + 4):
        raise ValueError('The signature is missing or invalid. Sign again before printing.')
    encoded = signature[len(SIGNATURE_PREFIX):]
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError('The signature is missing or invalid. Sign again before printing.') from exc
    if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('The signature is missing or invalid. Sign again before printing.')
    return encoded


def printer_status():
    lp = shutil.which('lp')
    return {'tools_installed': bool(lp), 'printer': os.getenv('KIOSK_PRINTER_NAME') or 'CUPS default',
            'message': 'Configure a CUPS queue and test with lpstat -p -d.' if lp else 'Install cups and cups-client on the Pi.'}


class PrintService:
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs = {}

    def submit(self, data, payment=None, document_bytes=None):
        data = document_data(data)
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
            cmd = [lp, '-t', 'Kiosk demonstration sticker and receipt', '-o', 'media=Letter', '-o', 'sides=one-sided',
                   '-o', 'outputorder=normal', '-o', 'number-up=1', '-o', 'page-set=all', '-n', '1']
            printer = os.getenv('KIOSK_PRINTER_NAME')
            if printer:
                cmd += ['-d', printer]
            try:
                completed = subprocess.run(cmd, input=document_bytes if document_bytes is not None else create_pdf(data, payment=payment), capture_output=True, timeout=20, check=False,
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
