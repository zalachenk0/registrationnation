"""Exercise the supplied parser fixes through the existing Pi scan API."""
import io
import json
from unittest.mock import patch

import cv2
import numpy as np
import pytest

import scanner_api as api
from kiosk_scanners.insurance_ocr import InsuranceFieldExtractor


@pytest.mark.parametrize('period,expected', [
    ('Policy Period: October 20, 2026 - Apr 20, 2027', '2027-04-20'),
    ('Coverage Period: March 5 2026 - Sept. 5, 2026', '2026-09-05'),
    ('Policy Term: 10/20/26 - April 20, 2027', '2027-04-20'),
    ('Effective / Expiration: 09/11/2025 09/11/2026', '2026-09-11'),
])
def test_updated_insurance_fields_reach_web_api_and_debug_files(tmp_path, monkeypatch, period, expected):
    monkeypatch.setattr('extraction_service.INSURANCE_DEBUG_ROOT', tmp_path / 'debug')
    monkeypatch.delenv('KIOSK_INSURANCE_SAVE_DEBUG', raising=False)
    text = '\n'.join([
        'PROGRESSIVE', period, 'Policy Number: ABC123456',
        '2017 HYUNDAI ELANTRA 5NPD84LF5HH123456',
    ])
    image = np.full((64, 96, 3), 220, np.uint8)
    ok, png = cv2.imencode('.png', image)
    assert ok
    with patch('extraction_service.configure_tesseract'), \
         patch('extraction_service.pytesseract.image_to_osd', return_value={'rotate': 0, 'orientation_conf': 10}), \
         patch('extraction_service.pytesseract.image_to_string', return_value=text), \
         patch('extraction_service.InsuranceCardPreprocessor.get_candidates', return_value=[]), \
         patch('extraction_service.InsuranceCardPreprocessor.preprocess', return_value=[('gray', image[:, :, 0])]):
        response = api.app.test_client().post('/api/scan/insurance', data={'image': (io.BytesIO(png.tobytes()), 'synthetic.png')})
    assert response.status_code == 200
    fields = response.json['fields']
    assert fields['insurance_company'] == 'PROGRESSIVE'
    assert fields['policy_number'] == 'ABC123456'
    assert fields['insurance_expiration'] == expected
    assert fields['vehicle_year'] == '2017'
    assert fields['vehicle_make'] == 'Hyundai'
    assert fields['vehicle_model'] == 'Elantra'
    assert fields['vehicle_vin'] == '5NPD84LF5HH123456'
    assert fields['vehicle_vin_last6'] == '123456'
    folder, = (tmp_path / 'debug').iterdir()
    np.testing.assert_array_equal(cv2.imread(str(folder / '00_original.png')), image)
    assert json.loads((folder / 'parsed_fields.json').read_text(encoding='utf-8')) == fields


@pytest.mark.parametrize('label', ['Policy Period', 'Policy Term', 'Policy Effective', 'Policy Expiration'])
def test_policy_date_label_is_not_a_policy_number(label):
    fields = InsuranceFieldExtractor().extract(f'{label}: October 20, 2026 - April 20, 2027')
    assert fields['policy_number'] == ''


def test_vehicle_year_comes_from_vin_row_not_nearby_policy_dates():
    fields = InsuranceFieldExtractor().extract(
        'Effective 09/11/2025 Expiration 09/11/2026\n'
        '2017 HYUNDAI ELANTRA 5NPD84LF5HH123456'
    )
    assert fields['vehicle_year'] == '2017'


@pytest.mark.parametrize('text,expected', [
    ('Policy Period: October 20, 2026 - April 20, 2027\n2017 HYUNDAI ELANTRA', '2017'),
    ('Policy Period: October 20, 2026 - April 20, 2027', ''),
    ('Policy Effective 09/11/2025\n2017 HYUNDAI ELANTRA', '2017'),
])
def test_missing_vin_does_not_turn_policy_date_into_vehicle_year(text, expected):
    assert InsuranceFieldExtractor().extract(text)['vehicle_year'] == expected
