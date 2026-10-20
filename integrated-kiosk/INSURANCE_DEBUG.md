# Saved insurance scans

Enabled by default. Each scan creates runtime/insurance_debug/<UTC timestamp>-<id>/.

- 00_original.png: exact input image, saved before OCR, including failed scans.
- 01_best_ocr.png: processed image for the chosen OCR result, when available.
- best_raw_text.txt and parsed_fields.json: selected OCR text and final values.

Files remain until manually removed. Resetting the customer session does not
delete these files. Previous unsaved images cannot be recovered. Disable with
KIOSK_INSURANCE_SAVE_DEBUG=0 in .env and restart. Saving failures are logged and
do not prevent extraction. Preserve runtime/ when replacing the project.

```bash
xdg-open "$HOME/integrated-kiosk-pi/runtime/insurance_debug"
```

For this complete upgrade use INSTALL_AND_RUN.md, not a single-file replacement.
