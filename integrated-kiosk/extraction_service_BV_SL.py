# ============================================================================
# File:         extraction_service_BV_SL.py
# Written by:   BV (connects the scanners to the web server); SL (the scanners
#               it calls)
# What it does: Runs the license, insurance and renewal notice scans for the web
#               server and returns the form fields.
# Team-written: Logic written by BV (connects the scanners to the web server);
#               SL (the scanners it calls). Outside code is used only through
#               the third-party sources listed below.
# Third-party:  OpenCV; pytesseract (Tesseract OCR); The kiosk_scanners_SL package.
# ============================================================================
"""Web adapter for the partner's PDF417 decoder, insurance OCR/parser and renewal notice OCR."""
from __future__ import annotations
import os
import re
import time
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

os.environ.setdefault('OMP_THREAD_LIMIT', '1')
import cv2
import pytesseract
import threading

from kiosk_scanners_SL import documents_SL as documents, registration_SL as registration
from kiosk_scanners_SL import drivers_license_SL as dl
from kiosk_scanners_SL.config_SL import ScannerConfig
from kiosk_scanners_SL.registration_ocr_SL import FIELD_KEYS as REGISTRATION_KEYS
from kiosk_scanners_SL.insurance_ocr_SL import InsuranceCardOCRConfig, InsuranceCardPreprocessor, InsuranceFieldExtractor, FIELD_KEYS
from kiosk_scanners_SL.results_SL import configure_tesseract

INSURANCE_DEBUG_ROOT = Path(__file__).resolve().parent / 'runtime' / 'insurance_debug'
INSURANCE_BRANDS = {'USAA', 'GEICO', 'PROGRESSIVE', 'ALLSTATE', 'STATE FARM', 'LIBERTY MUTUAL', 'NATIONWIDE', 'FARMERS', 'TRAVELERS'}


class InsuranceDebug:
    """Keep each scan's original and winning OCR image without blocking OCR on disk errors."""

    def __init__(self, enabled, notes):
        self.directory = None
        self.notes = notes
        if enabled:
            try:
                INSURANCE_DEBUG_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
                directory = INSURANCE_DEBUG_ROOT / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + '-' + uuid4().hex[:8])
                directory.mkdir(mode=0o700)
                self.directory = directory
            except OSError:
                self._failed()

    def _failed(self):
        self.directory = None
        self.notes.append('Debug files could not be saved. Check free disk space and folder permissions.')
        logging.getLogger(__name__).warning('Insurance debug saving failed; check disk space and folder permissions.')

    def image(self, name, image):
        if self.directory is not None:
            try:
                if not cv2.imwrite(str(self.directory / name), image, [cv2.IMWRITE_PNG_COMPRESSION, 1]):
                    raise OSError('Image write failed')
            except (OSError, cv2.error):
                self._failed()

    def text(self, name, text):
        if self.directory is not None:
            try:
                (self.directory / name).write_text(text, encoding='utf-8')
            except OSError:
                self._failed()


def normalized_date(value, formats=('%m%d%Y', '%Y%m%d')):
    for fmt in formats:
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return ''


def parse_license(raw):
    # AAMVA's first element can follow the DL subfile designator on the same
    # line as the ANSI directory. Preserve that element before using the parser.
    raw = re.sub(r'(DL|ID)(?=D[A-Z]{2})', r'\1\n', raw)
    fields = dl.parse_aamva(raw)
    if not (fields.get('DAQ') and (fields.get('DCS') or fields.get('DAA'))):
        raise ValueError('The PDF417 barcode does not contain recognizable driver-license fields. Scan the back of the license.')
    result = dl.extract_form_fields(fields)
    result.update(date_of_birth=normalized_date(fields.get('DBB', '')),
                  license_number=fields.get('DAQ', ''),
                  license_expiration=normalized_date(fields.get('DBA', '')),
                  license_class=fields.get('DCA', ''))
    return result


def response(fields, notes=()):
    missing = [key for key, value in fields.items() if not value]
    return dict(success=any(fields.values()), fields=fields, missing=missing, notes=list(notes),
                recommendation='Review the extracted values. Fill any blank fields manually.' if missing else 'Review all extracted values before continuing.')


def scan_license(image):
    card = dl.crop_card_offline(image)
    raw = dl.decode_pdf417(card)
    if not raw and card is not image:
        raw = dl.decode_pdf417(image)
    if not raw:
        # Tiling is expensive on Pi: the operator can move a small barcode closer.
        raise ValueError('Barcode not readable. Show the back of the license, move closer, wait for focus, and avoid glare.')
    return response(parse_license(raw))


