# ============================================================================
# File:         __main__.py
# Written by:   SL (Seth Leija)
# What it does: Command line for running a scan outside the web app.
# Team-written: Logic written by SL (Seth Leija). Outside code is used only
#               through the third-party sources listed below.
# Third-party:  none (standard library only)
# ============================================================================
"""
Command line. Any application (Python or not) can run a scan and read the
JSON result from stdout; logs go to stderr.

  py -m kiosk_scanners_SL demo [--fullscreen]
  py -m kiosk_scanners_SL scan license|insurance|registration [--output DIR] [--fullscreen]
  py -m kiosk_scanners_SL read license FRONT.jpg BACK.jpg [--output DIR]
  py -m kiosk_scanners_SL read insurance CARD.jpg
  py -m kiosk_scanners_SL read registration NOTICE.jpg

Exit codes: 0 = scanned (JSON on stdout), 1 = error (JSON with "error"),
2 = customer cancelled (JSON with "cancelled": true).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import demo_SL as demo
from .config_SL import ScannerConfig
from .results_SL import ScannerError
from .scanner_SL import KioskScanner

DOCUMENTS = {"license": "drivers_license", "insurance": "insurance_card",
             "registration": "registration_renewal"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="py -m kiosk_scanners_SL",
                                 description="Kiosk document scanners (license, insurance, renewal notice).")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--camera", type=int, help="OpenCV camera index (default 1)")
    common.add_argument("--fullscreen", action="store_true", help="full-screen scan window")
    common.add_argument("--output", type=Path, help="save images + form JSON to this folder")
    common.add_argument("--tesseract", help="path to tesseract.exe")
    common.add_argument("--diagnostics", action="store_true", help="show sharpness/focus readouts")
    common.add_argument("-v", "--verbose", action="store_true", help="log progress to stderr")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("demo", parents=[common], help="home screen to run any scan on demand")
    p = sub.add_parser("scan", parents=[common], help="run one live scan, print JSON")
    p.add_argument("document", choices=DOCUMENTS)
    p = sub.add_parser("read", parents=[common], help="read image files (no camera), print JSON")
    p.add_argument("document", choices=DOCUMENTS)
    p.add_argument("images", nargs="+", type=Path, help="license: FRONT BACK; others: one image")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, stream=sys.stderr,
                        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    # The OCR engines log every pass at INFO — too noisy even for --verbose
    for name in ("kiosk_scanners_SL.insurance_ocr_SL", "kiosk_scanners_SL.registration_ocr_SL"):
        logging.getLogger(name).setLevel(logging.WARNING)

    config = ScannerConfig(fullscreen=args.fullscreen, output_dir=args.output,
                           diagnostics=args.diagnostics)
    if args.camera is not None:
        config.camera_index = args.camera
    if args.tesseract:
        config.tesseract_cmd = args.tesseract

    if args.command == "demo":
        demo.run(config)
        return 0

    def emit(payload: dict) -> None:
        print(json.dumps(payload, indent=2))

    document = DOCUMENTS[args.document]
    # Checked here, not with ap.error(): its exit code 2 means "cancelled"
    if args.command == "read":
        needed = 2 if document == "drivers_license" else 1
        if len(args.images) != needed:
            emit({"error": "read license needs FRONT and BACK images" if needed == 2
                  else f"read {args.document} needs one image"})
            return 1

    try:
        with KioskScanner(config) as scanner:
            if args.command == "scan":
                result = scanner.scan(document)
            elif document == "drivers_license":
                result = scanner.read_drivers_license(*args.images)
            else:
                result = (scanner.read_insurance_card if document == "insurance_card"
                          else scanner.read_registration_renewal)(args.images[0])
            # The calling program owns the result (and any --output files):
            # it deletes them when its transaction is completed or cancelled
            scanner._hand_off()
    except ScannerError as e:
        emit({"error": str(e)})
        return 1
    except Exception as e:           # still answer in JSON, so the caller can parse it
        logging.getLogger(__name__).exception("Scan failed")
        emit({"error": f"{type(e).__name__}: {e}"})
        return 1
    if result is None:
        emit({"cancelled": True})
        return 2
    emit(result.to_dict())
    return 0


if __name__ == "__main__":
    sys.exit(main())
