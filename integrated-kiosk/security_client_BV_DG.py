# ============================================================================
# File:         security_client_BV_DG.py
# Written by:   BV (kiosk side); DG (Diego, the security Pi side it talks to)
# What it does: Asks the security Pi to take a front camera photo for a
#               transaction event.
# Team-written: Logic written by BV (kiosk side); DG (Diego, the security Pi side it
#               talks to). Outside code is used only through the third-party
#               sources listed below.
# Third-party:  pyserial
# ============================================================================
"""Server-to-server camera request; credentials never enter the browser."""
import json
import os
import time
import uuid
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def capture_front(event='license_scan', request_id=None):
    if event not in {'license_scan', 'insurance_scan', 'payment_click', 'print_completed'}:
        raise ValueError('Unknown security event.')
    request_id = request_id or uuid.uuid4().hex
    transport = os.getenv('SECURITY_TRANSPORT', 'http').lower()
    if transport == 'serial':
        return capture_serial(event, request_id)
    if transport != 'http':
        raise ValueError('SECURITY_TRANSPORT must be http or serial')
    url = os.getenv('SECURITY_PI_URL', '').rstrip('/')
    token = os.getenv('SECURITY_SHARED_TOKEN', '')
    if not url:
        return {'enabled': False}
    if len(token) < 32:
        raise ValueError('Configure a security camera token of at least 32 characters.')
    req = Request(url + '/capture/front', data=json.dumps({'id': request_id, 'event': event}).encode(), method='POST',
                  headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    # Do not forward a bearer credential through redirects or environment proxies.
    with build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=45) as response:
        result = json.loads(response.read(4096))
    if not result.get('saved'):
        raise ValueError('Security capture did not complete.')
    return {'enabled': True, **result}


def capture_serial(event='license_scan', request_id=None):
    import serial
    from security_wire_BV_DG import encode, decode, read_frame
    token = os.getenv('SECURITY_SHARED_TOKEN', '')
    if len(token) < 32:
        raise ValueError('Configure the same token of at least 32 characters on both Pis.')
    request_id = request_id or uuid.uuid4().hex
    with serial.Serial(os.getenv('SECURITY_SERIAL_PORT', '/dev/serial0'),
                       baudrate=115200, timeout=0.2, write_timeout=2, exclusive=True) as port:
        port.reset_input_buffer()
        port.write(encode({'id': request_id, 'action': 'capture_front', 'event': event}, token))
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            raw = read_frame(port, deadline)
            try:
                result = decode(raw, token)
            except (ValueError, KeyError, TypeError):
                continue
            if result.get('id') != request_id:
                continue
            return {'enabled': True, 'saved': bool(result.get('saved')),
                    'uploaded': bool(result.get('uploaded')), 'upload_pending': bool(result.get('upload_pending')), 'error': result.get('error', '')}
    raise TimeoutError('Security camera response timed out; a photo may already be saved.')
