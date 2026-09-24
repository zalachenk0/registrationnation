from __future__ import annotations

# Logs a completed renewal to the company's Monday.com board.
#
# Kept separate from payment_service.py on purpose: a failed Monday.com
# write should never be able to affect a payment that already succeeded.
# The route in scanner_api.py calls this AFTER a payment completes, not
# before or during.

import os
from typing import Any
import json

import requests

MONDAY_API_URL = "https://api.monday.com/v2"

# Left side: field names the frontend sends us (its choice).
# Right side: the matching column's real ID on the Monday board.
# TODO: replace these with the board's actual column IDs. Get them from
# the board's column settings, or from whoever manages it - Monday's
# API uses internal IDs like "text" or "status4", not the column's
# display name.
MONDAY_COLUMN_MAP = {
    "plate": "text",
    "county": "text1",
    "serviceType": "status",
    "amount": "numbers",
    "paymentIntentId": "text2",
}


# Monday.com wasn't configured, or rejected the write.
class MondayError(RuntimeError):
    pass


def create_monday_record(item_name: str, fields: dict[str, Any]) -> str:
    # Creates one item (row) on the configured Monday.com board.
    # Returns the new item's ID. Raises MondayError if Monday.com isn't
    # configured yet or the API rejects the request.
    api_token = os.getenv("MONDAY_API_TOKEN")
    board_id = os.getenv("MONDAY_BOARD_ID")

    if not api_token or not board_id:
        raise MondayError(
            "Monday.com isn't configured yet - set MONDAY_API_TOKEN and MONDAY_BOARD_ID in .env"
        )

    if not item_name:
        raise MondayError("item_name is required")

    # Translate our field names into Monday's column IDs. Monday.com
    # expects a different value SHAPE depending on the column type (see
    # developer.monday.com/api-reference/docs/items):
    #   - text / numbers columns want a plain string, e.g. "Harris"
    #   - status columns want an object like {"label": "Standard"}
    #   - date columns want an object like {"date": "2026-09-22"}
    # If MONDAY_COLUMN_MAP points a field at a status or date column,
    # add a case for it here, same as serviceType below.
    column_values: dict[str, Any] = {}
    for our_field, monday_column_id in MONDAY_COLUMN_MAP.items():
        value = fields.get(our_field)
        if value is None:
            continue
        if our_field == "serviceType":
            # TODO: confirm this matches the real status column's
            # labels on your board (must match exactly, including
            # capitalization).
            column_values[monday_column_id] = {"label": str(value)}
        else:
            column_values[monday_column_id] = str(value)

    query = """
        mutation ($boardId: ID!, $itemName: String!, $columnValues: JSON!) {
          create_item(
            board_id: $boardId,
            item_name: $itemName,
            column_values: $columnValues
          ) {
            id
          }
        }
    """

    try:
        response = requests.post(
            MONDAY_API_URL,
            headers={
                "Content-Type": "application/json",
                "Authorization": api_token,
            },
            json={
                "query": query,
                "variables": {
                    "boardId": board_id,
                    "itemName": item_name,
                    "columnValues": json.dumps(column_values),
                },
            },
            timeout=15,
        )
    except requests.exceptions.RequestException as exc:
        raise MondayError(f"Could not reach Monday.com: {exc}") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise MondayError(
            f"Monday.com returned an unexpected (non-JSON) response, status {response.status_code}."
        ) from exc

    if data.get("errors"):
        raise MondayError(data["errors"][0].get("message", "Monday.com rejected the request."))

    try:
        return data["data"]["create_item"]["id"]
    except (KeyError, TypeError) as exc:
        raise MondayError("Monday.com returned an unexpected response.") from exc
