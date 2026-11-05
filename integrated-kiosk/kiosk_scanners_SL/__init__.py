# ============================================================================
# File:         __init__.py
# Written by:   SL (Seth Leija)
# What it does: Package entry point: exposes KioskScanner and the scan result
#               types.
# Team-written: Logic written by SL (Seth Leija). Outside code is used only
#               through the third-party sources listed below.
# Third-party:  none (standard library only)
# ============================================================================
"""
kiosk_scanners_SL — camera scanning of a driver's license, insurance card and
Texas registration renewal notice for the kiosk form.

Typical use (keeps the camera open between scans):

    from kiosk_scanners_SL import KioskScanner

    with KioskScanner() as scanner:
        license = scanner.scan_drivers_license()    # ScanResult, or None if cancelled
        if license:
            print(license.fields["customer_last_name"], license.missing)
        ...
        scanner.end_transaction()    # customer done or cancelled: erase their scans

One-off helpers open and close the camera for a single scan:

    from kiosk_scanners_SL import scan_insurance_card
    result = scan_insurance_card(fullscreen=True)

See README.md for the fields each scan returns.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .config_SL import ScannerConfig
from .results_SL import (DRIVERS_LICENSE, INSURANCE_CARD, REGISTRATION_RENEWAL, ScannerError,
                      ScanResult)
from .scanner_SL import KioskScanner

__all__ = [
    "KioskScanner", "ScannerConfig", "ScanResult", "ScannerError",
    "DRIVERS_LICENSE", "INSURANCE_CARD", "REGISTRATION_RENEWAL",
    "scan_drivers_license", "scan_insurance_card", "scan_registration_renewal",
]
__version__ = "1.0.0"


def _single_scan(document: str, config: Dict[str, Any]) -> Optional[ScanResult]:
    with KioskScanner(**config) as scanner:
        result = scanner.scan(document)
        scanner._hand_off()          # closing must not erase the result being returned
        return result


def scan_drivers_license(**config: Any) -> Optional[ScanResult]:
    """
    Single scan with its own camera session. Keyword args override
    ScannerConfig fields. Call result.erase() when the transaction is
    completed or cancelled.
    """
    return _single_scan(DRIVERS_LICENSE, config)


def scan_insurance_card(**config: Any) -> Optional[ScanResult]:
    return _single_scan(INSURANCE_CARD, config)


def scan_registration_renewal(**config: Any) -> Optional[ScanResult]:
    return _single_scan(REGISTRATION_RENEWAL, config)
