# Registration Nation: technical workflow

These diagrams describe the current implementation. Read each sequence from top to bottom. Solid arrows are requests or function calls; dashed arrows are returns. Flask, the camera adapter and the document processors are local backend components, not separately deployed network services.

## Runtime architecture

The React/TypeScript application runs in the kiosk browser. In the integrated deployment, Flask serves the built `dist/` assets and the API on port 5000 by default. During development, Vite proxies `/api` requests to Flask. The Raspberry Pi controls camera capture on the server side, so the normal Capture & Scan path does not use the browser camera API.

Staff sign-in is a frontend credential comparison that sets React state. It does not establish a backend-authenticated session. Customer details, payment fields and the current step also live in React state. This implementation has no registration database write or DMV integration.

## Slide 1: document capture and extraction

### 1. React starts a scan

`ScanPrompt` moves from `idle` to `scanning`. Camera capture calls `scanPiCamera(kind)`, which sends `POST /api/camera/scan/license` or `POST /api/camera/scan/insurance` without uploading an image.

The upload alternative calls `scanDocument(kind, file)`. It creates a multipart request whose file field is `image`, then posts to `/api/scan/license` or `/api/scan/insurance`. Both input methods converge on the same document processing functions.

### 2. Flask obtains a temporary image

For camera capture, Flask creates a temporary JPEG path and invokes `KioskCamera.capture(path)`. A threading lock serializes camera captures. Automatic backend selection prefers `rpicam-still`, then `libcamera-still`, then OpenCV for a USB camera. This selects an installed backend; it does not automatically retry the next backend after a capture failure.

The default capture settings are 2304 x 1296, a 1200 ms warmup and autofocus enabled. The Pi command uses JPEG quality 95 and a 20-second subprocess timeout. The adapter checks that the resulting file exists and is at least 1024 bytes.

For upload, Flask requires a named file, checks its filename extension and saves it to a temporary path. Flask's request limit is 15 MiB. The allowed extensions are JPG, JPEG, PNG, WEBP, BMP, TIF and TIFF.

### 3. The backend chooses a document processor

**Driver's license:** `process_license(path)` calls `DriverLicenseBarcodeScanner.scan(path)`. The scanner first attempts a direct barcode crop, then tries preprocessing variants if necessary. It uses zxing-cpp when installed, with pyzbar as the import-time fallback. After decoding PDF417, `AAMVAParser` extracts tagged license data. The API adds date of birth, license number, expiration and class, and normalizes date strings to ISO dates.

License `scan_success` means a barcode was decoded. It does not require every customer or license field to be present and does not establish the license's authenticity or validity.

**Insurance:** `process_insurance(path)` runs `InsuranceCardOCR.extract(path)` with debug-image saving disabled. OpenCV prepares image candidates and preprocessing variants. Tesseract reads each candidate using configured page segmentation modes. The extractor scores five target fields: vehicle year, make, model, VIN last six characters and insurance expiration. It retains the result with the highest field count and marks success when at least two of those fields are present.

The API separately searches the selected raw OCR text for a full 17-character VIN and normalizes insurance expiration. A successful insurance extraction can therefore still lack a complete VIN or other required form values.

### 4. Flask returns JSON and removes the image

The scan response contains `success`, `recommendation` and `fields`. The API route deletes its temporary image in a `finally` block after processing, including processing failures. This describes the normal post-save route cleanup; it is not a claim that the application never logs extracted data.

React checks both the HTTP status and the `success` flag. A non-OK response or `success: false` produces an error. On success, the component maps returned values into parent form state and switches to `done`. The code generally retains an existing field value when the corresponding extracted value is empty.

License scans update personal and license state. Insurance scans update vehicle year, make, model, VIN and insurance end date. Policy provider, policy number, policy holder, start date, coverage and contact information still require user input.

### 5. Scan error paths

| Condition | Backend / frontend behavior |
| --- | --- |
| Missing uploaded image | HTTP 400 |
| Unsupported upload extension | HTTP 415 |
| Request exceeds configured limit | Flask rejects with HTTP 413 |
| Unknown camera scan kind | HTTP 404 |
| Camera capture error | HTTP 503 with an error message |
| Unhandled processing exception | HTTP 500 with an error message |
| Processing returns insufficient extraction | HTTP 200 with `success: false`; frontend still treats it as failure |
| UI recovery | Error message appears; Try Again opens the image picker |

