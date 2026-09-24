from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename

from driver_license_barcode_rev4 import AAMVAParser, DriverLicenseBarcodeScanner
from insurance_card_ocr_rev3 import InsuranceCardOCR, InsuranceCardOCRConfig
from camera_capture import CameraError, KioskCamera
from printer_service import PrinterError, print_registration_mockup, printer_status
from payment_service import PaymentError, create_connection_token, create_payment_intent, capture_payment_intent

load_dotenv()

PROJECT_DIR = Path(__file__).resolve().parent
DIST_DIR = PROJECT_DIR / "dist"
app = Flask(__name__, static_folder=str(DIST_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024
camera = KioskCamera()

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
VIN_RE = re.compile(r"\b([A-HJ-NPR-Z0-9]{17})\b", re.IGNORECASE)


def aamva_date(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    for fmt in ("%m%d%Y", "%Y%m%d", "%m%d%y"):
        try:
            return datetime.strptime(digits, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def insurance_date(value: str) -> str:
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def uploaded_image() -> tuple[Path | None, tuple[Any, int] | None]:
    upload = request.files.get("image")
    if upload is None or not upload.filename:
        return None, (jsonify(error="Attach an image in the 'image' field."), 400)

    suffix = Path(secure_filename(upload.filename)).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        return None, (jsonify(error="Use a JPG, PNG, WEBP, BMP, or TIFF image."), 415)

    handle = tempfile.NamedTemporaryFile(prefix="kiosk-scan-", suffix=suffix, delete=False)
    path = Path(handle.name)
    handle.close()
    upload.save(path)
    return path, None


@app.get("/api/health")
def health():
    return jsonify(status="ok", camera=camera.status(), printer=printer_status())


def process_license(path: Path):
    result = DriverLicenseBarcodeScanner().scan(path)
    fields = dict(result.parsed_fields)
    _, raw_fields = AAMVAParser().parse(result.raw_aamva)
    fields.update(
        date_of_birth=aamva_date(raw_fields.get("DBB", "")),
        license_number=raw_fields.get("DAQ", "").strip(),
        license_expiration=aamva_date(raw_fields.get("DBA", "")),
        license_class=raw_fields.get("DCA", "").strip()[:1],
    )
    return jsonify(success=result.scan_success, recommendation=result.recommendation, fields=fields)


def process_insurance(path: Path):
    tesseract_cmd = os.getenv("TESSERACT_CMD") or None
    result = InsuranceCardOCR(
        InsuranceCardOCRConfig(tesseract_cmd=tesseract_cmd, save_debug=False)
    ).extract(path)
    fields = dict(result.parsed_fields)
    vin_match = VIN_RE.search(result.raw_text.upper())
    fields["vehicle_vin"] = vin_match.group(1) if vin_match else ""
    fields["insurance_expiration"] = insurance_date(str(fields.get("insurance_expiration", "")))
    return jsonify(success=result.scan_success, recommendation=result.recommendation, fields=fields)


@app.post("/api/scan/license")
def scan_license():
    path, error = uploaded_image()
    if error:
        return error

    assert path is not None
    try:
        return process_license(path)
    except Exception as exc:
        app.logger.exception("Driver-license scan failed")
        return jsonify(error=f"Driver-license scanner failed: {exc}"), 500
    finally:
        path.unlink(missing_ok=True)


@app.post("/api/scan/insurance")
def scan_insurance():
    path, error = uploaded_image()
    if error:
        return error

    assert path is not None
    try:
        return process_insurance(path)
    except Exception as exc:
        app.logger.exception("Insurance scan failed")
        return jsonify(error=f"Insurance scanner failed: {exc}"), 500
    finally:
        path.unlink(missing_ok=True)


@app.post("/api/camera/scan/<kind>")
def capture_and_scan(kind: str):
    if kind not in {"license", "insurance"}:
        return jsonify(error="Scan type must be 'license' or 'insurance'."), 404
    handle = tempfile.NamedTemporaryFile(prefix="kiosk-camera-", suffix=".jpg", delete=False)
    path = Path(handle.name)
    handle.close()
    try:
        camera.capture(path)
        return process_license(path) if kind == "license" else process_insurance(path)
    except CameraError as exc:
        return jsonify(error=f"Camera capture failed: {exc}"), 503
    except Exception as exc:
        app.logger.exception("Camera scan failed")
        return jsonify(error=f"Document scan failed: {exc}"), 500
    finally:
        path.unlink(missing_ok=True)


@app.post("/api/print/registration")
def print_registration():
    payload = request.get_json(silent=True) or {}
    required = ("registrant", "license_plate", "confirmation_number")
    missing = [field for field in required if not str(payload.get(field, "")).strip()]
    if missing:
        return jsonify(error=f"Missing required print fields: {', '.join(missing)}."), 400

    try:
        job = print_registration_mockup(payload)
        return jsonify(success=True, message="Mock registration sent to printer.", job=job)
    except PrinterError as exc:
        return jsonify(error=str(exc)), 503


# --- Payment (Stripe Terminal) -------------------------------------------
#
# Three routes, called in this order by a payment:
#   1. /api/payment/connection_token - authenticates the card reader
#   2. /api/payment/create_intent    - opens a charge for an amount
#   3. /api/payment/capture_intent   - finalizes the charge after the
#                                      reader confirms the card
# The actual Stripe calls live in payment_service.py; these routes just
# translate between HTTP/JSON and that module.


@app.post("/api/payment/connection_token")
def payment_connection_token():
    try:
        secret = create_connection_token()
        return jsonify(secret=secret)
    except PaymentError as exc:
        return jsonify(error=str(exc)), 500


@app.post("/api/payment/create_intent")
def payment_create_intent():
    payload = request.get_json(silent=True) or {}
    try:
        amount = int(payload.get("amount"))
    except (TypeError, ValueError):
        return jsonify(error="amount must be a whole number of cents"), 400

    receipt_email = payload.get("receiptEmail") or None

    try:
        intent = create_payment_intent(amount, receipt_email=receipt_email)
        return jsonify(**intent)
    except PaymentError as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/payment/capture_intent")
def payment_capture_intent():
    payload = request.get_json(silent=True) or {}
    payment_intent_id = payload.get("paymentIntentId")
    if not payment_intent_id:
        return jsonify(error="paymentIntentId is required"), 400

    try:
        result = capture_payment_intent(payment_intent_id)
        return jsonify(**result)
    except PaymentError as exc:
        return jsonify(error=str(exc)), 500


@app.get("/")
def serve_ui():
    if not (DIST_DIR / "index.html").exists():
        return jsonify(error="UI is not built. Run 'pnpm build' first."), 503
    return send_from_directory(DIST_DIR, "index.html")


@app.get("/<path:asset_path>")
def serve_ui_asset(asset_path: str):
    candidate = DIST_DIR / asset_path
    if candidate.is_file():
        return send_from_directory(DIST_DIR, asset_path)
    return send_from_directory(DIST_DIR, "index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("SCANNER_API_PORT", "5000")))
