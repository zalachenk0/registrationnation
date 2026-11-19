# ============================================================================
# File:         scanner_api_BV_CG.py
# Written by:   BV (server, login, camera, scan and print routes); CG (payment
#               routes, Monday.com route and the photo lines, tagged [CG]); SL
#               (the scanning package it calls)
# What it does: Flask server that connects the touchscreen page to scanning,
#               payment, printing, Monday.com and the security camera.
# Team-written: Logic written by BV (server, login, camera, scan and print
#               routes); CG (payment routes, Monday.com route and the photo
#               lines, tagged [CG]); SL (the scanning package it calls). Outside
#               code is used only through the third-party sources listed below.
# Third-party:  OpenCV; NumPy; Pillow; Flask; Werkzeug; python-dotenv; Stripe
#               and Monday.com services (reached through payment_service_CG.py and
#               monday_service_CG.py); CUPS (through delivery_service_BV.py).
# ============================================================================
from __future__ import annotations
import atexit
import io
import os
import threading
from pathlib import Path
from urllib.parse import urlsplit

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError
from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException
from dotenv import load_dotenv
from camera_service_BV_SL import Camera, CameraError
from extraction_service_BV_SL import scan_license, scan_insurance, scan_registration
from kiosk_scanners_SL.results_SL import ScannerError
from printing_BV import PrintService, PrintError, create_pdf, printer_status
from payment_service_CG import PaymentService, PaymentError, configuration, create_connection_token, intent_only
from monday_service_CG import MondayError, create_monday_record
from uuid import uuid4
from datetime import datetime, timezone
from kiosk_auth_BV import install_auth
from delivery_service_BV import DeliveryService
from crm_photos_CG import PhotoDesk, clean_reference, log_problem  # [CG] log_problem writes the office's runtime/kiosk-problems.log
from security_photos_CG import SecurityPhotos  # [CG] the security Pi's photos on the Monday record

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')
load_dotenv(ROOT / 'kiosk-auth.env')
app = Flask(__name__, static_folder=None)
app.config['MAX_CONTENT_LENGTH'] = 15 * 1024 * 1024
app.config['TRUSTED_HOSTS'] = ['localhost', '127.0.0.1', '[::1]']
camera = Camera()
printer = PrintService()
payments = PaymentService()
delivery = DeliveryService(ROOT, printer)
delivery.start()
atexit.register(delivery.stop.set)
photos = PhotoDesk(ROOT)  # [CG] remembers each customer's license and insurance photos and attaches them to their Monday record
photos.start()  # [CG]
atexit.register(photos.stop.set)  # [CG]
security_photos = SecurityPhotos(ROOT)  # [CG] license-scanner, Pay and print-finished photos from the security Pi go on the Monday record
security_photos.start()  # [CG]
atexit.register(security_photos.stop.set)  # [CG]
scan_lock = threading.Lock()
atexit.register(camera.close)


# ---- [BV] request guards, staff login and response headers ----
@app.before_request
def local_requests():
    if request.path.startswith('/api/'):
        origin = request.headers.get('Origin')
        if (origin and urlsplit(origin).netloc != request.host) or request.headers.get('Sec-Fetch-Site') == 'cross-site':
            return jsonify(error='Use the kiosk from its local page.'), 403
        if request.path.startswith('/api/payment/') and request.method == 'POST' and request.headers.get('X-Kiosk-Payment') != '1':
            return jsonify(error='Use the payment controls on the local kiosk page.'), 403


auth = install_auth(app, ROOT)
atexit.register(auth.stop.set)


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


# ---- [BV] health check, camera preview and the security camera trigger ----
@app.get('/api/health')
def health():
    return jsonify(status='ok', camera=camera.status(), printer=printer_status())


@app.get('/api/camera/frame.jpg')
def frame():
    try:
        return Response(camera.get(preview=True), mimetype='image/jpeg')
    except CameraError as exc:
        return jsonify(error=str(exc)), 503


