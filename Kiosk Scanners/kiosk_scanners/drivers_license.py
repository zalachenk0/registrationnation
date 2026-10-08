"""
Driver's license scanner (from DL Code/driver_license_capture_rev6.py).

Captures the FRONT, then the BACK of the license; the back's PDF417 barcode
is decoded with zxing-cpp and parsed (AAMVA) into the kiosk form fields.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Dict, Optional, Tuple

import cv2
import numpy as np
import zxingcpp

from . import ui
from .camera import Camera
from .config import ScannerConfig
from .results import DRIVERS_LICENSE, ImageSource, ScannerError, ScanResult, load_image

LOGGER = logging.getLogger(__name__)

FIELD_LABELS = {
    "customer_first_name": "First name",
    "customer_last_name": "Last name",
    "street_address": "Street address",
    "city": "City",
    "state": "State",
    "zip_code": "ZIP code",
}

ID1_ASPECT = 85.6 / 54.0          # standard ID card (CR80) width / height
GUIDE_WIDTH_FRAC = 0.36           # guide width as a fraction of the frame (card is ~27%)
CROP_MARGIN = 0.05                # border kept around the card so no edge is ever cut off
DISPLAY_ZOOM = 2.0                # preview shows a region this many guide-widths wide
SHARPNESS_MIN = 300               # blurry ~5-70, in focus ~800 at the current mount
TITLE = "Driver's License"
STEPS = ("Front", "Back", "Confirm")


# ---------------------------------------------------------------------------
# Card detection + cropping
# ---------------------------------------------------------------------------

def guide_rect(width: int, height: int) -> Tuple[int, int, int, int]:
    """Centered ID-1 shaped guide box as (x, y, w, h) for a frame size."""
    gw = int(width * GUIDE_WIDTH_FRAC)
    gh = int(gw / ID1_ASPECT)
    return (width - gw) // 2, (height - gh) // 2, gw, gh


def find_card_corners(image: np.ndarray, min_area_frac: float = 0.15) -> Optional[np.ndarray]:
    """
    The license's four corners (TL, TR, BR, BL) in `image` pixels, or None
    if no card-shaped quadrilateral covering `min_area_frac` of it is found.
    """
    scale = min(1.0, 1000.0 / max(image.shape[:2]))
    small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    edges = cv2.dilate(cv2.Canny(gray, 50, 150), np.ones((3, 3), np.uint8), iterations=2)

    # RETR_LIST so a card lying on a larger rectangle (clipboard, desk mat) is still found
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    min_area = min_area_frac * small.shape[0] * small.shape[1]
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:20]:
        if cv2.contourArea(contour) < min_area:
            break
        approx = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
        if len(approx) != 4:
            continue
        (_, _), (rw, rh), _ = cv2.minAreaRect(approx)
        aspect = max(rw, rh) / max(1.0, min(rw, rh))
        if 1.45 <= aspect <= 1.75:     # ID-1 is 1.586; letter paper/clipboards are ~1.3-1.4
            return order_points(approx.reshape(4, 2).astype(np.float32) / scale)
    return None


def order_points(pts: np.ndarray) -> np.ndarray:
    rect = np.zeros((4, 2), dtype=np.float32)
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]      # top-left
    rect[2] = pts[np.argmax(s)]      # bottom-right
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]   # top-right
    rect[3] = pts[np.argmax(diff)]   # bottom-left
    return rect


def warp_card(image: np.ndarray, corners: np.ndarray) -> np.ndarray:
    """Perspective-corrects the card (plus CROP_MARGIN border) to a flat, landscape image."""
    center = corners.mean(axis=0)
    corners = center + (corners - center) * (1.0 + 2.0 * CROP_MARGIN)
    h, w = image.shape[:2]
    corners[:, 0] = np.clip(corners[:, 0], 0, w - 1)
    corners[:, 1] = np.clip(corners[:, 1], 0, h - 1)
    tl, tr, br, bl = corners.astype(np.float32)
    width = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
    height = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
    src = np.array([tl, tr, br, bl], dtype=np.float32)
    if height > width:
        # Card held portrait — rotate corner order so output is landscape
        src = np.array([tr, br, bl, tl], dtype=np.float32)
        width = height
    height = int(width / ID1_ASPECT)
    dst = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
                   dtype=np.float32)
    return cv2.warpPerspective(image, cv2.getPerspectiveTransform(src, dst), (width, height),
                               flags=cv2.INTER_CUBIC)


def crop_card(image: np.ndarray) -> Optional[np.ndarray]:
    """Full-res card crop found near the guide box, or None if all four edges aren't found."""
    h, w = image.shape[:2]
    gx, gy, gw, gh = guide_rect(w, h)
    # Search a region 15% larger than the guide so slightly misaligned cards still count
    pad_x, pad_y = int(gw * 0.15), int(gh * 0.15)
    x0, y0 = max(0, gx - pad_x), max(0, gy - pad_y)
    x1, y1 = min(w, gx + gw + pad_x), min(h, gy + gh + pad_y)
    corners = find_card_corners(image[y0:y1, x0:x1], min_area_frac=0.25)
    if corners is None:
        return None
    # Warp from the full frame so the margin isn't clipped by the search region
    return warp_card(image, corners + np.float32([x0, y0]))