## Slide 2: review, print submission and completion

### 1. Review updates local state

The Review screen edits the same parent React state used by the earlier form steps. Continuing between steps does not save a registration to a server. Required-field markers are present, but the navigation handlers do not enforce complete form validation.

### 2. The final action assembles a print request

`handlePaymentAndPrint()` generates a `REG-` confirmation string using `Math.random()`, sets `printing = true`, clears any previous print error and calls `printRegistration(payload)`.

The JSON payload includes `registrant`, `license_plate`, `vehicle_year`, `vehicle_make`, `vehicle_model`, `vin`, `amount` and `confirmation_number`. The amount is the fixed demo value `$71.00`. Card number, CVV and other payment fields are not included in this request. There is no payment gateway call or card authorization.

The React button disables itself while the request is pending. This prevents another click during that pending request, but it is not server-side duplicate-job protection or idempotency.

### 3. Flask validates minimum print fields

`POST /api/print/registration` checks for non-empty registrant, license plate and confirmation number. Missing values return HTTP 400. This validation is narrower than the full set of fields shown as required in the UI.

### 4. The print service creates and submits the mockup

`print_registration_mockup(payload)` builds a text sticker / receipt labeled as a demonstration, writes a temporary text file and executes the CUPS `lp` command. If `KIOSK_PRINTER_NAME` is configured, the command includes `-d` for that printer; otherwise it uses the system default. The command has a 20-second timeout.

The service captures the command output and removes the temporary print file in `finally`. A missing `lp` command, command failure or timeout raises `PrinterError`, which the API maps to HTTP 503.

### 5. A successful response completes the UI flow

When the print command succeeds, Flask returns `success: true`, a message and the job output. React stores the confirmation number and sets `submitted = true`, showing the completion screen. `printing` resets to false in the frontend's `finally` block.

This verifies print-job submission only. The application does not monitor printer paper movement or physical completion. The UI's “Payment verified” wording is not backed by payment processing.

### 6. Error and reset paths

On an API or print failure, React sets `printError`, keeps the Payment screen visible and re-enables the button. A retry generates another confirmation string and submits another request.

Start Next Customer clears personal, license, insurance and payment state, resets confirmation and error state, and returns to step 1. It retains the frontend operator sign-in state.

## Supporting health endpoint

`GET /api/health` returns `status: ok`, camera configuration information and printer status. Camera information describes the selected backend and settings; printer availability checks whether `lp` exists. This endpoint is not proof of a successful camera capture or physical printer readiness, and it is not a gate in the customer flow.

## Short technical narration

“The kiosk browser runs a React application backed by a local Flask API. A scan request either captures a temporary image through the Raspberry Pi camera adapter or accepts an uploaded image. License images use PDF417 decoding and AAMVA parsing, while insurance documents use OpenCV preprocessing and Tesseract OCR. Flask normalizes the extracted fields and returns JSON, which React merges into editable form state. After review, the final demo action sends registration data to the print endpoint. The backend checks the minimum print fields, builds a labeled mockup and submits it through CUPS. A successful submission triggers the confirmation screen; failures remain recoverable in the UI. Payment processing and registration persistence are not connected in this version.”

## Implementation references

- `integrated-kiosk/src/App.tsx`: frontend scan helpers, field mapping, review, print payload and state transitions.
- `integrated-kiosk/scanner_api.py`: Flask routes, normalization, temporary images and status codes.
- `integrated-kiosk/camera_capture.py`: capture locking, backend selection and camera commands.
- `integrated-kiosk/driver_license_barcode_rev4.py`: PDF417 scanning and AAMVA extraction.
- `integrated-kiosk/insurance_card_ocr_rev3.py`: OCR variants, field scoring and success threshold.
- `integrated-kiosk/printer_service.py`: mockup creation and CUPS submission.
- `integrated-kiosk/vite.config.ts`: development API proxy.
