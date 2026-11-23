# ============================================================================
# File:         test_form_fixes_CG.py
# Written by:   CG (Cristian Gonzalez)
# What it does: Tests the server side of the Nov 19 fixes from the sponsor
#               meeting notes: the VIN read from insurance cards, the scan
#               confidence sent to the form, the registration county on the
#               Monday record, and the shorter transaction ID on the receipt.
# Team-written: All logic written by CG (Cristian Gonzalez).
# Third-party:  pytest; ReportLab (measures text width); pypdf (reads the PDF).
# ============================================================================
import io
import json
from unittest.mock import Mock, patch

import numpy as np
import pytest
from pypdf import PdfReader
from reportlab.pdfbase.pdfmetrics import stringWidth

import monday_service_CG as ms
import scanner_api_BV_CG as api
from extraction_service_BV_SL import find_vin, scan_insurance, vin_check_digit_ok
from receipt_documents_BV import build_documents
from printing_BV import document_data

VIN = '1HGCM82633A004352'        # a real-format VIN whose check digit is right
OTHER_VIN = '1M8GDM9AXKP042788'  # a second one with a right check digit
NO_CHECK = '5NPD84LF5HH123456'   # VIN-shaped, but its check digit doesn't agree


# ---- the VIN from an insurance card ----

def test_check_digit():
    assert vin_check_digit_ok(VIN) and vin_check_digit_ok(OTHER_VIN)
    assert not vin_check_digit_ok(NO_CHECK)
    assert not vin_check_digit_ok(VIN[:-1])


@pytest.mark.parametrize('text', [
    f'VIN {VIN}',
    f'VIN:{VIN}',                          # glued to its label
    f'VIN# 1HGCM 82633A004352',            # OCR split it in two
    f'VIN 1HG CM826 33A004352',            # ... or in three
    'VIN 1HGCM82633AOO4352',               # O read for 0 (a VIN never has an O)
    'VIN 1HGCM82633A0O4352',
    'VIN IHGCM82633A004352',               # I read for 1
])
def test_vins_the_old_exact_match_missed_are_found(text):
    assert find_vin(text) == (VIN, 0.95, False)


def test_a_vin_whose_check_digit_disagrees_is_still_offered_but_marked_unsure():
    assert find_vin(f'VIN {NO_CHECK}') == (NO_CHECK, 0.7, False)


def test_two_real_vins_mean_two_vehicles_and_none_is_picked():
    assert find_vin(f'VEH 1 {VIN}\nVEH 2 {OTHER_VIN}') == ('', 0.0, True)


def test_a_real_vin_wins_over_a_17_character_policy_number():
    assert find_vin(f'POLICY ABCDE12345FGHJK67\nVIN {VIN}')[0] == VIN


def test_plain_words_are_never_glued_into_a_vin():
    assert find_vin('POLICY NUMBER 12345 EFFECTIVE DATES')[0] == ''
    assert find_vin('nothing to see here') == ('', 0.0, False)


def scan(text):
    with patch('extraction_service_BV_SL.configure_tesseract'), \
         patch('extraction_service_BV_SL.pytesseract.image_to_osd', return_value={'rotate': 0, 'orientation_conf': 10}), \
         patch('extraction_service_BV_SL.pytesseract.image_to_string', return_value=text), \
         patch('extraction_service_BV_SL.InsuranceCardPreprocessor.get_candidates', return_value=[]), \
         patch('extraction_service_BV_SL.InsuranceCardPreprocessor.preprocess', return_value=[('gray', np.ones((32, 32), np.uint8))]):
        return scan_insurance(np.ones((32, 32, 3), np.uint8))


def test_insurance_scan_fills_a_spaced_out_vin_and_sends_confidence(monkeypatch, tmp_path):
    monkeypatch.setenv('KIOSK_INSURANCE_SAVE_DEBUG', '0')
    result = scan('PROGRESSIVE\nPolicy Number: ABC123456\nExpiration Date: 12/31/2027\n2008 HONDA ACCORD\nVIN 1HGCM 82633A004352')
    assert result['fields']['vehicle_vin'] == VIN
    assert result['confidence']['vehicle_vin'] == 0.95
    assert all(0 <= value <= 1 for value in result['confidence'].values())
    assert set(result['confidence']) <= {key for key, value in result['fields'].items() if value}  # only fields it filled


