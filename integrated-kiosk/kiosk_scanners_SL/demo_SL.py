# ============================================================================
# File:         demo_SL.py
# Written by:   SL (Seth Leija)
# What it does: Demonstration launcher that runs any of the three scans on a
#               computer.
# Team-written: Logic written by SL (Seth Leija). Outside code is used only
#               through the third-party sources listed below.
# Third-party:  OpenCV; NumPy
# ============================================================================
"""
Demonstration launcher: a home screen to run any of the three scans on
demand, showing the last result for each.

    py -m kiosk_scanners_SL demo [--fullscreen] [--camera N]
"""

from __future__ import annotations

import json
import logging
from typing import Dict, Optional

import cv2
import numpy as np

from . import drivers_license_SL as drivers_license, insurance_SL as insurance, registration_SL as registration, ui_SL as ui
from .config_SL import ScannerConfig
from .results_SL import ScannerError, ScanResult
from .scanner_SL import KioskScanner

LOGGER = logging.getLogger(__name__)

# (key, document, title, description, field labels)
TILES = (
    ("1", "drivers_license", "Driver's License",
     "Front and back. Name and address are read from the barcode.", drivers_license.FIELD_LABELS),
    ("2", "insurance_card", "Insurance Card",
     "Card or printout. Insurer, policy, expiration and vehicle.", insurance.FIELD_LABELS),
    ("3", "registration_renewal", "Registration Renewal",
     "Renewal notice. Vehicle, fees, owner and mailing address.", {}),
)


def _tile_icon(s: ui.Screen, x: int, y: int, document: str) -> None:
    """Simple line icon for each document type, 64x64 at (x, y)."""
    s.rect(x, y, x + 64, y + 64, ui.ACCENT_SOFT, radius=14)
    c = ui.ACCENT
    if document == "drivers_license":
        s.rect(x + 12, y + 18, x + 52, y + 46, c, radius=4, border=2)
        cv2.circle(s.img, (x + 22, y + 30), 4, c, -1, cv2.LINE_AA)
        cv2.line(s.img, (x + 18, y + 40), (x + 27, y + 40), c, 2, cv2.LINE_AA)
        for dy in (28, 34, 40):
            cv2.line(s.img, (x + 32, y + dy), (x + 46, y + dy), c, 2, cv2.LINE_AA)
    elif document == "insurance_card":
        shield = np.int32([[x + 32, y + 12], [x + 50, y + 19], [x + 48, y + 38], [x + 32, y + 52],
                           [x + 16, y + 38], [x + 14, y + 19]])
        cv2.polylines(s.img, [shield], True, c, 2, cv2.LINE_AA)
        check = np.int32([[x + 24, y + 32], [x + 30, y + 38], [x + 41, y + 26]])
        cv2.polylines(s.img, [check], False, c, 3, cv2.LINE_AA)
    else:
        s.rect(x + 18, y + 10, x + 46, y + 54, c, radius=3, border=2)
        for dy in (20, 27, 34, 41):
            cv2.line(s.img, (x + 24, y + dy), (x + 40, y + dy), c, 2, cv2.LINE_AA)


