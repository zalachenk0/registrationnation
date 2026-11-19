# ============================================================================
# File:         monday_service_CG.py
# Written by:   CG (Cristian Gonzalez)
# What it does: Saves each finished renewal to the Monday.com board and uploads
#               the license and insurance photos to the record.
# Team-written: All logic written by CG (Cristian Gonzalez), except the lines
#               tagged [3RD PARTY].
# Third-party:  Requests; OpenCV; NumPy; python-dotenv; Monday.com GraphQL API
#               and file upload endpoint (developer.monday.com/api-reference).
# ============================================================================
from __future__ import annotations  # runs type hints when needed instead of when program is opened

# monday.com crm backend
# makes one new item (row) on our crm board for each finished renewal
# kept separate from payment_service_CG.py so a monday problem can never touch a payment
#
# command line helpers (run from the integrated-kiosk folder):
#   py monday_service_CG.py columns   shows the board's column ids/types so COLUMN_MAP can be filled in
#   py monday_service_CG.py test      makes a [TEST] item on the board to check everything works
#   py monday_service_CG.py photo-test ITEM_ID COLUMN_ID   uploads a sample photo to a Files column of an existing item

import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any

import requests  # monday api

MONDAY_API_URL = os.getenv("MONDAY_API_URL", "https://api.monday.com/v2")  # [3RD PARTY: Monday.com API] only overridden when testing
MONDAY_FILE_API_URL = os.getenv("MONDAY_FILE_API_URL", "https://api.monday.com/v2/file")  # [3RD PARTY: Monday.com API] photos go to a separate endpoint


# our design
class MondayError(RuntimeError):  # monday said no, or we're not set up right
    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status  # the http status the flask route sends back


# ---------------------------------------------------------------------------
# which info from the form goes into which column on the board
# board: "UTSA TTT Online Registration Renewal Duplicate"
#
#   "formField": {"column": "<monday column id>", "type": "<monday column type>"},
#   "anyName":   {"column": "<id>", "type": "status", "value": "Label"},   <- "value" means always send exactly that
#
# - column ids are NOT the column titles. run  py monday_service_CG.py columns  to see them
# - only the fields listed here are ever sent to monday, everything else stays on the kiosk
# - types we can fill: text, long_text, numbers, status, dropdown, date, datetime, email, phone, link, checkbox
# - the form fields you can use are listed in SAMPLE_FIELDS at the bottom of this file
# - status values have to match one of the board's labels exactly
# ---------------------------------------------------------------------------
COLUMN_MAP: dict[str, dict[str, str]] = {
    # customer (the first First/Last Name pair on the board, the one next to the address, phone and email)
    "firstName":           {"column": "text09__1",      "type": "text"},       # First Name
    "lastName":            {"column": "text1__1",       "type": "text"},       # Last Name
    "address":             {"column": "text016__1",     "type": "text"},       # Street Address
    "city":                {"column": "text4__1",       "type": "text"},       # City
    "state":               {"column": "text3__1",       "type": "text"},       # State / Province
    "zip":                 {"column": "text33__1",      "type": "text"},       # Postal / Zip Code
    "phone":               {"column": "phone__1",       "type": "phone"},      # Phone Number
    "email":               {"column": "email__1",       "type": "email"},      # Email

    # vehicle
    "licensePlate":        {"column": "text60__1",      "type": "text"},       # License Plate
    "vinLast6":            {"column": "text9__1",       "type": "text"},       # Vehicle VIN (Last 6), worked out from the full vin below
    "vehicleYear":         {"column": "text39__1",      "type": "text"},       # Vehicle Year
    "vehicleMake":         {"column": "text96__1",      "type": "text"},       # Vehicle Make
    "vehicleModel":        {"column": "text2__1",       "type": "text"},       # Vehicle Model
    "insuranceProvider":   {"column": "text_mm88wyt8",  "type": "text"},       # Insurance Provider (column added for the kiosk)
    "policyNumber":        {"column": "text_mm88cmrd",  "type": "text"},       # Policy Number (column added for the kiosk)
    "insuranceExpiration": {"column": "date4__1",       "type": "date"},       # Vehicle Insurance Expiration

    # the payment
    "submittedAt":         {"column": "date__1",        "type": "datetime"},   # Submission Date (this column shows the time too)
    "amountPaid":          {"column": "numbers5__1",    "type": "numbers"},     # Total Cost
    "confirmationNumber":  {"column": "text32__1",      "type": "text"},       # Invoice Number
    "paymentIntentId":     {"column": "text_mm88bzfy",  "type": "text"},       # Stripe Payment ID (column added for the kiosk)
    "adminNote":           {"column": "long_text0__1",  "type": "long_text"},  # Admin Notes (has the stripe payment id for reconciling)

    # same every time
    "source":              {"column": "text20__1",      "type": "text",   "value": "Kiosk"},                  # Source
    "transactionStatus":   {"column": "status9__1",     "type": "status", "value": "Received"},               # Transaction Status
    "renewalType":         {"column": "status59__1",    "type": "status", "value": "Registration Renewal"},   # Registration Renewal Store*
    "renewalStatus":       {"column": "status0__1",     "type": "status", "value": "Completed at kiosk"},      # Renewal Status (this label has to be added to the board first, spelled exactly like this)
}

