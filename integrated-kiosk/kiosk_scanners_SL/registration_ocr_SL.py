# ============================================================================
# File:         registration_ocr_SL.py
# Written by:   SL (Seth Leija)
# What it does: Renewal notice text recognition and field parsing.
# Team-written: Logic written by SL (Seth Leija). Outside code is used only
#               through the third-party sources listed below.
# Third-party:  OpenCV; NumPy; pytesseract (Tesseract OCR); Tesseract OCR
#               (through pytesseract); TxDMV renewal notice form VTR-39-A
#               (layout reference).
# ============================================================================
from __future__ import annotations

"""
registration_ocr_SL.py  (copied from RRN Code/registration_renewal_ocr_rev2.py)

OCR for the Texas Vehicle Registration Renewal Notice (TxDMV VTR-39-A).

Changes vs rev1:
- SPEED: OCR runs in parallel (one Tesseract process per CPU core): the
  full-page passes run together, as do each re-read's scale/PSM tries.
  Results are collected in the original order, so the fields are
  identical to running them one by one.
- FIX: Fields are found by their printed LABELS and the geometry of the
  words around them, not by fixed page regions. rev1's ROIs were measured
  on VRRNS.jpg; the first live scan was a newer notice (no county copy,
  different block positions) with the top of the page out of frame, so
  every ROI landed on the wrong block and only 2 fields were read.
- FIX: Ruled lines are erased before OCR. On the live scan the vehicle box
  borders and the green page-edge stripe made Tesseract skip the whole
  left half of the page and chop the first letters off labels ("VEH.",
  "EMPTY"). Horizontal and vertical lines are found with morphological
  opening and painted white.
- ADD: Adaptive-threshold variant — handles the shadow/uneven lighting on
  camera captures and removes the green/yellow tint of the form.
- ADD: Word boxes from Tesseract (image_to_data) are grouped into "cells":
  runs of words on one line with no wide gap. A label's value is the rest
  of its cell, the nearest cell to its right on the same row, or (for some
  labels) the cell below. Columns stay separate even when Tesseract puts
  them on one text line.
- ADD: Every copy of a field on the page votes (the plate is printed up to
  three times), not just the first one found.
- ADD: Name/address blocks are stacks of left-aligned cells ending in a
  "CITY, ST ZIP" cell. The nearest label above the stack says whose block
  it is: "VEHICLE OWNER" → owner, "RENEWAL RECIPIENT" → recipient,
  "CORRECT FEE TO" / tax assessor → skipped.
- ADD: Owner/recipient names are voted line by line, so one misread name
  line doesn't split the vote for the others.
- ADD: current_expiration is derived from new_expiration (one year earlier)
  when the top of the page (customer copy) wasn't captured.
- ADD: Targeted re-reads from the grayscale page, cached so each spot is
  read once. VIN and plate cells: single line (PSM 7/8) at 1x and 2x with
  a character whitelist — the full-page pass read the live plate as
  "XY12ZIL" and the VIN as "...456898"; the re-reads got "XY12ZL" and
  "...456838". Name/address blocks: the whole block (PSM 6) at 0.75x, 1x
  and 2x, plain and slightly blurred — the notice's monospace "J" was read
  as "UJ"/"U" ("OUJEDA" for OJEDA) by the full-page pass.
- ADD: VIN repair by check digit. The VIN reads are combined position by
  position; if the result fails the North American check digit, runner-up
  characters that OCR commonly confuses (J/U, 3/8, 5/S, ...) are tried at
  the disputed positions ("UN1EV..." -> "JN1EV...").

Kept from rev1: field list, label patterns, VIN check digit, make codes,
fee cross-check (registration + local = total), voting and debug output.

Usage:
    ocr = RegistrationRenewalOCR()
    result = ocr.extract("VRRNS.jpg")
    print(result.parsed_fields)
"""

import json
import logging
import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import product
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np
import pytesseract

LOGGER = logging.getLogger(__name__)

# One thread per Tesseract process: the pool already runs one per core, and
# more threads on the Pi's 4 cores only fight each other
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

# Fields collected for the kiosk form, in display order
FIELD_KEYS = (
    # Vehicle information
    "license_plate", "plate_type", "vehicle_vin", "vehicle_vin_last6",
    "vehicle_year", "vehicle_make", "vehicle_make_code", "vehicle_body_style",
    "vehicle_class", "fuel_type", "empty_weight", "carrying_capacity",
    "gross_weight", "current_expiration", "new_expiration",
    # Fees due
    "registration_fee", "local_fee", "total_fee", "total_fee_mailed",
    # Vehicle owner and renewal recipient
    "owner_names", "owner_street", "owner_city", "owner_state", "owner_zip",
    "recipient_name", "recipient_street", "recipient_city",
    "recipient_state", "recipient_zip",
)

# Fields the kiosk needs; used for success and the recommendation
REQUIRED_KEYS = (
    "license_plate", "vehicle_vin", "vehicle_year", "vehicle_make",
    "current_expiration", "total_fee",
    "owner_names", "owner_street", "owner_city", "owner_state", "owner_zip",
)

