# ============================================================================
# File:         config_SL.py
# Written by:   SL (Seth Leija)
# What it does: Settings shared by the three scanners.
# Team-written: Logic written by SL (Seth Leija). Outside code is used only
#               through the third-party sources listed below.
# Third-party:  none (standard library only)
# ============================================================================
"""
Settings shared by all three scanners.

Pass overrides when creating the scanner, e.g.
    KioskScanner(camera_index=0, fullscreen=True, output_dir="scans")
"""

from __future__ import annotations

import ctypes
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


@dataclass
class ScannerConfig:
    # --- Camera (16MP USB camera on the kiosk mount) ---
    camera_index: int = 1                    # OpenCV index; 0/2 are the laptop's built-in webcam
    # Stream continuously at 12MP (~7.5 fps) and downscale for the preview.
    # Switching resolutions per capture resets the lens focus (~6 s), and
    # 16MP streams black frames for ~8 s at ~1 fps.
    capture_res: Tuple[int, int] = (4000, 3000)
    preview_res: Tuple[int, int] = (1280, 960)   # screen layout size, same 4:3 field of view
    # Manual focus for a document lying on the kiosk surface. Autofocus hunts
    # on the background. Sharp from ~560-640 at the current mount, best ~590.
    focus: int = 590
    focus_step: int = 10
    # The camera hangs upside down in the scanner box
    rotate_180: bool = True
    # Manual exposure: auto exposure blows out white paper under the LEDs.
    # DirectShow units are log2 seconds (-7 = 1/128 s). From a sweep with the
    # LEDs on: -6 clips 92% of the paper, -7 clips none (paper mean ~218).
    auto_exposure: bool = False
    exposure: float = -7
    gain: float = 0

    # --- OCR ---
    # Falls back to `tesseract` on PATH if this file doesn't exist
    tesseract_cmd: str = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    # Folder with the smaller, faster English model (tessdata_fast
    # eng.traineddata). Used for the text passes when the file is there;
    # orientation detection keeps the standard model (osd.traineddata).
    tessdata_fast_dir: Path = Path(__file__).resolve().parent.parent / "tessdata_fast"

    def tesseract_extra(self) -> str:
        """Extra Tesseract options for the text passes."""
        if not (self.tessdata_fast_dir / "eng.traineddata").is_file():
            return ""
        folder = str(self.tessdata_fast_dir)
        if os.name == "nt":
            # pytesseract keeps quotes on Windows, so a path with spaces
            # goes in its short 8.3 form instead
            buf = ctypes.create_unicode_buffer(1024)
            if ctypes.windll.kernel32.GetShortPathNameW(folder, buf, len(buf)):
                folder = buf.value
            return f"--tessdata-dir {folder}"
        return f"--tessdata-dir {shlex.quote(folder)}"

    # --- Window ---
    company_name: str = "Texas Tag & Title"     # demo home screen title
    window_title: str = "Texas Tag & Title — Document Scanner"
    window_size: Tuple[int, int] = (1024, 768)  # ignored when fullscreen
    fullscreen: bool = False
    # Keep the scan window open between scans (the demo menu). When False the
    # window closes after each scan so the host application comes back.
    keep_window_open: bool = False
    # Show sharpness / focus readouts and the [ ] focus keys (setup only)
    diagnostics: bool = False

    # --- Files ---
    # If set, every accepted scan writes its images + form JSON here
    output_dir: Optional[Path] = None
    # If set, the OCR engines write their intermediate images here
    debug_dir: Optional[Path] = None
