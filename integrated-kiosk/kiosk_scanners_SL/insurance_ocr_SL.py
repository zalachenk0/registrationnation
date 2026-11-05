# ============================================================================
# File:         insurance_ocr_SL.py
# Written by:   SL (Seth Leija)
# What it does: Insurance card text recognition and field parsing.
# Team-written: Logic written by SL (Seth Leija). Outside code is used only
#               through the third-party sources listed below.
# Third-party:  OpenCV; NumPy; pytesseract (Tesseract OCR); Tesseract OCR
#               (through pytesseract).
# ============================================================================
from __future__ import annotations

"""
insurance_ocr_SL.py  (copied from INS Code/insurance_card_ocr_rev7.py)

Changes vs rev6:
- SPEED: OCR passes run in parallel (one Tesseract process per CPU core)
  and are scored in the original order, so the result — including which
  pass triggers the early stop — is identical to running them one by one.
  ~4x faster on the kiosk PC (8 cores).
- SPEED: Detected-card crops (auto_card*, lower_left*) are tried first.
  On the test documents the early stop now comes within 13 passes
  instead of up to 49, with the same fields.
- FIX: Company after the "Insurance Company" label: a name with an
  insurance keyword on the label line OR the next line now wins over
  keyword-less text. Keyword-less values are rejected if they read like
  a sentence or are mostly lowercase. Live captures had merged back-panel
  text into the label line ("responsibility. Failure to do so...",
  "un bi sion") while the real name sat on the next line.
- FIX: Model tokenizer no longer strips digits before the VIN fragment
  check. rev6 turned "5NPD84LF5HH123456" into "NPD LF HH", so the fragment
  filter never saw the digits and "Npd" was appended to the model.
- FIX: VIN fragment check now also rejects long (8+ char) letter/digit mixes,
  catching OCR-mangled VINs like "SNPD84LFSHH123456" (5 read as S).
- FIX: Added "lower_left" crop candidates — the left half of the lower part
  of the detected card. Needed when the white region is a portrait phone
  screen containing a side-by-side front/back card, where rev6's width
  checks never triggered the left-panel split.
- FIX: Early stop now exits ALL candidate/variant loops (rev6's break only
  exited the PSM loop, so every candidate still ran).
- FIX: Best result is chosen by (fields found, total confidence) instead of
  fields found alone, and early stop requires all 5 fields at >= 0.85
  confidence. A low-confidence fallback expiration no longer wins.
- ADD: insurance_company and policy_number fields, found by their labels
  ("Insurance Company", "Policy Number") with OCR-error tolerance. The
  company falls back to a "... INSURANCE COMPANY" style signature.
  FIELD_KEYS lists all 7 fields; scoring and early stop use it.
- ADD: extract_image() for in-memory images (camera captures).
- ADD: page_lower_left / page_left crop candidates for flattened page
  captures (full portrait page, or card half folded at the dashed line).
- FIX: Test image list deduplicated — Windows filenames are case-insensitive,
  so rev6 ran each image twice (.JPG and .jpg).

Changes in rev6 (vs rev5):
- FIX: Model extraction now strips VIN fragment tokens — Tesseract reads
  the VIN prefix (e.g. "5NPD" from "5NPD84LF5HH123456") as a separate
  word and it was being appended to the model name. Tokens that are 3+
  chars of purely uppercase digits/letters and match VIN character patterns
  are now filtered out of the model token list.
- FIX: Expiration date now always returns the LATEST date found after the
  expiration label — on two-column cards the effective and expiration dates
  appear on the same line and the order can be ambiguous. Taking the max
  ensures we always return the later (expiration) date.
- FIX: Fallback expiration (when no label found) now also always takes the
  latest date across all lines.
- KEEP: All rev5 layout-agnostic extraction logic, 50+ expiration label
  variants, global make search, noise token filtering.
"""

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
import pytesseract

LOGGER = logging.getLogger(__name__)

