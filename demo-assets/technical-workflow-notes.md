# Registration Nation: technical workflow

These diagrams describe the current implementation. Read each sequence from top to bottom. Solid arrows are requests or function calls; dashed arrows (or the combined "→" notation on one arrow) are returns. Flask, the camera adapter, and the document processors are local backend components, not separately deployed network services — Stripe's own servers are the only genuinely external system involved.

## Runtime architecture

The React/TypeScript application runs in the kiosk browser. In the integrated deployment, Flask serves the built `dist/` assets and the API on port 5000 by default. During development, Vite proxies `/api` requests to Flask. The Raspberry Pi controls camera capture on the server side, so the normal Capture & Scan path does not use the browser camera API.

Staff sign-in is a frontend credential comparison that sets React state. It does not establish a backend-authenticated session. Customer details and the current step live in React state. Payment is different: the Payment step runs an actual Stripe Terminal charge (test mode) through `/api/payment/*` routes backed by the Stripe Python SDK. This implementation still has no registration database write or DMV integration.

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

## Slide 2: payment (Stripe Terminal)

### 1. The Payment step connects to a reader

Mounting Step 5 calls `connectToReader()` in `src/payment.ts`, which creates a Stripe Terminal SDK instance (`@stripe/terminal-js`) and supplies `onFetchConnectionToken`, backed by `POST /api/payment/connection_token`. That route calls `stripe.terminal.ConnectionToken.create()` server-side — the Stripe secret key never reaches the browser.

The SDK then calls `discoverReaders({ simulated: true })` and `connectReader(...)`. This is Stripe's own reader simulator, so the flow works with no physical hardware. The UI shows connecting, then ready. Swapping to a real S700 reader is a one-line change (`discoverReaders({ location: '<Stripe Location ID>' })`) — nothing else in `payment.ts` or `scanner_api.py` changes for that swap.

### 2. Selecting "Pay & Print" opens a charge

`handlePaymentAndPrint()` in `App.tsx` calls `chargeCard(RENEWAL_FEE_CENTS)`. This posts to `POST /api/payment/create_intent`, which calls `stripe.PaymentIntent.create()` with `capture_method: 'manual'` — the card is authorized but no money moves yet. The route returns a `client_secret` used by the next step.

### 3. The reader collects and confirms the card

`collectPaymentMethod(client_secret)` and `processPayment(...)` run against the (simulated, for now) reader. In test mode, `payment.ts` also calls `setSimulatorConfiguration({ testCardNumber: '4242424242424242' })` immediately beforehand — this call does not exist for a real reader and is removed when real hardware is used.

### 4. Flask captures the payment

`POST /api/payment/capture_intent` calls `stripe.PaymentIntent.capture()` — this is the moment funds actually move. A `status: 'succeeded'` response is required before the frontend proceeds; any other outcome returns `{ success: false, error }` from `chargeCard()`, and nothing is printed.

### 5. No double-charging on a print retry

`App.tsx` stores the completed `paymentIntentId` in `pendingPaymentIntentId` once a charge succeeds. If printing (Slide 3) then fails, a retry reuses that same ID instead of calling `chargeCard()` again — the customer is not charged twice for one renewal. This value clears only once printing succeeds.

## Slide 3: review, print submission and completion

### 1. Review updates local state

The Review screen edits the same parent React state used by the earlier form steps. Continuing between steps does not save a registration to a server. Required-field markers are present, but the navigation handlers do not enforce complete form validation.

### 2. The final action assembles a print request

Once `chargeCard()` (Slide 2) succeeds, `handlePaymentAndPrint()` generates a `REG-` confirmation string using `Math.random()`, sets the payment stage to `printing`, clears any previous error, and calls `printRegistration(payload)`.

The JSON payload includes `registrant`, `license_plate`, `vehicle_year`, `vehicle_make`, `vehicle_model`, `vin`, `amount` and `confirmation_number`. The amount is the fixed value `$71.00`. Card number and other payment details are never included in this request — printing only knows that payment already succeeded, not how.

