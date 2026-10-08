# Kiosk Scanners

Camera scanning for the kiosk form. The package can scan three documents:

| Scan | What the customer does | Where the fields come from |
|---|---|---|
| **Driver's license** | Scans the front, then the back | The PDF417 barcode on the back |
| **Insurance card** | Scans one card or printout | OCR (Tesseract) |
| **Registration renewal notice** (TxDMV VTR-39-A) | Lays the page sideways and scans it once | OCR (Tesseract) |

Each scan opens its own window. The window shows a live preview with a guide frame and walks the customer through capture. A review screen then shows what was read. The customer confirms it, retakes, or cancels.

---

## Setup (Windows)

1. Install **Tesseract OCR 5** from https://github.com/UB-Mannheim/tesseract/wiki.
   Keep the default path, `C:\Program Files\Tesseract-OCR`.
2. Install the Python packages. This needs Python 3.10 or newer.
   ```
   py -m pip install -r requirements.txt
   ```
3. Plug in the 16MP USB document camera. It is OpenCV camera **index 1** on the kiosk PC; indexes 0 and 2 are the laptop webcam.

Put the `kiosk_scanners` folder next to your app's code, or add the `Kiosk Scanners` folder to `PYTHONPATH`.

## Quick start: the demo

```
py -m kiosk_scanners demo
```

The demo opens a home screen. Press 1, 2 or 3 to scan a document. The last result for each document stays on screen. Press Esc to exit.

---

## Using it from Python

```python
from kiosk_scanners import KioskScanner, ScannerError

with KioskScanner() as scanner:                 # one session for the life of the kiosk app
    result = scanner.scan_drivers_license()     # blocks until the customer confirms or cancels
    if result is None:
        ...                                     # cancelled: let them type it in
    else:
        form.update(result.fields)              # dict of str
        ask_customer_to_fill(result.missing)    # keys that weren't found
    ...
    scanner.end_transaction()                   # customer finished or cancelled: erase their scans
```

- Call `scan_drivers_license()`, `scan_insurance_card()` and `scan_registration_renewal()` whenever your form needs them, in any order, as many times as needed.
- The camera stays open for the whole session, so later scans start instantly. Call `scanner.preload()` to open the camera and the OCR engines before the first scan.
- If you only need one scan, there are shortcut functions that open and close the camera themselves: `from kiosk_scanners import scan_insurance_card`, then `scan_insurance_card()`. You own that result: call `result.erase()` when the transaction ends.
- Problems that stop a scan raise `ScannerError`. Examples: the camera is unplugged, Tesseract is missing, or an image can't be read. If the camera stops sending images for 15 seconds, the scan raises `ScannerError` and the next scan reopens the camera.
- The scan call blocks while it runs. In a GUI app, run it from the UI thread with your own window hidden, or from a worker thread. Don't run two scans at once.

### Fitting scans into the kiosk form

Scans are **not** done all at once. Each one runs at its own step of the form, and the customer types in between:

| Form screen | Scan | Then the customer… |
|---|---|---|
| Administrator screen | — | — |
| **Personal information** | `scan_drivers_license()` prefills name and address | completes the fields the license doesn't have |
| Driver license details | — | enters them |
| **Insurance and vehicle** | `scan_insurance_card()`, then `scan_registration_renewal()` **only if they have the notice** | completes the rest |
| Review → Payment → Print request | — | — |