def crop_card_offline(image: np.ndarray) -> np.ndarray:
    """Photos not framed to the guide: search the whole image, else keep it whole."""
    corners = find_card_corners(image, min_area_frac=0.01)
    return warp_card(image, corners) if corners is not None else image


# ---------------------------------------------------------------------------
# PDF417 barcode
# ---------------------------------------------------------------------------

def decode_pdf417(card: np.ndarray) -> Optional[str]:
    """
    Tries a few image variants until zxing-cpp decodes the PDF417 barcode.
    The red/pink holographic overlay on TX licenses is suppressed by
    favoring the blue and green channels.
    """
    gray = cv2.cvtColor(card, cv2.COLOR_BGR2GRAY)
    b, g, r = cv2.split(card.astype(np.float32))
    suppressed = np.clip(b * 0.5 + g * 0.4 + r * 0.1, 0, 255).astype(np.uint8)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    variants = [
        ("color", card),
        ("gray", gray),
        ("blue_channel", card[:, :, 0]),
        ("clahe_suppressed", clahe.apply(suppressed)),
        ("upscaled_suppressed", cv2.resize(suppressed, None, fx=2.0, fy=2.0,
                                           interpolation=cv2.INTER_CUBIC)),
    ]
    for name, img in variants:
        try:
            results = zxingcpp.read_barcodes(img, formats=zxingcpp.BarcodeFormat.PDF417)
        except Exception as e:
            LOGGER.debug(f"zxingcpp error on {name}: {e}")
            continue
        for result in results:
            if result.valid and result.text:
                LOGGER.info(f"PDF417 decoded using variant: {name}")
                return result.text
    return None


