# ============================================================================
# File:         security_photos_CG.py
# Written by:   CG (Cristian Gonzalez)
# What it does: Puts the security Pi's three photos for a renewal (license
#               scanner opened, Pay pressed, receipt printed) on that renewal's
#               Monday.com record.
# Team-written: All logic written by CG (Cristian Gonzalez), except the lines
#               tagged [3RD PARTY]. It reads BV's camera queue
#               (delivery_service_BV.py) and talks to DG's security Pi through
#               BV and DG's message format (security_wire_BV_DG.py).
# Third-party:  pyserial; Monday.com file upload (through monday_service_CG.py);
#               SQLite (Python standard library).
# ============================================================================
"""Security photos on the Monday.com record.

The security Pi (Diego's) takes a front-camera photo at three moments and keeps it on its own disk:
  license_scan     the customer opened the driver's license scanner
  payment_click    the customer pressed Pay (one photo per attempt, so a declined card and the retry both show up)
  print_completed  the printer reported the sticker and receipt finished

The kiosk picks a random 32-character id for every one of those photos before asking for it, and keeps it in the
camera queue (runtime/deliveries/delivery.sqlite3, delivery_service_BV.py). That id is all it takes to ask the
security Pi for the same photo back, so this file:
  1. When a payment starts, links that renewal's Pay photos and the customer's license-scanner photo to it.
     The Pay photos are easy, their queue key already has the renewal reference in it. The license-scanner photo
     has no reference, so it's the latest one no earlier renewal has claimed, plus any taken shortly before it
     (the same 10 minute "same customer" rule as crm_photos_CG.py).
  2. Waits for the Monday record, then for each photo: waits until the security Pi has saved it, asks for it back
     over the same serial wire (or HTTP) the kiosk already uses, and uploads it to that photo's Files column.
     The print photo only exists once the printer finishes, which can be after the Monday record, so it's looked
     up then.
  3. Retries on its own and picks unfinished work back up after a restart. Nothing here can slow down or fail a
     payment, a print or the Monday record itself. Problems go to runtime/kiosk-problems.log for the office.

The photo is only ever held in memory on the kiosk, never saved here. The original stays on the security Pi.

Settings in .env (a photo is only fetched and uploaded if its column is set; run  python3 monday_service_CG.py columns
to find the ids). Each one must be the id of a Files column:
  MONDAY_SECURITY_LICENSE_COLUMN   photo from when the license scanner was opened
  MONDAY_SECURITY_PAY_COLUMN       photo from when Pay was pressed
  MONDAY_SECURITY_PRINT_COLUMN     photo from when the receipt finished printing
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import monday_service_CG as monday_service
from crm_photos_CG import MAX_AGE_MINUTES, SAME_CUSTOMER_MINUTES, clean_reference, log_problem

EVENTS = {  # event -> (.env setting for its Monday Files column, start of the file name on Monday, words for the office log)
    "license_scan": ("MONDAY_SECURITY_LICENSE_COLUMN", "security-license-scan", "license-scanner"),
    "payment_click": ("MONDAY_SECURITY_PAY_COLUMN", "security-pay", "Pay"),
    "print_completed": ("MONDAY_SECURITY_PRINT_COLUMN", "security-print-done", "print-finished"),
}
PRINT_WAIT_HOURS = 3  # if the printer still hasn't reported finishing by then, stop waiting for that photo
MAX_ATTEMPTS = 6  # downloads/uploads tried per photo before giving up and telling the office
MAX_PHOTO = 3 * 1024 * 1024  # bytes. The security Pi sends a shrunk copy, so a real one is far smaller
PIECES_PER_TURN = 8  # serial pieces fetched before letting a new capture have the wire (about 2 seconds)
PIECE_SECONDS = 6  # how long to wait for one piece before deciding the security Pi isn't answering


class SecurityPhotoError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):  # never send the shared token on to somewhere else
    def redirect_request(self, *args, **kwargs):
        return None


# ---- getting a photo back from the security Pi ----

def fetch_photo(capture_id: str) -> bytes:
    """The jpeg the security Pi saved for this capture id, over whichever link the kiosk is set up to use."""
    if not re.fullmatch(r"[0-9a-f]{32}", capture_id or ""):
        raise SecurityPhotoError("that isn't a capture id")
    token = os.getenv("SECURITY_SHARED_TOKEN", "")
    if len(token) < 32:
        raise SecurityPhotoError("SECURITY_SHARED_TOKEN isn't set on the kiosk")
    transport = os.getenv("SECURITY_TRANSPORT", "http").lower()  # same default as security_client_BV_DG.py
    data = _fetch_serial(capture_id, token) if transport == "serial" else _fetch_http(capture_id, token) if transport == "http" else None
    if data is None:
        raise SecurityPhotoError("SECURITY_TRANSPORT must be http or serial")
    if not data.startswith(b"\xff\xd8"):
        raise SecurityPhotoError("the security Pi sent something that isn't a jpeg")
    return data


def _fetch_http(capture_id: str, token: str) -> bytes:
    url = os.getenv("SECURITY_PI_URL", "").rstrip("/")
    if not url:
        raise SecurityPhotoError("SECURITY_PI_URL isn't set on the kiosk")
    request = Request(url + "/photo/" + capture_id, data=b"", method="POST", headers={"Authorization": "Bearer " + token})
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=30) as response:
            data = response.read(MAX_PHOTO + 1)
            digest = response.headers.get("X-Photo-SHA256", "")
    except Exception as exc:
        if getattr(exc, "code", None) == 404:
            raise SecurityPhotoError("the security Pi has no saved photo with that id") from exc
        raise SecurityPhotoError(f"could not get the photo from the security Pi ({type(exc).__name__})") from exc
    if len(data) > MAX_PHOTO or (digest and hashlib.sha256(data).hexdigest() != digest):
        raise SecurityPhotoError("the photo from the security Pi came through damaged")
    return data


def _open_port():
    import serial  # [3RD PARTY: pyserial]
    return serial.Serial(os.getenv("SECURITY_SERIAL_PORT", "/dev/serial0"), baudrate=115200, timeout=0.2,
                         write_timeout=2, exclusive=True)


def _fetch_serial(capture_id: str, token: str) -> bytes:
    from security_client_BV_DG import SERIAL_LOCK
    data, total, digest = bytearray(), None, None
    for _ in range(MAX_PHOTO // 1024 + 10):  # a turn always moves forward, so this is only a safety limit
        if not SERIAL_LOCK.acquire(timeout=90):
            raise SecurityPhotoError("the serial wire stayed busy")
        try:
            with _open_port() as port:
                port.reset_input_buffer()
                for _ in range(PIECES_PER_TURN):
                    answer = _ask(port, token, capture_id, len(data))
                    if answer.get("error") == "not_found":
                        raise SecurityPhotoError("the security Pi has no saved photo with that id")
                    size, sha = answer.get("total"), answer.get("sha256")
                    if not isinstance(size, int) or not 0 < size <= MAX_PHOTO or not isinstance(sha, str):
                        raise SecurityPhotoError("the security Pi sent an answer that doesn't make sense")
                    if total is None:
                        total, digest = size, sha
                    elif (size, sha) != (total, digest):
                        raise SecurityPhotoError("the photo changed while it was being sent")
                    try:
                        piece = base64.b64decode(answer.get("data", ""), validate=True)
                    except ValueError as exc:
                        raise SecurityPhotoError("a piece of the photo came through damaged") from exc
                    if not piece or len(data) + len(piece) > total:
                        raise SecurityPhotoError("a piece of the photo came through damaged")
                    data += piece
                    if len(data) == total:
                        if hashlib.sha256(data).hexdigest() != digest:
                            raise SecurityPhotoError("the photo came through damaged")
                        return bytes(data)
        finally:
            SERIAL_LOCK.release()
        time.sleep(0.2)  # a capture waiting for the wire gets it now, so the photo for a customer at the kiosk is never late
    raise SecurityPhotoError("the photo never finished coming through")


def _ask(port, token: str, capture_id: str, offset: int) -> dict:
    from security_wire_BV_DG import decode, encode, read_frame
    message_id = uuid.uuid4().hex
    port.write(encode({"id": message_id, "action": "photo_chunk", "photo": capture_id, "offset": offset}, token))
    deadline = time.monotonic() + PIECE_SECONDS
    while True:
        raw = read_frame(port, deadline)  # raises TimeoutError once the deadline passes
        try:
            answer = decode(raw, token)
        except (ValueError, KeyError, TypeError):
            continue  # noise, or an answer signed with another token
        if answer.get("id") == message_id and answer.get("photo") == capture_id and (answer.get("error") or answer.get("offset") == offset):
            return answer  # anything else is a late answer to an earlier question, so it's skipped


# ---- linking photos to renewals, and the background worker ----

class SecurityPhotos:
    def __init__(self, root, database=None, camera_queue=None) -> None:
        self.root = Path(root)
        self.database = Path(database) if database else self.root / "runtime" / "security_photos.sqlite3"
        self.camera_queue = Path(camera_queue) if camera_queue else self.root / "runtime" / "deliveries" / "delivery.sqlite3"
        self.database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.stop = threading.Event()
        self.wake = threading.Event()
        with self._db() as db:
            db.executescript(
                "CREATE TABLE IF NOT EXISTS security_photos (id INTEGER PRIMARY KEY, reference TEXT NOT NULL, event TEXT NOT NULL,"
                " request_id TEXT NOT NULL DEFAULT '', taken REAL NOT NULL, item_id TEXT NOT NULL DEFAULT '',"
                " state TEXT NOT NULL DEFAULT 'waiting', attempts INTEGER NOT NULL DEFAULT 0, next_try REAL NOT NULL DEFAULT 0,"
                " error TEXT NOT NULL DEFAULT '');"
                "CREATE UNIQUE INDEX IF NOT EXISTS one_row_per_photo ON security_photos (request_id) WHERE request_id != '';"
                "CREATE UNIQUE INDEX IF NOT EXISTS one_print_per_renewal ON security_photos (reference) WHERE event = 'print_completed';"
            )
            month = time.time() - 30 * 86400
            db.execute("DELETE FROM security_photos WHERE state != 'waiting' AND taken < ?", (month,))  # finished rows don't need keeping
            db.execute("DELETE FROM security_photos WHERE item_id = '' AND taken < ?", (time.time() - 7 * 86400,))  # never reached monday
        try:
            os.chmod(self.database, 0o600)
        except OSError:
            pass

    @contextmanager
    def _db(self):
        connection = sqlite3.connect(self.database, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _cameras(self, query: str, params=()) -> list:
        """Reads BV's camera queue without ever writing to it. No queue yet just means no photos yet."""
        if not self.camera_queue.exists():
            return []
        connection = sqlite3.connect(f"file:{self.camera_queue}?mode=ro", uri=True, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            return connection.execute(query, params).fetchall()
        finally:
            connection.close()

    def bind(self, reference) -> None:
        """A payment just started: link this customer's security photos to its renewal. Safe to call on every attempt."""
        reference = clean_reference(reference)
        if not reference:
            return
        now = time.time()
        prefix = "pay:" + reference + ":"  # the key payment_started() in scanner_api_BV_CG.py gives every Pay photo
        pays = self._cameras("SELECT request_id, created FROM cameras WHERE substr(key, 1, ?) = ?", (len(prefix), prefix))
        scans = self._cameras("SELECT request_id, created FROM cameras WHERE substr(key, 1, 8) = 'license:' AND event = 'license_scan'"
                              " AND created > ? ORDER BY created DESC", (now - MAX_AGE_MINUTES * 60,))
        customer = []  # the newest scanner photo, and the ones just before it from the same visit
        for row in scans:
            if customer and customer[-1]["created"] - row["created"] > SAME_CUSTOMER_MINUTES * 60:
                break  # a long gap means that one belonged to somebody who left without paying
            customer.append(row)
        with self._db() as db:
            # a photo already linked to an earlier renewal stays with it (one_row_per_photo), so the last customer's
            # scanner photo is never reused for the next customer, who may not have opened the scanner at all
            for event, rows in (("license_scan", customer), ("payment_click", pays)):
                for row in rows:
                    db.execute("INSERT OR IGNORE INTO security_photos (reference, event, request_id, taken) VALUES (?,?,?,?)",
                               (reference, event, row["request_id"], row["created"]))
            db.execute("INSERT OR IGNORE INTO security_photos (reference, event, taken) VALUES (?, 'print_completed', ?)", (reference, now))
            have_scan = db.execute("SELECT 1 FROM security_photos WHERE reference = ? AND event = 'license_scan'", (reference,)).fetchone()
        if not have_scan and os.getenv(EVENTS["license_scan"][0], "").strip():
            log_problem("SECURITY PHOTO MISSING", f"payment {reference}: no recent license-scanner security photo was asked for")

    def attach(self, reference, item_id) -> None:
        """The monday record exists: tell the worker which item the photos belong to."""
        reference = clean_reference(reference)
        item = str(item_id or "")
        if not reference or not re.fullmatch(r"\d{1,20}", item):
            return
        with self._db() as db:
            db.execute("UPDATE security_photos SET item_id = ? WHERE reference = ? AND item_id = ''", (item, reference))
        self.wake.set()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="security-photo-monday").start()

    def _run(self) -> None:
        while not self.stop.is_set():
            try:
                self.process_once()
            except Exception:  # the thread must never die, or photos would stop until the next restart
                log_problem("SECURITY PHOTO WORKER", "unexpected error while moving security photos to Monday; it will try again")
            self.wake.wait(15)
            self.wake.clear()

    def process_once(self) -> None:
        with self._db() as db:
            rows = db.execute("SELECT * FROM security_photos WHERE state = 'waiting' AND item_id != '' AND next_try <= ? ORDER BY taken",
                              (time.time(),)).fetchall()
        for row in rows:
            if self.stop.is_set():
                return
            self._handle(row)

    def _handle(self, row) -> None:
        setting, label, words = EVENTS[row["event"]]
        column = os.getenv(setting, "").strip()
        if not column:
            self._set(row, state="skipped", error=f"{setting} is not set, so this photo is not uploaded")
            return
        request_id = row["request_id"]
        if not request_id:  # the print photo: asked for only once the printer reports the job finished
            found = self._cameras("SELECT request_id FROM cameras WHERE key = ?", ("print:" + row["reference"],))
            if not found:
                if time.time() - row["taken"] > PRINT_WAIT_HOURS * 3600:
                    self._set(row, state="skipped", error="the printer never reported this job finished")
                    log_problem("SECURITY PHOTO MISSING", f"payment {row['reference']}: the printer never reported the job finished, so there is no print-finished photo")
                else:
                    self._set(row, next_try=time.time() + 60)
                return
            request_id = found[0]["request_id"]
            self._set(row, request_id=request_id)
        queued = self._cameras("SELECT state FROM cameras WHERE request_id = ?", (request_id,))
        camera_state = queued[0]["state"] if queued else "saved"
        if camera_state in {"pending", "sending"}:
            self._set(row, next_try=time.time() + 20)  # the security Pi hasn't taken it yet
            return
        if camera_state in {"failed", "disabled"}:  # no photo exists. 'uncertain' still gets a try: it may well be saved
            self._set(row, state="skipped", error=f"the security Pi did not save this photo ({camera_state})")
            log_problem("SECURITY PHOTO MISSING", f"payment {row['reference']}: the security Pi did not save the {words} photo")
            return
        try:
            data = fetch_photo(request_id)
            filename = f"{label}-{row['reference'][-12:]}-{datetime.fromtimestamp(row['taken']):%H%M%S}.jpg"
            monday_service.upload_file(row["item_id"], column, filename, data, setting)
        except (SecurityPhotoError, monday_service.MondayError, OSError, TimeoutError, ValueError) as exc:
            self._failed(row, words, str(exc))
            return
        self._set(row, state="uploaded", error="")

    def _set(self, row, **changes) -> None:
        names = ", ".join(f"{name} = ?" for name in changes)
        with self._db() as db:
            db.execute(f"UPDATE security_photos SET {names} WHERE id = ?", (*changes.values(), row["id"]))

    def _failed(self, row, words: str, message: str) -> None:
        attempts = row["attempts"] + 1
        if attempts >= MAX_ATTEMPTS:
            self._set(row, state="failed", attempts=attempts, error=message[:300])
            log_problem("SECURITY PHOTO NOT ATTACHED",
                        f"{words} photo for payment {row['reference']} (monday item {row['item_id']}) after {attempts} tries: {message}"
                        f" - the photo is still on the security Pi, capture id {row['request_id']}")
        else:
            self._set(row, attempts=attempts, next_try=time.time() + min(30 * 2 ** attempts, 900), error=message[:300])  # 1, 2, 4, 8, 15 min


if __name__ == "__main__":
    # py security_photos_CG.py   shows where each renewal's security photos are, newest first
    from dotenv import load_dotenv  # [3RD PARTY: python-dotenv]
    root = Path(__file__).resolve().parent
    load_dotenv(root / ".env")
    path = root / "runtime" / "security_photos.sqlite3"
    if not path.exists():
        raise SystemExit("No security photos have been linked to a payment yet.")
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        for row in connection.execute("SELECT reference, event, state, attempts, error FROM security_photos ORDER BY taken DESC LIMIT 30"):
            print(json.dumps(dict(row)))
