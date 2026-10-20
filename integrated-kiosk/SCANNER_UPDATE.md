# Scanner integration — October 9, 2026

The latest supplied registrationnation-main (1).zip was compared against the
previous source. Its Kiosk Scanners/kiosk_scanners package is included here;
the ZIP's separate integrated-kiosk scanner source was unchanged. Its Windows
camera window is not started on the Pi: camera_service.py and extraction_service.py
connect the package to the web UI and Picamera2.

Included parser changes handle month-name insurance dates, distinguish policy
numbers from policy-period labels, and prioritize vehicle years near the VIN.
An additional local fix also excludes policy dates from vehicle-year fallback
when the VIN is unreadable. The supplied license barcode decoder was unchanged.

This release tries quick OCR on the priority crop and whole document before
additional candidates, then falls back to alternate preprocessing and page
segmentation. It keeps the best complete result; early exit requires complete
fields with high confidence. The default time budget stays 60 seconds. No
speedup measurement or all-document accuracy guarantee has been made on a Pi.

License extraction uses the back-side PDF417 barcode, not front-side text OCR.
Keep the entire barcode visible and focused. Insurance can be paper or a screen;
glare, small text, and camera field of view still affect results.

The uncropped live camera preview, autofocus settings, and default insurance
debug saving remain. Coverage start date and policy type are optional in the UI.
For this release replace the COMPLETE project, including dist/, src/, backend,
and scanner package; follow INSTALL_AND_RUN.md. Older two-file update steps do
not include the new payment or reset behavior.
