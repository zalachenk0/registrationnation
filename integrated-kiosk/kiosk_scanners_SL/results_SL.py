# ============================================================================
# File:         results_SL.py
# Written by:   SL (Seth Leija)
# What it does: Scan result type, errors and small image helpers.
# Team-written: Logic written by SL (Seth Leija). Outside code is used only
#               through the third-party sources listed below.
# Third-party:  OpenCV; NumPy; pytesseract (Tesseract OCR)
# ============================================================================
"""Scan result type, errors, and small image helpers."""

from __future__ import annotations

import json
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Union

import cv2
import numpy as np
import pytesseract

ImageSource = Union[str, Path, np.ndarray]

DRIVERS_LICENSE = "drivers_license"
INSURANCE_CARD = "insurance_card"
REGISTRATION_RENEWAL = "registration_renewal"

# File name prefixes (match the original capture scripts: dl_front.jpg, ins_card.jpg, ...)
_FILE_PREFIX = {DRIVERS_LICENSE: "dl", INSURANCE_CARD: "ins", REGISTRATION_RENEWAL: "rrn"}

class ScannerError(RuntimeError):
    """The scan couldn't run: camera missing, Tesseract missing, unreadable image or barcode."""


@dataclass
class ScanResult:
    """
    One accepted scan.

    document  "drivers_license" | "insurance_card" | "registration_renewal"
    fields    form values by key; "" when a field wasn't found
    images    cropped, upright document images (BGR numpy arrays) by name
    notes     things the operator should know ("Page was rotated 90 deg ...")
    files     paths written by save(), empty until saved
    """
    document: str
    fields: Dict[str, str]
    images: Dict[str, np.ndarray] = field(default_factory=dict, repr=False)
    notes: List[str] = field(default_factory=list)
    files: Dict[str, Path] = field(default_factory=dict)

    @property
    def missing(self) -> List[str]:
        """Keys of fields that weren't found — ask the customer to fill these in."""
        return [k for k, v in self.fields.items() if not v]

    def save(self, output_dir: Union[str, Path]) -> Dict[str, Path]:
        """
        Writes <prefix>_<scan id>_<image>.jpg for each image and
        <prefix>_<scan id>_form_data.json. The scan id (time + random suffix)
        keeps each scan's files apart, so erasing one customer's scan can
        never delete another's.
        """
        self.delete_files()          # saved before: replace, don't leave orphans
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{_FILE_PREFIX[self.document]}_{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        for name, image in self.images.items():
            path = output_dir / f"{stem}_{name}.jpg"
            self.files[name] = path      # recorded first so erase() also removes a partial write
            if not cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, 95]):
                raise ScannerError(f"Could not write {path}")
        path = output_dir / f"{stem}_form_data.json"
        self.files["form_data"] = path
        path.write_text(json.dumps(self.fields, indent=2), encoding="utf-8")
        return self.files

    def delete_files(self) -> None:
        """Deletes the saved files (they hold personal data)."""
        for path in self.files.values():
            Path(path).unlink(missing_ok=True)
        self.files.clear()

    def erase(self) -> None:
        """Deletes the saved files and clears the images, fields and notes held in memory."""
        self.delete_files()
        self.images.clear()
        self.fields.clear()
        self.notes.clear()

    def to_dict(self) -> dict:
        """JSON-ready summary (no image data)."""
        return {
            "document": self.document,
            "fields": self.fields,
            "missing": self.missing,
            "notes": self.notes,
            "files": {k: str(v) for k, v in self.files.items()},
        }


def load_image(source: ImageSource) -> np.ndarray:
    """A BGR image from a file path or an array."""
    if isinstance(source, np.ndarray):
        return source
    image = cv2.imread(str(source))
    if image is None:
        raise ScannerError(f"Could not load image: {source}")
    return image


def configure_tesseract(cmd: str) -> str:
    """
    Points pytesseract at Tesseract and returns the path it used; raises
    ScannerError with install help if it's missing.
    """
    if cmd and Path(cmd).exists():
        pytesseract.pytesseract.tesseract_cmd = cmd
        return cmd
    found = shutil.which("tesseract")
    if found:
        pytesseract.pytesseract.tesseract_cmd = found
        return found
    raise ScannerError(
        f"Tesseract OCR not found at {cmd!r} or on PATH. Install it from "
        "https://github.com/UB-Mannheim/tesseract/wiki or set tesseract_cmd.")