# TxDMV prints the make as a 4-letter NCIC code
MAKE_CODES = {
    "ACUR": "Acura", "ALFA": "Alfa Romeo", "AUDI": "Audi", "BENT": "Bentley",
    "BMW": "BMW", "BUIC": "Buick", "CADI": "Cadillac", "CHEV": "Chevrolet",
    "CHRY": "Chrysler", "DODG": "Dodge", "FERR": "Ferrari", "FIAT": "Fiat",
    "FORD": "Ford", "FRHT": "Freightliner", "GENS": "Genesis", "GMC": "GMC",
    "HOND": "Honda", "HUMM": "Hummer", "HYUN": "Hyundai", "INFI": "Infiniti",
    "INTL": "International", "ISU": "Isuzu", "JAGU": "Jaguar", "JEEP": "Jeep",
    "KENW": "Kenworth", "KIA": "Kia", "LAMO": "Lamborghini", "LNDR": "Land Rover",
    "LEXS": "Lexus", "LINC": "Lincoln", "MASE": "Maserati", "MAZD": "Mazda",
    "MERZ": "Mercedes-Benz", "MERC": "Mercury", "MINI": "Mini",
    "MITS": "Mitsubishi", "NISS": "Nissan", "OLDS": "Oldsmobile",
    "PTRB": "Peterbilt", "PLYM": "Plymouth", "PONT": "Pontiac",
    "PORS": "Porsche", "RAM": "Ram", "RIVN": "Rivian", "ROL": "Rolls-Royce",
    "SATR": "Saturn", "SCIO": "Scion", "SUBA": "Subaru", "SUZI": "Suzuki",
    "TESL": "Tesla", "TOYT": "Toyota", "VOLK": "Volkswagen", "VOLV": "Volvo",
    "HD": "Harley-Davidson", "HRLD": "Harley-Davidson", "YAMA": "Yamaha",
    "KAWK": "Kawasaki",
}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RegistrationRenewalOCRConfig:
    tesseract_cmd: Optional[str] = None
    language: str = "eng"
    save_debug: bool = True
    debug_output_dir: str = "rrn_debug"
    # PSM 3 = automatic layout (keeps the form's columns apart),
    # PSM 11 = sparse text (finds isolated values like "PASS", "0").
    # Pi: PSM 3 alone read the kiosk scans as well or better (49/50 fields
    # vs 46/50 with both) in half the full-page passes.
    psm_modes: tuple = (3,)
    # Page versions given the full-page passes ("bw", "gray"); the re-reads
    # always use the gray page
    page_variants: tuple = ("bw", "gray")
    # kind -> (scales, PSM modes, also try blurred) for the cell re-reads;
    # unset kinds use _REREAD_SETTINGS
    reread_overrides: Optional[Dict[str, Tuple[tuple, tuple, bool]]] = None
    # Extra Tesseract options for every call, e.g. "--tessdata-dir <dir>"
    tesseract_extra: str = ""
    # The page is scaled to about this width. The form uses ~9pt type;
    # 2400px across the page gives Tesseract ~25px capitals.
    page_target_width: int = 2400
    # A horizontal gap wider than this many text heights splits a line
    # into separate cells (label | value, left column | right column)
    cell_gap_factor: float = 2.0
    # Re-read VIN/plate/name cells as single lines (slower, more accurate)
    reread_cells: bool = True
    # The input is already a flattened page (from a capture script) — skip
    # page detection
    assume_flat_page: bool = False
    # Tesseract processes run at once (8-core kiosk PC: ~4x faster than one)
    ocr_workers: int = os.cpu_count() or 4


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class RegistrationRenewalOCRResult:
    source_path: Path
    scan_success: bool
    raw_text: str
    parsed_fields: Dict[str, object]
    recommendation: str


# ---------------------------------------------------------------------------
# Cells: word boxes grouped into runs of text
# ---------------------------------------------------------------------------

@dataclass
class Cell:
    text: str
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2.0


def build_cells(data: Dict[str, list], gap_factor: float) -> Tuple[List[Cell], float]:
    """
    Groups Tesseract's words by its own line numbering, then splits each
    line wherever the gap between words is wider than gap_factor text
    heights. Returns (cells, median text height).
    """
    lines: Dict[Tuple[int, int, int], List[Tuple[int, int, int, int, str]]] = defaultdict(list)
    for i, text in enumerate(data["text"]):
        text = text.strip()
        if not text or float(data["conf"][i]) < 0:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines[key].append((data["left"][i], data["top"][i], data["width"][i],
                           data["height"][i], text.upper()))

    heights = [w[3] for words in lines.values() for w in words
               if sum(ch.isalnum() for ch in w[4]) >= 2]
    med_h = float(np.median(heights)) if heights else 20.0

    cells: List[Cell] = []

    def flush(group) -> None:
        text = re.sub(r"\s+", " ", " ".join(w[4] for w in group)).strip()
        if any(ch.isalnum() for ch in text):
            cells.append(Cell(text,
                              min(w[0] for w in group), min(w[1] for w in group),
                              max(w[0] + w[2] for w in group), max(w[1] + w[3] for w in group)))

    for words in lines.values():
        words.sort()
        group = [words[0]]
        for word in words[1:]:
            prev = group[-1]
            if word[0] - (prev[0] + prev[2]) > gap_factor * med_h:
                flush(group)
                group = [word]
            else:
                group.append(word)
        flush(group)
    return cells, med_h


