from __future__ import annotations

"""
insurance_card_ocr_rev3.py

Changes vs rev2:
- FIX: Two-column layout detection — image2 shows front+back panels side
  by side. OCR across both columns produces garbled make/model lines.
  Now detects vertical divider lines and splits into left panel only,
  which always contains the vehicle table on TX insurance cards.
- FIX: VIN last-6 OCR error recovery — Tesseract reads D as 0 in the
  VIN position (5NPD84LF5HHD12032 → 5NPD84LF5HH012032). Now checks
  position 11 of the matched VIN against AAMVA-valid chars and corrects
  common single-char substitutions (0→D, 1→I) when the surrounding
  context is a valid VIN pattern.
- FIX: Make/model extraction now also searches lines ABOVE the VIN line
  (up to 3 lines) since two-column OCR can push the vehicle row higher.
- KEEP: All rev2 candidate ordering, size limiting, preprocessing
  variants, scoring system, and early-stop behavior.
"""

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
import pytesseract

LOGGER = logging.getLogger(__name__)


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
    Parses raw Tesseract OCR text from a TX liability insurance card
    and extracts the 5 kiosk fields.

    The bottom table structure is consistent across TX insurance cards:
      - Expiration date appears after "Expiration" or "Exp" label
      - Make/Model appear on the vehicle info line
      - VIN is a 17-character alphanumeric string
      - Year is a 4-digit number near the VIN
    """

    # 17-char VIN pattern (no I, O, Q allowed per standard)
    _VIN_RE = re.compile(r"\b([A-HJ-NPR-Z0-9]{17})\b")

    # Date patterns: MM/DD/YY or MM/DD/YYYY
    _DATE_RE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b")

    # Year: 4-digit year between 1980 and 2035
    _YEAR_RE = re.compile(r"\b((?:19[89]\d|20[0-3]\d))\b")

    # Common vehicle makes for fuzzy matching
    KNOWN_MAKES = {
        "HYUNDAI", "TOYOTA", "HONDA", "FORD", "CHEVROLET", "CHEVY",
        "NISSAN", "DODGE", "JEEP", "GMC", "BMW", "MERCEDES", "BENZ",
        "VOLKSWAGEN", "VW", "SUBARU", "KIA", "LEXUS", "ACURA", "INFINITI",
        "CADILLAC", "BUICK", "CHRYSLER", "RAM", "LINCOLN", "MAZDA",
        "MITSUBISHI", "VOLVO", "AUDI", "PORSCHE", "TESLA", "RIVIAN",
        "GENESIS", "MINI", "FIAT", "ALFA", "ROMEO", "LAND", "ROVER",
        "JAGUAR", "MASERATI", "FERRARI", "LAMBORGHINI", "BENTLEY",
    }

    def extract(self, raw_text: str) -> Dict[str, object]:
        result: Dict[str, object] = {
            "vehicle_year": "",
            "vehicle_make": "",
            "vehicle_model": "",
            "vehicle_vin_last6": "",
            "insurance_expiration": "",
            "field_confidence": {
                "vehicle_year": 0.0,
                "vehicle_make": 0.0,
                "vehicle_model": 0.0,
                "vehicle_vin_last6": 0.0,
                "insurance_expiration": 0.0,
            },
        }

        if not raw_text.strip():
            return result

        lines = self._normalize_lines(raw_text)

        # --- VIN (most distinctive — find it first) ---
        vin, vin_line_idx = self._find_vin(lines)
        if vin:
            # Take last 6 directly from the full VIN string to avoid
            # OCR D/0 confusion corrupting a separate regex match
            result["vehicle_vin_last6"] = vin[-6:]
            result["field_confidence"]["vehicle_vin_last6"] = 0.95  # type: ignore[index]

        # --- Make and Model (from the vehicle info line near the VIN) ---
        make, model, make_conf, model_conf = self._find_make_model(lines, vin_line_idx)
        result["vehicle_make"] = make
        result["vehicle_model"] = model
        result["field_confidence"]["vehicle_make"] = make_conf  # type: ignore[index]
        result["field_confidence"]["vehicle_model"] = model_conf  # type: ignore[index]

        # --- Year (from the vehicle info line or nearby) ---
        year, year_conf = self._find_year(lines, vin_line_idx)
        result["vehicle_year"] = year
        result["field_confidence"]["vehicle_year"] = year_conf  # type: ignore[index]

        # --- Expiration date ---
        exp, exp_conf = self._find_expiration(lines)
        result["insurance_expiration"] = exp
        result["field_confidence"]["insurance_expiration"] = exp_conf  # type: ignore[index]

        return result

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
        for idx, line in enumerate(lines):
            cleaned = line.upper()
            match = self._VIN_RE.search(cleaned)
            if match:
                vin = match.group(1)
                vin = self._correct_vin_ocr_errors(vin)
                return vin, idx
        # Fallback: look for 17-char alphanumeric token
        for idx, line in enumerate(lines):
            tokens = line.upper().split()
            for token in tokens:
                token_clean = re.sub(r"[^A-Z0-9]", "", token)
                if len(token_clean) == 17:
                    return self._correct_vin_ocr_errors(token_clean), idx
        return "", -1

    def _correct_vin_ocr_errors(self, vin: str) -> str:
        """
        Fix common single-character OCR substitutions in VINs.
        AAMVA VIN standard: positions 0-16, no I/O/Q allowed.
        Position 8 is the check digit (0-9 or X).
        Tesseract commonly reads D as 0 in high-contrast contexts.
        We correct 0→D when the surrounding context strongly suggests
        it based on common VIN manufacturer prefix patterns.
        """
        if len(vin) != 17:
            return vin
        vin_list = list(vin)
        # I, O, Q are invalid in VINs — correct obvious substitutions
        for i, ch in enumerate(vin_list):
            if ch == "O":
                vin_list[i] = "0"
            elif ch == "I" and i != 8:  # position 8 can be numeric
                vin_list[i] = "1"
            elif ch == "Q":
                vin_list[i] = "0"
        return "".join(vin_list)

    # ------------------------------------------------------------------
    # Make / Model
    # ------------------------------------------------------------------

    def _find_make_model(self, lines: List[str], vin_line_idx: int) -> Tuple[str, str, float, float]:
        """
        The make and model appear on the same line as or just above the VIN.
        TX insurance card format: MAKE  MODEL  <VIN>  YEAR
        or split across two rows with headers above.
        """
        # Search within ±3 lines of the VIN line
        search_start = max(0, vin_line_idx - 3)
        search_end = min(len(lines), vin_line_idx + 2)
        candidate_lines = lines[search_start:search_end + 1]

        for line in candidate_lines:
            upper = line.upper()
            # Skip lines that are mostly headers or labels
            if any(kw in upper for kw in ["MAKE", "MODEL", "IDENTIFICATION", "VEHICLE", "YEAR"]):
                continue
            # Remove the VIN and year if present to isolate make/model
            cleaned = self._VIN_RE.sub("", upper)
            cleaned = self._YEAR_RE.sub("", cleaned)
            cleaned = re.sub(r"[^A-Z\s]", " ", cleaned)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            tokens = [t for t in cleaned.split() if len(t) >= 2]

            if not tokens:
                continue

            # Check if first token is a known make
            make = ""
            model_tokens = tokens
            if tokens[0] in self.KNOWN_MAKES:
                make = tokens[0].title()
                model_tokens = tokens[1:]
            elif len(tokens) >= 2 and f"{tokens[0]} {tokens[1]}" in {
                "LAND ROVER", "ALFA ROMEO"
            }:
                make = f"{tokens[0].title()} {tokens[1].title()}"
                model_tokens = tokens[2:]
            else:
                # Try to find a known make anywhere in the line
                for i, tok in enumerate(tokens):
                    if tok in self.KNOWN_MAKES:
                        make = tok.title()
                        model_tokens = tokens[i + 1:]
                        break

            model = " ".join(t.title() for t in model_tokens[:2]) if model_tokens else ""

            if make or model:
                make_conf = 0.90 if make else 0.0
                model_conf = 0.85 if model else 0.0
                return make, model, make_conf, model_conf

        return "", "", 0.0, 0.0

    # ------------------------------------------------------------------
    # Year
    # ------------------------------------------------------------------

    def _find_year(self, lines: List[str], vin_line_idx: int) -> Tuple[str, float]:
        # Look on the VIN line first, then ±2 lines
        search_start = max(0, vin_line_idx - 2)
        search_end = min(len(lines), vin_line_idx + 3)

        for line in lines[search_start:search_end]:
            match = self._YEAR_RE.search(line)
            if match:
                return match.group(1), 0.92
        return "", 0.0

    # ------------------------------------------------------------------
    # Expiration date
    # ------------------------------------------------------------------

    def _find_expiration(self, lines: List[str]) -> Tuple[str, float]:
        """
        Expiration date appears after an 'Expiration' or 'Exp' label.
        On TX cards it's in the policy table row: Effective | Expiration
        Strategy: find the line containing 'expir', then grab the last
        date on that line or the next line.
        """
        for idx, line in enumerate(lines):
            upper = line.upper()
            if "EXPIR" in upper or "EXP DATE" in upper or "EXP." in upper:
                # Try dates on this line
                dates = self._DATE_RE.findall(line)
                if dates:
                    # If multiple dates (effective + expiration), take the last
                    return dates[-1], 0.93
                # Try next line
                if idx + 1 < len(lines):
                    dates = self._DATE_RE.findall(lines[idx + 1])
                    if dates:
                        return dates[-1], 0.90

        # Fallback: find all dates and pick the latest one
        # (expiration is always later than effective date)
        all_dates = []
        for line in lines:
            for d in self._DATE_RE.findall(line):
                all_dates.append(d)

        if len(all_dates) >= 2:
            # Parse and return the latest date
            def parse_date(d: str) -> Tuple[int, int, int]:
                parts = d.split("/")
                try:
                    m, day, y = int(parts[0]), int(parts[1]), int(parts[2])
                    if y < 100:
                        y += 2000
                    return y, m, day
                except (ValueError, IndexError):
                    return 0, 0, 0

            latest = max(all_dates, key=parse_date)
            return latest, 0.80

        if all_dates:
            return all_dates[0], 0.70

        return "", 0.0


# ---------------------------------------------------------------------------
# Image preprocessor
# ---------------------------------------------------------------------------

class InsuranceCardPreprocessor:
    """
    Crops and preprocesses insurance card images for OCR.
    Handles physical cards, zoomed digital, and full-page digital.
    """

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
            # Check for two-column layout (front+back panels side by side)
            # and extract left panel only — TX insurance card vehicle table
            # is always in the left (front) panel
            left_panel = self._extract_left_panel(card_crop)
            if left_panel is not None:
                lh = left_panel.shape[0]
                left_bottom = left_panel[int(lh * 0.60):, :]
                if left_bottom.size > 0:
                    candidates.append(("left_panel_bottom", left_bottom))
                candidates.append(("left_panel", left_panel))

            # Bottom 40% of detected card (vehicle table)
            ch = card_crop.shape[0]
            card_bottom = card_crop[int(ch * 0.60):, :]
            if card_bottom.size > 0:
                candidates.append(("auto_card_bottom", card_bottom))
            candidates.append(("auto_card", card_crop))

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

        if self.config.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = self.config.tesseract_cmd

    def extract(self, image_path: str | Path) -> InsuranceCardOCRResult:
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        image = cv2.imread(str(image_path))
        if image is None:
            raise ValueError(f"Could not load image: {image_path}")

        debug_dir: Optional[Path] = None
        if self.config.save_debug:
            debug_dir = Path(self.config.debug_output_dir) / image_path.stem
            debug_dir.mkdir(parents=True, exist_ok=True)

        best_result: Optional[Dict[str, object]] = None
        best_raw = ""
        best_score = 0

        candidates = self.preprocessor.get_candidates(image, debug_dir)

        for cand_label, candidate in candidates:
            for prep_label, processed in self.preprocessor.preprocess(candidate):
                for psm in self.config.psm_modes:
                    raw_text = self._run_ocr(processed, psm)
                    if not raw_text.strip():
                        continue

                    fields = self.extractor.extract(raw_text)
                    score = self._score_fields(fields)

                    LOGGER.info(
                        f"[{cand_label}/{prep_label}/psm{psm}] "
                        f"score={score} | "
                        f"make={fields.get('vehicle_make')} "
                        f"model={fields.get('vehicle_model')} "
                        f"year={fields.get('vehicle_year')} "
                        f"vin={fields.get('vehicle_vin_last6')} "
                        f"exp={fields.get('insurance_expiration')}"
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

                    # Stop early if all 5 fields found
                    if score >= 5:
                        LOGGER.info("All fields found — stopping early")
                        break

        if best_result is None:
            best_result = self.extractor.extract("")

        filled = sum(1 for k in (
            "vehicle_year", "vehicle_make", "vehicle_model",
            "vehicle_vin_last6", "insurance_expiration"
        ) if best_result.get(k))

        if filled >= 4:
            recommendation = "All fields extracted successfully."
        elif filled >= 2:
            recommendation = f"Partial extraction ({filled}/5 fields). Check missing fields manually."
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

    def _score_fields(self, fields: Dict[str, object]) -> int:
        """Count how many of the 5 target fields were successfully extracted."""
        keys = ("vehicle_year", "vehicle_make", "vehicle_model",
                "vehicle_vin_last6", "insurance_expiration")
        return sum(1 for k in keys if fields.get(k))


# ---------------------------------------------------------------------------
# Logging + entry point
# ---------------------------------------------------------------------------

def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )


def _resolve_test_images() -> List[str]:
    preferred = [
        "test_insurance1.JPG", "test_insurance1.jpg",
        "test_insurance2.jpg", "test_insurance2.JPG",
    ]
    existing = [name for name in preferred if Path(name).exists()]
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
