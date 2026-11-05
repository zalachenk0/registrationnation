# ============================================================================
# File:         registration_SL.py
# Written by:   SL (Seth Leija)
# What it does: Texas renewal notice (VTR-39-A) scanner.
# Team-written: Logic written by SL (Seth Leija). Outside code is used only
#               through the third-party sources listed below.
# Third-party:  NumPy; TxDMV renewal notice form VTR-39-A (layout reference).
# ============================================================================
"""
Texas Vehicle Registration Renewal Notice (VTR-39-A) scanner.
Settings from RRN Code/registration_renewal_capture_rev1.py.

The notice lies SIDEWAYS: a letter-size portrait page only fits the
landscape camera frame that way. Either sideways direction works; OSD
straightens it after capture.
"""

from __future__ import annotations

from typing import Dict

import numpy as np

from .config_SL import ScannerConfig
from .documents_SL import DocumentProfile
from .registration_ocr_SL import (FIELD_KEYS, REQUIRED_KEYS, RegistrationRenewalOCR,
                               RegistrationRenewalOCRConfig)
from .results_SL import REGISTRATION_RENEWAL

REVIEW_LAYOUT = (
    ("Vehicle", (
        ("License plate", ("license_plate",)),
        ("VIN", ("vehicle_vin",)),
        ("Year / make / body", ("vehicle_year", "vehicle_make", "vehicle_body_style")),
        ("Class / fuel", ("vehicle_class", "fuel_type")),
        ("Current expiration", ("current_expiration",)),
        ("New expiration", ("new_expiration",)),
    )),
    ("Fees due", (
        ("Registration fee", ("registration_fee",)),
        ("Local fee", ("local_fee",)),
        ("Total (in person)", ("total_fee",)),
        ("Total (if mailed)", ("total_fee_mailed",)),
    )),
    ("Vehicle owner", (
        ("Name(s)", ("owner_names",)),
        ("Street", ("owner_street",)),
        ("City / state / ZIP", ("owner_city", "owner_state", "owner_zip")),
    )),
    ("Renewal recipient", (
        ("Name", ("recipient_name",)),
        ("Street", ("recipient_street",)),
        ("City / state / ZIP", ("recipient_city", "recipient_state", "recipient_zip")),
    )),
)


def make_ocr(config: ScannerConfig) -> RegistrationRenewalOCR:
    return RegistrationRenewalOCR(RegistrationRenewalOCRConfig(
        tesseract_cmd=config.tesseract_cmd,
        tesseract_extra=config.tesseract_extra(),
        save_debug=config.debug_dir is not None,
        debug_output_dir=str(config.debug_dir / "registration") if config.debug_dir else "rrn_debug"))


def read_fields(ocr: RegistrationRenewalOCR, document: np.ndarray) -> Dict[str, str]:
    result = ocr.extract_image(document, "rrn_capture")
    form = {}
    for k in FIELD_KEYS:
        value = result.parsed_fields.get(k, "")
        # Co-owners come back as a list, one name per line on the notice
        form[k] = " / ".join(value) if isinstance(value, list) else str(value)
    return form


PROFILE = DocumentProfile(
    document=REGISTRATION_RENEWAL,
    title="Registration Renewal Notice",
    noun="renewal notice",
    image_name="notice",
    instruction="Lay the notice sideways, top edge to the left, whole page inside the frame",
    top_marker="left",
    sideways_ok=True,
    # TODO: re-measure on live scans of a renewal notice (insurance values for now)
    sharpness_min=400,
    required_keys=REQUIRED_KEYS,
    review_layout=REVIEW_LAYOUT,
    review_image_frac=0.38,
    # A flatbed scan IS the page: the guide-box fallback would trim it and
    # shift the OCR's page regions
    keep_whole_offline=True,
    make_ocr=make_ocr,
    read_fields=read_fields,
)