# county column comes from .env since it was never in COLUMN_MAP. unset = county just isn't sent.
# MONDAY_COUNTY_COLUMN_TYPE is only for a dropdown or status column (those need a label for every county)
def _column_map() -> dict[str, dict[str, str]]:
    columns = dict(COLUMN_MAP)
    county = os.getenv("MONDAY_COUNTY_COLUMN", "").strip()
    if county:
        kind = os.getenv("MONDAY_COUNTY_COLUMN_TYPE", "").strip().lower()
        columns["county"] = {"column": county, "type": kind if kind in {"dropdown", "status"} else "text"}
    return columns


# column types we know how to fill (used by the 'columns' helper)
SUPPORTED_TYPES = {"text", "long_text", "numbers", "status", "dropdown", "date", "datetime", "email", "phone", "link", "checkbox"}


# monday api
# every column type wants its value in a different shape, these come from monday's column value docs
def _format_value(column_type: str, raw: Any) -> tuple[Any, str | None]:
    text = str(raw).strip()

    if column_type == "text":
        return text, None

    if column_type == "long_text":
        return {"text": text}, None

    if column_type == "numbers":
        cleaned = re.sub(r"[^0-9.\-]", "", text)  # drops $ and commas
        if not re.search(r"\d", cleaned):
            return None, f"'{text}' isn't a number"
        return cleaned, None

    if column_type == "status":
        return {"label": text}, None  # label has to match one of the board's status labels exactly

    if column_type == "dropdown":
        return {"labels": [text]}, None

    if column_type == "date":
        iso = _to_iso_date(text)
        if iso is None:
            return None, f"couldn't read '{text}' as a date"
        return {"date": iso}, None

    if column_type == "datetime":  # a date column that also shows the time
        parsed = _to_utc_datetime(text)
        if parsed is not None:
            return {"date": parsed.date().isoformat(), "time": parsed.strftime("%H:%M:%S")}, None
        iso = _to_iso_date(text)  # no time in there, so just send the date
        if iso is None:
            return None, f"couldn't read '{text}' as a date and time"
        return {"date": iso}, None

    if column_type == "email":
        if "@" not in text:
            return None, f"'{text}' isn't an email address"
        return {"email": text, "text": text}, None

    if column_type == "phone":
        digits = re.sub(r"\D", "", text)
        if len(digits) < 7:
            return None, f"'{text}' doesn't look like a phone number"
        return {"phone": digits, "countryShortName": "US"}, None

    if column_type == "link":
        return {"url": text, "text": text}, None

    if column_type == "checkbox":
        checked = text.lower() not in ("false", "no", "n", "0")
        return {"checked": "true" if checked else "false"}, None

    return None, f"column type '{column_type}' isn't supported"


# our design
# the form can hand us dates a few different ways, monday only takes YYYY-MM-DD
def _to_iso_date(text: str) -> str | None:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


# monday api
# date columns that show the time want the time in UTC, so we convert whatever we're given
def _to_utc_datetime(text: str) -> datetime | None:
    if "T" not in text and ":" not in text:
        return None  # just a date, no time to convert
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)  # no timezone given, assume utc
    return parsed.astimezone(timezone.utc)


