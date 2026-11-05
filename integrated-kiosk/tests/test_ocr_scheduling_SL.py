# ============================================================================
# File:         test_ocr_scheduling_SL.py
# Written by:   SL (Seth Leija)
# What it does: Tests for how OCR passes are scheduled.
# Team-written: Logic written by SL (Seth Leija). Outside code is used only
#               through the third-party sources listed below.
# Third-party:  NumPy
# ============================================================================
from unittest.mock import patch
import time
import numpy as np
from extraction_service_BV_SL import insurance_passes, scan_insurance
from kiosk_scanners_SL.insurance_ocr_SL import InsuranceCardOCRConfig, FIELD_KEYS


def test_quick_pass_visits_each_crop_before_slow_variants():
    class Preprocessor:
        def preprocess(self, image):
            return [('gray', image), ('contrast', image)]
    image = np.ones((32, 32), np.uint8)
    passes = list(insurance_passes(Preprocessor(), [('card', image), ('page', image)], InsuranceCardOCRConfig(psm_modes=(6, 11)), time.monotonic()+10))
    assert [(label, psm) for label, _, psm in passes[:2]] == [('card', 6), ('page', 6)]
    assert len(passes) == 8
    assert list(insurance_passes(Preprocessor(), [('card', image)], InsuranceCardOCRConfig(), time.monotonic()-1)) == []


def test_low_confidence_complete_result_does_not_end_scan(monkeypatch):
    monkeypatch.setenv('KIOSK_INSURANCE_SAVE_DEBUG', '0')
    image = np.ones((32, 32, 3), np.uint8)
    low = dict.fromkeys(FIELD_KEYS, 'value')
    low.update(policy_number='WRONG123', insurance_expiration='12/31/2027', field_confidence=dict.fromkeys(FIELD_KEYS, .6))
    high = dict(low, policy_number='RIGHT456', field_confidence=dict.fromkeys(FIELD_KEYS, .95))
    with patch('extraction_service_BV_SL.configure_tesseract'), patch('extraction_service_BV_SL.pytesseract.image_to_osd', return_value={}), \
         patch('extraction_service_BV_SL.InsuranceCardPreprocessor.get_candidates', return_value=[('card', image)]), \
         patch('extraction_service_BV_SL.InsuranceCardPreprocessor.preprocess', return_value=[('gray', image)]), \
         patch('extraction_service_BV_SL.pytesseract.image_to_string', return_value='text') as ocr, \
         patch('extraction_service_BV_SL.InsuranceFieldExtractor.extract', side_effect=[low, high]):
        result = scan_insurance(image)
    assert ocr.call_count == 2
    assert result['fields']['policy_number'] == 'RIGHT456'
