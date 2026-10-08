"""
Shared capture flow for the paper documents (insurance card printout,
registration renewal notice). From INS Code/insurance_card_capture_rev1.py
and RRN Code/registration_renewal_capture_rev1.py, which were identical
apart from the per-document settings now held in a DocumentProfile.

Flow: live preview with a large guide box and a TOP marker; a background
thread runs Tesseract orientation detection (OSD) so the customer can fix
the page before capturing; the capture is perspective-corrected, turned
upright and read by the document's OCR engine; a review screen confirms.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

import cv2
import numpy as np
import pytesseract

from . import ui
from .camera import Camera
from .config import ScannerConfig
from .results import ImageSource, ScanResult, load_image

LOGGER = logging.getLogger(__name__)

GUIDE_FRAC = 0.92                 # guide box size as a fraction of the frame
MIN_DOC_FRAC = 0.08               # smallest document (fraction of frame) to accept
CROP_MARGIN = 0.02                # border kept around the detected page
OSD_MAX_SIDE = 2000               # OSD input size: small text needs resolution
OSD_CONF_MIN = 1.0                # below this, OSD's answer is treated as a guess
OSD_FRESH_S = 3.0                 # a preview OSD result this recent is reused at capture


@dataclass(frozen=True)
class DocumentProfile:
    document: str                 # ScanResult.document
    title: str                    # header title
    noun: str                     # "insurance card" — used in messages
    image_name: str               # ScanResult.images key
    instruction: str              # preview instruction
    top_marker: str               # where the page's top goes: "top" or "left"
    sideways_ok: bool             # sideways text is expected (rotated after capture)
    sharpness_min: float
    required_keys: Tuple[str, ...]
    # Review screen: ((section title or None, ((label, (field keys...)), ...)), ...)
    review_layout: Tuple[Tuple[Optional[str], Tuple[Tuple[str, Tuple[str, ...]], ...]], ...]
    review_image_frac: float      # width share of the image column on the review screen
    keep_whole_offline: bool      # offline photo with no page edges: OCR the whole image
    make_ocr: Callable[[ScannerConfig], Any]
    read_fields: Callable[[Any, np.ndarray], Dict[str, str]]


# ---------------------------------------------------------------------------
# Page detection + cropping
# ---------------------------------------------------------------------------

def guide_rect(width: int, height: int) -> Tuple[int, int, int, int]:
    gw, gh = int(width * GUIDE_FRAC), int(height * GUIDE_FRAC)
    return (width - gw) // 2, (height - gh) // 2, gw, gh


def find_document_corners(image: np.ndarray, min_area_frac: float = MIN_DOC_FRAC
                          ) -> Optional[np.ndarray]:
    """
    The page's four corners (TL, TR, BR, BL) in `image` pixels, or None. No
    aspect check: a full page, a cut-out card and a folded page all differ.
    """
    scale = min(1.0, 1000.0 / max(image.shape[:2]))
    small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    edges = cv2.dilate(cv2.Canny(gray, 50, 150), np.ones((3, 3), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    min_area = min_area_frac * small.shape[0] * small.shape[1]
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:20]:
        if cv2.contourArea(contour) < min_area:
            break
        approx = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        (_, _), (rw, rh), _ = cv2.minAreaRect(approx)
        if max(rw, rh) / max(1.0, min(rw, rh)) > 2.5:   # a strip, not a page
            continue
        return _order_points(approx.reshape(4, 2).astype(np.float32) / scale)
    return None


def _order_points(pts: np.ndarray) -> np.ndarray:
    rect = np.zeros((4, 2), dtype=np.float32)
    s = pts.sum(axis=1)
    rect[0], rect[2] = pts[np.argmin(s)], pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1], rect[3] = pts[np.argmin(diff)], pts[np.argmax(diff)]
    return rect


def warp_document(image: np.ndarray, corners: np.ndarray) -> np.ndarray:
    """Perspective-corrects the page (plus CROP_MARGIN). Rotation is decided by OSD, not shape."""
    center = corners.mean(axis=0)
    corners = center + (corners - center) * (1.0 + 2.0 * CROP_MARGIN)
    h, w = image.shape[:2]
    corners[:, 0] = np.clip(corners[:, 0], 0, w - 1)
    corners[:, 1] = np.clip(corners[:, 1], 0, h - 1)
    tl, tr, br, bl = corners.astype(np.float32)
    width = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
    height = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
    dst = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
                   dtype=np.float32)
    M = cv2.getPerspectiveTransform(np.array([tl, tr, br, bl]), dst)
    return cv2.warpPerspective(image, M, (width, height), flags=cv2.INTER_CUBIC)


def find_paper_region(image: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
    """Bounding box of the largest bright area (Otsu threshold adapts to the kiosk lighting)."""
    scale = min(1.0, 1000.0 / max(image.shape[:2]))
    small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Close the gaps left by dark text so the page is one blob
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    biggest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(biggest) < MIN_DOC_FRAC * small.shape[0] * small.shape[1]:
        return None
    x, y, w, h = cv2.boundingRect(biggest)
    return int(x / scale), int(y / scale), int(w / scale), int(h / scale)


def crop_document(image: np.ndarray, corners: Optional[np.ndarray]) -> np.ndarray:
    """Warped page if all four corners were found; else the bright paper inside the guide; else the guide."""
    if corners is not None:
        return warp_document(image, corners)
    h, w = image.shape[:2]
    gx, gy, gw, gh = guide_rect(w, h)
    guide = image[gy:gy + gh, gx:gx + gw]
    paper = find_paper_region(guide)
    if paper is not None:
        px, py, pw, ph = paper
        return guide[py:py + ph, px:px + pw].copy()
    return guide.copy()


# ---------------------------------------------------------------------------
# Orientation (Tesseract OSD)
# ---------------------------------------------------------------------------

_ROTATE_CODES = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
                 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


def detect_rotation(document: np.ndarray) -> Tuple[Optional[int], float]:
    """(clockwise degrees to make the text upright, confidence), or (None, 0.0) if unreadable."""
    # Upscaling a small crop (up to 2x) noticeably raised OSD confidence
    scale = min(2.0, OSD_MAX_SIDE / float(max(document.shape[:2])))
    interp = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
    gray = cv2.cvtColor(cv2.resize(document, None, fx=scale, fy=scale, interpolation=interp),
                        cv2.COLOR_BGR2GRAY)
    # Uneven kiosk lighting can make OSD find "too few characters" on sharp
    # text; a local threshold fixes that
    adaptive = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                     cv2.THRESH_BINARY, 51, 15)
    for variant in (gray, adaptive):
        try:
            osd = pytesseract.image_to_osd(variant, output_type=pytesseract.Output.DICT)
            return int(osd["rotate"]), float(osd["orientation_conf"])
        except pytesseract.TesseractError:
            continue         # "Too few characters" — blank or unreadable
    return None, 0.0


def rotate_upright(document: np.ndarray, rotate: Optional[int]) -> np.ndarray:
    code = _ROTATE_CODES.get(rotate or 0)
    return cv2.rotate(document, code) if code is not None else document


class OrientationWorker:
    """Runs OSD in the background on the newest crop so the preview stays smooth (~1-2 s per call)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: Optional[np.ndarray] = None
        self._wake = threading.Event()
        self._stop = False
        self._running = False
        self.result: Tuple[Optional[int], float] = (None, 0.0)
        self.updated_at = 0.0
        threading.Thread(target=self._run, daemon=True).start()

    def submit(self, document: np.ndarray) -> None:
        with self._lock:
            self._pending = document
        self._wake.set()

    def reset(self) -> None:
        with self._lock:
            self.result, self.updated_at = (None, 0.0), 0.0

    def busy(self) -> bool:
        return self._wake.is_set() or self._running

    def stop(self) -> None:
        self._stop = True
        self._wake.set()

    def _run(self) -> None:
        while not self._stop:
            self._wake.wait()
            with self._lock:
                document, self._pending = self._pending, None
                self._running = document is not None
            self._wake.clear()
            if document is None or self._stop:
                continue
            result = detect_rotation(document)
            with self._lock:
                self.result, self.updated_at = result, time.time()
                self._running = False


