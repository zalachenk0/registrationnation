# Complete Pi replacement — October 9, 2026

This is the complete application, including the rebuilt `dist/` UI. The ZIP opens
to one `integrated-kiosk-pi` folder. Use Raspberry Pi OS 64-bit with the desktop,
as before. No OS reinstall, Node.js, npm, pnpm, or Stripe CLI is needed to run this package
(the S700 card reader and its location ID are optional, see S700_AND_MONDAY.md).
The Python Stripe library is installed below.

## Before replacing the old folder

Close Chromium and stop the running backend with Ctrl+C in its terminal. If you
configured automatic startup, stop that service first. Keep a backup of the old
folder. In particular, preserve its hidden `.env` file and the entire `runtime/`
folder, including `payments.sqlite3`, so existing settings, payment references,
and saved scans are retained. Do not copy a Windows `.venv` or `node_modules`.

For manual replacement: name the new folder `integrated-kiosk-pi`, place it
directly in your home directory, then copy the old `.env` and `runtime/` into it.
Do not nest one `integrated-kiosk-pi` folder inside another.

Alternatively, if you have NOT replaced it yet, put the ZIP in Downloads and use:

```bash
cd ~
kiosk_backup="$HOME/integrated-kiosk-pi-backup-$(date +%Y%m%d-%H%M%S)"
mv "$HOME/integrated-kiosk-pi" "$kiosk_backup" && \
unzip "$HOME/Downloads/registrationnation-pi-updated.zip" -d "$HOME" && \
{ if [ -f "$kiosk_backup/.env" ]; then cp "$kiosk_backup/.env" "$HOME/integrated-kiosk-pi/.env"; fi; } && \
{ if [ -d "$kiosk_backup/runtime" ]; then cp -a "$kiosk_backup/runtime" "$HOME/integrated-kiosk-pi/"; fi; }
```

If `unzip` is missing, run `sudo apt install unzip` and retry only the extraction
and copy steps; your old project is safe in the backup folder.

## After replacing: install the runtime

Run in a terminal on the Pi. This checks/installs the camera, OCR, printing,
Chromium, and Python packages; already installed apt packages are reused.
The new folder should start without a `.venv`; the script creates one on the Pi.

```bash
cd "$HOME/integrated-kiosk-pi"
bash setup-pi.sh
test -f .env || cp .env.example .env
chmod 600 .env
nano .env
```

In `.env`, keep your camera and printer settings. Keep or add these two values,
using your actual test keys from the SAME Stripe account/test environment:

```dotenv
STRIPE_SECRET_KEY=sk_test_YOUR_ACTUAL_SECRET_KEY
STRIPE_PUBLISHABLE_KEY=pk_test_YOUR_ACTUAL_PUBLISHABLE_KEY
```

Save in nano: Ctrl+O, Enter, Ctrl+X. The publishable key is NEW for this form;
the old secret key can stay. Do not put either key in React source or send your
secret key to anyone. `STRIPE_LOCATION_ID` and `STRIPE_SIMULATED` control the S700 card
reader (primary method; see S700_AND_MONDAY.md). Leave both blank to use only the typed card.

Existing full-resolution/autofocus settings are retained by copying `.env`.
If they are missing and you want the prior IMX708 configuration, add:

```dotenv
KIOSK_CAMERA_WIDTH=4608
KIOSK_CAMERA_HEIGHT=2592
KIOSK_CAMERA_AUTOFOCUS=1
KIOSK_CAMERA_LENS_POSITION=
```

Do not overwrite other working settings. These settings cannot fit paper that
is physically outside the camera's field of view; raise the camera if needed.

## Check and start the backend

With the backend still stopped, check camera, imports, OCR, printer, and keys:

```bash
cd "$HOME/integrated-kiosk-pi"
bash check-pi.sh
```