def insurance_gray(image, config):
    """Preserve small full-page text; never shrink then enlarge the capture."""
    h, w = image.shape[:2]
    scale = min(config.upscale_factor, 3000 / float(max(h, w)))
    image = cv2.resize(image, None, fx=scale, fy=scale,
                       interpolation=cv2.INTER_CUBIC)
    h, w = image.shape[:2]
    if max(h, w) > 3000:
        scale = 3000 / float(max(h, w))
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image


def insurance_brand(fields, raw):
    """Recognize unique standalone logos, including logos merged into a date row."""
    if not fields.get('insurance_company'):
        matches = set()
        for line in raw.upper().splitlines():
            for brand in INSURANCE_BRANDS:
                if re.search(r'\b' + re.escape(brand) + r'\b', line):
                    if line.strip() == brand or (
                        re.search(r'\b(?:POLICY|COVERAGE)\s+(?:PERIOD|TERM)\b', line)
                        and re.search(r'\b' + re.escape(brand) + r'\s*$', line)
                    ):
                        matches.add(brand)
        if len(matches) == 1:
            fields['insurance_company'] = next(iter(matches))
            confidence = dict(fields.get('field_confidence', {}))
            confidence['insurance_company'] = 0.9
            fields['field_confidence'] = confidence
    return fields


def insurance_passes(preprocessor, candidates, config, deadline):
    # Try each useful crop with the cheapest OCR variant before spending the
    # remaining budget on contrast/sharpening variants of any single crop.
    # Retain only the current candidate's variants to bound Pi memory use.
    for quick in (True, False):
        for label, candidate in candidates:
            if time.monotonic() >= deadline:
                return
            if quick:
                processed = insurance_gray(candidate, config)
                if time.monotonic() < deadline:
                    yield label, processed, config.psm_modes[0]
                # Sparse-text mode handles separated date/vehicle columns.
                # Run it on intact full pages before spending the budget on crops.
                if label.startswith('whole_document'):
                    for psm in config.psm_modes[1:]:
                        if time.monotonic() >= deadline:
                            return
                        yield label, processed, psm
                continue
            variants = preprocessor.preprocess(candidate)
            for index, (_, processed) in enumerate(variants):
                if quick and index != 0:
                    continue
                for psm in config.psm_modes:
                    if quick != (index == 0 and psm == config.psm_modes[0]):
                        continue
                    if time.monotonic() >= deadline:
                        return
                    yield label, processed, psm