def home_screen(size, results: Dict[str, Optional[ScanResult]], last: Optional[str],
                message: str = "", company: str = "Texas Tag & Title") -> np.ndarray:
    s = ui.Screen.blank(size)
    ui.header(s, company, "Document scanner — select a document to scan")
    ui.footer(s, [("1-3", "Scan"), ("F", "Refocus"), ("C", "Clear"), ("ESC", "Exit")])

    pad, gap = 32, 24
    tile_w = (s.w - 2 * pad - 2 * gap) / 3
    ty0, ty1 = ui.HEADER_H + 28, ui.HEADER_H + 28 + 250
    for i, (key, document, title, desc, _) in enumerate(TILES):
        x0 = pad + i * (tile_w + gap)
        x1 = x0 + tile_w
        selected = document == last
        s.rect(x0, ty0, x1, ty1, ui.CARD, radius=18)
        s.rect(x0, ty0, x1, ty1, ui.ACCENT if selected else ui.BORDER, radius=18,
               border=2 if selected else 1)
        _tile_icon(s, int(x0 + 24), int(ty0 + 24), document)
        ui.keycap(s, x1 - 24 - 34, ty0 + 40, key)
        s.text((x0 + 24, ty0 + 108), title, 22, ui.TEXT, "semibold")
        for j, line in enumerate(ui.wrap_text(desc, tile_w - 48, 15)[:3]):
            s.text((x0 + 24, ty0 + 145 + j * 22), line, 15, ui.MUTED)
        result = results.get(document)
        if result is None:
            s.text((x0 + 24, ty1 - 34), "Not scanned yet", 14, ui.MUTED)
        else:
            n = len(result.fields)
            found = n - len(result.missing)
            kind = "ok" if found == n else "warn"
            label = f"Scanned · {found}/{n} fields"
            w = ui.text_width(label, 14, "semibold") + 44
            ui.pill(s, x0 + 24 + w / 2, ty1 - 48, label, kind, 14)

    # Last result details
    py0, py1 = ty1 + gap, s.h - ui.FOOTER_H - pad
    s.rect(pad, py0, s.w - pad, py1, ui.CARD, radius=18)
    s.rect(pad, py0, s.w - pad, py1, ui.BORDER, radius=18, border=1)
    if message:
        s.text((s.w / 2, (py0 + py1) / 2), message, 18, ui.ERROR, "semibold", anchor="mm")
    elif last is None or results.get(last) is None:
        s.text((s.w / 2, (py0 + py1) / 2), "Scan results will appear here", 18, ui.MUTED, anchor="mm")
    else:
        result = results[last]
        tile = next(t for t in TILES if t[1] == last)
        labels = tile[4]
        s.text((pad + 24, py0 + 22), f"LAST SCAN — {tile[2].upper()}", 13, ui.ACCENT, "bold")
        items = list(result.fields.items())
        cols = 1 if len(items) <= 7 else 3
        per_col = -(-len(items) // cols)
        col_w = (s.w - 2 * pad - 48) / cols
        row_h = min(34, (py1 - py0 - 70) / per_col)
        size = int(max(12, min(17, row_h * 0.5)))
        for i, (k, v) in enumerate(items):
            col, row = divmod(i, per_col)
            x = pad + 24 + col * col_w
            y = py0 + 58 + row * row_h
            label = labels.get(k, k.replace("_", " ").capitalize())
            s.text((x, y), label, size, ui.MUTED)
            lw = min(col_w * 0.45, 210) if cols == 1 else col_w * 0.48
            shown, vsize = ui.fit_text(v or "Not found", col_w - lw - 16, size, "semibold")
            s.text((x + lw, y), shown, vsize, ui.TEXT if v else ui.ERROR, "semibold")
    return s.finish()


def run(config: Optional[ScannerConfig] = None) -> None:
    config = config or ScannerConfig()
    config.keep_window_open = True
    results: Dict[str, Optional[ScanResult]] = {}
    last: Optional[str] = None
    message = ""
    with KioskScanner(config) as scanner:
        by_key = {ord(t[0]): t[1] for t in TILES}
        while True:
            key = scanner.window.show(home_screen(config.preview_res, results, last, message,
                                                         config.company_name), wait_ms=50)
            if ui.is_cancel(key):
                break
            if key in (ord("f"), ord("F")):
                scanner.refocus()
            elif key in (ord("c"), ord("C")):
                # Like a finished customer: erase the scans (exiting does it too)
                scanner.end_transaction()
                results, last, message = {}, None, ""
            elif key in by_key:
                document = by_key[key]
                message = ""
                try:
                    result = scanner.scan(document)
                except ScannerError as e:
                    message = str(e)
                    continue
                except Exception as e:           # keep the demo running
                    LOGGER.exception(f"{document} scan failed")
                    message = f"Scan failed: {e}"
                    continue
                if result is not None:
                    results[document], last = result, document
                    print(json.dumps(result.to_dict(), indent=2))