# our design
# a few things the board wants that the form doesn't hand us directly, so we work them out here
def _add_derived_fields(fields: dict[str, Any]) -> dict[str, Any]:
    result = dict(fields)  # copy so we never change what the caller sent

    vin = re.sub(r"\s", "", str(result.get("vin") or ""))
    if not result.get("vinLast6") and len(vin) >= 6:
        result["vinLast6"] = vin[-6:].upper()  # the board only has a 'last 6' column, so the full vin never leaves the kiosk

    if not result.get("submittedAt"):
        result["submittedAt"] = datetime.now(timezone.utc).isoformat()  # the moment the record is made

    if not result.get("adminNote"):
        note = "Kiosk renewal, paid by card through Stripe."
        if result.get("paymentIntentId"):
            note += f" Payment ID: {result['paymentIntentId']}."
        if result.get("confirmationNumber"):
            note += f" Confirmation: {result['confirmationNumber']}."
        result["adminNote"] = note

    return result


def _config() -> tuple[str, str, str | None]:
    token = os.getenv("MONDAY_API_TOKEN", "").strip()  # secret, lives in .env only
    board_id = os.getenv("MONDAY_BOARD_ID", "").strip()
    group_id = os.getenv("MONDAY_GROUP_ID", "").strip() or None  # optional, otherwise monday uses the board's top group
    if not token or not board_id:
        raise MondayError("Monday.com isn't set up yet - add MONDAY_API_TOKEN and MONDAY_BOARD_ID to .env", 503)
    return token, board_id, group_id


