from __future__ import annotations

"""Print a clearly labeled mock registration sticker through CUPS."""

import os
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from typing import Any


class PrinterError(RuntimeError):
    """The configured printer was unavailable or rejected the print job."""


def printer_status() -> dict[str, object]:
    return {
        "available": shutil.which("lp") is not None,
        "printer": os.getenv("KIOSK_PRINTER_NAME") or "system default",
    }


def clean(value: Any, fallback: str = "N/A") -> str:
    text = " ".join(str(value or "").split())
    return text[:80] or fallback


def build_registration_mockup(data: dict[str, Any]) -> str:
    vehicle = " ".join(
        part
        for part in (
            clean(data.get("vehicle_year"), ""),
            clean(data.get("vehicle_make"), ""),
            clean(data.get("vehicle_model"), ""),
        )
        if part
    ) or "N/A"
    expiration_year = date.today().year + 1
    return "\n".join(
        [
            "+--------------------------------------------------+",
            "|          TEXAS VEHICLE REGISTRATION              |",
            "|             DEMO STICKER / RECEIPT               |",
            "+--------------------------------------------------+",
            f"  PLATE:        {clean(data.get('license_plate'))}",
            f"  VEHICLE:      {vehicle}",
            f"  VIN:          {clean(data.get('vin'))}",
            f"  REGISTRANT:   {clean(data.get('registrant'))}",
            f"  EXPIRES:      12/31/{expiration_year}",
            f"  AMOUNT:       {clean(data.get('amount'), '$71.00')}",
            f"  CONFIRMATION: {clean(data.get('confirmation_number'))}",
            "+--------------------------------------------------+",
            "| DEMONSTRATION ONLY - NOT AN OFFICIAL DOCUMENT    |",
            "+--------------------------------------------------+",
            "",
        ]
    )


def print_registration_mockup(data: dict[str, Any]) -> str:
    lp_command = shutil.which("lp")
    if lp_command is None:
        raise PrinterError("Printing is unavailable. Install cups-client so the 'lp' command is present.")

    handle = tempfile.NamedTemporaryFile(
        prefix="registration-demo-", suffix=".txt", mode="w", encoding="utf-8", delete=False
    )
    path = Path(handle.name)
    try:
        with handle:
            handle.write(build_registration_mockup(data))

        command = [lp_command, "-t", "Registration Demo"]
        printer_name = os.getenv("KIOSK_PRINTER_NAME", "").strip()
        if printer_name:
            command.extend(["-d", printer_name])
        command.append(str(path))

        completed = subprocess.run(command, check=True, capture_output=True, text=True, timeout=20)
        return (completed.stdout or "Print job accepted.").strip()
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "Printer rejected the job.").strip()
        raise PrinterError(detail[-500:]) from exc
    except subprocess.TimeoutExpired as exc:
        raise PrinterError("The printer did not accept the job within 20 seconds.") from exc
    finally:
        path.unlink(missing_ok=True)