def _orientation_status(profile: DocumentProfile, rotate: Optional[int], conf: float
                        ) -> Tuple[str, str]:
    """(message, pill kind) for the preview."""
    if rotate is None:
        return f"Checking orientation — place the {profile.noun} in the frame", "info"
    if conf < OSD_CONF_MIN:
        return "Checking orientation…", "info"
    if rotate == 0:
        return "Orientation OK", "ok"
    if rotate == 180:
        return "Upside down — turn the page around", "error"
    if profile.sideways_ok:
        return "Sideways OK — it will be straightened automatically", "ok"
    return "Sideways — rotate the page so the text reads left to right", "error"


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _filled(profile: DocumentProfile, form: Dict[str, str]) -> int:
    return sum(1 for k in profile.required_keys if form.get(k))


def orient_and_read(profile: DocumentProfile, ocr: Any, document: np.ndarray,
                    osd: Optional[Tuple[Optional[int], float]] = None
                    ) -> Tuple[np.ndarray, Dict[str, str], str]:
    """
    Makes the document upright and reads it. Returns (document, form, note).
    A confident OSD answer is applied directly. A low-confidence one is only
    tried if the page as-is reads poorly, and the better read is kept — so a
    wrong guess can never make an upright page worse. `osd` is a confident
    result the live preview just found; it saves re-running OSD (~3-5 s).
    """
    if osd is not None and osd[0] is not None and osd[1] >= OSD_CONF_MIN:
        rotate, conf = osd
        LOGGER.info(f"OSD (from preview): rotate {rotate} deg, confidence {conf:.2f}")
    else:
        rotate, conf = detect_rotation(document)
        LOGGER.info(f"OSD: rotate {rotate} deg, confidence {conf:.2f}")
    if rotate and conf >= OSD_CONF_MIN:
        document = rotate_upright(document, rotate)
        return document, profile.read_fields(ocr, document), ""

    form = profile.read_fields(ocr, document)
    filled = _filled(profile, form)
    if rotate and filled < len(profile.required_keys) - 2:
        turned = rotate_upright(document, rotate)
        turned_form = profile.read_fields(ocr, turned)
        if _filled(profile, turned_form) > filled:
            return turned, turned_form, ""
    note = "" if rotate == 0 and conf >= OSD_CONF_MIN else "Orientation uncertain — check the image is upright"
    return document, form, note


