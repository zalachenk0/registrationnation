"""
How the kiosk form application uses the scanners. Scans happen at different
points of the form, with the customer typing in between:

  Administrator screen
  Personal information     <- scan driver's license, prefill, customer completes the rest
  Driver license details      (customer enters)
  Insurance and vehicle    <- scan insurance card, then the renewal notice IF they have one;
                              prefill, customer completes the rest
  Review -> Payment -> Print request

The form here is simulated in the console so the example runs on its own.
Run from the "Kiosk Scanners" folder:
    py examples\\integration_example.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # not needed once installed

from kiosk_scanners import KioskScanner, ScannerError


def fill_in(form: dict, keys: list) -> None:
    """Stand-in for the form screen: the customer types whatever wasn't prefilled."""
    for key in keys:
        if not form.get(key):
            form[key] = input(f"  {key}: ")


def personal_information_screen(scanner: KioskScanner, form: dict) -> None:
    print("\n== Personal information ==")
    license = scanner.scan_drivers_license()       # window opens, customer scans, window closes
    if license is not None:
        form.update(license.fields)                # prefill name + address
    # Customer completes anything not read (or everything, if they cancelled the scan)
    fill_in(form, ["customer_first_name", "customer_last_name", "street_address",
                   "city", "state", "zip_code", "phone", "email"])


def driver_license_details_screen(form: dict) -> None:
    print("\n== Driver license details ==")
    fill_in(form, ["license_number", "date_of_birth"])


def insurance_and_vehicle_screen(scanner: KioskScanner, form: dict) -> None:
    print("\n== Insurance and vehicle ==")
    insurance = scanner.scan_insurance_card()
    if insurance is not None:
        form.update(insurance.fields)

    # The renewal notice is optional: only scan it if the customer has one
    if input("Do you have your registration renewal notice? (y/n) ").strip().lower() == "y":
        renewal = scanner.scan_registration_renewal()
        if renewal is not None:
            for key in ("license_plate", "vehicle_vin", "vehicle_year", "vehicle_make", "total_fee"):
                # Only fill gaps: the insurance card already gave year/make
                if not form.get(key):
                    form[key] = renewal.fields[key]

    fill_in(form, ["insurance_company", "policy_number", "insurance_expiration",
                   "vehicle_year", "vehicle_make", "vehicle_model", "license_plate"])


def main() -> None:
    # Create ONE scanner when the kiosk app starts and keep it for every
    # customer: the camera stays open between scans, so each scan starts
    # instantly even though they're minutes apart.
    try:
        scanner = KioskScanner()
        scanner.preload()                          # open camera + OCR engines up front
    except ScannerError as e:
        print("Scanner unavailable - customers will type everything:", e)
        return

    try:
        form: dict = {}                            # one customer visit
        personal_information_screen(scanner, form)
        driver_license_details_screen(form)
        insurance_and_vehicle_screen(scanner, form)
        print("\n== Review ==")
        for key, value in form.items():
            print(f"  {key}: {value}")
    finally:
        scanner.close()                            # when the kiosk app exits


if __name__ == "__main__":
    main()
