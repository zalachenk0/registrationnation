# ============================================================================
# File:         test_initials_and_focus_CG.py
# Written by:   CG (Cristian Gonzalez)
# What it does: Tests the Nov 23 rollbacks: typed initials print on the receipt
#               again (the finger signature was rejected on the Pi), and the
#               license scan is back to the Nov 19 focus behavior (no forced
#               refocus, no full lens sweep).
# Team-written: Logic written by CG (Cristian Gonzalez). Outside code is used
#               only through the third-party sources listed below.
# Third-party:  pytest; pypdf; NumPy; Flask's test client.
# ============================================================================
import io

import numpy as np
import pytest
from pypdf import PdfReader

import camera_service_BV_SL as camera_service
from printing_BV import document_data
from receipt_documents_BV import build_documents

FORM = {'registrant': 'Jane Sample', 'license_plate': 'ABC123', 'vehicle_year': '2019', 'vehicle_make': 'Honda',
        'vehicle_model': 'Accord', 'vin': '1HGCM82633A004352', 'confirmation_number': 'RN1-20261123-213000-A1B2C3'}


# ---- typed initials ----

def test_typed_initials_without_a_signature_print_on_the_receipt():
    data = document_data({**FORM, 'initials': 'JQS'})
    assert data['initials'] == 'JQS' and 'signature' not in data
    pdf = build_documents(data, payment={'id': 'pi_test', 'amount': 11425, 'testMode': True})
    text = ''.join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)
    assert 'JQS' in text


@pytest.mark.parametrize('bad', ['', 'jqs', 'J Q', 'J1', 'ABCDEFGHI'])
def test_initials_the_screen_could_never_send_are_refused(bad):
    with pytest.raises(ValueError, match='initials'):
        document_data({**FORM, 'initials': bad})


# ---- camera focus, back to Nov 19 ----

def test_the_camera_has_no_forced_or_full_sweep_anymore():
    assert not hasattr(camera_service.Camera, 'force_focus')
    assert '_full_sweep' not in vars(camera_service.Camera())


def test_the_license_scan_takes_one_shot_and_leaves_focus_to_the_preview(monkeypatch):
    import scanner_api_BV_CG as api
    shots = []
    monkeypatch.setattr(api.camera, 'get', lambda preview=False, timeout=10: shots.append(preview) or np.zeros((60, 90, 3), np.uint8))
    monkeypatch.setattr(api.photos, 'license_scanned', lambda fields: None)
    monkeypatch.setattr(api.photos, 'save_license_back', lambda image: None)

    def unreadable(image):
        raise ValueError('Barcode not readable.')
    monkeypatch.setattr(api, 'scan_license', unreadable)
    reply = api.app.test_client().post('/api/camera/scan/license')
    assert reply.status_code == 422 and 'Barcode not readable' in reply.json['error']
    assert shots == [False], 'one capture, no retry with a second sweep'
