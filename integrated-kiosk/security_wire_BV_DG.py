# ============================================================================
# File:         security_wire_BV_DG.py
# Written by:   BV (kiosk side); DG (Diego, the security Pi side it talks to)
# What it does: Authenticated message format shared by the kiosk Pi and the
#               security Pi.
# Team-written: Logic written by BV (kiosk side); DG (Diego, the security Pi side it
#               talks to). Outside code is used only through the third-party
#               sources listed below.
# Third-party:  none (standard library only)
# ============================================================================
"""Small authenticated newline protocol shared by both Pis."""
import hashlib
import hmac
import json
import time

MAX_FRAME = 2048


def encode(payload, token):
    body = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    signature = hmac.new(token.encode(), body.encode(), hashlib.sha256).hexdigest()
    return (json.dumps({'body': body, 'signature': signature}) + '\n').encode()


def decode(raw, token):
    if len(raw) > MAX_FRAME:
        raise ValueError('Frame too large')
    envelope = json.loads(raw)
    body = envelope['body']
    signature = hmac.new(token.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(envelope['signature'], signature):
        raise ValueError('Authentication failed')
    result = json.loads(body)
    if not isinstance(result, dict):
        raise ValueError('Invalid payload')
    return result


def read_frame(port, deadline=None):
    buffer = bytearray()
    oversized = False
    while deadline is None or time.monotonic() < deadline:
        byte = port.read(1)
        if not byte:
            continue
        if byte == b'\n':
            if not oversized and buffer:
                return bytes(buffer)
            buffer.clear()
            oversized = False
        elif len(buffer) < MAX_FRAME and not oversized:
            buffer.extend(byte)
        else:
            oversized = True
    raise TimeoutError('No camera response. Check wiring, UART settings and camera service.')