`check-pi.sh` checks local key format, not internet access or Stripe account
validity. Keep your existing configured CUPS printer. Check it with
`lpstat -p -d`; a CUPS printer queue must exist before completing a renewal.
If choosing among existing queues, use `lpoptions -d YOUR_EXISTING_QUEUE_NAME`.

Start the backend in this terminal and leave it open:

```bash
cd "$HOME/integrated-kiosk-pi"
bash start-pi.sh
```

Open a SECOND terminal and start Chromium:

```bash
if command -v chromium >/dev/null 2>&1; then
  chromium --kiosk --noerrdialogs http://127.0.0.1:5000
else
  chromium-browser --kiosk --noerrdialogs http://127.0.0.1:5000
fi
```

Use Alt+F4 to close Chromium. If you see the old version, fully close Chromium,
restart the backend from this folder, and reopen it. The updated total is $114.25.

## Every later startup

Terminal 1:

```bash
cd "$HOME/integrated-kiosk-pi"
bash start-pi.sh
```

Terminal 2: run the Chromium block above. Setup and editing `.env` are one-time
steps unless dependencies or keys change.

## Payment and reset behavior

This build is test-only and rejects live Stripe keys. Type a Stripe test card
into the secure fields: `4242 4242 4242 4242`, a future expiration, any three-digit
CVC, and any requested test billing details. When the S700 is configured it is shown first and
the typed card is the backup ("Reader not working? Enter the card manually instead").
The card fields are not filled automatically by this app. Stripe-hosted fields
and the backend both need internet access. If school Wi-Fi times out, use a
working hotspot/network; replacing code cannot remove a network block.

Network-only checks (no keys or customer data):

```bash
curl -I --connect-timeout 10 --max-time 20 https://api.stripe.com
curl -I --connect-timeout 10 --max-time 20 https://js.stripe.com/v3/
```

Any HTTP response proves that host responded; a non-200 response from the API
root does not by itself indicate a payment problem. A timeout means it did not
respond in time.

The 20-second countdown starts after payment succeeds AND CUPS accepts the
sticker/receipt job. It is not a physical paper-exit sensor. “Wait, I'm still
here” pauses indefinitely; “Resume 20-second countdown” starts a fresh 20 seconds.
Automatic reset clears customer fields and the payment form but keeps the staff
session signed in. Saved insurance debug files are retained.

If payment succeeds but printing fails, use “Retry Printing”; do not start a
second payment. A payment timeout should be retried in the same customer session
so the existing reference is reused. Preserve `runtime/payments.sqlite3`.
Finish any payment started in the old version before switching versions.

## Scanner files and saved images

The Pi adapter is `extraction_service.py` in the project root. Updated teammate
parsers are in `kiosk_scanners/`. The license scanner reads the PDF417 barcode
on the BACK of the license. Keep it horizontal, fully visible, and in focus.
Insurance uses OCR; check and correct extracted fields before paying. Coverage
start date and policy type can now be left blank.

Insurance originals and OCR output are saved by default. Open:

```bash
xdg-open "$HOME/integrated-kiosk-pi/runtime/insurance_debug"
```

Each scan has a UTC timestamped folder with `00_original.png`, and, when OCR
completes, `01_best_ocr.png`, `best_raw_text.txt`, and `parsed_fields.json`.
Set `KIOSK_INSURANCE_SAVE_DEBUG=0` in `.env` only if you want to disable saving.

## Verification and limits

43 Python tests and 13 JavaScript/React tests passed; TypeScript and the production
UI build passed. Browser checks used synthetic documents and simulated Stripe
and printer responses, including payment success followed by print failure and
retry, optional fields, and pausing/resuming/resetting the customer session.
No real Stripe account, Pi camera, or physical printer was available here.
Extraction speed/accuracy still needs validation with your camera and documents.
Printed stickers remain demonstration mockups; no official registration is issued.

Stripe references: [Payment Element integration](https://docs.stripe.com/payments/accept-a-payment-deferred?type=payment)
and [test card instructions](https://docs.stripe.com/testing).
