from __future__ import annotations  # runs type hints when needed instead of when program is opened

# monday.com crm backend
# makes one new item (row) on our crm board for each finished renewal
# kept separate from payment_service.py so a monday problem can never touch a payment
#
# command line helpers (run from the integrated-kiosk folder):
#   py monday_service.py columns   shows the board's column ids/types so COLUMN_MAP can be filled in
#   py monday_service.py test      makes a [TEST] item on the board to check everything works

import json
import os
import re
import sys
from datetime import datetime
from typing import Any

import requests  # monday api

MONDAY_API_URL = os.getenv("MONDAY_API_URL", "https://api.monday.com/v2")  # only overridden when testing


# our design
class MondayError(RuntimeError):  # monday said no, or we're not set up right
    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status  # the http status the flask route sends back


# ---------------------------------------------------------------------------
# EDIT THIS: which info from the form goes into which monday column
#
#   "formField": {"column": "<monday column id>", "type": "<monday column type>"},
#
# - column ids are NOT the column titles. run  py monday_service.py columns  to see them
# - only the fields listed here are ever sent to monday, everything else stays on the kiosk
# - types we can fill: text, long_text, numbers, status, dropdown, date, email, phone, link, checkbox
# - the form fields you can use are listed in SAMPLE_FIELDS at the bottom of this file
#
# example (made up ids):
#   "email":        {"column": "email_mkx12ab",    "type": "email"},
#   "phone":        {"column": "phone_mkx34cd",    "type": "phone"},
#   "licensePlate": {"column": "text_mkx56ef",     "type": "text"},
#   "amountPaid":   {"column": "numbers_mkx78gh",  "type": "numbers"},
#   "status":       {"column": "status_mkx90ij",   "type": "status"},
#   "paidDate":     {"column": "date_mkx11kl",     "type": "date"},
# ---------------------------------------------------------------------------
COLUMN_MAP: dict[str, dict[str, str]] = {
}

# column types we know how to fill (used by the 'columns' helper)
SUPPORTED_TYPES = {"text", "long_text", "numbers", "status", "dropdown", "date", "email", "phone", "link", "checkbox"}


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
        response = requests.post(
            MONDAY_API_URL,
            headers=headers,
            json={"query": query, "variables": variables},
            timeout=(5, 15),
        )
    except requests.exceptions.RequestException as exc:
        raise MondayError(f"Could not reach Monday.com: {exc}") from exc

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


# our design
# adds a hint to the errors we're most likely to hit
def _friendly(message: str, code: str | None) -> str:
    text = f"{message} ({code})" if code else message
    low = message.lower()
    if "not authenticated" in low or "unauthorized" in low:
        text += " - check MONDAY_API_TOKEN"
    elif "column" in low:
        text += " - check COLUMN_MAP in monday_service.py matches the board"
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
    column_values: dict[str, Any] = {}
    warnings: list[str] = []
    for field_name, spec in COLUMN_MAP.items():
        raw = fields.get(field_name)
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
        elif column["type"] == "name":
            options = "(this is the item name, set automatically)"
        else:
            options = "(can't be filled from the kiosk)"
        print(f"  {column['id']:<28} {column['type']:<14} {column['title']:<28} {options}")

    print("\nNext: put the columns you want filled into COLUMN_MAP in monday_service.py.")
    print("For status/dropdown columns the value we send has to match one of the OPTIONS exactly.\n")


def _test_record() -> None:
    result = create_monday_record("[TEST] Sample Customer - TEST123", SAMPLE_FIELDS)
    print(f"\nWorked! Monday made item {result['itemId']} on the board.")
    if not COLUMN_MAP:
        print("COLUMN_MAP is empty, so only the item name was sent. Fill it in to send real info.")
    for warning in result["warnings"]:
        print(f"  skipped - {warning}")
    print("Find the item named '[TEST] Sample Customer - TEST123' on your board, check the columns, then delete it.\n")


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
        else:
            print("Usage:  py monday_service.py columns   |   py monday_service.py test")
            return 2
    except MondayError as exc:
        print(f"\nProblem: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