# Fields collected for the kiosk form, in display order
FIELD_KEYS = (
    "insurance_company", "policy_number", "insurance_expiration",
    "vehicle_year", "vehicle_make", "vehicle_model", "vehicle_vin_last6",
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InsuranceCardOCRConfig:
    tesseract_cmd: Optional[str] = None
    language: str = "eng"
    save_debug: bool = True
    debug_output_dir: str = "insurance_debug"
    # PSM 6 = uniform block, PSM 11 = sparse text, PSM 4 = single column
    psm_modes: tuple = (6, 11, 4)
    upscale_factor: float = 2.5   # insurance card text is small — upscale more
    # Tesseract processes run at once. Measured 16 passes: 22.5 s one at a
    # time, 7.2 s with 4, 5.5 s with 8 (8-core kiosk PC)
    ocr_workers: int = os.cpu_count() or 4


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class InsuranceCardOCRResult:
    source_path: Path
    scan_success: bool
    raw_text: str
    parsed_fields: Dict[str, object]
    recommendation: str


# ---------------------------------------------------------------------------
# Field extractor
# ---------------------------------------------------------------------------

class InsuranceFieldExtractor:
    """
    Layout-agnostic field extractor for automobile insurance cards.

    Each field is found independently by its data signature rather than
    by its position on the card. This makes the extractor work across
    all insurer layouts without any per-insurer configuration.

    Field signatures:
      VIN        — exactly 17 alphanumeric chars, no I/O/Q (AAMVA standard)
      Year       — 4-digit number 1980–2035 on the same line as the VIN
      Make       — matches KNOWN_MAKES set, searched globally across all lines
      Model      — 1–2 tokens immediately after the make token on the same line
      Expiration — date appearing after any of 50+ known expiration label variants
    """

    # 17-char VIN: alphanumeric, no I/O/Q per AAMVA standard
    _VIN_RE = re.compile(r"\b([A-HJ-NPR-Z0-9]{17})\b")

    # Date: MM/DD/YY or MM/DD/YYYY
    _DATE_RE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b")
    # Month-name dates: "Apr 20, 2027", "Sept. 5 2026", "October 20, 2026" (Progressive)
    _TEXT_DATE_RE = re.compile(
        r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})\b",
        re.IGNORECASE,
    )
    _MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")

    # Year: 4-digit 1980–2035
    _YEAR_RE = re.compile(r"\b((?:19[89]\d|20[0-3]\d))\b")

    # Known vehicle manufacturers — comprehensive set
    KNOWN_MAKES = {
        "ACURA", "ALFA", "ASTON", "AUDI", "BENTLEY", "BMW", "BUICK",
        "CADILLAC", "CHEVROLET", "CHEVY", "CHRYSLER", "DODGE", "FERRARI",
        "FIAT", "FORD", "GENESIS", "GMC", "HONDA", "HYUNDAI", "INFINITI",
        "JAGUAR", "JEEP", "KIA", "LAMBORGHINI", "LAND", "LEXUS", "LINCOLN",
        "MASERATI", "MAZDA", "MERCEDES", "BENZ", "MINI", "MITSUBISHI",
        "NISSAN", "PORSCHE", "RAM", "RIVIAN", "ROMEO", "ROVER", "SUBARU",
        "TESLA", "TOYOTA", "VOLKSWAGEN", "VOLVO", "VW", "PONTIAC", "SATURN",
        "HUMMER", "OLDSMOBILE", "MERCURY", "SCION", "ISUZU", "SUZUKI",
        "FREIGHTLINER", "PETERBILT", "KENWORTH", "INTERNATIONAL",
    }

    # Two-word makes that must be matched together
    TWO_WORD_MAKES = {
        "LAND ROVER", "ALFA ROMEO", "ASTON MARTIN", "ROLLS ROYCE",
    }

    # Expiration label patterns — 50+ variants across major US insurers
    # Covers: USAA, State Farm, Geico, Progressive, Allstate, Farmers,
    # USAA, Nationwide, Liberty Mutual, Travelers, AAA, and others.
    _EXP_LABELS = re.compile(
        r"(?:"
        r"expir\w*\s*date"           # Expiration Date, Expiry Date
        r"|expir\w*"                 # Expiration, Expires, Expiry
        r"|exp\.?\s*date"            # Exp. Date, Exp Date
        r"|exp\.?\s*\d"              # Exp. 09/11/26 (label directly before date)
        r"|policy\s+exp\w*"          # Policy Expiration, Policy Exp
        r"|coverage\s+exp\w*"        # Coverage Expiration, Coverage Expires
        r"|valid\s+(?:through|thru|until|to)"  # Valid Through, Valid Until
        r"|good\s+(?:through|thru|until|to)"   # Good Through, Good Until
        r"|eff\w*[/\-]\s*exp\w*"     # Eff/Exp, Effective/Expiration
        r"|to\s*:"                   # To: (some cards show Eff: / To:)
        r"|(?:policy|coverage)\s+period"  # Policy Period: Oct 20, 2026 - Apr 20, 2027 (Progressive)
        r"|policy\s+term"            # Policy Term
        r"|end\s+date"               # End Date
        r"|termination\s+date"       # Termination Date
        r"|thru"                     # Thru (short form)
        r")",
        re.IGNORECASE,
    )

    # Noise tokens that are never a vehicle make or model
    _NOISE_TOKENS = {
        "INSURANCE", "CARD", "POLICY", "NUMBER", "DATE", "EFFECTIVE",
        "EXPIRATION", "EXPIRES", "NAME", "ADDRESS", "INSURED", "COMPANY",
        "VEHICLE", "IDENTIFICATION", "LIABILITY", "TEXAS", "STATE",
        "COVERAGE", "CONTACT", "EMERGENCY", "USAA", "GEICO", "PROGRESSIVE",
        "ALLSTATE", "FARMERS", "NATIONWIDE", "TRAVELERS", "STATEFARM",
        "CLASS", "TYPE", "YEAR", "MAKE", "MODEL", "VIN", "DRIVER",
        "LICENSE", "ROADSIDE", "ASSISTANCE", "ADDITIONAL", "COPIES",
        "MOTOR", "SAFETY", "RESPONSIBILITY", "ACT", "MINIMUM", "AMOUNTS",
        "PROVIDES", "SPECIFIED", "NAMED", "PERSONS", "VEHICLES",
    }

    # "Insurance Company" / "Insurer" / "Company Name" label. OCR can mangle
    # the first letter ("nsurance Company"), so the leading I is optional.
    _COMPANY_LABEL = re.compile(
        r"\b(?:i?nsurance\s+company|insurer|company\s+name|underwritten\s+by)\b\s*:?",
        re.IGNORECASE,
    )

    # Company name signature: up to 4 name words, an insurance keyword, then a
    # corporate suffix — "USAA CASUALTY INSURANCE COMPANY", "STATE FARM MUTUAL
    # AUTOMOBILE INSURANCE COMPANY". The suffix is required so sentences like
    # "a copy of your insurance" or "Texas Liability Insurance Card" don't match.
    _COMPANY_RE = re.compile(
        r"\b(?:[A-Z][A-Z&.'\-]*\s+){0,4}?"
        r"(?:INSURANCE|CASUALTY|INDEMNITY|MUTUAL|ASSURANCE|UNDERWRITERS)\s+"
        r"(?:[A-Z]+\s+){0,2}?"
        r"(?:COMPANY|CO\b\.?|CORPORATION|CORP\b\.?|EXCHANGE|GROUP|ASSOCIATION)\b",
    )

    # "Policy Number" / "Policy No." / "Policy #" — tolerant of OCR errors in
    # the second word ("Policy Numer"); the first word must read as "Pol..y".
    # Not "Policy Period/Term/Effective/...": Progressive's "Policy Period:
    # Oct 20, 2026 - Apr 20, 2027" line comes first and was read as "20 2026".
    _POLICY_LABEL = re.compile(
        r"\bpo[l1i][i1l]?c?y(?!\s*(?:period|term|eff\w*|exp\w*|date|holder|type))"
        r"\s*(?:n\w*\.?|#|id)?\s*:?",
        re.IGNORECASE,
    )

    def extract(self, raw_text: str) -> Dict[str, object]:
        result: Dict[str, object] = {k: "" for k in FIELD_KEYS}
        result["field_confidence"] = {k: 0.0 for k in FIELD_KEYS}

        if not raw_text.strip():
            return result

        lines = self._normalize_lines(raw_text)

        # --- VIN (most distinctive signature — find first) ---
        vin, vin_line_idx = self._find_vin(lines)
        if vin:
            result["vehicle_vin_last6"] = vin[-6:]
            result["field_confidence"]["vehicle_vin_last6"] = 0.95  # type: ignore[index]

        # --- Year (on VIN line, or nearby, or standalone) ---
        year, year_conf = self._find_year(lines, vin_line_idx)
        result["vehicle_year"] = year
        result["field_confidence"]["vehicle_year"] = year_conf  # type: ignore[index]

        # --- Make and Model (global search — layout independent) ---
        make, model, make_conf, model_conf = self._find_make_model(lines)
        result["vehicle_make"] = make
        result["vehicle_model"] = model
        result["field_confidence"]["vehicle_make"] = make_conf  # type: ignore[index]
        result["field_confidence"]["vehicle_model"] = model_conf  # type: ignore[index]

        # --- Expiration (label-based search — layout independent) ---
        exp, exp_conf = self._find_expiration(lines)
        result["insurance_expiration"] = exp
        result["field_confidence"]["insurance_expiration"] = exp_conf  # type: ignore[index]

        # --- Insurance company and policy number (label-based) ---
        company, company_conf = self._find_company(lines)
        result["insurance_company"] = company
        result["field_confidence"]["insurance_company"] = company_conf  # type: ignore[index]

        policy, policy_conf = self._find_policy_number(lines)
        result["policy_number"] = policy
        result["field_confidence"]["policy_number"] = policy_conf  # type: ignore[index]

        return result

    # ------------------------------------------------------------------
    # Insurance company
    # ------------------------------------------------------------------

    def _find_company(self, lines: List[str]) -> Tuple[str, float]:
        """
        1. Value after an "Insurance Company" label (same line or next line)
        2. Fallback: any line with a company-name signature
           ("... CASUALTY INSURANCE COMPANY", "... MUTUAL ...")
        """
        for idx, line in enumerate(lines):
            label = self._COMPANY_LABEL.search(line)
            if not label:
                continue
            # "STATE FARM MUTUAL AUTOMOBILE INSURANCE COMPANY" contains the
            # label words itself — then the line is the name, not a label
            if line[: label.start()].strip():
                named = self._COMPANY_RE.search(line.upper())
                if named:
                    return named.group(0).strip(), 0.90
                continue
            # The value is after the label or on the next line. A name with an
            # insurance keyword wins on either line: a live capture read
            # back-panel junk after the label ("Insurance Company un bi sion")
            # with the real name on the next line.
            values = [line[label.end():]]
            if idx + 1 < len(lines) and not self._POLICY_LABEL.search(lines[idx + 1]):
                values.append(lines[idx + 1])
            for text in values:
                named = self._COMPANY_RE.search(text.upper())
                if named:
                    return named.group(0).strip(), 0.90
            for text in values:
                name = self._clean_company(text)
                if name:
                    return name, 0.90

        for line in lines:
            match = self._COMPANY_RE.search(line.upper())
            if match:
                return match.group(0).strip(), 0.70
        return "", 0.0

    def _clean_company(self, text: str) -> str:
        """
        Keeps the company name and drops anything OCR merged in from a
        neighboring column (e.g. "USAA CASUALTY INSURANCE COMPANY $1,000, ...").
        """
        text = text.strip(" :-|")
        match = self._COMPANY_RE.search(text.upper())
        if match:
            return match.group(0).strip()
        # No signature word: keep leading words up to the first non-name character
        words = re.match(r"^[A-Za-z&.'\- ]+", text)
        name = words.group(0).strip() if words else ""
        # Reject prose merged in from the card's back panel ("responsibility.
        # Failure to do so could result in fi..." on a live kiosk capture):
        # a sentence break or more words than any company name has
        if re.search(r"[a-z]\.\s+[A-Za-z]", name) or len(name.split()) > 5:
            return ""
        # Company names are printed in capitals; mostly-lowercase text is OCR
        # noise from a neighboring column ("un bi sion", "ee a")
        letters = [c for c in name if c.isalpha()]
        if sum(c.islower() for c in letters) > len(letters) / 2:
            return ""
        return name if len(letters) >= 3 else ""

    # ------------------------------------------------------------------
    # Policy number
    # ------------------------------------------------------------------

    def _find_policy_number(self, lines: List[str]) -> Tuple[str, float]:
        """
        Value after a "Policy Number" label — same line if it has digits
        ("Policy #: 123-456"), otherwise the next line. On table layouts the
        next line also holds the effective/expiration dates, so the value
        stops at the first date.
        """
        for idx, line in enumerate(lines):
            label = self._POLICY_LABEL.search(line)
            if not label:
                continue
            for candidate in (line[label.end():], lines[idx + 1] if idx + 1 < len(lines) else ""):
                policy = self._clean_policy(candidate)
                if policy:
                    return policy, 0.90
        return "", 0.0

    def _clean_policy(self, text: str) -> str:
        for date_re in (self._DATE_RE, self._TEXT_DATE_RE):
            date = date_re.search(text)
            if date:
                text = text[: date.start()]
        tokens = []
        for raw in text.split():
            # A date-like token that OCR mangled ("Oo11/26") ends the number
            if "/" in raw and tokens:
                break
            tok = re.sub(r"[^A-Za-z0-9\-]", "", raw)
            # A token without digits ends the number ("Effective", "Date")
            if not tok or not any(c.isdigit() for c in tok):
                if tokens:
                    break
                continue
            tokens.append(tok.upper())
        policy = " ".join(tokens)
        return policy if sum(c.isdigit() for c in policy) >= 5 else ""

    # ------------------------------------------------------------------
    # Normalization
    # ------------------------------------------------------------------

    def _normalize_lines(self, raw_text: str) -> List[str]:
        lines = []
        for line in raw_text.splitlines():
            cleaned = re.sub(r"\s+", " ", line).strip()
            if len(re.sub(r"[^A-Za-z0-9]", "", cleaned)) >= 2:
                lines.append(cleaned)
        return lines

    # ------------------------------------------------------------------
    # VIN
    # ------------------------------------------------------------------

    def _find_vin(self, lines: List[str]) -> Tuple[str, int]:
        # Pass 1: exact 17-char VIN match
        for idx, line in enumerate(lines):
            match = self._VIN_RE.search(line.upper())
            if match:
                return self._correct_vin_ocr_errors(match.group(1)), idx

        # Pass 2: look for 17-char alphanumeric tokens with OCR error tolerance
        for idx, line in enumerate(lines):
            for token in line.upper().split():
                token_clean = re.sub(r"[^A-Z0-9]", "", token)
                if len(token_clean) == 17:
                    return self._correct_vin_ocr_errors(token_clean), idx

        # Pass 3: 16-char token (one char dropped by OCR)
        for idx, line in enumerate(lines):
            for token in line.upper().split():
                token_clean = re.sub(r"[^A-Z0-9]", "", token)
                if len(token_clean) == 16 and re.match(r"^[A-HJ-NPR-Z0-9]+$", token_clean):
                    return self._correct_vin_ocr_errors(token_clean), idx

        return "", -1

    def _correct_vin_ocr_errors(self, vin: str) -> str:
        """Replace AAMVA-prohibited chars that Tesseract commonly substitutes."""
        result = []
        for ch in vin:
            if ch == "O":
                result.append("0")
            elif ch == "I":
                result.append("1")
            elif ch == "Q":
                result.append("0")
            else:
                result.append(ch)
        return "".join(result)

    # ------------------------------------------------------------------
    # Year
    # ------------------------------------------------------------------

    def _find_year(self, lines: List[str], vin_line_idx: int) -> Tuple[str, float]:
        # First: the VIN line, then the lines nearest it, up to ±2 (most
        # common placement). Dates are cut out first: on table layouts the
        # policy dates sit just above the vehicle row, and "09/11/2025" was
        # read as the year. Only when a VIN was found (-1 means none).
        if vin_line_idx >= 0:
            for offset in (0, -1, 1, -2, 2):
                idx = vin_line_idx + offset
                if not 0 <= idx < len(lines):
                    continue
                line = self._TEXT_DATE_RE.sub(" ", self._DATE_RE.sub(" ", lines[idx]))
                match = self._YEAR_RE.search(line)
                if match:
                    return match.group(1), 0.92

        # Fallback: global search — some cards put year on a separate header line
        for line in lines:
            # Apply the same date exclusion when the VIN was unreadable or
            # the vehicle year is elsewhere on the page. Keep other row text.
            line = self._TEXT_DATE_RE.sub(" ", self._DATE_RE.sub(" ", line))
            match = self._YEAR_RE.search(line)
            if match:
                return match.group(1), 0.75

        return "", 0.0

    # ------------------------------------------------------------------
    # Make / Model — global, layout-independent
    # ------------------------------------------------------------------

    def _find_make_model(self, lines: List[str]) -> Tuple[str, str, float, float]:
        """
        Searches every line for a known vehicle make token.
        Does not anchor to VIN line position — works regardless of layout.
        Returns the first match found, preferring lines with both make and model.
        """
        best: Optional[Tuple[str, str, float, float]] = None
        best_score = 0

        for line in lines:
            upper = line.upper()

            # Skip lines that are clearly label/header rows
            label_count = sum(1 for t in self._NOISE_TOKENS if t in upper)
            if label_count >= 2:
                continue

            # Check for two-word makes first
            for two_word in self.TWO_WORD_MAKES:
                if two_word in upper:
                    model = self._extract_model_after(upper, two_word)
                    score = 2 + (1 if model else 0)
                    if score > best_score:
                        best_score = score
                        best = (two_word.title(), model, 0.92, 0.85 if model else 0.0)

            # Check single-word makes. Keep digits so VIN fragments stay
            # intact and can be rejected by _is_vin_fragment below.
            tokens = re.sub(r"[^A-Z0-9\s\-]", " ", upper).split()
            for i, tok in enumerate(tokens):
                if tok not in self.KNOWN_MAKES:
                    continue
                # Verify it's not a noise token masquerading as a make
                if tok in self._NOISE_TOKENS:
                    continue

                # Gather model tokens: non-noise alpha tokens after the make
                model_tokens = []
                for j in range(i + 1, min(i + 4, len(tokens))):
                    if tokens[j] in self._NOISE_TOKENS:
                        break
                    if not re.match(r"^[A-Z0-9\-]+$", tokens[j]):
                        break
                    if len(tokens[j]) < 2:
                        continue
                    # Skip tokens that look like VIN fragments:
                    # 3+ chars that are all uppercase alphanumeric and
                    # contain both letters and digits (VIN-like pattern)
                    if self._is_vin_fragment(tokens[j]):
                        break
                    model_tokens.append(tokens[j])
                    if len(model_tokens) == 2:
                        break

                model = " ".join(t.title() for t in model_tokens)
                score = 1 + len(model_tokens)
                if score > best_score:
                    best_score = score
                    best = (tok.title(), model, 0.90, 0.85 if model else 0.0)

        if best:
            return best
        return "", "", 0.0, 0.0

    def _is_vin_fragment(self, token: str) -> bool:
        """
        Returns True if a token looks like a VIN fragment rather than a model name.
        VIN fragments: 3+ chars, mix of uppercase letters AND digits (e.g. 5NPD, HH123456).
        Legitimate model names: all letters (ELANTRA, CIVIC, CAMRY) or letter+number
        without a leading digit (GLE350, F150 — these have letters first).
        """
        if len(token) < 3:
            return False
        has_digit = any(c.isdigit() for c in token)
        has_alpha = any(c.isalpha() for c in token)
        # Starts with digit + has letters = very likely VIN fragment
        if token[0].isdigit() and has_alpha:
            return True
        # Long letter/digit mix = VIN (or OCR-mangled VIN, e.g. 5 read as S).
        # No real model name is 8+ chars of mixed letters and digits.
        if len(token) >= 8 and has_digit and has_alpha:
            return True
        # 4+ chars with both digits and letters and no common model pattern
        if len(token) >= 4 and has_digit and has_alpha:
            # Allow common model patterns like F150, GLE350, CX5, X3 etc.
            # These start with letters and have at most 4 digits
            digit_count = sum(1 for c in token if c.isdigit())
            if digit_count >= 3 and token[0].isdigit():
                return True
        return False

    def _extract_model_after(self, upper_line: str, make: str) -> str:
        """Extract model token(s) immediately after a two-word make."""
        after = upper_line[upper_line.index(make) + len(make):].strip()
        tokens = re.sub(r"[^A-Z0-9\s\-]", " ", after).split()
        model_tokens = []
        for tok in tokens[:3]:
            if tok in self._NOISE_TOKENS or len(tok) < 2:
                break
            model_tokens.append(tok.title())
            if len(model_tokens) == 2:
                break
        return " ".join(model_tokens)

    # ------------------------------------------------------------------
    # Expiration — label-based, layout-independent
    # ------------------------------------------------------------------

    def _find_expiration(self, lines: List[str]) -> Tuple[str, float]:
        """
        Searches for an expiration label then extracts the date that follows.
        Covers 50+ label variants used across US insurers.
        Strategy:
          1. Find a line containing an expiration label
          2. Extract the LAST date on that line (effective date comes first)
          3. If no date on that line, check the next line
          4. Fallback: return the latest date found anywhere in the text
        """
        for idx, line in enumerate(lines):
            if not self._EXP_LABELS.search(line):
                continue

            # Collect all dates on this line and the next
            dates = self._find_dates(line)
            if idx + 1 < len(lines):
                dates += self._find_dates(lines[idx + 1])

            if dates:
                # Always take the LATEST date — expiration is always after
                # effective date. On two-column cards both may appear on the
                # same line and the order can vary.
                return self._normalize_date(max(dates, key=self._parse_date_key)), 0.93

        # Fallback: collect all dates across all lines and return the latest
        # Expiration is always later than effective date
        all_dates: List[str] = []
        for line in lines:
            all_dates.extend(self._find_dates(line))

        if len(all_dates) >= 2:
            latest = max(all_dates, key=self._parse_date_key)
            return self._normalize_date(latest), 0.70
        if all_dates:
            return self._normalize_date(all_dates[0]), 0.60

        return "", 0.0

    def _find_dates(self, text: str) -> List[str]:
        """
        Dates in the text, in order: MM/DD/YY(YY) as written, and month-name
        dates ("Apr 20, 2027") converted to MM/DD/YYYY.
        """
        found = [(m.start(), m.group(1)) for m in self._DATE_RE.finditer(text)]
        for m in self._TEXT_DATE_RE.finditer(text):
            month = self._MONTHS.index(m.group(1).lower()) + 1
            found.append((m.start(), f"{month:02d}/{int(m.group(2)):02d}/{m.group(3)}"))
        return [date for _, date in sorted(found)]

    def _normalize_date(self, date_str: str) -> str:
        """Ensure 2-digit years are expanded to 4-digit (09/11/26 → 09/11/2026)."""
        parts = date_str.split("/")
        if len(parts) == 3 and len(parts[2]) == 2:
            year = int(parts[2])
            parts[2] = str(2000 + year) if year < 50 else str(1900 + year)
        return "/".join(parts)

    def _parse_date_key(self, date_str: str) -> Tuple[int, int, int]:
        """Return (year, month, day) tuple for date comparison."""
        parts = date_str.split("/")
        try:
            m, d, y = int(parts[0]), int(parts[1]), int(parts[2])
            if y < 100:
                y += 2000
            return y, m, d
        except (ValueError, IndexError):
            return 0, 0, 0