def read(profile: DocumentProfile, ocr: Any, image: ImageSource) -> ScanResult:
    """Same pipeline on an existing photo (no camera)."""
    image = load_image(image)
    corners = find_document_corners(image)
    if corners is not None:
        document = crop_document(image, corners)
    else:
        # No page edges (e.g. a flatbed scan, which IS the page)
        document = image if profile.keep_whole_offline else crop_document(image, None)
    document, form, note = orient_and_read(profile, ocr, document)
    return ScanResult(profile.document, form, {profile.image_name: document}, [note] if note else [])


# ---------------------------------------------------------------------------
# Live capture
# ---------------------------------------------------------------------------

def _draw_top_marker(s: ui.Screen, rect: Tuple[float, float, float, float], where: str) -> None:
    """A 'TOP' tab sitting on the guide's top (or left) edge, with an arrow pointing out."""
    x, y, w, h = rect
    tw, th = 92, 34
    cx, cy = (x, y + h / 2) if where == "left" else (x + w / 2, y)
    x0, y0 = cx - tw / 2, cy - th / 2
    s.rect(x0, y0, x0 + tw, y0 + th, ui.ACCENT, radius=th // 2)
    ax, ay = int(x0 + 24), int(cy)
    if where == "left":
        tri = np.int32([[ax - 7, ay], [ax + 5, ay - 7], [ax + 5, ay + 7]])
    else:
        tri = np.int32([[ax, ay - 7], [ax - 7, ay + 5], [ax + 7, ay + 5]])
    cv2.fillPoly(s.img, [tri], ui.WHITE, cv2.LINE_AA)
    s.text((x0 + 58, cy), "TOP", 15, ui.WHITE, "bold", anchor="mm")


def _preview(profile: DocumentProfile, config: ScannerConfig, preview: np.ndarray,
             frame: np.ndarray, worker: OrientationWorker, focus: int, toast: str
             ) -> Tuple[np.ndarray, Optional[np.ndarray], float]:
    """Returns (screen image, full-res corners or None, sharpness)."""
    video = preview.copy()
    h, w = video.shape[:2]
    gx, gy, gw, gh = guide_rect(w, h)
    ui.dim_outside(video, (gx, gy, gw, gh))

    corners = find_document_corners(preview[gy:gy + gh, gx:gx + gw])
    full_corners = None
    if corners is not None:
        corners = corners + np.float32([gx, gy])
        overlay = video.copy()
        cv2.fillPoly(overlay, [corners.astype(np.int32)], ui.SUCCESS)
        cv2.addWeighted(overlay, 0.12, video, 0.88, 0, video)
        cv2.polylines(video, [corners.astype(np.int32)], True, ui.SUCCESS, 3, cv2.LINE_AA)
        full_corners = corners * np.float32([frame.shape[1] / w, frame.shape[0] / h])

    region = preview[gy:gy + gh, gx:gx + gw] if corners is None else warp_document(preview, corners)
    sharpness = cv2.Laplacian(cv2.cvtColor(region, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
    sharp = sharpness >= profile.sharpness_min
    ui.guide_frame(video, (gx, gy, gw, gh), ui.SUCCESS if corners is not None and sharp else ui.WHITE)

    # Feed the latest full-res crop to the OSD thread whenever it's idle
    if not worker.busy():
        worker.submit(crop_document(frame, full_corners))
    orient_msg, orient_kind = _orientation_status(profile, *worker.result)

    s, (ox, oy, k) = ui.place_video(config.preview_res, video)
    _draw_top_marker(s, (ox + gx * k, oy + gy * k, gw * k, gh * k), profile.top_marker)
    ui.header(s, profile.title, profile.instruction, ("Position", "Confirm"), 0)
    # Both messages sit at the bottom: the page fills the frame, and its top
    # (with the TOP tab) is what the customer is lining up
    status_y = s.h - ui.FOOTER_H - 62
    ui.pill(s, s.w / 2, status_y - 52, orient_msg, orient_kind)
    if toast:
        ui.pill(s, s.w / 2, status_y, toast, "error")
    elif not sharp:
        ui.pill(s, s.w / 2, status_y, "Hold still — bringing the page into focus", "warn")
    elif corners is None:
        ui.pill(s, s.w / 2, status_y, "Page edges not found — you can still capture", "info")
    else:
        ui.pill(s, s.w / 2, status_y, "Ready — press SPACE to capture", "ok")
    hints = [("SPACE", "Capture"), ("ESC", "Cancel")]
    diag = ""
    if config.diagnostics:
        hints.insert(1, ("[ ]", "Focus"))
        diag = f"Sharpness {sharpness:.0f}   Focus {focus}"
    ui.footer(s, hints, diag)
    return s.finish(), full_corners, sharpness


def _review_sections(profile: DocumentProfile, form: Dict[str, str]) -> list:
    sections = []
    for title, rows in profile.review_layout:
        out = []
        for label, keys in rows:
            value = " ".join(form[k] for k in keys if form.get(k))
            out.append((label, value, all(form.get(k) for k in keys)))
        sections.append((title, out))
    return sections


def capture(profile: DocumentProfile, ocr: Any, camera: Camera, window: ui.Window,
            config: ScannerConfig) -> Optional[ScanResult]:
    """Preview -> capture -> review. Returns the result, or None if the customer cancelled."""
    worker = OrientationWorker()
    toast, toast_until = "", 0.0
    t0 = time.time()
    try:
        while True:
            frame = camera.read()
            if frame is None:
                if ui.is_cancel(window.show(ui.starting_screen(config.preview_res, profile.title,
                                                               time.time() - t0))):
                    return None
                continue
            preview = cv2.resize(frame, config.preview_res, interpolation=cv2.INTER_AREA)
            if time.time() > toast_until:
                toast = ""
            screen, corners, sharpness = _preview(profile, config, preview, frame, worker,
                                                  camera.focus, toast)
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
            if sharpness < profile.sharpness_min:
                toast, toast_until = "Image is blurry — hold the page still", time.time() + 3
                continue

            # Reuse the preview's orientation if fresh and confident, else check the capture
            fresh = time.time() - worker.updated_at < OSD_FRESH_S
            document, form, note = ui.run_busy(
                window, screen, f"Reading {profile.noun}…", "This usually takes a few seconds",
                orient_and_read, profile, ocr, crop_document(frame, corners),
                worker.result if fresh else None)
            if corners is None:
                note = (note + "  ·  " if note else "") + "Page edges not found — cropped to the paper"
            review = ui.review_screen(config.preview_res, "Confirm your details",
                                      f"Check the information read from the {profile.noun}",
                                      [("", document)], _review_sections(profile, form),
                                      ("Position", "Confirm"), 1, note, profile.review_image_frac)
            while True:
                key = window.show(review, wait_ms=20)
                if key in ui.KEYS_ENTER:
                    return ScanResult(profile.document, form, {profile.image_name: document},
                                      [note] if note else [])
                if key in (ord("r"), ord("R")) or ui.is_cancel(key):
                    break
            if ui.is_cancel(key):
                return None
            worker.reset()
    finally:
        worker.stop()
