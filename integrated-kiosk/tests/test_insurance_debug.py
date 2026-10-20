import json
from unittest.mock import patch

import cv2
import numpy as np
import pytest

import extraction_service as extraction


@pytest.fixture
def scan_setup(tmp_path, monkeypatch):
    root = tmp_path / 'insurance_debug'
    monkeypatch.setattr(extraction, 'INSURANCE_DEBUG_ROOT', root)
    monkeypatch.delenv('KIOSK_INSURANCE_SAVE_DEBUG', raising=False)
    original = np.arange(32 * 48 * 3, dtype=np.uint8).reshape(32, 48, 3)
    processed = np.full((48, 32), 190, dtype=np.uint8)
    with patch.object(extraction, 'configure_tesseract'), \
         patch.object(extraction.pytesseract, 'image_to_osd', return_value={'rotate': 90, 'orientation_conf': 10}), \
         patch.object(extraction.pytesseract, 'image_to_string', return_value='USAA') as ocr, \
         patch.object(extraction.InsuranceCardPreprocessor, 'get_candidates', return_value=[]), \
         patch.object(extraction.InsuranceCardPreprocessor, 'preprocess', return_value=[('gray', processed)]):
        yield root, original, processed, ocr


def test_default_saves_exact_original_and_best_ocr_in_separate_scan_folders(scan_setup):
    root, original, processed, _ = scan_setup
    result = extraction.scan_insurance(original)
    extraction.scan_insurance(original)
    folders = list(root.iterdir())
    assert len(folders) == 2
    for folder in folders:
        np.testing.assert_array_equal(cv2.imread(str(folder / '00_original.png')), original)
        np.testing.assert_array_equal(cv2.imread(str(folder / '01_best_ocr.png'), cv2.IMREAD_GRAYSCALE), processed)
        assert (folder / 'best_raw_text.txt').read_text(encoding='utf-8') == 'USAA'
        assert json.loads((folder / 'parsed_fields.json').read_text(encoding='utf-8')) == result['fields']


def test_unreadable_scan_still_saves_original(scan_setup):
    root, original, _, ocr = scan_setup
    ocr.return_value = ''
    with pytest.raises(ValueError, match='No insurance fields'):
        extraction.scan_insurance(original)
    folder, = root.iterdir()
    np.testing.assert_array_equal(cv2.imread(str(folder / '00_original.png')), original)


def test_saving_can_be_disabled(scan_setup, monkeypatch):
    root, original, _, _ = scan_setup
    monkeypatch.setenv('KIOSK_INSURANCE_SAVE_DEBUG', '0')
    assert extraction.scan_insurance(original)['success']
    assert not root.exists()


@pytest.mark.parametrize('failure', ['directory', 'image', 'text'])
def test_disk_failure_does_not_break_extraction(scan_setup, failure):
    root, original, _, _ = scan_setup
    if failure == 'directory':
        root.write_text('A file prevents directory creation', encoding='utf-8')
        result = extraction.scan_insurance(original)
    elif failure == 'image':
        with patch.object(extraction.cv2, 'imwrite', return_value=False):
            result = extraction.scan_insurance(original)
    else:
        with patch('pathlib.Path.write_text', side_effect=OSError('Disk full')):
            result = extraction.scan_insurance(original)
    assert result['success']
    assert any('Debug files could not be saved' in note for note in result['notes'])
