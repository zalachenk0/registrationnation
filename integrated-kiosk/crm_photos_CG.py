# ============================================================================
# File:         crm_photos_CG.py
# Written by:   CG (Cristian Gonzalez)
# What it does: Links each customer's license and insurance photos to their
#               payment, uploads them to the Monday.com record and deletes the
#               kiosk copy once Monday has it.
# Team-written: All logic written by CG (Cristian Gonzalez), except the lines
#               tagged [3RD PARTY].
# Third-party:  OpenCV; Monday.com file upload (through monday_service_CG.py);
#               SQLite (Python standard library).
# ============================================================================
"""Photos for the Monday.com record: the front of the driver's license and the insurance card.

How a photo finds its record, without the browser having to carry anything:
  1. The kiosk saves a photo as the customer scans (license front, insurance card). Each is remembered as
     "the photos for the customer at the kiosk right now".
  2. When that customer starts to pay, the photos are linked to the payment reference and the memory is cleared,
     so the next customer starts with nothing.
  3. When the Monday record for that payment is created, its item id is added to the link.
  4. A background worker uploads the photos to the item's Files columns. It retries on its own, and it
     picks up unfinished uploads again after a restart. Nothing here can slow down or fail a payment, a print
     or the Monday record itself.
  5. Once Monday has confirmed a photo, its copy on the kiosk is deleted, so no customer document stays on the Pi.
     Only a photo that really reached Monday is deleted: one that was skipped (no column set), is still waiting,
     or failed is kept, and the office log says where it is. Set KIOSK_KEEP_LOCAL_PHOTOS=1 to keep every copy.

Keeping one customer's photos off the next customer's record:
  - a license photo taken more than SAME_CUSTOMER_MINUTES after the last one starts a new customer
  - scanning a different license barcode than before also means a new customer, and drops the old insurance photo
  - photos older than MAX_AGE_MINUTES are never attached
  - once a payment has taken the photos, they are gone from memory

Settings in .env (a photo is only uploaded if its column is set; run  py monday_service_CG.py columns  to find the ids):
  MONDAY_LICENSE_PHOTO_COLUMN     id of a Files column for the front of the driver's license
  MONDAY_INSURANCE_PHOTO_COLUMN   id of a Files column for the insurance card
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import cv2

import monday_service_CG as monday_service

KINDS = ("license", "insurance")
SETTINGS = {"license": "MONDAY_LICENSE_PHOTO_COLUMN", "insurance": "MONDAY_INSURANCE_PHOTO_COLUMN"}
NAMES = {"license": "license-front", "insurance": "insurance-card"}
SAME_CUSTOMER_MINUTES = 10  # a license photo this soon after the last one is the same person retaking it
MAX_AGE_MINUTES = 45  # photos older than this are never attached to a payment
KEEP_SETTING = "KIOSK_KEEP_LOCAL_PHOTOS"  # set to 1 to keep photos on the kiosk after they reach Monday
MAX_ATTEMPTS = 5  # uploads tried per photo before giving up and telling the office
LONGEST_SIDE = 2000  # pixels. The camera's full-size photos are far bigger than the office needs to read a card


# one line per problem in runtime/kiosk-problems.log, the same file and format the rest of the kiosk uses for the office
def log_problem(kind: str, detail: str) -> None:
    try:
        path = Path(os.getenv("KIOSK_PROBLEM_LOG") or Path(__file__).resolve().parent / "runtime" / "kiosk-problems.log")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as log:
            log.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {kind}  {' '.join(str(detail).split())[:500]}\n")
    except OSError:
        pass  # a logging problem must never get in a customer's way


def clean_reference(value) -> str | None:
    text = value.strip() if isinstance(value, str) else ""
    return text if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", text) else None


# makes the photo a sensible size for Monday: still easy to read, a fraction of the camera's file size
def prepare(path: str) -> bytes:
    image = cv2.imread(str(path))
    if image is None:
        raise ValueError("the saved photo is missing or can't be read")
    height, width = image.shape[:2]
    longest = max(height, width)
    if longest > LONGEST_SIDE:
        scale = LONGEST_SIDE / longest
        image = cv2.resize(image, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise ValueError("the photo could not be prepared for upload")
    return encoded.tobytes()


class PhotoDesk:
    def __init__(self, root, database=None) -> None:
        self.root = Path(root)
        self.database = Path(database) if database else self.root / "runtime" / "crm_photos.sqlite3"
        self.database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.current: dict[str, tuple[str, float]] = {}  # kind -> (photo path, when it was taken): the customer at the kiosk right now
        self.license_print: str | None = None  # fingerprint of the license being handled, never stored anywhere
        with self._db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS crm_photos (reference TEXT NOT NULL, kind TEXT NOT NULL, path TEXT NOT NULL,"
                " taken REAL NOT NULL, item_id TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'waiting',"
                " attempts INTEGER NOT NULL DEFAULT 0, next_try REAL NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',"
                " PRIMARY KEY (reference, kind))"
            )
            month = time.time() - 30 * 86400
            db.execute("DELETE FROM crm_photos WHERE state != 'waiting' AND taken < ?", (month,))  # finished rows don't need keeping
            db.execute("DELETE FROM crm_photos WHERE item_id = '' AND taken < ?", (time.time() - 7 * 86400,))  # payments that never reached monday
        try:
            os.chmod(self.database, 0o600)
        except OSError:
            pass

    @contextmanager
    def _db(self):
        connection = sqlite3.connect(self.database, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            with connection:  # commits on success, rolls back on an error
                yield connection
        finally:
            connection.close()

    # ---- while the customer scans ----

    def license_taken(self, path) -> None:
        """The front-of-license photo was just saved."""
        with self.lock:
            last = self.current.get("license")
            if last is None or time.time() - last[1] > SAME_CUSTOMER_MINUTES * 60:
                self.current = {}  # nobody has been at the kiosk for a while: this is a new customer
                self.license_print = None
            self.current["license"] = (str(path), time.time())  # a retake by the same person just replaces it

    def license_scanned(self, decoded) -> None:
        """The back of the license was scanned. A different card than before means a different customer."""
        fingerprint = hashlib.sha256(json.dumps(decoded, sort_keys=True, default=str).encode()).hexdigest()
        with self.lock:
            if self.license_print not in (None, fingerprint):
                self.current.pop("insurance", None)  # that insurance photo belonged to the previous customer
            self.license_print = fingerprint

    def save_insurance(self, image) -> Path:
        """Saves the photo that was just read as the insurance card, as a jpeg (the OCR debug images may be switched off)."""
        directory = Path(os.getenv("KIOSK_INSURANCE_PHOTO_DIR") or self.root / "runtime" / "insurance_front")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid.uuid4().hex + ".jpg"
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            raise OSError("could not encode the insurance photo")
        target = directory / name
        try:
            with target.open("xb") as photo:
                os.chmod(target, 0o600)
                photo.write(encoded.tobytes())
        except OSError:
            target.unlink(missing_ok=True)
            raise
        with self.lock:
            self.current["insurance"] = (str(target), time.time())
        return target

    # ---- when the customer pays, and when the monday record exists ----

    def bind(self, reference) -> None:
        """Links the photos taken so far to this payment, then clears the memory for the next customer."""
        reference = clean_reference(reference)
        if not reference:
            return
        with self._db() as db:
            if db.execute("SELECT 1 FROM crm_photos WHERE reference = ? LIMIT 1", (reference,)).fetchone():
                return  # the same payment again (a retry, or an old unpaid payment replaced): the first link stands, and nothing is cleared
        with self.lock:
            photos, self.current = self.current, {}
            self.license_print = None
        now = time.time()
        fresh = {kind: item for kind, item in photos.items() if now - item[1] <= MAX_AGE_MINUTES * 60}
        with self._db() as db:
            for kind, (path, taken) in fresh.items():
                db.execute("INSERT OR IGNORE INTO crm_photos (reference, kind, path, taken) VALUES (?,?,?,?)", (reference, kind, path, taken))
        wanted = [kind for kind in KINDS if os.getenv(SETTINGS[kind], "").strip()]
        missing = [NAMES[kind] for kind in wanted if kind not in fresh]
        if missing:
            log_problem("MONDAY PHOTOS MISSING", f"payment {reference}: no recent {' or '.join(missing)} photo was captured for this customer")

    def attach(self, reference, item_id) -> None:
        """The monday record exists: tell the worker which item the photos belong to."""
        reference = clean_reference(reference)
        item = str(item_id or "")
        if not reference or not re.fullmatch(r"\d{1,20}", item):
            return
        with self._db() as db:
            db.execute("UPDATE crm_photos SET item_id = ? WHERE reference = ? AND item_id = ''", (item, reference))
        self.wake.set()

    # ---- the background worker ----

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="crm-photo-upload").start()

    def _run(self) -> None:
        while not self.stop.is_set():
            try:
                self.process_once()
            except Exception:  # the thread must never die, or photos would stop uploading until the next restart
                log_problem("MONDAY PHOTO WORKER", "unexpected error while uploading photos; it will try again")
            self.wake.wait(15)
            self.wake.clear()

    def process_once(self) -> None:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM crm_photos WHERE state = 'waiting' AND item_id != '' AND next_try <= ? ORDER BY taken", (time.time(),)
            ).fetchall()
        for row in rows:
            self._upload(row)

    def _upload(self, row) -> None:
        kind = row["kind"]
        column = os.getenv(SETTINGS[kind], "").strip()
        if not column:
            self._finish(row, "skipped", f"{SETTINGS[kind]} is not set, so this photo is not uploaded")
            return
        filename = f"{NAMES[kind]}-{row['reference'][-12:]}.jpg"
        try:
            data = prepare(row["path"])
            monday_service.upload_file(row["item_id"], column, filename, data, SETTINGS[kind])
        except (monday_service.MondayError, OSError, ValueError) as exc:
            self._failed(row, str(exc))
            return
        self._finish(row, "uploaded", "")
        self._delete_local_copy(row)  # monday has it now, so the kiosk does not need to keep it

    # the only folders a photo may be deleted from: the two this kiosk saves customer photos in
    def _photo_folders(self) -> list[Path]:
        return [Path(os.getenv("KIOSK_LICENSE_PHOTO_DIR") or self.root / "runtime" / "license_front"),
                Path(os.getenv("KIOSK_INSURANCE_PHOTO_DIR") or self.root / "runtime" / "insurance_front")]

    # called only after monday has confirmed the photo. A delete that goes wrong never undoes the upload
    def _delete_local_copy(self, row) -> None:
        if os.getenv(KEEP_SETTING, "").strip().lower() in {"1", "true", "yes", "on"}:
            return
        try:
            target = Path(row["path"]).resolve()  # resolve() follows links, so a link pointing outside the folders is refused
            if not any(target.is_relative_to(folder.resolve()) for folder in self._photo_folders()):
                log_problem("PHOTO NOT DELETED", f"{NAMES[row['kind']]} photo for payment {row['reference']} is outside the photo folders, so it was left alone: {row['path']}")
                return
            target.unlink(missing_ok=True)
        except OSError as exc:
            log_problem("PHOTO NOT DELETED", f"{NAMES[row['kind']]} photo for payment {row['reference']} reached Monday but could not be deleted from the kiosk: {exc}")

    def _finish(self, row, state: str, note: str) -> None:
        with self._db() as db:
            db.execute("UPDATE crm_photos SET state = ?, error = ? WHERE reference = ? AND kind = ?", (state, note, row["reference"], row["kind"]))

    def _failed(self, row, message: str) -> None:
        attempts = row["attempts"] + 1
        if attempts >= MAX_ATTEMPTS:
            state, wait = "failed", 0
            log_problem(
                "MONDAY PHOTO NOT ATTACHED",
                f"{NAMES[row['kind']]} photo for payment {row['reference']} (monday item {row['item_id']}) after {attempts} tries: {message}"
                f" - the photo is saved on the kiosk at {row['path']}",
            )
        else:
            state, wait = "waiting", min(30 * 2 ** attempts, 900)  # 1, 2, 4, 8 minutes
        with self._db() as db:
            db.execute(
                "UPDATE crm_photos SET state = ?, attempts = ?, next_try = ?, error = ? WHERE reference = ? AND kind = ?",
                (state, attempts, time.time() + wait, message[:300], row["reference"], row["kind"]),
            )
