import io
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pytest
from pypdf import PdfReader

import scanner_api as api
from camera_service import Camera, Settings, CameraError
from extraction_service import parse_license, normalized_date, scan_insurance
from printing import PrintService, PrintError, create_pdf

RAW = '@\n\x1e\rANSI 636015080002DL00410288ZA03290015DLDAQTEST12345\nDCSSAMPLE\nDACJANE\nDBB04121988\nDBA08152029\nDAG123 EXAMPLE ST\nDAIAUSTIN\nDAJTX\nDAK787010000\nDCAC\n'
PRINT = dict(registrant='Jane Sample', license_plate='DEMO-123', vehicle_year='2021', vehicle_make='Example',
             vehicle_model='Sedan', vin='1HGCM82633A004352', confirmation_number='DEMO-1234567890')


def image_file():
    ok, image = cv2.imencode('.png', np.full((320, 640, 3), 255, np.uint8))
    assert ok
    return io.BytesIO(image.tobytes()), 'synthetic.png'


def test_aamva_directory_first_field_and_dates():
    fields = parse_license(RAW)
    assert fields['license_number'] == 'TEST12345'
    assert fields['customer_first_name'] == 'Jane'
    assert fields['zip_code'] == '78701'
    assert fields['date_of_birth'] == '1988-04-12'
    assert fields['license_expiration'] == '2029-08-15'
    assert normalized_date('20290230') == ''
    with pytest.raises(ValueError):
        parse_license('Not a driver license')


def test_upload_adapter_and_partial_fields():
    with patch('extraction_service.dl.decode_pdf417', return_value=RAW):
        response = api.app.test_client().post('/api/scan/license', data={'image': image_file()})
    assert response.status_code == 200
    assert response.json['fields']['license_class'] == 'C'
    assert response.headers['Cache-Control'].startswith('no-store')
    assert 'raw_text' not in response.json


def test_real_pdf417_round_trip():
    # Generated from RAW using zxing-cpp; includes only synthetic identity data.
    gray = cv2.imread(str(Path(__file__).parent / 'fixtures/synthetic-license.png'), cv2.IMREAD_GRAYSCALE)
    image = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR) if gray.ndim == 2 else gray
    from extraction_service import scan_license
    assert scan_license(image)['fields']['license_number'] == 'TEST12345'


def test_api_invalid_inputs_and_cross_site():
    client = api.app.test_client()
    assert client.post('/api/scan/unknown').status_code == 404
    assert client.post('/api/scan/license').status_code == 422
    assert client.post('/api/scan/license', data={'image': (io.BytesIO(b'not image'), 'x.png')}).status_code == 422
    assert client.get('/api/camera/frame.jpg', headers={'Origin': 'https://unrelated.example'}).status_code == 403
    assert client.get('/api/health', headers={'Host': 'unrelated.example'}).status_code == 400
    assert client.get('/scanner_api.py').status_code == 404
    assert client.post('/api/print/registration', json={}).status_code == 422


def test_scan_lock_and_camera_failure():
    client = api.app.test_client()
    api.scan_lock.acquire()
    try:
        assert client.post('/api/camera/scan/license').status_code == 409
    finally:
        api.scan_lock.release()
    with patch.object(api.camera, 'get', side_effect=CameraError('No camera')):
        assert client.get('/api/camera/frame.jpg').status_code == 503
        assert client.post('/api/camera/scan/license').status_code == 503
    assert not api.scan_lock.locked()


def test_single_camera_owner_preview_capture_and_idle_release():
    camera = Camera(Settings(warmup=0, idle_seconds=.2, fps=30))
    closes, opens = [], []
    def open_fake():
        opens.append(1)
        def read():
            time.sleep(.01)
            return np.full((48, 64, 3), 100, np.uint8)
        return object(), read, lambda: closes.append(1)
    with patch.object(camera, '_open', side_effect=open_fake):
        preview = camera.get(preview=True)
        still = camera.get()
        assert preview[:2] == b'\xff\xd8'
        assert still.shape == (48, 64, 3)
        assert len(opens) == 1
        thread = camera.thread
        thread.join(timeout=2)
        assert camera.frame is None and closes == [1]
        camera.get(preview=True)
        assert len(opens) == 2
        camera.close()


def test_insurance_uses_partner_extractor_and_keeps_partial_fields(tmp_path, monkeypatch):
    monkeypatch.setattr('extraction_service.INSURANCE_DEBUG_ROOT', tmp_path / 'debug')
    text = 'USAA\nPolicy Number: ABC123456\nExpiration Date: 12/31/2027\n2021 TOYOTA CAMRY\nVIN 1HGCM82633A004352'
    with patch('extraction_service.configure_tesseract'), patch('extraction_service.pytesseract.image_to_osd', return_value={'rotate': 0, 'orientation_conf': 10}), patch('extraction_service.pytesseract.image_to_string', return_value=text), patch('extraction_service.InsuranceCardPreprocessor.get_candidates', return_value=[]), patch('extraction_service.InsuranceCardPreprocessor.preprocess', return_value=[('gray', np.ones((32,32), np.uint8))]):
        result = scan_insurance(np.ones((32,32,3), np.uint8))
    assert result['success']
    assert result['fields']['vehicle_vin'] == '1HGCM82633A004352'
    assert result['fields']['insurance_company']
    assert result['fields']['policy_number']


def test_pdf_and_print_idempotency():
    data = create_pdf(PRINT)
    reader = PdfReader(io.BytesIO(data))
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text()
    assert 'MOCK RENEWAL STICKER' in text and 'DEMONSTRATION RECEIPT' in text
    assert '$0.00' in text and 'DEMO-123' in text
    service = PrintService()
    accepted = subprocess.CompletedProcess(['lp'], 0, b'request id is Demo-1', b'')
    with patch('printing.shutil.which', return_value='/usr/bin/lp'), patch('printing.subprocess.run', return_value=accepted) as run:
        assert not service.submit(PRINT)['duplicate']
        assert service.submit(PRINT)['duplicate']
        assert run.call_count == 1
        assert run.call_args.kwargs['input'].startswith(b'%PDF')
        with pytest.raises(ValueError):
            service.submit(dict(PRINT, license_plate='CHANGED'))


def test_printer_failure_retry_and_unknown_outcome():
    service = PrintService()
    with patch('printing.shutil.which', return_value='/usr/bin/lp'), patch('printing.subprocess.run', side_effect=subprocess.TimeoutExpired('lp', 20)) as run:
        with pytest.raises(PrintError):
            service.submit(PRINT)
        with pytest.raises(PrintError):
            service.submit(PRINT)
        assert run.call_count == 1
    service = PrintService()
    with patch('printing.shutil.which', return_value=None):
        with pytest.raises(PrintError):
            service.submit(PRINT)
    assert not service.jobs


def test_pdf_preview_endpoint_and_real_print_error():
    client = api.app.test_client()
    result = client.post('/api/print/preview', json=PRINT)
    assert result.status_code == 200 and result.data.startswith(b'%PDF')
    assert result.headers['Content-Type'].startswith('application/pdf')
    with patch('printing.shutil.which', return_value=None), patch.object(api.payments, 'verify_paid', return_value={'id': 'pi_fixture', 'amount': 11425, 'testMode': True}):
        result = client.post('/api/print/registration', json=dict(PRINT, confirmation_number='DEMO-MISSING-PRINTER'))
    assert result.status_code == 503
    assert 'missing' in result.json['error']
