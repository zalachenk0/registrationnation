from __future__ import annotations
import atexit
import io
import os
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError
from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException
from dotenv import load_dotenv
from camera_service import Camera, CameraError
from extraction_service import scan_license, scan_insurance
from kiosk_scanners.results import ScannerError
from printing import PrintService, PrintError, create_pdf, printer_status
from payment_service import PaymentService, PaymentError, configuration, create_connection_token
from monday_service import MondayError, create_monday_record
from receipt_email import ReceiptMailer, MailError, MailNotConfigured

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')
app = Flask(__name__, static_folder=None)
app.config['MAX_CONTENT_LENGTH'] = 15 * 1024 * 1024
app.config['TRUSTED_HOSTS'] = ['localhost', '127.0.0.1', '[::1]']
camera = Camera()
printer = PrintService()
payments = PaymentService()
mailer = ReceiptMailer()
PROBLEM_LOG = Path(os.getenv('KIOSK_PROBLEM_LOG') or ROOT / 'runtime' / 'kiosk-problems.log')


def log_problem(kind, detail):
    """One line per problem for whoever looks after the kiosk (the office). The customer never sees these details.
    Never lets a logging problem get in the way of a customer."""
    try:
        PROBLEM_LOG.parent.mkdir(parents=True, exist_ok=True)
        with PROBLEM_LOG.open('a', encoding='utf-8') as log:
            log.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {kind}  {' '.join(str(detail).split())[:500]}\n")
    except OSError:
        pass
scan_lock = threading.Lock()
atexit.register(camera.close)


@app.before_request
def local_requests():
    if request.path.startswith('/api/'):
        origin = request.headers.get('Origin')
        if (origin and urlsplit(origin).netloc != request.host) or request.headers.get('Sec-Fetch-Site') == 'cross-site':
            return jsonify(error='Use the kiosk from its local page.'), 403
        if request.path.startswith('/api/payment/') and request.method == 'POST' and request.headers.get('X-Kiosk-Payment') != '1':
            return jsonify(error='Use the payment controls on the local kiosk page.'), 403


@app.after_request
def headers(response):
    if request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store, max-age=0'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@app.errorhandler(HTTPException)
def http_error(exc):
    return jsonify(error='Image is too large (15 MB maximum).' if exc.code == 413 else exc.description), exc.code


@app.get('/api/health')
def health():
    return jsonify(status='ok', camera=camera.status(), printer=printer_status())


@app.get('/api/camera/frame.jpg')
def frame():
    try:
        return Response(camera.get(preview=True), mimetype='image/jpeg')
    except CameraError as exc:
        return jsonify(error=str(exc)), 503


def upload_image():
    uploaded = request.files.get('image')
    if uploaded is None:
        raise ValueError('Upload an image in the image field.')
    raw = uploaded.read()
    try:
        with Image.open(io.BytesIO(raw)) as im:
            if im.width * im.height > 20_000_000 or min(im.size) < 32:
                raise ValueError('Use an image between 32 pixels per side and 20 megapixels.')
            im.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError('This file is not a readable image. Use JPG or PNG.') from exc
    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError('The uploaded image could not be decoded.')
    return image


@app.post('/api/camera/scan/<kind>')
@app.post('/api/scan/<kind>')
def scan(kind):
    if kind not in {'license', 'insurance'}:
        return jsonify(error='Unknown document type.'), 404
    if not scan_lock.acquire(blocking=False):
        return jsonify(error='A scan is still processing. Wait for it to finish before trying again.'), 409
    try:
        image = camera.get() if request.path.startswith('/api/camera/') else upload_image()
        result = scan_license(image) if kind == 'license' else scan_insurance(image)
        return jsonify(result)
    except ValueError as exc:
        return jsonify(error=str(exc)), 422
    except (ScannerError, CameraError) as exc:
        return jsonify(error=str(exc)), 503
    except Exception:
        # Do not log document contents or decoded personal information.
        return jsonify(error='Document processing failed. Check the local OCR installation and try a sharper image.'), 500
    finally:
        scan_lock.release()