def test_insurance_scan_with_two_vehicles_leaves_the_vehicle_blank(monkeypatch):
    monkeypatch.setenv('KIOSK_INSURANCE_SAVE_DEBUG', '0')
    result = scan(f'Policy Number: ABC123456\nExpiration Date: 12/31/2027\n2008 HONDA ACCORD {VIN}\n2019 MACK TRUCK {OTHER_VIN}')
    assert result['fields']['vehicle_vin'] == '' and result['fields']['vehicle_make'] == ''
    assert 'vehicle_vin' not in result['confidence']
    assert any('Multiple vehicles' in note for note in result['notes'])


# ---- county on the Monday record ----

@pytest.fixture
def monday(monkeypatch):
    monkeypatch.setenv('MONDAY_API_TOKEN', 'test-token')
    monkeypatch.setenv('MONDAY_BOARD_ID', '111')
    monkeypatch.delenv('MONDAY_GROUP_ID', raising=False)
    monkeypatch.delenv('MONDAY_COUNTY_COLUMN', raising=False)
    monkeypatch.delenv('MONDAY_COUNTY_COLUMN_TYPE', raising=False)
    sent = []

    def post(url, headers=None, json=None, timeout=None):
        sent.append(json)
        response = Mock(status_code=200)
        response.json.return_value = dict(data=dict(create_item=dict(id='987654321')))
        return response

    monkeypatch.setattr(ms.requests, 'post', post)
    return sent


def columns(sent):
    return json.loads(sent[-1]['variables']['columnValues'])


def test_county_is_skipped_until_its_column_is_set(monday):
    ms.create_monday_record('Doe, Jane', dict(ms.SAMPLE_FIELDS))
    assert 'Nueces' not in json.dumps(columns(monday))


def test_county_goes_to_the_column_set_in_env(monday, monkeypatch):
    monkeypatch.setenv('MONDAY_COUNTY_COLUMN', 'text_county1')
    ms.create_monday_record('Doe, Jane', dict(ms.SAMPLE_FIELDS))
    assert columns(monday)['text_county1'] == 'Nueces'


def test_county_column_can_be_a_dropdown_and_a_bad_type_falls_back_to_text(monday, monkeypatch):
    monkeypatch.setenv('MONDAY_COUNTY_COLUMN', 'dropdown_county')
    monkeypatch.setenv('MONDAY_COUNTY_COLUMN_TYPE', 'Dropdown')
    ms.create_monday_record('Doe, Jane', dict(ms.SAMPLE_FIELDS))
    assert columns(monday)['dropdown_county'] == {'labels': ['Nueces']}
    monkeypatch.setenv('MONDAY_COUNTY_COLUMN_TYPE', 'checkbox')
    ms.create_monday_record('Doe, Jane', dict(ms.SAMPLE_FIELDS))
    assert columns(monday)['dropdown_county'] == 'Nueces'


def test_the_county_setting_never_changes_column_map_itself(monkeypatch):
    monkeypatch.setenv('MONDAY_COUNTY_COLUMN', 'text_county1')
    ms._column_map()
    assert 'county' not in ms.COLUMN_MAP


# ---- the transaction ID on the receipt ----

SHORT_ID = 'RN1-20261119-075512-A3F9C2'
LONG_ID = '20261119T075512123Z-RN-Kiosk-1-' + 'a3f9' * 8


