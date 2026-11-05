# ==============================================================================
# File:         home_form_server_CG.py
# Written by:   CG (Cristian Gonzalez)
# What it does: Small test server for trying the payment, Monday.com record
#               and photo upload at home. No camera, scanner, printer or
#               staff login. It runs on this computer only.
# Team-written: All logic written by CG. The payment, Monday.com and photo
#               steps call the same kiosk modules the real server uses
#               (payment_service_CG.py, monday_service_CG.py, crm_photos_CG.py).
# Third-party:  Flask; python-dotenv; Pillow; OpenCV; NumPy; Stripe API and
#               Monday.com API (reached through the kiosk modules above).
#               The page loads Stripe.js and the Stripe Terminal JS SDK from
#               Stripe's website. The server-driven reader routes follow
#               Stripe's server-driven Terminal guide.
# ==============================================================================
# Start it from the kiosk folder:  python3 home_test_CG/home_form_server_CG.py
# then open http://127.0.0.1:5055 in a browser.
import contextlib
import io
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))  # the kiosk modules live one folder up

import cv2  # [3RD PARTY: OpenCV] decodes and re-saves the uploaded pictures the way the kiosk saves camera photos
import numpy as np  # [3RD PARTY: NumPy]
from dotenv import load_dotenv  # [3RD PARTY: python-dotenv] reads the same .env file as the kiosk
from flask import Flask, jsonify, request, send_file  # [3RD PARTY: Flask]
from PIL import Image, UnidentifiedImageError  # [3RD PARTY: Pillow] checks an upload really is a picture

from crm_photos_CG import KEEP_SETTING, PhotoDesk, clean_reference, log_problem  # [CG] same photo desk and office log the kiosk uses
from monday_service_CG import MondayError, create_monday_record  # [CG] same Monday code the kiosk uses
from payment_service_CG import PaymentError, PaymentService, configuration, create_connection_token, server_reader_settings, terminal_settings  # [CG]

MAX_PHOTO_BYTES = 15 * 1024 * 1024


def monday_who(payload, fields):  # same wording as monday_who() in scanner_api_BV_CG.py, so both logs read alike
    reference = clean_reference(fields.get('confirmationNumber')) or 'no reference'
    name = ' '.join(str(payload.get('itemName') or 'no name').split())[:80]
    intent = fields.get('paymentIntentId')
    paid = f', payment {intent}' if isinstance(intent, str) and intent.startswith('pi_') and len(intent) < 80 else ''
    return f'renewal {reference} ({name}){paid}'