def decode_pdf417_tiled(image: np.ndarray) -> Optional[str]:
    """Slower fallback for photos where the card is small: scans overlapping tiles."""
    h, w = image.shape[:2]
    th, tw = h // 3, w // 2
    for y in range(0, h - th + 1, th // 2):
        for x in range(0, w - tw + 1, tw // 2):
            text = decode_pdf417(image[y:y + th, x:x + tw])
            if text:
                return text
    return None


# ---------------------------------------------------------------------------
# AAMVA -> form fields
# ---------------------------------------------------------------------------

def parse_aamva(raw: str) -> Dict[str, str]:
    """{3-char AAMVA code: value}, e.g. {'DCS': 'SMITH', 'DAG': '...'}."""
    # TX licenses separate fields with plain LF; some decoders render control
    # characters as literal "<LF>" style tokens
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    for token in ("<LF>", "<CR>", "<RS>", "<GS>"):
        normalized = normalized.replace(token, "\n")
    fields: Dict[str, str] = {}
    for line in normalized.splitlines():
        line = line.strip().strip("\x00")
        if len(line) < 4 or not re.match(r"^[A-Z]{2}[A-Z0-9]$", line[:3]):
            continue
        value = line[3:].strip()
        if value:
            fields[line[:3]] = value
    return fields


def extract_form_fields(aamva: Dict[str, str]) -> Dict[str, str]:
    last, first = _extract_name(aamva)
    street = aamva.get("DAG", "")
    if aamva.get("DAH"):
        street = f"{street} {aamva['DAH']}"
    # DAK is 9 digits padded with zeros: "752010000" -> "75201"
    zip_digits = re.sub(r"[^0-9]", "", aamva.get("DAK", ""))
    return {
        "customer_first_name": first,
        "customer_last_name": last,
        "street_address": street.title(),
        "city": aamva.get("DAI", "").title(),
        "state": aamva.get("DAJ", "").upper(),
        "zip_code": zip_digits[:5],
    }


def _extract_name(aamva: Dict[str, str]) -> Tuple[str, str]:
    """DCS = last, DAC (or DCT) = first [+ middle]. Legacy DAA = "LAST$FIRST$MIDDLE" or "LAST,FIRST"."""
    last = aamva.get("DCS", "")
    first = aamva.get("DAC") or aamva.get("DCT", "")
    if last and first:
        return last.title(), re.split(r"[,\s]+", first)[0].title()
    daa = aamva.get("DAA", "")
    for sep in ("$", ","):
        if sep in daa:
            parts = [p.strip() for p in daa.split(sep)]
            return parts[0].title(), (parts[1].title() if len(parts) > 1 else "")
    return last.title(), first.title()


# ---------------------------------------------------------------------------
# Offline (image files, no camera)
# ---------------------------------------------------------------------------

def read(front: ImageSource, back: ImageSource) -> ScanResult:
    """Same pipeline on existing photos of the front and back."""
    front_img, back_img = load_image(front), load_image(back)
    front_card, back_card = crop_card_offline(front_img), crop_card_offline(back_img)
    barcode = decode_pdf417(back_card) or decode_pdf417_tiled(back_img)
    if barcode is None:
        raise ScannerError("Barcode not readable in the back image")
    notes = [] if back_card is not back_img else ["Card edges not found in back image - saved uncropped"]
    return ScanResult(DRIVERS_LICENSE, extract_form_fields(parse_aamva(barcode)),
                      {"front": front_card, "back": back_card}, notes)


# ---------------------------------------------------------------------------
# Live capture
# ---------------------------------------------------------------------------

def _zoom(frame: np.ndarray) -> Tuple[np.ndarray, Tuple[int, int, float]]:
    """Crops the preview to DISPLAY_ZOOM guide-widths so the card looks big. Returns (image, (x0, y0, scale))."""
    h, w = frame.shape[:2]
    _, _, gw, _ = guide_rect(w, h)
    zw = min(w, int(gw * DISPLAY_ZOOM))
    zh = min(h, int(zw * h / w))
    x0, y0 = (w - zw) // 2, (h - zh) // 2
    return (cv2.resize(frame[y0:y0 + zh, x0:x0 + zw], (w, h), interpolation=cv2.INTER_LINEAR),
            (x0, y0, w / zw))


def _preview(preview: np.ndarray, config: ScannerConfig, side: str, step: int, focus: int,
             toast: str) -> Tuple[np.ndarray, Optional[np.ndarray], float]:
    """Returns (screen image, card corners in preview pixels or None, sharpness)."""
    h, w = preview.shape[:2]
    gx, gy, gw, gh = guide_rect(w, h)
    guide = preview[gy:gy + gh, gx:gx + gw]
    corners = find_card_corners(guide, min_area_frac=0.35)
    sharpness = cv2.Laplacian(cv2.cvtColor(guide, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
    ready = corners is not None and sharpness >= SHARPNESS_MIN

    video, (zx, zy, zs) = _zoom(preview)
    zg = (int((gx - zx) * zs), int((gy - zy) * zs), int(gw * zs), int(gh * zs))
    ui.dim_outside(video, zg)
    color = ui.SUCCESS if ready else ui.WARNING if corners is not None else ui.WHITE
    ui.guide_frame(video, zg, color)

    s, (ox, oy, k) = ui.place_video(config.preview_res, video)
    ui.header(s, TITLE, "Scan both sides of the license", STEPS, step)
    ui.pill(s, s.w / 2, ui.HEADER_H + 18, f"Place the {side} of the license inside the frame", "info")
    status_y = s.h - ui.FOOTER_H - 62
    if toast:
        ui.pill(s, s.w / 2, status_y, toast, "error")
    elif ready:
        ui.pill(s, s.w / 2, status_y, "Ready — press SPACE to capture", "ok")
    elif corners is not None:
        ui.pill(s, s.w / 2, status_y, "Hold still — bringing the card into focus", "warn")
    else:
        ui.pill(s, s.w / 2, status_y, "Looking for the card…", "info")
    hints = [("SPACE", "Capture"), ("ESC", "Cancel")]
    diag = ""
    if config.diagnostics:
        hints.insert(1, ("[ ]", "Focus"))
        diag = f"Sharpness {sharpness:.0f}   Focus {focus}"
    ui.footer(s, hints, diag)
    full_corners = corners + np.float32([gx, gy]) if corners is not None else None
    return s.finish(), full_corners, sharpness


def _capture_side(camera: Camera, window: ui.Window, config: ScannerConfig, side: str,
                  step: int, needs_barcode: bool) -> Optional[Tuple[np.ndarray, Optional[str]]]:
    """Preview -> capture -> review for one side. Returns (card, barcode text) or None if cancelled."""
    toast, toast_until = "", 0.0
    t0 = time.time()
    while True:
        frame = camera.read()
        if frame is None:
            if ui.is_cancel(window.show(ui.starting_screen(config.preview_res, TITLE, time.time() - t0))):
                return None
            continue
        preview = cv2.resize(frame, config.preview_res, interpolation=cv2.INTER_AREA)
        if time.time() > toast_until:
            toast = ""
        screen, corners, sharpness = _preview(preview, config, side, step, camera.focus, toast)
        key = window.show(screen)

        if ui.is_cancel(key):
            return None
        if key == ord("f"):
            camera.refocus()
            continue
        if config.diagnostics and key in (ord("["), ord("]")):
            camera.set_focus(camera.focus + (config.focus_step if key == ord("]") else -config.focus_step))
            continue
        if key != ui.KEY_SPACE:
            continue

        # Never save a partial or blurry card
        if sharpness < SHARPNESS_MIN:
            toast, toast_until = "Image is blurry — hold the card still", time.time() + 3
            continue
        if corners is None:
            toast, toast_until = "Whole card not found — place it fully inside the frame", time.time() + 3
            continue
        # The card edges are re-found at full resolution for a precise crop. That
        # search uses a wider area at a different scale and on the kiosk it misses
        # cards the preview found, so fall back to the preview's corners (all four
        # edges were seen inside the guide).
        card = crop_card(frame)
        if card is None:
            LOGGER.info("Full-res edge search missed the card - using the preview's corners")
            k = np.float32([frame.shape[1] / preview.shape[1], frame.shape[0] / preview.shape[0]])
            card = warp_card(frame, corners * k)

        barcode = None
        if needs_barcode:
            barcode = ui.run_busy(window, screen, "Reading barcode…", "This takes a moment",
                                  decode_pdf417, card)
            if barcode is None:
                toast, toast_until = "Barcode not readable — reduce glare and try again", time.time() + 4
                continue

        if needs_barcode:
            form = extract_form_fields(parse_aamva(barcode))
            sections = [(None, [(FIELD_LABELS[k], v, bool(v)) for k, v in form.items()])]
            title, subtitle = "Confirm your details", "Check the information read from the barcode"
        else:
            sections = []
            title, subtitle = f"Check the {side.lower()}", "Make sure the whole card is visible and clear"
        review = ui.review_screen(config.preview_res, title, subtitle, [(side.title(), card)],
                                  sections, STEPS, step + (1 if needs_barcode else 0),
                                  image_frac=0.5)
        while True:
            key = window.show(review, wait_ms=20)
            if key in ui.KEYS_ENTER:
                return card, barcode
            if key in (ord("r"), ord("R")) or ui.is_cancel(key):
                break
        if ui.is_cancel(key):
            return None


def capture(camera: Camera, window: ui.Window, config: ScannerConfig) -> Optional[ScanResult]:
    """Front, then back. Returns the result, or None if the customer cancelled."""
    front = _capture_side(camera, window, config, "FRONT", 0, needs_barcode=False)
    if front is None:
        return None
    back = _capture_side(camera, window, config, "BACK", 1, needs_barcode=True)
    if back is None:
        return None
    fields = extract_form_fields(parse_aamva(back[1] or ""))
    return ScanResult(DRIVERS_LICENSE, fields, {"front": front[0], "back": back[0]})