@app.post('/api/print/preview')
def preview_print():
    try:
        return Response(create_pdf(request.get_json(silent=True)), mimetype='application/pdf',
                        headers={'Content-Disposition': 'inline; filename="mock-sticker-and-receipt.pdf"'})
    except ValueError as exc:
        return jsonify(error=str(exc)), 422


@app.post('/api/print/registration')
def print_registration():
    try:
        payload = request.get_json(silent=True)
        paid = payments.verify_paid(payload)
        return jsonify(success=True, **printer.submit(payload, payment=paid))
    except ValueError as exc:
        return jsonify(error=str(exc)), 422
    except PrintError as exc:
        return jsonify(error=str(exc)), 503


# Emails the customer the TTT receipt (page 2, no signature block). Under /api/payment/ so it needs the same kiosk header,
# and verify_paid means no confirmed payment, no email. Never blocks printing: the screen just reports what happened
@app.post('/api/payment/email_receipt')
def payment_email_receipt():
    payload = request.get_json(silent=True)
    reference = payload.get('confirmation_number') if isinstance(payload, dict) else None
    try:
        paid = payments.verify_paid(payload)
        return jsonify(success=True, **mailer.send(payload, paid))
    except ValueError as exc:
        log_problem('RECEIPT EMAIL NOT SENT', f'{reference}: {exc}')
        return jsonify(error=str(exc)), 422
    except MailNotConfigured as exc:
        log_problem('RECEIPT EMAIL NOT SENT', f'{reference}: {exc}')
        return jsonify(error=str(exc), notConfigured=True), 503
    except MailError as exc:
        log_problem('RECEIPT EMAIL NOT SENT', f'{reference}: {exc}')
        return jsonify(error=str(exc)), 502


@app.errorhandler(PaymentError)
def payment_error(exc):
    return jsonify(error=str(exc)), 409


@app.get('/api/payment/config')
def payment_config():
    return jsonify(configuration())


@app.post('/api/payment/create_intent')
def payment_create_intent():
    try:
        payload = request.get_json(silent=True)
        mode = payload.get('payment_mode') if isinstance(payload, dict) else None  # 'terminal' (S700) or 'card' (typed)
        return jsonify(payments.create_payment_intent(payload, mode=mode or 'card'))
    except ValueError as exc:
        return jsonify(error=str(exc)), 422


@app.post('/api/payment/capture_intent')
def payment_capture_intent():
    try:
        return jsonify(payments.capture_payment_intent(request.get_json(silent=True)))
    except ValueError as exc:
        return jsonify(error=str(exc)), 422


# lets the browser's Terminal SDK connect to the S700 (only works when a reader is set up in .env)
@app.post('/api/payment/connection_token')
def payment_connection_token():
    return jsonify(secret=create_connection_token())


# monday.com crm: the form calls this after a renewal is paid and printed. the real work is in monday_service.py
@app.post('/api/monday/create_record')
def monday_create_record():
    payload = request.get_json(silent=True) or {}
    fields = payload.get('fields') if isinstance(payload.get('fields'), dict) else {}
    who = f"{payload.get('itemName')} (payment {fields.get('paymentIntentId')})"      # enough for the office to find the customer
    try:
        result = create_monday_record(payload.get('itemName'), fields)
    except MondayError as exc:
        log_problem('MONDAY NOT SAVED', f'{who}: {exc}')
        return jsonify(error=str(exc)), exc.status
    if result.get('warnings'):
        log_problem('MONDAY SAVED, FIELDS SKIPPED', f"{who}: {'; '.join(result['warnings'])}")
    return jsonify(success=True, **result)


@app.get('/')
def index():
    if not (ROOT / 'dist/index.html').exists():
        return jsonify(error='Build the interface with pnpm build.'), 503
    return send_from_directory(ROOT / 'dist', 'index.html')


@app.get('/<path:asset>')
def asset(asset):
    return send_from_directory(ROOT / 'dist', asset)


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, threaded=True, debug=False)