The React button disables itself while either the charge or the print request is pending. This prevents another click during that window, but it is not server-side duplicate-job protection or idempotency beyond the `pendingPaymentIntentId` reuse described in Slide 2.

### 3. Flask validates minimum print fields

`POST /api/print/registration` checks for non-empty registrant, license plate and confirmation number. Missing values return HTTP 400. This validation is narrower than the full set of fields shown as required in the UI.

### 4. The print service creates and submits the mockup

`print_registration_mockup(payload)` builds a text sticker / receipt labeled as a demonstration, writes a temporary text file and executes the CUPS `lp` command. If `KIOSK_PRINTER_NAME` is configured, the command includes `-d` for that printer; otherwise it uses the system default. The command has a 20-second timeout.

The service captures the command output and removes the temporary print file in `finally`. A missing `lp` command, command failure or timeout raises `PrinterError`, which the API maps to HTTP 503.

### 5. A successful response completes the UI flow

When the print command succeeds, Flask returns `success: true`, a message and the job output. React stores the confirmation number and sets `submitted = true`, showing the completion screen. The payment stage resets to idle in the frontend's `finally` block.

This verifies print-job submission only. The application does not monitor printer paper movement or physical completion. The UI's "Payment verified" wording is now accurate — payment is real — but print completion itself is still only submission-verified, not physically confirmed.

### 6. Error and reset paths

On a charge failure, React never reaches the print step — the error shows immediately on Payment. On a print failure (after a successful charge), React sets `printError`, keeps the Payment screen visible, and re-enables the button; a retry reprints using the already-completed charge (see Slide 2, step 5) rather than billing again.

Start Next Customer clears personal, license, insurance and payment state, resets confirmation, error, and the pending-charge tracking, and returns to step 1. It retains the frontend operator sign-in state.

## Supporting health endpoint

`GET /api/health` returns `status: ok`, camera configuration information and printer status. Camera information describes the selected backend and settings; printer availability checks whether `lp` exists. This endpoint is not proof of a successful camera capture or physical printer readiness, and it is not a gate in the customer flow. It does not report Stripe configuration status.

## Short technical narration

"The kiosk browser runs a React application backed by a local Flask API. A scan request either captures a temporary image through the Raspberry Pi camera adapter or accepts an uploaded image. License images use PDF417 decoding and AAMVA parsing, while insurance documents use OpenCV preprocessing and Tesseract OCR. Flask normalizes the extracted fields and returns JSON, which React merges into editable form state. After review, the customer's card is charged through a real Stripe Terminal flow — a simulated reader for this demo, a physical S700 reader with a one-line change — using manual capture, so funds only move after the reader confirms the card. Only once that charge succeeds does the app send registration data to the print endpoint, which checks the minimum print fields, builds a labeled mockup and submits it through CUPS. A successful submission triggers the confirmation screen; failures remain recoverable in the UI, and a print retry after a successful charge never re-bills the card."

## Implementation references

- `integrated-kiosk/src/App.tsx`: frontend scan helpers, field mapping, review, payment/print orchestration, and state transitions.
- `integrated-kiosk/src/payment.ts`: Stripe Terminal SDK connection and charge flow.
- `integrated-kiosk/scanner_api.py`: Flask routes, normalization, temporary images and status codes (scan, print, and payment routes).
- `integrated-kiosk/payment_service.py`: Stripe Python SDK calls (connection tokens, PaymentIntent create/capture).
- `integrated-kiosk/camera_capture.py`: capture locking, backend selection and camera commands.
- `integrated-kiosk/driver_license_barcode_rev4.py`: PDF417 scanning and AAMVA extraction.
- `integrated-kiosk/insurance_card_ocr_rev3.py`: OCR variants, field scoring and success threshold.
- `integrated-kiosk/printer_service.py`: mockup creation and CUPS submission.
- `integrated-kiosk/vite.config.ts`: development API proxy.

Monday.com record-keeping (logging each completed renewal to a company board) is
built but not wired into this version of the app — see
`saved-for-later/monday-integration/` for that code, ready to reconnect for a
future demo.