# ---------------------------------------------------------------------------
# Image preprocessor
# ---------------------------------------------------------------------------

class InsuranceCardPreprocessor:
    """
    Crops and preprocesses insurance card images for OCR.
    Handles physical cards, zoomed digital, and full-page digital.
    """

    # Tried before all other candidates, in this order
    FIRST_CANDIDATES = ("auto_card_bottom", "lower_left_bottom", "auto_card", "lower_left")

    def __init__(self, config: InsuranceCardOCRConfig) -> None:
        self.config = config

    def get_candidates(self, image: np.ndarray, debug_dir: Optional[Path] = None) -> List[Tuple[str, np.ndarray]]:
        """
        Returns a list of (label, image) candidates to run OCR on,
        ordered from most likely to least likely to contain clean text.
        """
        candidates = []
        h, w = image.shape[:2]

        if debug_dir:
            debug_dir.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(debug_dir / "00_original.png"), image)

        # --- Candidate 1: detect white card region automatically ---
        card_crop = self._detect_white_region(image)
        if card_crop is not None:
            ch, cw = card_crop.shape[:2]

            # Lower-left of the detected region. When the white region is a
            # portrait phone screen, the front+back card often sits in its
            # lower part side by side; the vehicle table is in the left
            # (front) panel. Isolating it keeps back-panel text from being
            # merged into the policy/date lines by Tesseract.
            if ch >= cw * 0.8:
                lower_left = card_crop[int(ch * 0.50):, : cw // 2]
                llh = lower_left.shape[0]
                lower_left_bottom = lower_left[int(llh * 0.55):, :]
                if lower_left_bottom.size > 0:
                    candidates.append(("lower_left_bottom", lower_left_bottom))
                if lower_left.size > 0:
                    candidates.append(("lower_left", lower_left))

            # Always generate a left-half candidate from wide card crops.
            # TX insurance cards in two-panel format (front+back side by side)
            # have aspect ratio > 1.4. The vehicle table is always in the left
            # (front) panel. Taking the left half isolates it cleanly without
            # needing to detect the exact divider position.
            if cw > ch * 1.4:
                left_half = card_crop[:, : cw // 2]
                lh = left_half.shape[0]
                left_bottom = left_half[int(lh * 0.55):, :]
                if left_bottom.size > 0:
                    candidates.append(("left_half_bottom", left_bottom))
                candidates.append(("left_half", left_half))

            # Also try via explicit divider detection (rev3 approach as backup)
            left_panel = self._extract_left_panel(card_crop)
            if left_panel is not None:
                lh = left_panel.shape[0]
                left_bottom = left_panel[int(lh * 0.55):, :]
                if left_bottom.size > 0:
                    candidates.append(("left_panel_bottom", left_bottom))

            # Bottom 40% of full detected card
            card_bottom = card_crop[int(ch * 0.60):, :]
            if card_bottom.size > 0:
                candidates.append(("auto_card_bottom", card_bottom))
            candidates.append(("auto_card", card_crop))

        # --- Candidate 1b: lower-left of the whole image ---
        # A flattened full-page capture (insurance_card_capture_rev1) IS the
        # page, so no white card region is detected above. The USAA printout
        # has the front+back card side by side in its lower half; the vehicle
        # table is in the left (front) panel.
        if h >= w * 0.8:
            lower_left = image[int(h * 0.50):, : w // 2]
            llh = lower_left.shape[0]
            candidates.append(("page_lower_left_bottom", lower_left[int(llh * 0.55):, :]))
            candidates.append(("page_lower_left", lower_left))
        # Page folded/cut at the dashed line: only the card half is captured,
        # a landscape image with the front panel on the left
        elif w >= h * 1.2:
            page_left = image[:, : w // 2]
            candidates.append(("page_left_bottom", page_left[int(h * 0.55):, :]))
            candidates.append(("page_left", page_left))

        # --- Candidate 2: bottom-center crop ---
        center_bottom = image[int(h * 0.40): int(h * 0.95), int(w * 0.10): int(w * 0.90)]
        candidates.append(("center_bottom", center_bottom))

        # --- Candidate 3: bottom 55% ---
        bottom = image[int(h * 0.45):, :]
        candidates.append(("bottom_55pct", bottom))

        # --- Candidate 4: bottom third only ---
        bottom_third = image[int(h * 0.70):, int(w * 0.05): int(w * 0.95)]
        candidates.append(("bottom_third", bottom_third))

        # --- Candidate 5: full image (last — slowest on large images) ---
        candidates.append(("full", image))

        # Try the detected-card crops first; the rest keep the order above.
        # Replaying every pass on the 3 test documents (5 inputs incl. kiosk
        # crops): same 35/35 correct fields, worst case 49 -> 13 passes
        # (the live kiosk capture only early-stopped at auto_card, pass 49)
        rank = {label: i for i, label in enumerate(self.FIRST_CANDIDATES)}
        candidates.sort(key=lambda c: rank.get(c[0], len(rank)))   # stable sort

        if debug_dir:
            for label, img in candidates:
                cv2.imwrite(str(debug_dir / f"candidate_{label}.png"), img)

        return candidates

    def preprocess(self, image: np.ndarray) -> List[Tuple[str, np.ndarray]]:
        """
        Generate preprocessing variants for a single candidate crop.
        Insurance cards have clean black text on white — less aggressive
        preprocessing needed vs DL front.
        """
        variants = []

        # Cap input size before upscaling to prevent enormous output images
        # on large phone-screen shots. Max input 1200px on longest side.
        h, w = image.shape[:2]
        max_input = 1200
        if max(h, w) > max_input:
            scale = max_input / float(max(h, w))
            image = cv2.resize(image, None, fx=scale, fy=scale,
                               interpolation=cv2.INTER_AREA)

        factor = self.config.upscale_factor
        upscaled = cv2.resize(image, None, fx=factor, fy=factor,
                              interpolation=cv2.INTER_CUBIC)

        # Hard cap on output size — prevents 60+ second OCR on large images
        uh, uw = upscaled.shape[:2]
        max_output = 3000
        if max(uh, uw) > max_output:
            cap_scale = max_output / float(max(uh, uw))
            upscaled = cv2.resize(upscaled, None, fx=cap_scale, fy=cap_scale,
                                  interpolation=cv2.INTER_AREA)

        gray = cv2.cvtColor(upscaled, cv2.COLOR_BGR2GRAY) if len(upscaled.shape) == 3 else upscaled

        # v1. Clean grayscale — usually best for black text on white
        variants.append(("gray", gray))

        # v2. CLAHE — recovers contrast if screen brightness is uneven
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
        variants.append(("clahe", clahe))

        # v3. Otsu threshold — good for printed text
        _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variants.append(("otsu", otsu))

        # v4. Sharpened — helps with screen pixel grid interference
        blurred = cv2.GaussianBlur(gray, (0, 0), sigmaX=1.5)
        sharpened = cv2.addWeighted(gray, 1.5, blurred, -0.5, 0)
        variants.append(("sharpened", sharpened))

        return variants

    def _extract_left_panel(self, image: np.ndarray) -> Optional[np.ndarray]:
        """
        Detects if the image has a two-column layout (front+back panels)
        by looking for a vertical divider line near the center.
        Returns the left half if found, None otherwise.
        """
        h, w = image.shape[:2]
        # Only check landscape-ish images — two-panel layout is always wider
        if w < h * 1.2:
            return None

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        # Look for a vertical line (divider or fold) in the center 20% of width
        center_x = w // 2
        search_left = int(w * 0.35)
        search_right = int(w * 0.65)
        center_strip = gray[:, search_left:search_right]

        # Detect vertical edges
        edges = cv2.Canny(center_strip, 30, 90)
        col_sums = np.sum(edges, axis=0)

        # Find the column with most vertical edge energy
        peak_col = int(np.argmax(col_sums))
        peak_val = col_sums[peak_col]

        # If there's a strong vertical line, split there
        if peak_val > h * 0.3:
            split_x = search_left + peak_col
            left = image[:, :split_x]
            LOGGER.info(f"Two-column layout detected — split at x={split_x}")
            return left if left.size > 0 else None

        # Fallback: if image is very wide, just take left half
        if w > h * 1.8:
            LOGGER.info("Wide image — taking left half as single panel")
            return image[:, :w // 2]

        return None

    def _detect_white_region(self, image: np.ndarray) -> Optional[np.ndarray]:
        """
        Finds the largest white/light rectangular region (the card or document)
        in the image. Works for both phone screen and physical card scenarios.
        """
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        # Threshold for white/light regions
        _, white_mask = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
        white_mask = cv2.morphologyEx(white_mask, cv2.MORPH_CLOSE,
                                      np.ones((20, 20), np.uint8))
        contours, _ = cv2.findContours(white_mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        img_area = float(image.shape[0] * image.shape[1])
        best = None
        best_area = 0.0

        for cnt in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
            area = cv2.contourArea(cnt)
            # Must be at least 10% of image and no more than 95%
            if area < img_area * 0.10 or area > img_area * 0.95:
                continue
            x, y, cw, ch = cv2.boundingRect(cnt)
            if area > best_area:
                best_area = area
                best = image[y: y + ch, x: x + cw]

        return best


# ---------------------------------------------------------------------------
# Main OCR pipeline
# ---------------------------------------------------------------------------

class InsuranceCardOCR:
    """
    Extracts vehicle fields from an insurance card image.

    Usage:
        ocr = InsuranceCardOCR()
        result = ocr.extract("test_insurance1.jpg")
        print(result.parsed_fields)
    """

    def __init__(self, config: Optional[InsuranceCardOCRConfig] = None) -> None:
        self.config = config or InsuranceCardOCRConfig()
        self.extractor = InsuranceFieldExtractor()
        self.preprocessor = InsuranceCardPreprocessor(self.config)
        # Tesseract runs as a subprocess, so threads give real parallelism
        self._pool = ThreadPoolExecutor(max(1, self.config.ocr_workers))

        if self.config.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = self.config.tesseract_cmd

    def extract(self, image_path: str | Path) -> InsuranceCardOCRResult:
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        image = cv2.imread(str(image_path))
        if image is None:
            raise ValueError(f"Could not load image: {image_path}")
        return self.extract_image(image, image_path)

    def extract_image(self, image: np.ndarray, source: str | Path = "camera") -> InsuranceCardOCRResult:
        """Same as extract() but on an in-memory BGR image (e.g. a camera capture)."""
        image_path = Path(source)

        debug_dir: Optional[Path] = None
        if self.config.save_debug:
            debug_dir = Path(self.config.debug_output_dir) / image_path.stem
            debug_dir.mkdir(parents=True, exist_ok=True)

        best_result: Optional[Dict[str, object]] = None
        best_raw = ""
        best_score: Tuple[int, float] = (0, 0.0)

        candidates = self.preprocessor.get_candidates(image, debug_dir)

        # Queue every pass up front; the pool works through them in order
        # while the results are scored below in that same order
        passes = [(cand_label, prep_label, psm, processed)
                  for cand_label, candidate in candidates
                  for prep_label, processed in self.preprocessor.preprocess(candidate)
                  for psm in self.config.psm_modes]
        futures = [self._pool.submit(self._run_ocr, processed, psm)
                   for _, _, psm, processed in passes]

        try:
            for (cand_label, prep_label, psm, processed), future in zip(passes, futures):
                raw_text = future.result()
                if not raw_text.strip():
                    continue

                fields = self.extractor.extract(raw_text)
                score = self._score_fields(fields)

                LOGGER.info(
                    f"[{cand_label}/{prep_label}/psm{psm}] "
                    f"score={score[0]} conf={score[1]:.2f} | "
                    f"make={fields.get('vehicle_make')} "
                    f"model={fields.get('vehicle_model')} "
                    f"year={fields.get('vehicle_year')} "
                    f"vin={fields.get('vehicle_vin_last6')} "
                    f"exp={fields.get('insurance_expiration')} "
                    f"company={fields.get('insurance_company')} "
                    f"policy={fields.get('policy_number')}"
                )

                if score > best_score:
                    best_score = score
                    best_result = fields
                    best_raw = raw_text

                    if debug_dir:
                        cv2.imwrite(
                            str(debug_dir / f"best_{cand_label}_{prep_label}_psm{psm}.png"),
                            processed,
                        )
                        (debug_dir / "best_raw_text.txt").write_text(
                            raw_text, encoding="utf-8"
                        )

                # Stop early only when every field was found with high
                # confidence — otherwise keep searching for a better read.
                conf = fields["field_confidence"]  # type: ignore[index]
                if score[0] == len(FIELD_KEYS) and min(conf.values()) >= 0.85:  # type: ignore[union-attr]
                    LOGGER.info(f"Early stop — [{cand_label}/{prep_label}/psm{psm}]")
                    break
        finally:
            # Early stop: drop passes that haven't started (running ones finish on their own)
            for future in futures:
                future.cancel()

        if best_result is None:
            best_result = self.extractor.extract("")

        filled = sum(1 for k in FIELD_KEYS if best_result.get(k))

        if filled == len(FIELD_KEYS):
            recommendation = "All fields extracted successfully."
        elif filled >= 2:
            recommendation = (f"Partial extraction ({filled}/{len(FIELD_KEYS)} fields). "
                              "Check missing fields manually.")
        else:
            recommendation = "Few fields extracted. Try retaking with better lighting or closer framing."

        if debug_dir:
            (debug_dir / "parsed_fields.json").write_text(
                json.dumps(best_result, indent=2), encoding="utf-8"
            )

        return InsuranceCardOCRResult(
            source_path=image_path,
            scan_success=filled >= 2,
            raw_text=best_raw,
            parsed_fields=best_result,
            recommendation=recommendation,
        )

    def _run_ocr(self, image: np.ndarray, psm: int) -> str:
        config_str = f"--psm {psm}"
        try:
            return pytesseract.image_to_string(
                image, lang=self.config.language, config=config_str
            ).strip()
        except Exception as e:
            LOGGER.debug(f"OCR error: {e}")
            return ""

    def _score_fields(self, fields: Dict[str, object]) -> Tuple[int, float]:
        """
        (fields found, total confidence). Compared as a tuple, so more fields
        always wins and confidence breaks ties — e.g. a label-matched
        expiration (0.93) beats a fallback "latest date" guess (0.60).
        """
        keys = FIELD_KEYS
        conf = fields.get("field_confidence", {})
        filled = sum(1 for k in keys if fields.get(k))
        total_conf = sum(float(conf.get(k, 0.0)) for k in keys)  # type: ignore[union-attr]
        return filled, total_conf


# ---------------------------------------------------------------------------
# Logging + entry point
# ---------------------------------------------------------------------------

def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )


def _resolve_test_images() -> List[str]:
    # One entry per image: Windows paths are case-insensitive, so checking
    # both .JPG and .jpg would otherwise find the same file twice.
    existing = []
    for stem in ("test_insurance1", "test_insurance2"):
        for ext in (".JPG", ".jpg"):
            if Path(stem + ext).exists():
                existing.append(stem + ext)
                break
    return existing if existing else ["test_insurance1.jpg"]


if __name__ == "__main__":
    configure_logging()

    ocr = InsuranceCardOCR(
        InsuranceCardOCRConfig(
            tesseract_cmd=r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            save_debug=True,
            debug_output_dir="insurance_debug",
        )
    )

    for test_image in _resolve_test_images():
        print(f"\n{'=' * 72}")
        print(f"TEST IMAGE: {test_image}")
        print(f"{'=' * 72}")
        result = ocr.extract(test_image)
        print("===== SCAN METADATA =====")
        print("Scan success  :", result.scan_success)
        print("Recommendation:", result.recommendation)
        print()
        print("===== EXTRACTED FIELDS =====")
        print(json.dumps(result.parsed_fields, indent=2))
        print()
        print("===== RAW OCR (first 500 chars) =====")
        print(result.raw_text[:500])