# monday api
# monday reports problems two different ways, so we check for both
def _post(token: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    headers = {"Content-Type": "application/json", "Authorization": token}
    version = os.getenv("MONDAY_API_VERSION", "").strip()  # optional, pins the api version
    if version:
        headers["API-Version"] = version

    try:
        response = requests.post(  # [3RD PARTY: Monday.com API]
            MONDAY_API_URL,
            headers=headers,
            json={"query": query, "variables": variables},
            timeout=(5, 15),
        )
    except requests.exceptions.RequestException as exc:
        raise MondayError(f"Could not reach Monday.com: {exc}") from exc

    return _unwrap(response)


# reads monday's answer and turns every kind of problem into a MondayError
def _unwrap(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise MondayError(f"Monday.com sent back something unexpected (HTTP {response.status_code}).") from exc

    if data.get("errors"):
        first = data["errors"][0]
        message = first.get("message") if isinstance(first, dict) else str(first)
        code = (first.get("extensions") or {}).get("code") if isinstance(first, dict) else None
        raise MondayError(_friendly(message or "Monday.com rejected the request.", code))

    if data.get("error_message"):
        raise MondayError(_friendly(data["error_message"], data.get("error_code")))

    return data.get("data") or {}


# attaches one photo to a Files column of an item that already exists
# monday has a separate endpoint for files and wants a multipart form instead of json
# item_id and column_id go straight into the query text (they can't be variables on this endpoint), so they're checked first
def upload_file(item_id: str, column_id: str, filename: str, data: bytes, setting_name: str = "the photo column setting") -> None:
    token, _board_id, _group_id = _config()
    if not re.fullmatch(r"\d{1,20}", str(item_id)):
        raise MondayError("Monday.com sent back an item id that doesn't look right, so the photo was not attached.")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", column_id or ""):
        raise MondayError(f"{setting_name} doesn't look like a Monday column id (run py monday_service_CG.py columns)", 503)
    query = ("mutation ($file: File!) { add_file_to_column (file: $file, item_id: " + str(item_id)
             + ', column_id: "' + column_id + '") { id } }')
    headers = {"Authorization": token}  # requests sets the multipart content type itself
    version = os.getenv("MONDAY_API_VERSION", "").strip()
    if version:
        headers["API-Version"] = version
    try:
        response = requests.post(  # [3RD PARTY: Monday.com API]
            MONDAY_FILE_API_URL,
            headers=headers,
            data={"query": query},
            files={"variables[file]": (filename, data, "image/jpeg")},
            timeout=(5, 60),  # photos are bigger than the other requests, so the read timeout is longer
        )
    except requests.exceptions.RequestException as exc:
        raise MondayError(f"Could not reach Monday.com: {exc}") from exc
    try:
        _unwrap(response)
    except MondayError as exc:
        text = str(exc).replace(" - check COLUMN_MAP in monday_service_CG.py matches the board", "")  # wrong hint for a photo column
        if "column" in text.lower():
            text += f" - check that {setting_name} is the id of a Files column"
        raise MondayError(text, exc.status) from exc


# our design
# adds a hint to the errors we're most likely to hit
def _friendly(message: str, code: str | None) -> str:
    text = f"{message} ({code})" if code else message
    low = message.lower()
    if "not authenticated" in low or "unauthorized" in low:
        text += " - check MONDAY_API_TOKEN"
    elif "column" in low:
        text += " - check COLUMN_MAP in monday_service_CG.py matches the board"
    return text


# the main function the flask route calls
# item_name is the row's name, fields is everything the form knows about this renewal
def create_monday_record(item_name: str | None, fields: dict[str, Any]) -> dict[str, Any]:
    token, board_id, group_id = _config()

    if not item_name or not str(item_name).strip():
        raise MondayError("itemName is required", 400)

    # our design
    # only the fields in COLUMN_MAP get sent, and anything that can't be formatted
    # is skipped with a warning instead of making the whole record fail
    fields = _add_derived_fields(fields)
    column_values: dict[str, Any] = {}
    warnings: list[str] = []
    for field_name, spec in _column_map().items():  # COLUMN_MAP plus the county column
        raw = spec["value"] if "value" in spec else fields.get(field_name)  # "value" = same every time
        if raw is None or str(raw).strip() == "":
            continue  # nothing to send for this one
        formatted, warning = _format_value(spec["type"], raw)
        if warning:
            warnings.append(f"{field_name}: {warning}")
        if formatted is not None:
            column_values[spec["column"]] = formatted

    # monday api
    # create_item makes the row. group_id only goes in the query if we have one
    group_variable = ", $groupId: String" if group_id else ""
    group_argument = "group_id: $groupId, " if group_id else ""
    query = (
        "mutation ($boardId: ID!, $itemName: String!, $columnValues: JSON!" + group_variable + ") {"
        " create_item(board_id: $boardId, " + group_argument
        + "item_name: $itemName, column_values: $columnValues) { id } }"
    )
    variables: dict[str, Any] = {
        "boardId": board_id,
        "itemName": str(item_name).strip()[:255],  # monday item names max out at 255 characters
        "columnValues": json.dumps(column_values),  # monday wants this as a json string, not an object
    }
    if group_id:
        variables["groupId"] = group_id

    data = _post(token, query, variables)

    try:
        item_id = data["create_item"]["id"]
    except (KeyError, TypeError) as exc:
        raise MondayError("Monday.com sent back an unexpected response.") from exc

    return {"itemId": str(item_id), "warnings": warnings}


# ---------------------------------------------------------------------------
# everything below is only for the command line helpers
# ---------------------------------------------------------------------------

# every field the form sends us, with a sample value. 'test' sends all of these
# (vinLast6, submittedAt and adminNote aren't here because they're worked out automatically)
SAMPLE_FIELDS: dict[str, str] = {
    "fullName": "Sample Customer",
    "firstName": "Sample",
    "lastName": "Customer",
    "email": "sample.customer@example.com",
    "phone": "(361) 555-0123",
    "dateOfBirth": "1990-01-15",
    "address": "123 Test Street",
    "city": "Corpus Christi",
    "state": "TX",
    "zip": "78412",
    "county": "Nueces",
    "fullAddress": "123 Test Street, Corpus Christi, TX 78412",
    "licenseNumber": "00000000",
    "licensePlate": "TEST123",
    "vin": "1HGCM82633A123456",
    "vehicleYear": "2020",
    "vehicleMake": "Honda",
    "vehicleModel": "Accord",
    "vehicle": "2020 Honda Accord",
    "insuranceProvider": "Sample Insurance Co",
    "policyNumber": "POL-123456",
    "insuranceExpiration": "2027-06-30",
    "service": "Vehicle Registration Renewal",
    "amountPaid": "71.00",
    "confirmationNumber": "REG-TEST01",
    "paymentIntentId": "pi_test_sample",
    "paidDate": datetime.now().date().isoformat(),
    "status": "Paid",
}


def _labels(settings_str: str | None) -> list[str]:
    try:
        settings = json.loads(settings_str or "{}")
    except ValueError:
        return []
    labels = settings.get("labels")
    if isinstance(labels, dict):  # status columns look like {"0": "Working on it", "1": "Done"}
        return [str(v) for v in labels.values() if v]
    if isinstance(labels, list):  # dropdown columns look like [{"id": 1, "name": "Option"}]
        return [str(item.get("name")) for item in labels if isinstance(item, dict) and item.get("name")]
    return []


def list_board_columns() -> dict[str, Any]:
    token, board_id, _ = _config()
    with_settings = "query ($ids: [ID!]) { boards(ids: $ids) { name groups { id title } columns { id title type settings_str } } }"
    without_settings = "query ($ids: [ID!]) { boards(ids: $ids) { name groups { id title } columns { id title type } } }"
    try:
        data = _post(token, with_settings, {"ids": [board_id]})
    except MondayError as exc:
        if "settings_str" not in str(exc):
            raise
        data = _post(token, without_settings, {"ids": [board_id]})  # newer api versions can drop settings_str
    boards = data.get("boards") or []
    if not boards:
        raise MondayError("No board found with that MONDAY_BOARD_ID (or the token can't see it).", 404)
    return boards[0]


def _print_columns() -> None:
    board = list_board_columns()
    print(f"\nBoard: {board.get('name')}\n")

    print("Groups (put an id in MONDAY_GROUP_ID to make new items land in that group):")
    for group in board.get("groups") or []:
        print(f"  {group['id']:<28} {group['title']}")

    print("\nColumns:")
    print(f"  {'ID':<28} {'TYPE':<14} {'TITLE':<28} OPTIONS")
    for column in board.get("columns") or []:
        if column["type"] in SUPPORTED_TYPES:
            options = " | ".join(_labels(column.get("settings_str")))
        elif column["type"] == "file":
            options = "(a Files column: use its id for MONDAY_LICENSE_PHOTO_COLUMN or MONDAY_INSURANCE_PHOTO_COLUMN)"
        elif column["type"] == "name":
            options = "(this is the item name, set automatically)"
        else:
            options = "(can't be filled from the kiosk)"
        print(f"  {column['id']:<28} {column['type']:<14} {column['title']:<28} {options}")

    print("\nNext: put the columns you want filled into COLUMN_MAP in monday_service_CG.py.")
    print("For status/dropdown columns the value we send has to match one of the OPTIONS exactly.\n")


def _test_record() -> None:
    result = create_monday_record("[TEST] Sample Customer - TEST123", SAMPLE_FIELDS)
    print(f"\nWorked! Monday made item {result['itemId']} on the board.")
    if not COLUMN_MAP:
        print("COLUMN_MAP is empty, so only the item name was sent. Fill it in to send real info.")
    for warning in result["warnings"]:
        print(f"  skipped - {warning}")
    print("Find the item named '[TEST] Sample Customer - TEST123' on your board, check the columns, then delete it.\n")


# makes a small picture and attaches it to an item that already exists, to check a Files column works
def _test_photo(item_id: str, column_id: str) -> None:
    import cv2
    import numpy as np

    picture = np.full((250, 400, 3), 255, np.uint8)
    cv2.putText(picture, "Registration Nation", (20, 110), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (30, 60, 200), 2)
    cv2.putText(picture, "test photo", (20, 160), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (30, 60, 200), 2)
    ok, encoded = cv2.imencode(".jpg", picture)
    if not ok:
        raise MondayError("Could not make the test picture.", 500)
    upload_file(item_id, column_id, "kiosk-photo-test.jpg", encoded.tobytes(), "the column id you gave")
    print(f"\nWorked! Open item {item_id} on the board: its Files column should show 'kiosk-photo-test.jpg'.\n")


def _cli(argv: list[str]) -> int:
    from dotenv import load_dotenv

    load_dotenv()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # board titles can have emoji and windows consoles choke on those
    command = argv[1] if len(argv) > 1 else ""
    try:
        if command == "columns":
            _print_columns()
        elif command == "test":
            _test_record()
        elif command == "photo-test" and len(argv) == 4:
            _test_photo(argv[2], argv[3])
        else:
            print("Usage:  py monday_service_CG.py columns   |   py monday_service_CG.py test   |   py monday_service_CG.py photo-test ITEM_ID COLUMN_ID")
            return 2
    except MondayError as exc:
        print(f"\nProblem: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