def cells_to_text(cells: List[Cell]) -> str:
    """Debug view: cells in reading order with their positions."""
    ordered = sorted(cells, key=lambda c: (c.y0 // 10, c.x0))
    return "\n".join(f"[{c.x0:5d},{c.y0:5d}] {c.text}" for c in ordered)


# ---------------------------------------------------------------------------
# Field extractor (one OCR pass → votes)
# ---------------------------------------------------------------------------

Vote = Tuple[str, object, float]      # (field key, value, confidence)

# reread(cell, kind) -> texts from re-reading that cell's image crop.
# kind is "vin" or "plate" (single line, whitelist) or "block" (a whole
# name/address block, multi-line text).
Reread = Callable[[Cell, str], List[str]]


class RegistrationFieldExtractor:
    """
    Finds fields in the cells of ONE OCR pass. Returns every value it
    finds as a vote — the same field printed in several places on the
    notice gives several votes. Voting across passes happens in
    RegistrationRenewalOCR.
    """

    _MONTHS = "JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC"
    # "AUG 2011", "AUG..2012", "1/SEP 2026" (plate term line)
    _MON_YR_RE = re.compile(rf"\b({_MONTHS})[A-Z]*[\s.,]*((?:19|20)\d{{2}})\b")

    # Money: "$ 62.75", "63.75", "52,75" (comma misread for the point)
    _MONEY_RE = re.compile(r"(?<![\d.])(\d{1,4})\s?[.,]\s?(\d{2})(?![\d.])")

    # "2006/PONT/2D" — YEAR/MAKE/BODY STYLE value, found without its label
    _YMB_RE = re.compile(
        r"\b((?:19|20)\d{2})\s*/\s*([A-Z0-9]{2,5})\s*/\s*([A-Z0-9]{1,4})\b"
    )

    _VIN_RE = re.compile(r"\b([A-HJ-NPR-Z0-9]{17})\b")

    # Labels are matched on upper-cased cells, tolerant of the OCR errors
    # seen on the scans ("VEH, I0ENT. NO,", "IDENT. NOQ.", "TOTAL FEEDUE")
    _PLATE_LABEL = re.compile(r"LICEN[SC]E\s+(?:PLATE\s+)?(?:NUMBER|NUMB\w*|N[O0Q])\b[.,:]?")
    _VIN_LABEL = re.compile(r"[I1L][D0O]ENT\w*[.,]?\s*N[O0Q]\w?\b[.,:]?|\bVIN\b[.,:]?")
    _CLASS_LABEL = re.compile(r"VEH\w*[.,]?\s*CLASS\w*[.,:]?|VEH\s*/\s*REG\s+CL\w*[.,:]?")
    _FUEL_LABEL = re.compile(r"FUEL\s+TYPE[.,:]?")
    _EMPTY_LABEL = re.compile(r"EMPTY\s+(?:WEIGHT|WT)\b[.,:]?")
    _CARRY_LABEL = re.compile(r"CARRYING\s+CAP\w*[.,:]?")
    _GROSS_LABEL = re.compile(r"GROSS\s+WE\s?IGHT(?:\s*/\s*TONNAGE)?[.,:]?|GROSS\s+WT[.,:]?")
    _CUR_EXP_LABEL = re.compile(r"CURRENT\s+EXP\w*[.,]?(?:\s+MON\s+YR)?")
    _NEW_EXP_LABEL = re.compile(r"LAST\s+DAY\s+OF[.,:;]?|WILL\s+EXP\w*|REGIS\w*[.,]?\s+EXP\w*[.,]?(?:\s+MON\s+YR)?")

    _REG_FEE_LABEL = re.compile(r"REGISTRATION\s+FEE")
    _LOCAL_FEE_LABEL = re.compile(r"LOCAL\s+FEE")
    _TOTAL_FEE_LABEL = re.compile(r"TOTAL[\s\-~]*FEE")
    _MAILED_LABEL = re.compile(r"\bIF\s+MAIL\w*")

    _FUEL_VALUES = re.compile(
        r"\b(GAS(?:OLINE)?|DIESEL|ELECTRIC|HYBRID|FLEX|PROPANE|CNG|LPG|HYDROGEN)\b"
    )

    # "CAR TOWN, TX 77833" / "ANYTOWN, TX 75001-1234". Zip digits may
    # be read as letters (S=5, O=0) and are fixed afterwards.
    _CSZ_RE = re.compile(
        r"(?P<city>[A-Z][A-Z.'\-]*(?:\s+[A-Z][A-Z.'\-]*){0,3})\s*[,.]?\s+"
        r"(?P<state>[A-Z]{2})[.,]?\s+"
        r"(?P<zip>[0-9SOIB]{5})(?:\s*[-~]\s*(?P<zip4>[0-9SOIB]{4}))?\b"
    )
    # Same line with the state lost ("CAR TOWN, � 77833")
    _CSZ_NO_STATE_RE = re.compile(
        r"(?P<city>[A-Z][A-Z.'\-]*(?:\s+[A-Z][A-Z.'\-]*){0,3})\s*,\s*\S{0,3}\s*"
        r"(?P<zip>\d{5})(?:\s*[-~]\s*(?P<zip4>\d{4}))?\b"
    )
    _LABEL_WORDS = re.compile(
        r"OWNER|NAME|ADDRESS|ADORESS|RECIPIENT|VEHICLE|INFORMATION|SECTION|"
        r"ASSESSOR|COLLECTOR|LICENSE|PLATE|STICKER|DIVISION"
    )
    # Labels that say whose name/address block sits below them
    _BLOCK_LABEL = re.compile(r"OWNER|RECIPIENT|CORRECT\s+FEE|FEE\s+TO|ASSESSOR|COLLECTOR")
    _TAX_OFFICE = re.compile(r"ASSESSOR|COLLECTOR|CNTY|COUNTY|TAX\s+OFFICE|CORRECT\s+FEE|FEE\s+TO")

    def extract(self, cells: List[Cell], med_h: float, reread: Optional[Reread] = None) -> List[Vote]:
        self.cells, self.h, self.reread = cells, med_h, reread
        votes: List[Vote] = []

        # --- Vehicle information ---
        votes += self._labeled("license_plate", self._PLATE_LABEL, self._plate_value, reread_kind="plate")
        votes += self._plate_type()
        vins = self._labeled("vehicle_vin", self._VIN_LABEL, self._vin_value, reread_kind="vin")
        votes += vins or self._unlabeled_vin()
        votes += self._year_make_body()
        votes += self._labeled("vehicle_class", self._CLASS_LABEL,
                               self._regex_value(r"^\W*([A-Z]{2,6})\b"))
        votes += self._labeled("fuel_type", self._FUEL_LABEL, self._regex_value(self._FUEL_VALUES))
        # Weights must be the whole value cell, so a street number on the
        # same row ("12345 OAK HOLLOW") is never taken for one
        votes += self._labeled("empty_weight", self._EMPTY_LABEL,
                               self._regex_value(r"^\W*(\d{3,6})(?:\s*/\s*[\d.]+)?\W*$"))
        votes += self._carrying_capacity()
        votes += self._labeled("gross_weight", self._GROSS_LABEL,
                               self._regex_value(r"^\W*(\d{3,6})(?:\s*/\s*[\d.]+)?\W*$"))
        votes += self._labeled("current_expiration", self._CUR_EXP_LABEL, self._mon_yr_value)
        votes += self._labeled("new_expiration", self._NEW_EXP_LABEL, self._mon_yr_value, below=True)

        # --- Fees due ---
        votes += self._labeled("registration_fee", self._REG_FEE_LABEL, self._money_value)
        votes += self._labeled("local_fee", self._LOCAL_FEE_LABEL, self._money_value)
        votes += self._labeled("total_fee", self._TOTAL_FEE_LABEL, self._money_value)
        votes += self._labeled("total_fee_mailed", self._MAILED_LABEL, self._money_value)

        # --- Name / address blocks ---
        votes += self._address_blocks()
        return votes

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------

    def _right_of(self, cell: Cell) -> List[Cell]:
        """Cells on the same row to the right, nearest first."""
        h = self.h
        out = [n for n in self.cells
               if n is not cell and n.x0 >= cell.x1 - 0.5 * h
               and abs(n.yc - cell.yc) < 0.6 * h and n.x0 - cell.x1 < 45 * h]
        return sorted(out, key=lambda n: n.x0)

    def _below(self, cell: Cell) -> List[Cell]:
        """Cells on the next line that overlap this cell horizontally."""
        h = self.h
        out = [n for n in self.cells
               if n is not cell and n.y0 >= cell.y1 - 0.3 * h and n.y0 - cell.y1 < 1.5 * h
               and n.x0 < cell.x1 and n.x1 > cell.x0]
        return sorted(out, key=lambda n: n.y0)

    def _above_aligned(self, cell: Cell) -> Optional[Cell]:
        """The cell on the line above with the same left edge (address lines)."""
        h = self.h
        out = [n for n in self.cells
               if n is not cell and n.y1 <= cell.y0 + 0.4 * h and cell.y0 - n.y1 < 1.6 * h
               and abs(n.x0 - cell.x0) < 1.5 * h]
        return min(out, key=lambda n: cell.y0 - n.y1) if out else None

    def _labeled(self, key: str, label: re.Pattern, value_fn: Callable[[str], str],
                 below: bool = False, reread_kind: str = "") -> List[Vote]:
        """
        One vote per occurrence of the label: the value is the rest of the
        label's cell, else one of the next two cells to its right, else
        (below=True) the cell under it. With reread_kind, a value cell to
        the right is also re-read and each re-read adds a vote.
        """
        votes: List[Vote] = []
        for cell in self.cells:
            match = label.search(cell.text)
            if not match:
                continue
            value = value_fn(cell.text[match.end():])
            if value:
                votes.append((key, value, 0.90))
                continue
            for right in self._right_of(cell)[:2]:
                value = value_fn(right.text)
                if value:
                    votes.append((key, value, 0.90))
                    if reread_kind and self.reread:
                        votes += [(key, v, 0.90) for v in map(value_fn, self.reread(right, reread_kind)) if v]
                    break
            else:
                if below:
                    for under in self._below(cell)[:1]:
                        value = value_fn(under.text)
                        if value:
                            votes.append((key, value, 0.75))
        return votes

    # ------------------------------------------------------------------
    # Value parsers (text of one cell → value or "")
    # ------------------------------------------------------------------

    @staticmethod
    def _regex_value(pattern) -> Callable[[str], str]:
        regex = re.compile(pattern) if isinstance(pattern, str) else pattern

        def parse(text: str) -> str:
            match = regex.search(text)
            return match.group(1) if match else ""
        return parse

    @staticmethod
    def _fix_digits(token: str) -> str:
        """Letters Tesseract commonly reads in place of digits."""
        return token.translate(str.maketrans("SOQIL|BZ", "50011182"))

    def _plate_value(self, text: str) -> str:
        tokens = [re.sub(r"[^A-Z0-9]", "", t) for t in text.split()[:2]]
        tokens = [t for t in tokens if t]
        # OCR can split the plate at the letter/digit boundary: "XX 1234"
        if (len(tokens) == 2 and tokens[0].isalpha() and len(tokens[0]) <= 4
                and tokens[1].isdigit() and len(tokens[0] + tokens[1]) <= 8):
            tokens = [tokens[0] + tokens[1]]
        # Plates are 2-8 chars with at least one digit; skips "required"
        # from "Driver's license number required on checks"
        if tokens and 2 <= len(tokens[0]) <= 8 and any(c.isdigit() for c in tokens[0]):
            return tokens[0]
        return ""

    def _vin_value(self, text: str) -> str:
        """
        Joins tokens until they make 17 characters (OCR often splits the
        VIN: "NNNNXXXXXNNNN 1234").
        """
        vin = ""
        for raw in text.split():
            tok = re.sub(r"[^A-Z0-9]", "", raw)
            if not tok:
                continue
            if len(vin) + len(tok) > 17:
                break
            vin += tok
            if len(vin) == 17:
                return self._correct_vin_ocr_errors(vin)
        return ""

    def _mon_yr_value(self, text: str) -> str:
        match = self._MON_YR_RE.search(text)
        return f"{match.group(1)} {match.group(2)}" if match else ""

    def _money_value(self, text: str) -> str:
        match = self._MONEY_RE.search(text)
        return f"{match.group(1)}.{match.group(2)}" if match else ""

    # ------------------------------------------------------------------
    # Vehicle information
    # ------------------------------------------------------------------

    def _plate_type(self) -> List[Vote]:
        votes = []
        for cell in self.cells:
            # "PASSENGER PLT", not "CURRENT PLT TERM/EXP"
            match = re.search(r"\b((?!PLT\b|CURRENT\b)[A-Z]{3,})\s+PLT\b(?!\s*TERM)", cell.text)
            if match:
                votes.append(("plate_type", f"{match.group(1)} PLT", 0.85))
        return votes

    def _unlabeled_vin(self) -> List[Vote]:
        for cell in self.cells:
            match = self._VIN_RE.search(cell.text)
            if match:
                vin = self._correct_vin_ocr_errors(match.group(1))
                return [("vehicle_vin", vin, 0.90 if self._vin_check_digit_ok(vin) else 0.70)]
        return []

    def _correct_vin_ocr_errors(self, vin: str) -> str:
        """Replace AAMVA-prohibited chars that Tesseract commonly substitutes."""
        return vin.translate(str.maketrans("OIQ", "010"))

    def _vin_check_digit_ok(self, vin: str) -> bool:
        """North American VIN check digit (position 9)."""
        values = {**{str(d): d for d in range(10)},
                  **dict(zip("ABCDEFGH", range(1, 9))),
                  **dict(zip("JKLMN", range(1, 6))), "P": 7, "R": 9,
                  **dict(zip("STUVWXYZ", range(2, 10)))}
        weights = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)
        try:
            total = sum(values[c] * w for c, w in zip(vin, weights))
        except KeyError:
            return False
        check = total % 11
        return vin[8] == ("X" if check == 10 else str(check))

    def _year_make_body(self) -> List[Vote]:
        votes: List[Vote] = []
        for cell in self.cells:
            match = self._YMB_RE.search(re.sub(r"\s*/\s*", "/", cell.text))
            if not match:
                continue
            year, make, body = match.groups()
            # Make codes are letters only: "P0NT" → "PONT", "INF1" → "INFI"
            make = make.translate(str.maketrans("0158", "OISB"))
            # Body style "2D"/"4D" is often read as "20"/"40"
            body = re.sub(r"^([2-5])[0O]$", r"\1D", body)
            if make.isalpha():
                votes += [("vehicle_year", year, 0.92), ("vehicle_make_code", make, 0.92),
                          ("vehicle_body_style", body, 0.92)]
        return votes

    def _carrying_capacity(self) -> List[Vote]:
        """
        Mail section: "CARRYING CAPACITY    0". County copy: "EMPTY WT./
        CARRYING CAP.  2800/0" — the capacity is the part after the slash.
        The printed 0 is often read as the letter O.
        """
        def number(token: str) -> str:
            digits = self._fix_digits(token)
            return str(int(digits)) if digits.isdigit() else ""

        def single(text: str) -> str:
            match = re.match(r"^\W*([0-9OQ]{1,6})\W*$", text)
            return number(match.group(1)) if match else ""

        def combined(text: str) -> str:
            match = re.search(r"\d{3,6}\s*/\s*([0-9OQ]{1,6})\b", text)
            return number(match.group(1)) if match else ""

        votes = []
        for key, value, conf in self._labeled("carrying_capacity", self._CARRY_LABEL, single):
            votes.append((key, value, conf))
        combined_votes = [v for v in self._labeled("carrying_capacity", self._CARRY_LABEL, combined)]
        # A combined-label cell's "single" read is the empty weight — drop it
        if any(self._EMPTY_LABEL.search(c.text) and self._CARRY_LABEL.search(c.text) for c in self.cells):
            votes = [v for v in votes if len(v[1]) <= 2] + combined_votes
        return votes

    # ------------------------------------------------------------------
    # Name / address blocks
    # ------------------------------------------------------------------

    def _address_blocks(self) -> List[Vote]:
        votes: List[Vote] = []
        for cell in self.cells:
            csz = self._parse_csz(cell.text)
            if not csz:
                continue
            # Walk up the left-aligned lines: street, then name(s)
            stack = [cell]
            while len(stack) < 6:
                up = self._above_aligned(stack[0])
                if up is None:
                    break
                stack.insert(0, up)

            kind = self._block_kind(stack)
            if kind is None:
                continue
            parsed = self._parse_block([c.text for c in stack], csz)
            if parsed is None:
                continue
            votes += self._block_votes(kind, parsed)

            # Re-read the whole block (names through city/state/zip)
            if self.reread:
                top = stack[parsed[1][0]]
                block = Cell("", min(c.x0 for c in stack), top.y0,
                             max(c.x1 for c in stack), cell.y1)
                for text in self.reread(block, "block"):
                    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
                    for j in range(len(lines) - 1, 0, -1):
                        csz2 = self._parse_csz(lines[j])
                        if csz2:
                            reparsed = self._parse_block(lines[: j + 1], csz2)
                            if reparsed:
                                votes += self._block_votes(kind, reparsed)
                            break
        return votes

    def _block_votes(self, kind: str, parsed) -> List[Vote]:
        names, _, street, _, (city, state, zip5, zip4), conf = parsed
        name_key = "owner_names" if kind == "owner" else "recipient_name"
        votes: List[Vote] = [(f"{name_key}#n", len(names), conf)]
        votes += [(f"{name_key}#{i}", name, conf) for i, name in enumerate(names)]
        votes += [(f"{kind}_street", street, conf), (f"{kind}_city", city, conf),
                  (f"{kind}_zip", zip5, conf)]
        if state:
            votes.append((f"{kind}_state", state, conf))
        if zip4:
            votes.append((f"_{kind}_zip4", zip4, conf))
        return votes

    def _block_kind(self, stack: List[Cell]) -> Optional[str]:
        """
        "owner", "recipient", or None (tax office / unknown) from the
        nearest block label above the stack that overlaps it horizontally.
        """
        if any(self._TAX_OFFICE.search(c.text) for c in stack):
            return None
        for c in stack:
            if "OWNER" in c.text:
                return "owner"
            if "RECIPIENT" in c.text:
                return "recipient"
        top, h = stack[0], self.h
        x0, x1 = min(c.x0 for c in stack), max(c.x1 for c in stack)
        labels = [n for n in self.cells
                  if n.y1 <= top.y0 + 0.3 * h and top.y0 - n.y1 < 14 * h
                  and n.x0 < x1 + 2 * h and n.x1 > x0 - 8 * h
                  and self._BLOCK_LABEL.search(n.text)]
        if not labels:
            return None
        nearest = min(labels, key=lambda n: top.y0 - n.y1)
        if self._TAX_OFFICE.search(nearest.text):
            return None
        return "owner" if "OWNER" in nearest.text else "recipient"

    def _parse_block(self, lines: List[str], csz):
        """
        lines end with the CITY, ST ZIP line. Returns (names, name line
        indexes, street, street line index, csz, conf).
        """
        street_idx = -1
        for j in range(len(lines) - 2, max(-1, len(lines) - 4), -1):
            if self._clean_street(lines[j]):
                street_idx = j
                break
        if street_idx < 0:
            return None
        street = self._clean_street(lines[street_idx])

        names: List[str] = []
        name_idx: List[int] = []
        for k in range(street_idx - 1, max(-1, street_idx - 4), -1):
            name = self._clean_name(lines[k])
            if not name:
                break
            names.insert(0, name)
            name_idx.insert(0, k)
        if not names:
            return None
        conf = 0.90 if csz[1] else 0.75
        return names, name_idx, street, street_idx, csz, conf

    def _parse_csz(self, text: str) -> Optional[Tuple[str, str, str, str]]:
        match = self._CSZ_RE.search(text)
        state = ""
        if match:
            state = match.group("state")
        else:
            match = self._CSZ_NO_STATE_RE.search(text)
            if not match:
                return None
        zip5 = self._fix_digits(match.group("zip"))
        zip4 = self._fix_digits(match.group("zip4") or "")
        if not re.fullmatch(r"\d{5}", zip5) or (zip4 and not re.fullmatch(r"\d{4}", zip4)):
            return None
        city = match.group("city").strip(" .,")
        if len(city) < 2 or self._LABEL_WORDS.search(city):
            return None
        return city, state, zip5, zip4

    def _clean_street(self, line: str) -> str:
        """"5555 RURAL LANE" / "PO BOX 123456"; OCR-mangled numbers are fixed."""
        # Anything after a column-separator character belongs to a neighbor
        line = re.split(r"[|:+;]", line)[0].strip(" .,'-~")
        # "PO BOX", also read as "PQ BOX" / "P.O. BOX" / "PD BOX"
        po = re.match(r"^P\.?\s?[O0QD]\.?\s*B[O0]X\s+([0-9SOIB]+)\b", line)
        if po:
            number = self._fix_digits(po.group(1))
            return f"PO BOX {number}" if number.isdigit() else ""
        tokens = line.split()
        if not tokens:
            return ""
        first = tokens[0]
        # House number: digits, possibly with S/O/I/B read in place of digits
        if re.fullmatch(r"[0-9SOIB]{1,6}[A-Z]?", first) and any(c.isdigit() for c in first):
            fixed = self._fix_digits(first[:-1]) + first[-1] if first[-1].isalpha() and len(first) > 1 \
                else self._fix_digits(first)
            if not re.match(r"^\d", fixed):
                return ""
            words = [t for t in tokens[1:] if re.fullmatch(r"[A-Z0-9#.'\-]+", t)]
            # Drop trailing 1-char OCR junk
            while words and len(words[-1]) == 1 and not words[-1].isdigit():
                words.pop()
            if words:
                return " ".join([fixed] + words)
        return ""

    def _clean_name(self, line: str) -> str:
        if self._LABEL_WORDS.search(line) or re.search(r"\d{2,}", line):
            return ""
        line = re.split(r"[|:+;]", line)[0]
        words = [re.sub(r"[^A-Z.'\-&]", "", t) for t in line.split()]
        words = [w for w in words if w]
        # Trim 1-char junk from both ends, keep middle initials
        while words and len(words[-1].strip(".")) <= 1:
            words.pop()
        while words and len(words[0].strip(".")) <= 1:
            words.pop(0)
        if len(words) < 2 or sum(len(w) for w in words) < 5:
            return ""
        # The postal barcode reads as one long run of letters
        if max(len(w) for w in words) > 15:
            return ""
        return " ".join(words)


# ---------------------------------------------------------------------------
# Image preprocessor
# ---------------------------------------------------------------------------

class RegistrationRenewalPreprocessor:
    def __init__(self, config: RegistrationRenewalOCRConfig) -> None:
        self.config = config

    def find_page(self, image: np.ndarray) -> np.ndarray:
        """
        Crops to the page when the photo shows background around it. The
        page must be portrait (letter is 1.29:1) — this rejects the white
        customer-copy section, which on its own is landscape.
        """
        h, w = image.shape[:2]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        _, mask = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        img_area = float(h * w)
        for cnt in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
            area = cv2.contourArea(cnt)
            if area < img_area * 0.30 or area > img_area * 0.97:
                continue
            x, y, cw, ch = cv2.boundingRect(cnt)
            if 1.1 <= ch / float(cw) <= 1.6:
                LOGGER.info(f"Page detected at x={x} y={y} {cw}x{ch}")
                return image[y: y + ch, x: x + cw]
        return image

    def variants(self, page: np.ndarray, debug_dir: Optional[Path] = None) -> List[Tuple[str, np.ndarray]]:
        """
        Scales the page to page_target_width and erases the form's ruled
        lines. Returns grayscale and adaptive-threshold versions.
        """
        factor = min(4.0, self.config.page_target_width / float(page.shape[1]))
        scaled = cv2.resize(page, None, fx=factor, fy=factor,
                            interpolation=cv2.INTER_CUBIC if factor > 1 else cv2.INTER_AREA)
        gray = cv2.cvtColor(scaled, cv2.COLOR_BGR2GRAY)

        # Adaptive threshold: evens out shadows and drops the green/yellow tint
        bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY, 41, 15)

        # Ruled lines = long horizontal/vertical runs of ink. No character
        # is 1/25 of the page wide or 1/40 of it tall.
        ink = 255 - bw
        h, w = ink.shape
        horizontal = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
                                      cv2.getStructuringElement(cv2.MORPH_RECT, (w // 25, 1)))
        vertical = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
                                    cv2.getStructuringElement(cv2.MORPH_RECT, (1, h // 40)))
        lines = cv2.dilate(horizontal | vertical, np.ones((3, 3), np.uint8))

        gray_clean = gray.copy()
        gray_clean[lines > 0] = 255
        bw_clean = bw.copy()
        bw_clean[lines > 0] = 255

        variants = [("bw", bw_clean), ("gray", gray_clean)]
        if debug_dir:
            cv2.imwrite(str(debug_dir / "00_page.png"), page)
            for label, img in variants:
                cv2.imwrite(str(debug_dir / f"variant_{label}.png"), img)
        return variants


# ---------------------------------------------------------------------------
# Main OCR pipeline
# ---------------------------------------------------------------------------

class RegistrationRenewalOCR:
    """
    Extracts vehicle, fee and owner fields from a registration renewal notice.

    Usage:
        ocr = RegistrationRenewalOCR()
        result = ocr.extract("VRRNS.jpg")
        print(result.parsed_fields)
    """

    def __init__(self, config: Optional[RegistrationRenewalOCRConfig] = None) -> None:
        self.config = config or RegistrationRenewalOCRConfig()
        self.extractor = RegistrationFieldExtractor()
        self.preprocessor = RegistrationRenewalPreprocessor(self.config)
        # Tesseract runs as a subprocess, so threads give real parallelism
        self._pool = ThreadPoolExecutor(max(1, self.config.ocr_workers))

        if self.config.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = self.config.tesseract_cmd

    def extract(self, image_path: str | Path) -> RegistrationRenewalOCRResult:
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        image = cv2.imread(str(image_path))
        if image is None:
            raise ValueError(f"Could not load image: {image_path}")
        return self.extract_image(image, image_path)

    def extract_image(self, image: np.ndarray, source: str | Path = "camera") -> RegistrationRenewalOCRResult:
        """Same as extract() but on an in-memory BGR image (e.g. a camera capture)."""
        image_path = Path(source)

        debug_dir: Optional[Path] = None
        if self.config.save_debug:
            debug_dir = Path(self.config.debug_output_dir) / image_path.stem
            debug_dir.mkdir(parents=True, exist_ok=True)

        page = image if self.config.assume_flat_page else self.preprocessor.find_page(image)

        # votes[field][value] = list of confidences
        votes: Dict[str, Dict[object, List[float]]] = defaultdict(lambda: defaultdict(list))
        raw_log: List[str] = []

        variants = self.preprocessor.variants(page, debug_dir)
        # Re-reads use the grayscale page: thresholding breaks up the thin
        # strokes of the notice's monospace font
        reread = self._make_reread(dict(variants)["gray"]) if self.config.reread_cells else None

        # All full-page passes at once; field extraction (and its re-reads)
        # then goes through them in the original order
        passes = [(prep_label, psm, self._pool.submit(self._run_ocr, processed, psm))
                  for prep_label, processed in variants if prep_label in self.config.page_variants
                  for psm in self.config.psm_modes]
        pages = []
        for prep_label, psm, future in passes:
            data = future.result()
            if data is None:
                continue
            cells, med_h = build_cells(data, self.config.cell_gap_factor)
            raw_log.append(f"===== [{prep_label}/psm{psm}] text height {med_h:.0f}px =====\n"
                           f"{cells_to_text(cells)}\n")
            # Queue this page's re-reads now, while the other passes still run
            if reread:
                self.extractor.extract(cells, med_h, reread.prefetch)  # type: ignore[attr-defined]
            pages.append((prep_label, psm, cells, med_h))

        for prep_label, psm, cells, med_h in pages:
            found = self.extractor.extract(cells, med_h, reread)
            for key, value, conf in found:
                votes[key][value].append(conf)
            LOGGER.info(f"[{prep_label}/psm{psm}] {len(cells)} cells, "
                        + ", ".join(sorted({k for k, _, _ in found})))

        fields = self._resolve(votes)

        filled = sum(1 for k in REQUIRED_KEYS if fields.get(k))
        if filled == len(REQUIRED_KEYS):
            recommendation = "All required fields extracted successfully."
        elif filled >= len(REQUIRED_KEYS) // 2:
            missing = [k for k in REQUIRED_KEYS if not fields.get(k)]
            recommendation = (f"Partial extraction ({filled}/{len(REQUIRED_KEYS)} required fields). "
                              f"Check manually: {', '.join(missing)}.")
        else:
            recommendation = "Few fields extracted. Try retaking with better lighting or a flatter page."

        raw_all = "\n".join(raw_log)
        if debug_dir:
            (debug_dir / "all_raw_text.txt").write_text(raw_all, encoding="utf-8")
            (debug_dir / "parsed_fields.json").write_text(json.dumps(fields, indent=2), encoding="utf-8")

        return RegistrationRenewalOCRResult(
            source_path=image_path,
            scan_success=filled >= len(REQUIRED_KEYS) // 2,
            raw_text=raw_all,
            parsed_fields=fields,
            recommendation=recommendation,
        )

    # ------------------------------------------------------------------
    # Voting
    # ------------------------------------------------------------------

    @staticmethod
    def _winner(by_value: Dict[object, List[float]]) -> Tuple[object, float, int]:
        """
        (value, confidence, number of reads) of the value with the highest
        summed confidence. Its confidence is its best single read, raised
        when several reads agree and lowered when the vote was contested.
        """
        ranked = sorted(by_value.items(), key=lambda kv: sum(kv[1]), reverse=True)
        value, confs = ranked[0]
        total = sum(sum(c) for _, c in ranked)
        agreement = sum(confs) / total if total else 0.0
        conf = min(0.99, max(confs) * (0.85 + 0.15 * agreement) + 0.02 * (len(confs) - 1))
        return value, round(conf, 2), len(confs)

    def _resolve(self, votes: Dict[str, Dict[object, List[float]]]) -> Dict[str, object]:
        fields: Dict[str, object] = {k: "" for k in FIELD_KEYS}
        fields["owner_names"] = []
        confidence: Dict[str, float] = {k: 0.0 for k in FIELD_KEYS}
        field_votes: Dict[str, int] = {k: 0 for k in FIELD_KEYS}

        for key in FIELD_KEYS:
            if votes.get(key):
                fields[key], confidence[key], field_votes[key] = self._winner(votes[key])

        if votes.get("vehicle_vin"):
            vin, vin_conf = self._resolve_vin(votes["vehicle_vin"])
            if vin:
                fields["vehicle_vin"], confidence["vehicle_vin"] = vin, vin_conf

        # Names are voted line by line: first the number of lines, then each line
        for key in ("owner_names", "recipient_name"):
            if not votes.get(f"{key}#n"):
                continue
            count, _, _ = self._winner(votes[f"{key}#n"])
            names, confs = [], []
            for i in range(int(count)):
                if votes.get(f"{key}#{i}"):
                    name, conf, n = self._winner(votes[f"{key}#{i}"])
                    names.append(name)
                    confs.append(conf)
                    field_votes[key] = max(field_votes[key], n)
            fields[key] = names if key == "owner_names" else " / ".join(names)
            confidence[key] = min(confs) if confs else 0.0

        # Zip+4, when the reads that had it agree
        for kind in ("owner", "recipient"):
            if fields[f"{kind}_zip"] and votes.get(f"_{kind}_zip4"):
                zip4, _, _ = self._winner(votes[f"_{kind}_zip4"])
                fields[f"{kind}_zip"] = f"{fields[f'{kind}_zip']}-{zip4}"

        self._resolve_fees(votes, fields, confidence)

        # The registration runs one year: derive whichever expiration is missing
        # (the customer copy with the current expiration may be out of frame)
        if fields["current_expiration"] and not fields["new_expiration"]:
            mon, year = str(fields["current_expiration"]).split()
            fields["new_expiration"] = f"{mon} {int(year) + 1}"
            confidence["new_expiration"] = 0.60
        elif fields["new_expiration"] and not fields["current_expiration"]:
            mon, year = str(fields["new_expiration"]).split()
            fields["current_expiration"] = f"{mon} {int(year) - 1}"
            confidence["current_expiration"] = 0.60

        # The notice is mailed to the owner — use the recipient block when
        # no owner block was read
        if not fields["owner_names"] and fields["recipient_name"]:
            fields["owner_names"] = str(fields["recipient_name"]).split(" / ")
            confidence["owner_names"] = 0.60
            for part in ("street", "city", "state", "zip"):
                if not fields[f"owner_{part}"] and fields[f"recipient_{part}"]:
                    fields[f"owner_{part}"] = fields[f"recipient_{part}"]
                    confidence[f"owner_{part}"] = 0.60

        # The recipient is usually the owner — prefer the owner's spelling
        # of a name line when they nearly match
        if fields["recipient_name"] and fields["owner_names"]:
            fixed = []
            for rname in str(fields["recipient_name"]).split(" / "):
                close = [o for o in fields["owner_names"]  # type: ignore[union-attr]
                         if SequenceMatcher(None, rname, o).ratio() >= 0.85]
                fixed.append(close[0] if close else rname)
            fields["recipient_name"] = " / ".join(fixed)

        # Derived fields
        vin = str(fields["vehicle_vin"])
        if vin:
            fields["vehicle_vin_last6"] = vin[-6:]
            confidence["vehicle_vin_last6"] = confidence["vehicle_vin"]
        code = str(fields["vehicle_make_code"])
        if code:
            fields["vehicle_make"] = MAKE_CODES.get(code, code.title())
            confidence["vehicle_make"] = confidence["vehicle_make_code"] if code in MAKE_CODES \
                else round(confidence["vehicle_make_code"] * 0.8, 2)

        fields["field_confidence"] = confidence
        fields["field_votes"] = field_votes
        return fields

    def _resolve_vin(self, by_value: Dict[object, List[float]]) -> Tuple[str, float]:
        """
        Combines the 17-char VIN reads position by position (weighted by
        confidence). If that fails the check digit, tries the runner-up
        characters at the disputed positions - one swap, then two - and
        keeps the valid VIN with the most vote weight.
        """
        reads = [(str(v), sum(c)) for v, c in by_value.items() if len(str(v)) == 17]
        if not reads:
            return "", 0.0
        position_votes: List[Dict[str, float]] = [defaultdict(float) for _ in range(17)]
        for vin, weight in reads:
            for i, ch in enumerate(vin):
                position_votes[i][ch] += weight

        ranked = [sorted(pv.items(), key=lambda kv: kv[1], reverse=True) for pv in position_votes]
        consensus = "".join(r[0][0] for r in ranked)
        check = self.extractor._vin_check_digit_ok
        if check(consensus):
            return consensus, 0.97

        def weight(vin: str) -> float:
            return sum(position_votes[i][ch] for i, ch in enumerate(vin))

        # Only swaps OCR plausibly makes; a random swap passes the check
        # digit 1 time in 11
        # (3S and 8S: the notice's monospace font at ~250 ppi, 2nd live scan)
        confusable = {frozenset(p) for p in ("JU", "38", "8B", "5S", "3S", "8S", "0D", "1T", "7T",
                                             "2Z", "6G", "1L", "UV", "MN", "0U", "HN", "4A", "1J")}
        disputed = [(i, ch) for i, r in enumerate(ranked) for ch, _ in r[1:3]
                    if frozenset((ch, consensus[i])) in confusable]
        candidates = [consensus[:i] + ch + consensus[i + 1:] for i, ch in disputed]
        if not any(check(c) for c in candidates):
            for (i, a), (j, b) in product(disputed, disputed):
                if i < j:
                    v = list(consensus)
                    v[i], v[j] = a, b
                    candidates.append("".join(v))
        valid = [c for c in candidates if check(c)]
        if valid:
            return max(valid, key=weight), 0.90
        # No valid VIN (a pre-1981 or sample VIN, or too many misreads):
        # the most-voted whole read, flagged lower. Mixing positions from
        # different reads is only safe when the check digit confirms it.
        best_read = max(reads, key=lambda r: r[1])[0]
        return best_read, 0.70

    def _resolve_fees(self, votes, fields: Dict[str, object], confidence: Dict[str, float]) -> None:
        """
        Registration fee + local fee = total fee. Among the top-voted
        candidates of each, the consistent combination with the most
        support replaces the independent winners. (Newer notices print
        only the total; then nothing is checked.)
        """
        def top(key: str) -> List[Tuple[str, float]]:
            ranked = sorted(votes.get(key, {}).items(), key=lambda kv: sum(kv[1]), reverse=True)
            return [(v, sum(c)) for v, c in ranked[:4]]

        best = None
        best_support = 0.0
        for (reg, rs), (loc, ls), (tot, ts) in product(top("registration_fee"), top("local_fee"),
                                                       top("total_fee")):
            if abs(float(reg) + float(loc) - float(tot)) < 0.005 and rs + ls + ts > best_support:
                best, best_support = (reg, loc, tot), rs + ls + ts
        if best:
            for key, value in zip(("registration_fee", "local_fee", "total_fee"), best):
                fields[key] = value
                confidence[key] = max(confidence[key], 0.95)
            return

        # Total not read, but both parts were
        if not fields["total_fee"] and fields["registration_fee"] and fields["local_fee"]:
            fields["total_fee"] = f"{float(fields['registration_fee']) + float(fields['local_fee']):.2f}"
            confidence["total_fee"] = 0.70

    # kind -> (character whitelist, PSM modes, scales, also try blurred)
    _REREAD_SETTINGS = {
        # VINs never contain I, O or Q
        "vin": ("ABCDEFGHJKLMNPRSTUVWXYZ0123456789", (7, 8), (1.0, 2.0), False),
        "plate": ("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", (7, 8), (1.0, 2.0), False),
        "block": ("", (6,), (0.75, 1.0, 2.0), True),
    }

    def _make_reread(self, image: np.ndarray) -> Reread:
        """
        Re-reads a cell's crop of `image` with the settings for its kind.
        Results are cached by position, so the same spot found again by a
        later pass isn't OCR'd again.

        `reread.prefetch(cell, kind)` only queues the re-read and returns [].
        The re-reads a page needs depend only on its cells, never on re-read
        results, so a prefetch run of the extractor queues exactly the
        re-reads the real run will ask for — all of them at once instead of
        one cell after another.
        """
        # key -> futures of the tries, in order; texts once collected
        pending: Dict[Tuple[str, int, int], list] = {}
        cache: Dict[Tuple[str, int, int], List[str]] = {}

        def prefetch(cell: Cell, kind: str) -> List[str]:
            key = (kind, cell.x0 // 25, cell.y0 // 25)
            if key not in pending:
                pending[key] = submit(cell, kind)
            return []

        def reread(cell: Cell, kind: str) -> List[str]:
            key = (kind, cell.x0 // 25, cell.y0 // 25)
            if key in cache:
                return cache[key]
            futures = pending.pop(key) if key in pending else submit(cell, kind)
            texts: List[str] = []
            cache[key] = texts
            for text in (f.result() for f in futures):
                if text:
                    # Keep line breaks for blocks, one line otherwise
                    texts.append("\n".join(re.sub(r"[ \t]+", " ", ln)
                                           for ln in text.upper().splitlines())
                                 if kind == "block" else re.sub(r"\s+", " ", text.upper()))
            return texts

        def submit(cell: Cell, kind: str) -> list:
            pad = int((cell.y1 - cell.y0) * 0.6) if kind != "block" else 20
            h, w = image.shape[:2]
            crop = image[max(0, cell.y0 - pad): min(h, cell.y1 + pad),
                         max(0, cell.x0 - pad): min(w, cell.x1 + pad)]
            if crop.size == 0:
                return []
            whitelist, psms, factors, blur = self._REREAD_SETTINGS[kind]
            override = (self.config.reread_overrides or {}).get(kind)
            if override:
                factors, psms, blur = override
            extra = f" -c tessedit_char_whitelist={whitelist}" if whitelist else ""
            extra += f" {self.config.tesseract_extra}" if self.config.tesseract_extra else ""
            tries = []
            for factor in factors:
                scaled = crop if factor == 1.0 else cv2.resize(
                    crop, None, fx=factor, fy=factor,
                    interpolation=cv2.INTER_CUBIC if factor > 1 else cv2.INTER_AREA)
                images = [scaled, cv2.GaussianBlur(scaled, (3, 3), 0)] if blur else [scaled]
                tries += [(img, psm) for img in images for psm in psms]

            def read(img: np.ndarray, psm: int) -> str:
                try:
                    return pytesseract.image_to_string(
                        img, lang=self.config.language, config=f"--psm {psm}{extra}").strip()
                except Exception as e:
                    LOGGER.debug(f"Re-read OCR error: {e}")
                    return ""

            # All tries at once, collected in the original order
            return [self._pool.submit(read, img, psm) for img, psm in tries]

        reread.prefetch = prefetch  # type: ignore[attr-defined]
        return reread

    def _run_ocr(self, image: np.ndarray, psm: int) -> Optional[Dict[str, list]]:
        try:
            return pytesseract.image_to_data(
                image, lang=self.config.language, config=f"--psm {psm} {self.config.tesseract_extra}".strip(),
                output_type=pytesseract.Output.DICT,
            )
        except Exception as e:
            LOGGER.debug(f"OCR error: {e}")
            return None


# ---------------------------------------------------------------------------
# Logging + entry point
# ---------------------------------------------------------------------------

def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )


if __name__ == "__main__":
    import sys

    configure_logging()

    ocr = RegistrationRenewalOCR(
        RegistrationRenewalOCRConfig(
            tesseract_cmd=r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            save_debug=True,
            debug_output_dir="rrn_debug",
        )
    )

    test_images = sys.argv[1:] or [p for p in ("VRRNS.jpg", "test_rrn_live1.jpg") if Path(p).exists()]
    for test_image in test_images:
        print(f"\n{'=' * 72}")
        print(f"TEST IMAGE: {test_image}")
        print(f"{'=' * 72}")
        result = ocr.extract(test_image)
        print("===== SCAN METADATA =====")
        print("Scan success  :", result.scan_success)
        print("Recommendation:", result.recommendation)
        print()
        print("===== EXTRACTED FIELDS =====")
        fields = dict(result.parsed_fields)
        conf = fields.pop("field_confidence")
        fields.pop("field_votes")
        for key, value in fields.items():
            print(f"  {key:20s} {str(value):40s} {conf.get(key, 0):.2f}")
