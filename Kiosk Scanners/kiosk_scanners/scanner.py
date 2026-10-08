"""KioskScanner: the session object a host application uses to run scans."""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from . import documents, drivers_license, insurance, registration, ui
from .camera import Camera
from .config import ScannerConfig
from .results import ImageSource, ScanResult, configure_tesseract

SCANNERS = {
    "drivers_license": "Driver's License",
    "insurance_card": "Insurance Card",
    "registration_renewal": "Registration Renewal Notice",
}


class KioskScanner:
    """
    Opens the camera once and runs scans on demand.

        with KioskScanner() as scanner:
            result = scanner.scan_drivers_license()     # None if the customer cancelled
            if result:
                form.fill(result.fields)

    Each scan_* method opens the scan window, walks the customer through the
    capture and review, and returns a ScanResult once they confirm. The
    camera stays open between scans (opening it costs ~1 s plus focus
    settling), and each OCR engine is created on first use and reused.

    Settings: pass a ScannerConfig and/or keyword overrides of its fields,
    e.g. KioskScanner(camera_index=0, fullscreen=True, output_dir="scans").
    """

    def __init__(self, config: Optional[ScannerConfig] = None, **overrides: Any) -> None:
        self.config = dataclasses.replace(config or ScannerConfig(), **overrides)
        for name in ("output_dir", "debug_dir"):
            value = getattr(self.config, name)
            if value is not None:
                setattr(self.config, name, Path(value))
        self.window = ui.Window(self.config.window_title, self.config.window_size,
                                self.config.fullscreen)
        self._camera: Optional[Camera] = None
        self._ocr: Dict[str, Any] = {}
        self._tesseract_ready = False

    # --- Live scans (camera + window) -------------------------------------

    def scan_drivers_license(self) -> Optional[ScanResult]:
        """Front and back of a driver's license; fields come from the PDF417 barcode."""
        return self._run(drivers_license.TITLE,
                         lambda camera: drivers_license.capture(camera, self.window, self.config))

    def scan_insurance_card(self) -> Optional[ScanResult]:
        """Insurance card or printout: company, policy, expiration, vehicle."""
        return self._run_document(insurance.PROFILE)

    def scan_registration_renewal(self) -> Optional[ScanResult]:
        """Texas registration renewal notice: vehicle, fees, owner, recipient."""
        return self._run_document(registration.PROFILE)

    def scan(self, document: str) -> Optional[ScanResult]:
        """Scan by name: "drivers_license", "insurance_card" or "registration_renewal"."""
        methods = {"drivers_license": self.scan_drivers_license,
                   "insurance_card": self.scan_insurance_card,
                   "registration_renewal": self.scan_registration_renewal}
        if document not in methods:
            raise ValueError(f"Unknown document {document!r}; expected one of {list(methods)}")
        return methods[document]()

    # --- Offline reads (image files, no camera or window) ------------------

    def read_drivers_license(self, front: ImageSource, back: ImageSource) -> ScanResult:
        return self._saved(drivers_license.read(front, back))

    def read_insurance_card(self, image: ImageSource) -> ScanResult:
        return self._saved(documents.read(insurance.PROFILE, self._get_ocr(insurance.PROFILE), image))

    def read_registration_renewal(self, image: ImageSource) -> ScanResult:
        return self._saved(documents.read(registration.PROFILE,
                                          self._get_ocr(registration.PROFILE), image))

    # --- Session -------------------------------------------------------------

    def preload(self) -> None:
        """Opens the camera and builds the OCR engines now, so the first scan starts instantly."""
        self._open_camera()
        self._get_ocr(insurance.PROFILE)
        self._get_ocr(registration.PROFILE)

    def refocus(self) -> None:
        """Re-sends the lens focus (if the preview looks blurry)."""
        if self._camera is not None:
            self._camera.refocus()

    def close(self) -> None:
        if self._camera is not None:
            self._camera.release()
            self._camera = None
        self.window.close()

    def __enter__(self) -> "KioskScanner":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- Internals -------------------------------------------------------------

    def _run_document(self, profile: documents.DocumentProfile) -> Optional[ScanResult]:
        ocr = self._get_ocr(profile)
        return self._run(profile.title,
                         lambda camera: documents.capture(profile, ocr, camera, self.window, self.config))

    def _run(self, title: str, flow: Callable[[Camera], Optional[ScanResult]]) -> Optional[ScanResult]:
        if self._camera is None:
            # Opening the camera blocks ~1 s — show the window first
            self.window.show(ui.starting_screen(self.config.preview_res, title))
            camera = self._open_camera()
        else:
            # The camera sat idle while the customer filled in the form
            camera = self._camera
            camera.flush()
        try:
            result = flow(camera)
        finally:
            if not self.config.keep_window_open:
                self.window.close()
        return self._saved(result) if result is not None else None

    def _saved(self, result: ScanResult) -> ScanResult:
        if self.config.output_dir is not None:
            result.save(self.config.output_dir)
        return result

    def _open_camera(self) -> Camera:
        if self._camera is None:
            self._camera = Camera(self.config)
        return self._camera

    def _get_ocr(self, profile: documents.DocumentProfile) -> Any:
        if not self._tesseract_ready:
            configure_tesseract(self.config.tesseract_cmd)
            self._tesseract_ready = True
        if profile.document not in self._ocr:
            self._ocr[profile.document] = profile.make_ocr(self.config)
        return self._ocr[profile.document]
