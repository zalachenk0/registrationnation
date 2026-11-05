# ============================================================================
# File:         insurance_SL.py
# Written by:   SL (Seth Leija)
# What it does: Insurance card scanner (paper printout or phone screen).
# Team-written: Logic written by SL (Seth Leija). Outside code is used only
#               through the third-party sources listed below.
# Third-party:  NumPy
# ============================================================================
"""Insurance card scanner (paper printout or card). Settings from INS Code/insurance_card_capture_rev1.py."""

from __future__ import annotations

from typing import Dict

import numpy as np

from .config_SL import ScannerConfig
from .documents_SL import DocumentProfile
from .insurance_ocr_SL import FIELD_KEYS, InsuranceCardOCR, InsuranceCardOCRConfig
from .results_SL import INSURANCE_CARD

FIELD_LABELS = {
    "insurance_company": "Insurance company",
    "policy_number": "Policy number",
    "insurance_expiration": "Expiration date",
    "vehicle_year": "Vehicle year",
    "vehicle_make": "Vehicle make",
    "vehicle_model": "Vehicle model",
    "vehicle_vin_last6": "VIN (last 6)",
}


def make_ocr(config: ScannerConfig) -> InsuranceCardOCR:
    return InsuranceCardOCR(InsuranceCardOCRConfig(
        tesseract_cmd=config.tesseract_cmd,
        save_debug=config.debug_dir is not None,
        debug_output_dir=str(config.debug_dir / "insurance") if config.debug_dir else "insurance_debug"))


def read_fields(ocr: InsuranceCardOCR, document: np.ndarray) -> Dict[str, str]:
    result = ocr.extract_image(document, "ins_capture")
    return {k: str(result.parsed_fields.get(k, "")) for k in FIELD_KEYS}


PROFILE = DocumentProfile(
    document=INSURANCE_CARD,
    title="Insurance Card",
    noun="insurance card",
    image_name="card",
    instruction="Place the card or printout inside the frame, top edge up",
    top_marker="top",
    sideways_ok=False,
    # Live scans of the USAA printout: sharp capture (all fields read) = 593,
    # blurry capture (VIN + expiration lost) = 215
    sharpness_min=400,
    required_keys=FIELD_KEYS,
    review_layout=((None, tuple((FIELD_LABELS[k], (k,)) for k in FIELD_KEYS)),),
    review_image_frac=0.55,
    keep_whole_offline=False,
    make_ocr=make_ocr,
    read_fields=read_fields,
)
