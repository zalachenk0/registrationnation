# ============================================================================
# File:         test_signature_SL.py
# Written by:   SL
# What it does: Checks the finger signature from the review step is validated
#               and drawn onto the receipt in place of typed initials.
# Team-written: All logic written by SL.
# Third-party:  pytest; pypdf.
# ============================================================================
import base64
import io
import struct
import zlib

import pytest
from pypdf import PdfReader

from printing_BV import document_data
from receipt_documents_BV import build_documents

VIN = '1HGCV1F30KA000001'


def png(width=60, height=20):
    """A small black-on-white PNG, made with the standard library only."""
    def chunk(kind, body):
        return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body) & 0xffffffff)
    rows = b''.join(b'\x00' + bytes(0 if 8 < y < 12 else 255 for _ in range(width)) for y in range(height))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def form(**extra):
    return dict(registrant='Jane Sample', license_plate='ABC1234', vehicle_year='2019', vehicle_make='Honda',
                vehicle_model='Accord', vin=VIN, confirmation_number='RN1-20261119-075512-A3F9C2', **extra)


def data_url(raw):
    return 'data:image/png;base64,' + base64.b64encode(raw).decode()


def test_a_drawn_signature_is_kept_and_printed_on_the_receipt():
    data = document_data(form(signature=data_url(png())))
    assert base64.b64decode(data['signature']).startswith(b'\x89PNG')
    pdf = build_documents(data, payment={'amount': 11425, 'id': 'pi_x', 'testMode': True})
    receipt = PdfReader(io.BytesIO(pdf)).pages[1]
    assert receipt.images, 'the signature image should be on the receipt page'


@pytest.mark.parametrize('bad', [
    '',
    'not a data url',
    'data:image/jpeg;base64,' + base64.b64encode(png()).decode(),
    'data:image/png;base64,' + base64.b64encode(b'GIF89a not a png').decode(),
    'data:image/png;base64,***',
    'data:image/png;base64,' + 'A' * 800_000,
    12345,
])
def test_a_bad_signature_is_refused(bad):
    with pytest.raises(ValueError, match='signature'):
        document_data(form(signature=bad))


def test_no_signature_still_builds_for_previews():
    assert 'signature' not in document_data(form())
