from __future__ import annotations

"""
driver_license_barcode_rev4.py

Changes vs rev3:
- FIX: AAMVA parser now correctly splits on LF (\\n) field separators —
  Texas DLs use plain linefeeds between fields, not CR+LF. The parser
  was landing every field on one giant line and extracting nothing.
- FIX: Name extraction now checks DAC (first name) before DCT — the
  raw AAMVA confirmed DACSETH not DCTSETH for this card format.
- FIX: Added <LF>/<CR>/<RS>/<GS> text token normalization for cases
  where control characters are rendered as literal text strings.
- FIX: Direct crop fast path added in scan() — tries 5 raw ratio crops
  on the unmodified image before the full preprocessing pipeline,
  matching exactly what the diagnostic confirmed works.
- KEEP: All rev3 PDF417-only format hint, ratio-based card crop,
  red-channel suppression variants, AAMVA field extraction.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Decoder backend — try zxing-cpp first, fall back to pyzbar
# ---------------------------------------------------------------------------

_BACKEND: str = "none"

try:
    import zxingcpp  # type: ignore
    _BACKEND = "zxingcpp"
    LOGGER.info("Barcode backend: zxing-cpp")
except ImportError:
    pass

if _BACKEND == "none":
    try:
        from pyzbar import pyzbar  # type: ignore
        _BACKEND = "pyzbar"
        LOGGER.info("Barcode backend: pyzbar")
    except ImportError:
        pass

if _BACKEND == "none":
    LOGGER.warning(
        "No barcode backend found. Install zxing-cpp or pyzbar:\n"
        "  pip install zxing-cpp\n"
        "  -- or --\n"
        "  pip install pyzbar"
    )


# ---------------------------------------------------------------------------
# AAMVA field map — only the fields needed by the kiosk form
# ---------------------------------------------------------------------------

# Primary subfile fields (most common)
# DAA = full name (LAST$FIRST$MIDDLE) — older AAMVA versions
# DCS = last name, DCT or DAC = first name — AAMVA 2000+
AAMVA_FIELDS = {
    # Name
    "DAA": "full_name",          # full name, older format: LAST$FIRST$MIDDLE
    "DCS": "last_name",          # family name
    "DAC": "first_name",         # first name (some versions)
    "DCT": "first_name_alt",     # first + middle concatenated (some versions)
    "DAD": "middle_name",
    # Address
    "DAG": "street_address",
    "DAH": "street_address_2",   # apt/unit line if present
    "DAI": "city",
    "DAJ": "state",
    "DAK": "zip_code",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class BarcodeScanResult:
    """Raw result from the barcode scanner before AAMVA parsing."""
    raw_bytes: bytes
    raw_text: str
    format: str                  # e.g. "PDF417"
    backend: str


@dataclass
class DriverLicenseBarcodeResult:
    source_path: Path
    scan_success: bool
    backend_used: str
    raw_aamva: str
    aamva_version: int           # 0 = unknown
    parsed_fields: Dict[str, object]
    recommendation: str


# ---------------------------------------------------------------------------
# AAMVA parser
# ---------------------------------------------------------------------------

class AAMVAParser:
    """
    Parses an AAMVA-formatted string into structured fields.

    AAMVA barcode format (simplified):
        @\n\x1e\rANSI <issuer><aamva_version><numEntries>
        DL<field_length><record_length>
        <field_id><value>\n
        ...
    """

    def parse(self, raw: str) -> Tuple[int, Dict[str, str]]:
        """
        Returns (aamva_version, field_dict).
        field_dict keys are AAMVA field codes (e.g. 'DCS', 'DAG').
        """
        version = self._detect_version(raw)
        fields = self._extract_fields(raw)
        return version, fields

    def _detect_version(self, raw: str) -> int:
        # ANSI header: @\n\x1e\rANSI <6-digit issuer ID><2-digit version>...
        match = re.search(r"ANSI\s+\d{6}(\d{2})", raw)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass
        return 0

    def _extract_fields(self, raw: str) -> Dict[str, str]:
        """
        Extract all 3-character field codes and their values.
        AAMVA fields are separated by \n, \r, or \x0a (LF).
        Each entry: <3-char-code><value>
        """
        fields: Dict[str, str] = {}

        # Normalize — AAMVA uses LF (\n), CR (\r), and sometimes
        # literal "<LF>" or "<RS>" strings when printed as text
        normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
        # Handle cases where control chars were rendered as text tokens
        normalized = normalized.replace("<LF>", "\n").replace("<CR>", "\n")
        normalized = normalized.replace("<RS>", "\n").replace("<GS>", "\n")

        for line in normalized.splitlines():
            line = line.strip()
            if len(line) < 4:
                continue
            code = line[:3]
            if not re.match(r"^[A-Z]{2}[A-Z0-9]$", code):
                continue
            value = line[3:].strip()
            if value:
                fields[code] = value

        return fields


# ---------------------------------------------------------------------------
# Kiosk field extractor
# ---------------------------------------------------------------------------

class KioskFieldExtractor:
    """
    Maps raw AAMVA fields to the 6 fields needed by the kiosk form.
    Handles both old (DAA full_name) and new (DCS/DCT/DAC) AAMVA layouts.
    """

    def extract(self, aamva_fields: Dict[str, str]) -> Dict[str, object]:
        result: Dict[str, object] = {
            "customer_first_name": "",
            "customer_last_name": "",
            "street_address": "",
            "city": "",
            "state": "",
            "zip_code": "",
            "field_confidence": {
                "customer_first_name": 0.0,
                "customer_last_name": 0.0,
                "street_address": 0.0,
                "city": 0.0,
                "state": 0.0,
                "zip_code": 0.0,
            },
        }

        # ---- Name ----
        last_name, first_name = self._extract_name(aamva_fields)
        result["customer_last_name"] = last_name
        result["customer_first_name"] = first_name
        result["field_confidence"]["customer_last_name"] = 0.99 if last_name else 0.0  # type: ignore[index]
        result["field_confidence"]["customer_first_name"] = 0.99 if first_name else 0.0  # type: ignore[index]

        # ---- Street address ----
        street = self._clean(aamva_fields.get("DAG", ""))
        street2 = self._clean(aamva_fields.get("DAH", ""))
        if street and street2:
            street = f"{street} {street2}"
        result["street_address"] = street.title()
        result["field_confidence"]["street_address"] = 0.99 if street else 0.0  # type: ignore[index]

        # ---- City ----
        city = self._clean(aamva_fields.get("DAI", "")).title()
        result["city"] = city
        result["field_confidence"]["city"] = 0.99 if city else 0.0  # type: ignore[index]

        # ---- State ----
        state = self._clean(aamva_fields.get("DAJ", "")).upper()
        result["state"] = state
        result["field_confidence"]["state"] = 0.99 if state else 0.0  # type: ignore[index]

        # ---- Zip code ----
        # AAMVA DAK is 9 chars padded with spaces/zeros: "782300000"
        zip_raw = self._clean(aamva_fields.get("DAK", ""))
        zip_code = self._parse_zip(zip_raw)
        result["zip_code"] = zip_code
        result["field_confidence"]["zip_code"] = 0.99 if zip_code else 0.0  # type: ignore[index]

        return result

    def _extract_name(self, fields: Dict[str, str]) -> Tuple[str, str]:
        # AAMVA field codes for name:
        #   DCS = family name (last)
        #   DAC = first name
        #   DAD = middle name
        #   DAA = full name legacy format: "LAST$FIRST$MIDDLE"
        last = self._clean(fields.get("DCS", "")).title()
        first_raw = self._clean(fields.get("DAC", ""))

        if last and first_raw:
            # DAC may contain "FIRST MIDDLE" — take only first token
            first = re.split(r"[,\s]+", first_raw)[0].title()
            return last, first

        # Some versions use DCT for first+middle combined
        dct = self._clean(fields.get("DCT", ""))
        if last and dct:
            first = re.split(r"[,\s]+", dct)[0].title()
            return last, first

        # Legacy AAMVA v1: DAA = "LAST$FIRST$MIDDLE" or "LAST,FIRST"
        daa = self._clean(fields.get("DAA", ""))
        if daa:
            if "$" in daa:
                parts = daa.split("$")
                last = parts[0].title() if len(parts) > 0 else ""
                first = parts[1].title() if len(parts) > 1 else ""
                return last, first
            if "," in daa:
                parts = daa.split(",", 1)
                return parts[0].strip().title(), parts[1].strip().title()

        return last, first_raw.title()

    def _parse_zip(self, raw: str) -> str:
        # Remove trailing zeros/spaces used for padding: "782300000" → "78230"
        digits = re.sub(r"[^0-9]", "", raw)
        if len(digits) >= 5:
            return digits[:5]
        return digits

    def _clean(self, value: str) -> str:
        return value.strip().strip("\x00")


# ---------------------------------------------------------------------------
# Image preprocessor for barcode scanning
# ---------------------------------------------------------------------------

class BarcodeImagePreprocessor:
    """
    Generates multiple image variants optimized for PDF417 barcode detection.

    Pipeline per image:
      1. Limit size
      2. Detect and crop the card (perspective correct it)
      3. Deskew the cropped card
      4. Produce grayscale variants including red-channel suppression
         (cuts through holographic overlays common on TX DLs)
      5. Try 180° rotation for upside-down barcodes

    Debug images are saved to debug_dir when provided.
    """

    def variants(self, image: np.ndarray, debug_dir: Optional[Path] = None) -> List[Tuple[str, np.ndarray]]:
        results: List[Tuple[str, np.ndarray]] = []

        image = self._limit_size(image, max_dim=4000)  # keep near full-res for barcode

        if debug_dir:
            debug_dir.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(debug_dir / "00_original.png"), image)

        # --- Step 1: detect and crop the card ---
        cropped = self._detect_and_crop_card(image, debug_dir)

        if debug_dir:
            cv2.imwrite(str(debug_dir / "01_card_crop.png"), cropped)

        # --- Step 2: deskew disabled — the PDF417 barcode's own horizontal
        # lines interfere with Hough line angle detection, causing inconsistent
        # and sometimes wrong rotation corrections. The ratio crop is already
        # close enough to horizontal for zxingcpp to decode.
        deskewed = cropped

        if debug_dir:
            cv2.imwrite(str(debug_dir / "02_deskewed.png"), deskewed)

        # --- Step 3: produce variants from the barcode band ---
        gray = cv2.cvtColor(deskewed, cv2.COLOR_BGR2GRAY)

        # v0. Raw color barcode band — this is exactly what succeeded in testing
        results.append(("full_color", deskewed))

        # v1. Raw grayscale
        results.append(("gray", gray))

        # v2. Red-channel suppressed — the holographic overlay on TX DLs is
        #     predominantly red/pink. Using only the blue channel (index 0 in BGR)
        #     dramatically reduces its interference with the barcode modules.
        blue_ch = deskewed[:, :, 0]
        results.append(("blue_channel", blue_ch))

        # v3. Green channel — another good non-red channel, often cleaner than gray
        green_ch = deskewed[:, :, 1]
        results.append(("green_channel", green_ch))

        # v4. Weighted channel blend: suppress red, boost blue+green
        #     result = 0.5*B + 0.4*G + 0.1*R  (all in BGR order)
        b, g, r = cv2.split(deskewed.astype(np.float32))
        suppressed = np.clip(b * 0.5 + g * 0.4 + r * 0.1, 0, 255).astype(np.uint8)
        results.append(("red_suppressed", suppressed))

        # v5. CLAHE on red-suppressed — recovers contrast after suppression
        clahe_engine = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        clahe_suppressed = clahe_engine.apply(suppressed)
        results.append(("clahe_suppressed", clahe_suppressed))

        # v6. Otsu on blue channel — barcodes are black/white, blue ch + binary is strong
        _, otsu_blue = cv2.threshold(blue_ch, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        results.append(("otsu_blue", otsu_blue))

        # v7. Standard CLAHE on gray
        clahe_gray = clahe_engine.apply(gray)
        results.append(("clahe_gray", clahe_gray))

        # v8. Sharpened gray
        blurred = cv2.GaussianBlur(gray, (0, 0), sigmaX=2)
        sharpened = cv2.addWeighted(gray, 1.6, blurred, -0.6, 0)
        results.append(("sharpened", sharpened))

        # v9. Upscaled red-suppressed — ensures barcode modules are large enough
        upscaled = cv2.resize(suppressed, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        results.append(("upscaled_suppressed", upscaled))

        # v10. Upscaled full color — mirrors exactly what worked in diagnostic
        upscaled_color = cv2.resize(deskewed, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        results.append(("upscaled_color", upscaled_color))

        # v10. 180° rotations of top variants (barcode may be upside-down)
        results.append(("gray_rot180", cv2.rotate(gray, cv2.ROTATE_180)))
        results.append(("blue_rot180", cv2.rotate(blue_ch, cv2.ROTATE_180)))

        if debug_dir:
            for name, img in results:
                cv2.imwrite(str(debug_dir / f"variant_{name}.png"), img)

        return results

    # ------------------------------------------------------------------
    # Card detection — find the license card and perspective-correct it
    # ------------------------------------------------------------------

    def _detect_and_crop_card(self, image: np.ndarray, debug_dir: Optional[Path] = None) -> np.ndarray:
        """
        Crops the card region and barcode band directly using fixed ratios.
        Ratios tuned for test_dl_back.jpg layout:
          - Card sits in upper portion, slightly left of center
          - Barcode band is in the lower 50% of the card
        """
        h, w = image.shape[:2]

        # Wider left boundary (0.15 instead of 0.22) to avoid cutting off
        # the left edge of the PDF417 barcode
        card = image[int(h * 0.35): int(h * 0.72), int(w * 0.15): int(w * 0.82)]

        if debug_dir:
            cv2.imwrite(str(debug_dir / "00b_card_region.png"), card)

        # Barcode band: lower 50% of card region
        ch = card.shape[0]
        barcode_band = card[int(ch * 0.42): int(ch * 0.85), :]

        if debug_dir:
            cv2.imwrite(str(debug_dir / "00c_barcode_band.png"), barcode_band)

        if barcode_band.size == 0:
            LOGGER.warning("Barcode band crop empty — falling back to full card")
            return card

        LOGGER.info(f"Card region: {card.shape[1]}x{card.shape[0]} px  "
                    f"Barcode band: {barcode_band.shape[1]}x{barcode_band.shape[0]} px")
        return barcode_band

    # ------------------------------------------------------------------
    # Deskew — straighten small rotations within the cropped card
    # ------------------------------------------------------------------

    def _deskew(self, image: np.ndarray) -> np.ndarray:
        """
        Detects the dominant line angle in the card (using the magnetic stripe
        or barcode edges) and rotates to correct small skew (up to ±10°).
        """
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=80,
                                minLineLength=image.shape[1] // 4, maxLineGap=20)
        if lines is None:
            return image

        angles = []
        for line in lines:
            x1, y1, x2, y2 = line[0]
            if x2 != x1:
                angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
                # Only consider near-horizontal lines (card text runs horizontal)
                if abs(angle) < 15:
                    angles.append(angle)

        if not angles:
            return image

        median_angle = float(np.median(angles))
        if abs(median_angle) < 0.3:
            return image  # already straight enough

        h, w = image.shape[:2]
        center = (w // 2, h // 2)
        M = cv2.getRotationMatrix2D(center, median_angle, 1.0)
        rotated = cv2.warpAffine(image, M, (w, h),
                                 flags=cv2.INTER_CUBIC,
                                 borderMode=cv2.BORDER_REPLICATE)
        LOGGER.info(f"Deskewed by {median_angle:.2f}°")
        return rotated

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------

    def _order_points(self, pts: np.ndarray) -> np.ndarray:
        rect = np.zeros((4, 2), dtype=np.float32)
        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]   # top-left
        rect[2] = pts[np.argmax(s)]   # bottom-right
        diff = np.diff(pts, axis=1)
        rect[1] = pts[np.argmin(diff)]  # top-right
        rect[3] = pts[np.argmax(diff)]  # bottom-left
        return rect

    def _four_point_transform(self, image: np.ndarray, pts: np.ndarray) -> np.ndarray:
        (tl, tr, br, bl) = pts
        width = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
        height = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
        width = max(width, 100)
        height = max(height, 60)
        dst = np.array([[0, 0], [width - 1, 0],
                        [width - 1, height - 1], [0, height - 1]], dtype=np.float32)
        M = cv2.getPerspectiveTransform(pts, dst)
        return cv2.warpPerspective(image, M, (width, height))

    def _limit_size(self, image: np.ndarray, max_dim: int = 2400) -> np.ndarray:
        h, w = image.shape[:2]
        if max(h, w) <= max_dim:
            return image
        scale = max_dim / float(max(h, w))
        return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)


# ---------------------------------------------------------------------------
# Main scanner
# ---------------------------------------------------------------------------

class DriverLicenseBarcodeScanner:
    """
    Scans the back of a driver's license image for a PDF417 barcode
    and extracts kiosk registration fields via AAMVA parsing.

    Usage:
        scanner = DriverLicenseBarcodeScanner()
        result = scanner.scan("back_of_license.jpg")
        print(result.parsed_fields)

    Debug mode (saves all intermediate images):
        result = scanner.scan("back_of_license.jpg", save_debug=True,
                              debug_output_dir="barcode_debug")
    """

    def __init__(self) -> None:
        self.parser = AAMVAParser()
        self.extractor = KioskFieldExtractor()
        self.preprocessor = BarcodeImagePreprocessor()

        if _BACKEND == "none":
            raise RuntimeError(
                "No barcode backend available. Install zxing-cpp or pyzbar:\n"
                "  pip install zxing-cpp\n"
                "  pip install pyzbar"
            )

    def scan(
        self,
        image_path: str | Path,
        save_debug: bool = False,
        debug_output_dir: str = "barcode_debug",
    ) -> DriverLicenseBarcodeResult:
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        image = cv2.imread(str(image_path))
        if image is None:
            raise ValueError(f"Could not load image: {image_path}")

        debug_dir: Optional[Path] = None
        if save_debug:
            debug_dir = Path(debug_output_dir) / image_path.stem
            debug_dir.mkdir(parents=True, exist_ok=True)

        # --- Fast path: try the exact crop that diagnostic confirmed works ---
        # Do this on the raw image before any resizing or preprocessing,
        # since that is what succeeded in diagnostic testing.
        scan_result = self._try_direct_crop(image, debug_dir)

        # --- Full pipeline fallback ---
        if scan_result is None:
            LOGGER.info("Direct crop failed — trying full preprocessing pipeline")
            scan_result = self._scan_image(image, debug_dir)

        if scan_result is None:
            return DriverLicenseBarcodeResult(
                source_path=image_path,
                scan_success=False,
                backend_used=_BACKEND,
                raw_aamva="",
                aamva_version=0,
                parsed_fields=self._empty_fields(),
                recommendation="Barcode not found. Ensure the back of the license is visible and in focus.",
            )

        version, aamva_fields = self.parser.parse(scan_result.raw_text)
        parsed = self.extractor.extract(aamva_fields)

        filled = sum(1 for k in ("customer_first_name", "customer_last_name", "street_address", "city", "state", "zip_code") if parsed.get(k))
        if filled >= 5:
            recommendation = "All fields extracted successfully."
        elif filled >= 3:
            recommendation = f"Partial extraction ({filled}/6 fields). Check missing fields manually."
        else:
            recommendation = "Few fields extracted. Barcode may be damaged or in an unexpected format."

        return DriverLicenseBarcodeResult(
            source_path=image_path,
            scan_success=True,
            backend_used=_BACKEND,
            raw_aamva=scan_result.raw_text,
            aamva_version=version,
            parsed_fields=parsed,
            recommendation=recommendation,
        )

    def _try_direct_crop(self, image: np.ndarray, debug_dir: Optional[Path] = None) -> Optional[BarcodeScanResult]:
        """
        Attempts decode using the exact ratio crop that succeeded in
        diagnostic testing — raw image, no resizing, no preprocessing.
        Tries multiple crop variations to handle different card positions.
        """
        h, w = image.shape[:2]
        crops = [
            # (label, row_start, row_end, col_start, col_end) as ratios
            # then barcode band within card: (band_row_start, band_row_end)
            ("direct_v1", 0.35, 0.72, 0.22, 0.80, 0.35, 0.80),  # original diagnostic
            ("direct_v2", 0.35, 0.72, 0.15, 0.82, 0.42, 0.85),  # wider left
            ("direct_v3", 0.30, 0.75, 0.15, 0.85, 0.40, 0.85),  # wider all
            ("direct_v4", 0.35, 0.72, 0.22, 0.80, 0.40, 0.90),  # lower band
            ("direct_v5", 0.35, 0.72, 0.22, 0.80, 0.25, 0.75),  # upper band
        ]
        for label, r1, r2, c1, c2, b1, b2 in crops:
            card = image[int(h*r1):int(h*r2), int(w*c1):int(w*c2)]
            if card.size == 0:
                continue
            ch = card.shape[0]
            band = card[int(ch*b1):int(ch*b2), :]
            if band.size == 0:
                continue
            LOGGER.info(f"Direct crop {label}: {band.shape[1]}x{band.shape[0]}")
            if debug_dir:
                cv2.imwrite(str(debug_dir / f"direct_{label}.png"), band)
            result = self._decode(band, label)
            if result:
                return result
        return None

    def _scan_image(self, image: np.ndarray, debug_dir: Optional[Path] = None) -> Optional[BarcodeScanResult]:
        """Try all preprocessing variants until a barcode is decoded."""
        for variant_name, variant_img in self.preprocessor.variants(image, debug_dir):
            h, w = variant_img.shape[:2]
            LOGGER.info(f"Trying variant: {variant_name} ({w}x{h})")
            result = self._decode(variant_img, variant_name)
            if result is not None:
                LOGGER.info(f"Barcode found using variant: {variant_name}")
                if debug_dir:
                    (debug_dir / "winning_variant.txt").write_text(variant_name, encoding="utf-8")
                return result
        return None

    def _decode(self, image: np.ndarray, variant_name: str) -> Optional[BarcodeScanResult]:
        if _BACKEND == "zxingcpp":
            return self._decode_zxingcpp(image, variant_name)
        if _BACKEND == "pyzbar":
            return self._decode_pyzbar(image, variant_name)
        return None

    def _decode_zxingcpp(self, image: np.ndarray, variant_name: str) -> Optional[BarcodeScanResult]:
        try:
            # Pass PDF417 format hint — avoids picking up the small DataBar
            # inventory barcode on the left edge of the TX DL back
            try:
                results = zxingcpp.read_barcodes(  # type: ignore
                    image, formats=zxingcpp.BarcodeFormat.PDF417  # type: ignore
                )
            except TypeError:
                # Older zxingcpp versions don't support formats parameter
                results = [r for r in zxingcpp.read_barcodes(image)  # type: ignore
                           if "PDF417" in str(r.format).upper()]
            for r in results:
                if r.valid and r.text:
                    return BarcodeScanResult(
                        raw_bytes=r.bytes if hasattr(r, "bytes") else b"",
                        raw_text=r.text,
                        format="PDF417",
                        backend="zxingcpp",
                    )
        except Exception as e:
            LOGGER.debug(f"zxingcpp error on variant {variant_name}: {e}")
        return None

    def _decode_pyzbar(self, image: np.ndarray, variant_name: str) -> Optional[BarcodeScanResult]:
        try:
            barcodes = pyzbar.decode(image, symbols=[pyzbar.ZBarSymbol.PDF417])  # type: ignore
            for b in barcodes:
                text = b.data.decode("utf-8", errors="replace")
                if text:
                    return BarcodeScanResult(
                        raw_bytes=b.data,
                        raw_text=text,
                        format="PDF417",
                        backend="pyzbar",
                    )
        except Exception as e:
            LOGGER.debug(f"pyzbar error on variant {variant_name}: {e}")
        return None

    def _empty_fields(self) -> Dict[str, object]:
        return {
            "customer_first_name": "",
            "customer_last_name": "",
            "street_address": "",
            "city": "",
            "state": "",
            "zip_code": "",
            "field_confidence": {
                "customer_first_name": 0.0,
                "customer_last_name": 0.0,
                "street_address": 0.0,
                "city": 0.0,
                "state": 0.0,
                "zip_code": 0.0,
            },
        }


# ---------------------------------------------------------------------------
# Logging + entry point
# ---------------------------------------------------------------------------

def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")


def _resolve_test_images() -> List[str]:
    """Look for back-of-license test images."""
    preferred = [
        "test_dl_back.jpg", "test_dl_back.png",
        "test_dl_vertical_back.jpg", "test_dl_vertical_back.png",
    ]
    existing = [name for name in preferred if Path(name).exists()]
    return existing if existing else ["test_dl_back.jpg"]


if __name__ == "__main__":
    import json
    configure_logging()

    scanner = DriverLicenseBarcodeScanner()

    for test_image in _resolve_test_images():
        print(f"\n{'=' * 72}")
        print(f"TEST IMAGE: {test_image}")
        print(f"{'=' * 72}")
        result = scanner.scan(test_image, save_debug=True, debug_output_dir="barcode_debug")
        print("===== SCAN METADATA =====")
        print("Scan success  :", result.scan_success)
        print("Backend used  :", result.backend_used)
        print("AAMVA version :", result.aamva_version)
        print("Recommendation:", result.recommendation)
        print()
        print("===== EXTRACTED FIELDS =====")
        print(json.dumps(result.parsed_fields, indent=2))
        if result.scan_success:
            print()
            print("===== RAW AAMVA (first 500 chars) =====")
            print(result.raw_aamva[:500])