@app.post('/api/security/front-capture')
def security_front_capture():
    if request.headers.get('X-Kiosk-Security') != '1':
        return jsonify(error='Use the local scanner controls.'), 403
    try:
        return jsonify(delivery.enqueue_capture('license_scan'))
    except Exception:
        return jsonify(error='Security capture could not be queued. Check the kiosk storage.'), 503


# ---- [BV] license front photo. The lines that call photos.* are [CG] ----
@app.post('/api/camera/license-front')
def license_front_photo():
    if not scan_lock.acquire(blocking=False):
        return jsonify(error='A scan is still processing. Please wait before taking another photo.'), 409
    try:
        image = camera.get()
        directory = Path(os.getenv('KIOSK_LICENSE_PHOTO_DIR', str(ROOT / 'runtime' / 'license_front')))
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + '-' + uuid4().hex + '.jpg'
        ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if not ok:
            raise OSError('Could not encode photo')
        target = directory / name
        try:
            with target.open('xb') as photo:
                os.chmod(target, 0o600)
                photo.write(encoded.tobytes())
        except OSError:
            target.unlink(missing_ok=True)
            raise
        camera.expect_flip()  # [CG] refocus once the customer flips the card for the barcode
        try:
            photos.license_taken(target)  # [CG] remembered so it can go on this customer's Monday record
        except Exception:
            app.logger.warning('The license photo could not be linked for the CRM record.')
        return jsonify(saved=True)
    except CameraError as exc:
        return jsonify(error=str(exc)), 503
    except (OSError, cv2.error):
        return jsonify(error='The front photo could not be saved. Check available storage and folder permissions, then try again.'), 503
    finally:
        scan_lock.release()


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


# ---- [BV] scan routes. The scanning itself is the SL package, called through extraction_service_BV_SL.py ----
@app.post('/api/camera/scan/<kind>')
@app.post('/api/scan/<kind>')
def scan(kind):
    if kind not in {'license', 'insurance', 'registration'}:
        return jsonify(error='Unknown document type.'), 404
    if not scan_lock.acquire(blocking=False):
        return jsonify(error='A scan is still processing. Wait for it to finish before trying again.'), 409
    try:
        image = camera.get() if request.path.startswith('/api/camera/') else upload_image()
        if kind == 'insurance':
            try:
                delivery.enqueue_capture('insurance_scan')
            except Exception:
                app.logger.warning('Insurance security photo could not be queued.')
        result = {'license': scan_license, 'insurance': scan_insurance, 'registration': scan_registration}[kind](image)
        try:
            if result.get('success') and kind in {'license', 'insurance'}:  # a scan that read nothing must not replace a good photo
                if kind == 'license':
                    photos.license_scanned(result.get('fields'))  # [CG] a different card than before means a different customer
                    photos.save_license_back(image)  # [CG] the barcode was read from this picture, so it's the back of the license: kept for the Monday record
                else:
                    photos.save_insurance(image)  # [CG] kept as a jpeg for the Monday record, even when the OCR debug images are off
        except Exception:
            app.logger.warning('The scan photo could not be saved for the CRM record.')
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


# ---- [BV] printing ----
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
        return jsonify(delivery.submit(payload, payment=paid))
    except ValueError as exc:
        return jsonify(error=str(exc)), 422
    except PrintError as exc:
        return jsonify(error=str(exc)), 503


@app.get('/api/print/status/<reference>')
def print_status(reference):
    try:
        return jsonify(delivery.status(reference))
    except ValueError as exc:
        return jsonify(error=str(exc)), 404


# ---- [CG] payment and Monday.com routes. Stripe and Monday.com are third-party services ----
@app.errorhandler(PaymentError)
def payment_error(exc):
    return jsonify(error=str(exc)), 409


@app.get('/api/payment/config')
def payment_config():
    return jsonify(configuration())