def create_app(scratch=None):
    scratch = Path(scratch) if scratch else ROOT / 'runtime' / 'home_test'
    scratch.mkdir(parents=True, exist_ok=True)
    # these three settings send every file this test writes to its own folder, so a home test never touches the real kiosk's photos or problem log
    os.environ['KIOSK_PROBLEM_LOG'] = str(scratch / 'kiosk-problems.log')
    os.environ['KIOSK_LICENSE_PHOTO_DIR'] = str(scratch / 'license_front')
    os.environ['KIOSK_INSURANCE_PHOTO_DIR'] = str(scratch / 'insurance_front')
    payments = PaymentService(scratch / 'payments.sqlite3')  # its own ledger, separate from the kiosk's
    photos = PhotoDesk(scratch, database=scratch / 'photos.sqlite3')

    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 40 * 1024 * 1024
    app.extensions['home'] = SimpleNamespace(payments=payments, photos=photos, scratch=scratch)

    @app.before_request
    def this_computer_only():
        if request.host.rsplit(':', 1)[0] not in {'127.0.0.1', 'localhost'}:
            return jsonify(error='The home test form only answers on this computer.'), 403

    @app.after_request
    def no_caching(response):
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(PaymentError)
    def payment_error(exc):
        return jsonify(error=str(exc)), 409  # the same answer the kiosk gives

    @app.errorhandler(Exception)
    def anything_else(exc):
        code = getattr(exc, 'code', 500) if isinstance(getattr(exc, 'code', None), int) else 500
        return jsonify(error=str(exc) if code < 500 else 'Something went wrong on the test server. Check its terminal window.'), code

    @app.get('/')
    def page():
        return send_file(HERE / 'home_form_CG.html')

    @app.get('/api/home/settings')
    def settings():  # what the page's "setup check" box shows. Only yes/no answers, never a key or token
        def has(name):
            return bool(os.getenv(name, '').strip())
        reader = terminal_settings()
        server = server_reader_settings()
        return jsonify(
            stripeKeys=os.getenv('STRIPE_SECRET_KEY', '').startswith('sk_test_') and os.getenv('STRIPE_PUBLISHABLE_KEY', '').startswith('pk_test_'),
            liveKeyFound=os.getenv('STRIPE_SECRET_KEY', '').startswith(('sk_live_', 'rk_live_')),
            reader='simulated' if reader['simulated'] else ('real' if reader['enabled'] else 'none'), readerProblem=reader['problem'],
            serverReader=server['enabled'], serverReaderProblem=server['problem'],
            monday=has('MONDAY_API_TOKEN') and has('MONDAY_BOARD_ID'),
            licenseColumn=has('MONDAY_LICENSE_PHOTO_COLUMN'), insuranceColumn=has('MONDAY_INSURANCE_PHOTO_COLUMN'),
            keepLocal=os.getenv(KEEP_SETTING, '').strip().lower() in {'1', 'true', 'yes', 'on'})

    # ---- payment: the same calls, in the same order, as the kiosk's scanner_api_BV_CG.py ----
    @app.get('/api/payment/config')
    def payment_config():
        return jsonify(configuration())

    @app.post('/api/payment/connection_token')
    def payment_connection_token():
        return jsonify(secret=create_connection_token())

    @app.post('/api/payment/create_intent')
    def payment_create_intent():
        try:
            payload = request.get_json(silent=True)
            mode = payload.get('payment_mode') if isinstance(payload, dict) else None
            result = payments.create_payment_intent(payload, mode=mode or 'card')
            photos.bind(payload.get('confirmation_number'))  # this customer's photos now belong to this payment, as at the kiosk
            return jsonify(result)
        except ValueError as exc:
            return jsonify(error=str(exc)), 422

    @app.post('/api/payment/capture_intent')
    def payment_capture_intent():
        try:
            return jsonify(payments.capture_payment_intent(request.get_json(silent=True)))
        except ValueError as exc:
            return jsonify(error=str(exc)), 422

    # ---- server-driven reader: same four routes as the kiosk's scanner_api_BV_CG.py. Stripe carries the payment to the
    # reader over the internet, so this works even on networks that block devices from reaching each other ----
    @app.post('/api/payment/reader_start')
    def payment_reader_start():
        try:
            payload = request.get_json(silent=True)
            result = payments.start_on_reader(payload)
            photos.bind(payload.get('confirmation_number'))  # as at the kiosk
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

    @app.post('/api/payment/reader_simulate_tap')
    def payment_reader_simulate_tap():
        try:
            payload = request.get_json(silent=True)
            return jsonify(payments.simulate_tap(payload, payload.get('test_card') if isinstance(payload, dict) else None))
        except ValueError as exc:
            return jsonify(error=str(exc)), 422

    # ---- monday.com: the same route as the kiosk, photos are queued after the answer ----
    @app.post('/api/monday/create_record')
    def monday_create_record():
        payload = request.get_json(silent=True)
        payload = payload if isinstance(payload, dict) else {}
        fields = payload.get('fields') if isinstance(payload.get('fields'), dict) else {}
        try:
            result = create_monday_record(payload.get('itemName'), payload.get('fields') or {})
        except MondayError as exc:  # logged the same way as the kiosk, into runtime/home_test/kiosk-problems.log
            log_problem('MONDAY NOT SAVED', f'{monday_who(payload, fields)}: {exc} Add this renewal to the board by hand.')
            return jsonify(error=str(exc)), exc.status
        if result.get('warnings'):
            log_problem('MONDAY SAVED, FIELDS SKIPPED', f"{monday_who(payload, fields)}, item {result.get('itemId')}: "
                        + '; '.join(str(w) for w in result['warnings']))
        photos.attach(fields.get('confirmationNumber'), result.get('itemId'))
        return jsonify(success=True, **result)

    # ---- photos: stand-ins for the camera. The kiosk takes these with its cameras; here you pick two pictures ----
    def read_picture(field):
        uploaded = request.files.get(field)
        if uploaded is None or not uploaded.filename:
            return None
        raw = uploaded.read(MAX_PHOTO_BYTES + 1)
        if len(raw) > MAX_PHOTO_BYTES:
            raise ValueError('Use a picture under 15 MB.')
        try:
            with Image.open(io.BytesIO(raw)) as picture:
                if picture.width * picture.height > 20_000_000 or min(picture.size) < 32:
                    raise ValueError('Use a picture between 32 pixels per side and 20 megapixels.')
                picture.verify()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ValueError('That file is not a readable picture.') from exc
        image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError('That file is not a readable picture.')
        return image

    @app.post('/api/home/photos')
    def add_photos():
        try:
            front, insurance = read_picture('license_front'), read_picture('insurance')
        except ValueError as exc:
            return jsonify(error=str(exc)), 422
        if front is None and insurance is None:
            return jsonify(error='Pick at least one picture.'), 422
        saved = []
        if front is not None:  # saved and remembered exactly like the kiosk's front-of-license photo
            folder = Path(os.environ['KIOSK_LICENSE_PHOTO_DIR'])
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            target = folder / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + '-' + uuid4().hex + '.jpg')
            ok, encoded = cv2.imencode('.jpg', front, [cv2.IMWRITE_JPEG_QUALITY, 95])
            if not ok:
                return jsonify(error='The license picture could not be saved.'), 500
            with target.open('xb') as stream:
                os.chmod(target, 0o600)
                stream.write(encoded.tobytes())
            photos.license_taken(target)
            saved.append('license')
        if insurance is not None:
            photos.save_insurance(insurance)
            saved.append('insurance')
        return jsonify(saved=saved)

    @app.get('/api/home/photo_status/<reference>')
    def photo_status(reference):  # what the page's photo table shows. No file paths are sent to the browser
        ref = clean_reference(reference)
        if not ref:
            return jsonify(error='Unknown payment reference.'), 404
        with contextlib.closing(sqlite3.connect(photos.database, timeout=15)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute('SELECT kind, state, attempts, error, path, next_try FROM crm_photos WHERE reference = ? ORDER BY kind', (ref,)).fetchall()

        def why(row):  # the reason Monday refused the photo, with any file path swapped out so the browser never learns where files are kept
            note = row['error'] or ''
            for place in sorted({str(scratch.resolve()), str(scratch), str(ROOT), str(Path.home())}, key=len, reverse=True):  # longest first
                note = re.sub(re.escape(place) + r'[^\s()\'"]*', '[file on this computer]', note)  # the whole path, file name included
            return note
        now = time.time()
        return jsonify(photos=[dict(kind=r['kind'], state=r['state'], attempts=r['attempts'], note=why(r), onKiosk=Path(r['path']).exists(),
                                    retryIn=max(0, round(r['next_try'] - now)) if r['state'] == 'waiting' and r['attempts'] else None) for r in rows])

    return app


if __name__ == '__main__':
    load_dotenv(ROOT / '.env')
    app = create_app()
    app.extensions['home'].photos.start()
    port = int(os.getenv('HOME_TEST_PORT', '5055'))
    print(f'\n  Home test form (TEST MODE): http://127.0.0.1:{port}\n  Test files are kept in {app.extensions["home"].scratch}\n  Press Ctrl+C to stop.\n')
    app.run(host='127.0.0.1', port=port, debug=False, threaded=True)