def scan_insurance(image):
    started = time.monotonic()
    attempts = 0
    notes = []
    save_debug = os.getenv('KIOSK_INSURANCE_SAVE_DEBUG', '1').strip().lower() not in {'0', 'false', 'no', 'off'}
    debug = InsuranceDebug(save_debug, notes)
    # Save the actual input before orientation correction or preprocessing,
    # including scans that later fail OCR or produce no readable fields.
    debug.image('00_original.png', image)
    configure_tesseract(os.getenv('TESSERACT_CMD', ''))
    config = InsuranceCardOCRConfig(save_debug=save_debug, ocr_workers=1, upscale_factor=2.0, psm_modes=(6, 11))
    preprocessor, extractor = InsuranceCardPreprocessor(config), InsuranceFieldExtractor()
    # Preserve the partner's preprocessing and field extraction, but run one
    # image at a time, with a total time budget, instead of queuing every image.
    rotation_setting = os.getenv('KIOSK_INSURANCE_ROTATION', 'auto').strip().lower()
    if rotation_setting not in {'auto', '0', '90', '180', '270'}:
        raise ValueError('KIOSK_INSURANCE_ROTATION must be auto, 0, 90, 180, or 270.')
    orientation_confirmed = rotation_setting != 'auto'
    try:
        if orientation_confirmed:
            osd = {'rotate': int(rotation_setting), 'orientation_conf': 100}
        else:
            osd = pytesseract.image_to_osd(image, output_type=pytesseract.Output.DICT, timeout=5)
        rotation = int(osd.get('rotate', 0))
        orientation_confirmed = float(osd.get('orientation_conf', 0)) >= 2
        if float(osd.get('orientation_conf', 0)) >= 2 and rotation in {90, 180, 270}:
            image = cv2.rotate(image, {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}[rotation])
        else:
            notes.append('Check that the page is upright.')
    except (RuntimeError, pytesseract.TesseractError):
        notes.append('Orientation could not be confirmed; keep the page upright.')
    best, best_text, best_score = {}, '', (-1, -1, -1.0)
    best_image = None
    deadline = time.monotonic() + float(os.getenv('KIOSK_OCR_SECONDS', '60'))
    crops = [(label, crop) for label, crop in preprocessor.get_candidates(image) if label != 'full']
    # Complete card/page crops can contain all seven fields; small bottom crops cannot.
    complete = [(label, crop) for label, crop in crops if label == 'auto_card']
    candidates = [('whole_document', image)]
    if not orientation_confirmed:
        # Try the scanner box's top-left paper orientation first when OSD fails.
        for angle, code in ((90, cv2.ROTATE_90_CLOCKWISE), (270, cv2.ROTATE_90_COUNTERCLOCKWISE), (180, cv2.ROTATE_180)):
            candidates.append((f'whole_document_{angle}', cv2.rotate(image, code)))
    candidates += complete + [(label, crop) for label, crop in crops if label != 'auto_card']
    for _, processed, psm in insurance_passes(preprocessor, candidates, config, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            attempts += 1
            raw = pytesseract.image_to_string(processed, lang='eng', config=f'--psm {psm}', timeout=min(10, remaining)).strip()
        except RuntimeError:
            continue
        fields = insurance_brand(extractor.extract(raw), raw)
        confidence = fields.get('field_confidence', {})
        count = sum(bool(fields.get(k)) for k in FIELD_KEYS)
        score = (int(bool(fields.get('insurance_expiration'))), count, sum(confidence.values()))
        if score > best_score:
            best, best_text, best_score = fields, raw, score
            if save_debug:
                best_image = processed
        if count == len(FIELD_KEYS) and all(confidence.get(key, 0) >= 0.85 for key in FIELD_KEYS):
            break
    fields = {key: str(best.get(key) or '') for key in FIELD_KEYS}
    fields['insurance_expiration'] = normalized_date(fields['insurance_expiration'], ('%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d'))
    if not fields['insurance_expiration']:
        notes.append('Policy end date could not be read reliably. Enter the printed expiration date; it is required.')
    vins = set(re.findall(r'\b[A-HJ-NPR-Z0-9]{17}\b', best_text.upper()))
    fields['vehicle_vin'] = next(iter(vins)) if len(vins) == 1 else ''
    if len(vins) > 1:
        for key in ('vehicle_vin_last6', 'vehicle_year', 'vehicle_make', 'vehicle_model'):
            fields[key] = ''
        notes.append('Multiple vehicles found. Enter the vehicle being renewed manually.')
    if best_image is not None:
        debug.image('01_best_ocr.png', best_image)
    debug.text('best_raw_text.txt', best_text)
    debug.text('parsed_fields.json', json.dumps(fields, indent=2, ensure_ascii=False))
    elapsed = round(time.monotonic() - started, 2)
    debug.text('timing.json', json.dumps({'elapsed_seconds': elapsed, 'ocr_passes': attempts}))
    logging.getLogger(__name__).info('Insurance extraction: %.2fs, %d OCR passes', elapsed, attempts)
    if not any(fields.values()):
        raise ValueError('No insurance fields were readable. Use a closer, sharp, well-lit image or enter the information manually.')
    return response(fields, notes)


# Renewal notice (TxDMV VTR-39-A). Optional: its values only fill form fields
# the customer left blank. The OCR engine keeps a Tesseract thread pool, so it
# is built once and reused.
_registration_ocr = None
_registration_lock = threading.Lock()


def registration_ocr():
    global _registration_ocr
    with _registration_lock:
        if _registration_ocr is None:
            cmd = configure_tesseract(os.getenv('TESSERACT_CMD', ''))
            _registration_ocr = registration.make_ocr(ScannerConfig(tesseract_cmd=cmd, debug_dir=None))
        return _registration_ocr


def scan_registration(image):
    """Seth's renewal notice reader: find the page, turn it upright (OSD), read the fields."""
    started = time.monotonic()
    configure_tesseract(os.getenv('TESSERACT_CMD', ''))
    # The notice lies sideways in the box; documents.read straightens it
    result = documents.read(registration.PROFILE, registration_ocr(), image)
    fields = {key: str(result.fields.get(key) or '') for key in REGISTRATION_KEYS}
    logging.getLogger(__name__).info('Renewal notice extraction: %.2fs, %d fields',
                                     time.monotonic() - started, sum(bool(v) for v in fields.values()))
    if not any(fields.values()):
        raise ValueError('No renewal notice fields were readable. Lay the whole notice flat in the box, '
                         'or skip this optional step.')
    notes = [note.replace('\u2014', '-') for note in result.notes]
    return response(fields, notes)