# what happens on the kiosk side every time a payment is started, whichever way the card is taken.
# Moved here unchanged from payment_create_intent so the server-driven reader route does exactly the same
def payment_started(payload):
    try:
        event_id = str(payload.get('security_event_id', ''))
        if not event_id or len(event_id) > 40 or not all(c.isalnum() or c == '-' for c in event_id):
            event_id = 'first-attempt'
        delivery.enqueue_capture('payment_click', 'pay:' + str(payload.get('confirmation_number', '')) + ':' + event_id)
    except Exception:
        app.logger.warning('Payment capture event could not be queued; payment processing is unchanged.')
    try:
        photos.bind(payload.get('confirmation_number'))  # [CG] this customer's photos now belong to this payment
    except Exception:
        app.logger.warning('The photos could not be linked to this payment; the Monday record will go without them.')
    try:
        security_photos.bind(payload.get('confirmation_number'))  # [CG] after the Pay photo was queued above, so it is found
    except Exception:
        app.logger.warning('The security photos could not be linked to this payment.')


@app.post('/api/payment/create_intent')
def payment_create_intent():
    try:
        payload = request.get_json(silent=True)
        mode = payload.get('payment_mode') if isinstance(payload, dict) else None  # [CG] only 'terminal' (S700) is accepted
        result = payments.create_payment_intent(payload, mode=mode or 'terminal')
        payment_started(payload)
        return jsonify(result)
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


# ---- [CG] server-driven reader: Stripe carries the payment to the reader over the internet, so the kiosk and the reader
# never have to reach each other on the local network. All three sit behind the X-Kiosk-Payment check above ----
@app.post('/api/payment/reader_start')
def payment_reader_start():
    try:
        payload = request.get_json(silent=True)
        result = payments.start_on_reader(payload)
        if not intent_only(payload):  # by payment id (today's built screen) means create_intent already did these steps
            payment_started(payload)
        return jsonify(result)
    except ValueError as exc:
        return jsonify(error=str(exc)), 422


@app.post('/api/payment/reader_status')
def payment_reader_status():
    try:
        return jsonify(payments.reader_status(request.get_json(silent=True)))
    except ValueError as exc:
        return jsonify(error=str(exc)), 422


@app.post('/api/payment/reader_cancel')
def payment_reader_cancel():
    try:
        return jsonify(payments.cancel_on_reader(request.get_json(silent=True)))
    except ValueError as exc:
        return jsonify(error=str(exc)), 422


# [CG] who a Monday problem is about, for the office log: the renewal reference, the record name and the Stripe payment.
# That is enough for staff to find the customer and add the record by hand. Nothing else from the form is written
def monday_who(payload, fields):
    reference = clean_reference(fields.get('confirmationNumber')) or 'no reference'
    name = ' '.join(str(payload.get('itemName') or 'no name').split())[:80]
    intent = fields.get('paymentIntentId')
    paid = f', payment {intent}' if isinstance(intent, str) and intent.startswith('pi_') and len(intent) < 80 else ''
    return f'renewal {reference} ({name}){paid}'


# monday.com crm: the form calls this after a renewal is paid and printed. the real work is in monday_service_CG.py
@app.post('/api/monday/create_record')
def monday_create_record():
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}  # a list or plain value used to crash this route
    fields = payload.get('fields') if isinstance(payload.get('fields'), dict) else {}
    try:
        result = create_monday_record(payload.get('itemName'), payload.get('fields') or {})
    except MondayError as exc:
        # [CG] the customer has already paid and walked away by now, so the office has to hear about it, not just the screen
        log_problem('MONDAY NOT SAVED', f'{monday_who(payload, fields)}: {exc} Add this renewal to the board by hand.')
        return jsonify(error=str(exc)), exc.status
    if result.get('warnings'):
        log_problem('MONDAY SAVED, FIELDS SKIPPED', f"{monday_who(payload, fields)}, item {result.get('itemId')}: "
                    + '; '.join(str(w) for w in result['warnings']))
    try:
        photos.attach(fields.get('confirmationNumber'), result.get('itemId'))  # [CG] the photos upload in the background, after this answer
    except Exception:
        app.logger.warning('The photos could not be queued for the Monday record.')
    try:
        security_photos.attach(fields.get('confirmationNumber'), result.get('itemId'))  # [CG] security photos follow in the background too
    except Exception:
        app.logger.warning('The security photos could not be queued for the Monday record.')
    return jsonify(success=True, **result)


# ---- [BV] serves the built touchscreen page ----
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