- **Call `scanner.end_transaction()` when the customer's transaction is completed or cancelled.** It erases everything scanned since the last call: the saved files and the images and field values held in memory. Copy what you need into your form first. `close()` treats a transaction that's still open as cancelled and erases it too.
- **Create one `KioskScanner` when the kiosk app starts, and keep it for every customer.** Call `preload()` once, then `close()` when the app exits. The camera stays open while the customer fills in the form, so a scan minutes later still starts instantly. Before each scan, the frames buffered while the camera sat idle are thrown away, so the preview never shows an old image (such as the previous customer's document).
- **Each scan call opens the scan window and closes it when done**, so your form comes back to the front.
- **Use the scan results to prefill, then let the customer edit.** Write `result.fields` into the form's inputs, but leave every input editable. Highlight `result.missing`, the fields the scan couldn't read.
- **Cancelling a scan isn't an error.** If the customer presses Esc, the scan returns `None`; show the form empty and let them type. A "skip" for the renewal notice works the same way: just don't call the scan.
- **Don't overwrite what's already filled.** The insurance card and the renewal notice both give the vehicle's year and make, so fill only empty inputs.

`examples/integration_example.py` walks through this exact flow.

### `ScanResult`

| Attribute | Meaning |
|---|---|
| `document` | `"drivers_license"`, `"insurance_card"` or `"registration_renewal"` |
| `fields` | `{key: value}`. A field that wasn't found is `""` |
| `missing` | List of the keys that came back empty |
| `images` | Cropped, upright images as BGR numpy arrays: `front`/`back`, `card` or `notice` |
| `notes` | Messages for staff, such as `"Orientation uncertain — check the image is upright"` |
| `save(folder)` | Writes the images and the form JSON, named `<prefix>_<scan id>_<name>`, so every scan's files are separate. Returns the paths |
| `delete_files()` | Deletes the saved files |
| `erase()` | Deletes the saved files and clears `images`, `fields` and `notes`. `end_transaction()` calls this for every scan in the transaction |
| `to_dict()` | JSON-ready summary, without the images |

### Offline (image files instead of the camera)

```python
scanner.read_drivers_license("front.jpg", "back.jpg")
scanner.read_insurance_card("card.jpg")
scanner.read_registration_renewal("notice.jpg")
```

These run the same pipeline on photos or numpy arrays, with no camera and no window. They're useful for testing without the kiosk.

### Settings

Pass any field of `ScannerConfig` as a keyword argument: `KioskScanner(camera_index=0, fullscreen=True, output_dir="scans")`.

| Setting | Default | Meaning |
|---|---|---|
| `camera_index` | `1` | OpenCV camera index |
| `focus` | `590` | Manual lens focus for the current mount height |
| `company_name` | `"Texas Tag & Title"` | Title on the demo home screen |
| `fullscreen` | `False` | Full-screen scan window, for the kiosk |
| `window_size` | `(1024, 768)` | Window size when not full screen |
| `keep_window_open` | `False` | `False` closes the window after each scan, so your app comes back to the front |
| `output_dir` | `None` | If set, every confirmed scan is saved there automatically |
| `tesseract_cmd` | `C:\Program Files\Tesseract-OCR\tesseract.exe` | Falls back to `tesseract` on PATH |
| `diagnostics` | `False` | Shows sharpness and focus readouts and the `[` `]` focus keys. For setup only |
| `debug_dir` | `None` | If set, the OCR engines save their intermediate images here |

---

## Using it from another language: command line + JSON

```
py -m kiosk_scanners scan license       [--fullscreen] [--output DIR] [--camera N]
py -m kiosk_scanners scan insurance
py -m kiosk_scanners scan registration
py -m kiosk_scanners read license FRONT.jpg BACK.jpg      (no camera)
py -m kiosk_scanners read insurance CARD.jpg
py -m kiosk_scanners read registration NOTICE.jpg
```

The JSON result is printed to stdout. Logs go to stderr; add `-v` for more detail.

| Exit code | Meaning | stdout |
|---|---|---|
| 0 | Scanned | `{"document", "fields", "missing", "notes", "files"}` |
| 1 | Error | `{"error": "..."}` |
| 2 | Customer cancelled | `{"cancelled": true}` |

Each call opens the camera again, which adds about 1 second. For back-to-back scans, the Python API is faster.

With `--output`, the files listed under `"files"` stay on disk after the command exits. The calling program must delete them when the customer's transaction is completed or cancelled.

---

## Fields returned

### Driver's license (`drivers_license`)
`customer_first_name`, `customer_last_name`, `street_address`, `city`, `state`, `zip_code`

### Insurance card (`insurance_card`)
`insurance_company`, `policy_number`, `insurance_expiration` (MM/DD/YYYY), `vehicle_year`, `vehicle_make`, `vehicle_model`, `vehicle_vin_last6`

### Registration renewal notice (`registration_renewal`)
- **Vehicle:** `license_plate`, `plate_type`, `vehicle_vin`, `vehicle_vin_last6`, `vehicle_year`, `vehicle_make`, `vehicle_make_code`, `vehicle_body_style`, `vehicle_class`, `fuel_type`, `empty_weight`, `carrying_capacity`, `gross_weight`, `current_expiration`, `new_expiration` (e.g. `SEP 2027`)
- **Fees:** `registration_fee`, `local_fee`, `total_fee`, `total_fee_mailed` (e.g. `85.50`)
- **Owner:** `owner_names` (co-owners joined with `" / "`), `owner_street`, `owner_city`, `owner_state`, `owner_zip`
- **Renewal recipient:** `recipient_name`, `recipient_street`, `recipient_city`, `recipient_state`, `recipient_zip`

Newer notices often don't print `registration_fee`, `local_fee`, `total_fee_mailed` or `plate_type`. Those fields can come back empty even when the scan is good.

---

## Keys in the scan window

| Key | Action |
|---|---|
| Space | Capture |
| Enter | Confirm the review |
| R | Retake |
| Esc / Q / window close | Cancel. The scan call returns `None` |
| F | Refocus the lens, if the preview looks blurry |
| `[` `]` | Adjust focus. Only active with `diagnostics=True` |

## Package layout

| File | Contents |
|---|---|
| `scanner.py` | `KioskScanner`, the public API |
| `drivers_license.py` | Card detection, PDF417 decoding, AAMVA parsing, and the front/back flow |
| `documents.py` | Shared page detection, orientation detection (OSD), and the capture flow for paper documents |
| `insurance.py`, `registration.py` | Per-document settings and the field layout on the review screen |
| `insurance_ocr.py`, `registration_ocr.py` | The OCR engines |
| `ui.py` | Theme, fonts and screen components. The colors are the constants at the top of the file |
| `camera.py` | The USB camera, with manual focus |
| `demo.py`, `__main__.py` | Demo home screen and command line |