def receipt_text(reference):
    data = dict(registrant='Jane Sample', license_plate='ABC1234', vehicle_year='2019', vehicle_make='Honda',
                vehicle_model='Accord', vin=VIN, confirmation_number=reference,
                form_details=json.dumps({'personal': {'address': '100 Main St', 'city': 'New Braunfels', 'state': 'Texas',
                                                      'zip': '78130', 'phone': '830-555-0123', 'email': 'jane@example.com'}}),
                initials='JS')
    pdf = build_documents(document_data(data), payment={'amount': 11425, 'id': 'pi_x', 'testMode': True})
    raw = pdf if isinstance(pdf, bytes) else pdf.getvalue()
    return PdfReader(io.BytesIO(raw)).pages[1].extract_text()


def test_new_id_fits_the_receipt_box_on_one_line():
    # the Transaction ID box is 163 points wide and starts at 8 point text (receipt_documents_BV.py)
    assert stringWidth(SHORT_ID, 'Helvetica', 8) < 163
    assert stringWidth(LONG_ID, 'Helvetica', 8) > 163  # the old one didn't, which is why it changed


def test_receipt_reads_the_time_and_kiosk_back_out_of_the_new_id():
    text = receipt_text(SHORT_ID)
    assert SHORT_ID in text
    assert '11/19/2026 07:55:12 UTC' in text and 'RN-Kiosk-1' in text


def test_a_reprint_of_an_older_renewal_still_shows_its_time():
    assert '11/19/2026 07:55:12 UTC' in receipt_text(LONG_ID)


def test_an_id_that_only_looks_new_gets_no_time_line():
    assert 'UTC' not in receipt_text('RN1-20261399-075512-A3F9C2')  # month 13: no made-up time


def test_print_and_payment_accept_the_new_id():
    assert len(SHORT_ID) == 26
    ok = dict(registrant='Jane', license_plate='ABC1234', vehicle_year='2019', vehicle_make='Honda', vehicle_model='Accord',
              vin=VIN, confirmation_number=SHORT_ID)
    assert document_data(ok)['confirmation_number'] == SHORT_ID
    from crm_photos_CG import clean_reference
    assert clean_reference(SHORT_ID) == SHORT_ID  # photos and the office log key on it too


# ---- the routes the restored form calls are still there ----

def test_routes_the_form_calls_exist():
    rules = {rule.rule for rule in api.app.url_map.iter_rules()}
    for route in ('/api/camera/license-front', '/api/security/front-capture', '/api/auth/logout', '/api/auth/session'):
        assert route in rules, route


# ---- [CG] Nov 23: the fee amounts always print on the receipt ----

def receipt_page(payment):
    data = dict(registrant='Jane Sample', license_plate='ABC1234', vehicle_year='2019', vehicle_make='Honda',
                vehicle_model='Accord', vin=VIN, confirmation_number=SHORT_ID)
    pdf = build_documents(document_data(data), payment=payment)
    raw = pdf if isinstance(pdf, bytes) else pdf.getvalue()
    return PdfReader(io.BytesIO(raw)).pages[1].extract_text()


def amounts(text):
    import re
    return re.findall(r'(?<![\d.])\d{1,3}\.\d\d(?![\d.])', text)  # money only, not the 210.696.5600 phone number


def test_paid_receipt_lists_each_charge_and_a_zero_balance():
    found = amounts(receipt_page({'amount': 11425, 'id': 'pi_x', 'testMode': True}))
    assert found[:3] == ['87.25', '21.64', '5.36']
    assert found.count('114.25') == 2          # balance due and the card payment
    assert found[-1] == '0.00'                 # outstanding balance
    assert '—' not in receipt_page({'amount': 11425, 'id': 'pi_x', 'testMode': True})


def test_a_receipt_without_a_payment_still_lists_the_charges():
    text = receipt_page(None)
    found = amounts(text)
    assert found[:3] == ['87.25', '21.64', '5.36'] and '—' not in text
    assert found[-1] == '114.25', 'nothing paid, so the whole amount is outstanding'


def test_the_charges_add_up_to_what_the_kiosk_charges():
    from receipt_documents_BV import FEES
    from payment_service_CG import AMOUNT_CENTS
    assert sum(FEES) == AMOUNT_CENTS
