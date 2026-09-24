# Raspberry Pi 4 + directly connected Arducam scanner

This project is one kiosk application: the React interface is built into `dist/`, and
`scanner_api.py` serves both that interface and the camera/scanning API on port 5000.
The camera is controlled by the Raspberry Pi, so Chromium does not need camera
permission.

## What the scan buttons do

1. The user puts a document under the Arducam and presses **Capture & Scan**.
2. The UI sends `POST /api/camera/scan/license` or
   `POST /api/camera/scan/insurance`.
3. `camera_capture.py` runs `rpicam-still` and saves one high-quality temporary JPEG.
4. A license image goes to the PDF417/AAMVA parser. An insurance image goes through
   OpenCV preprocessing and Tesseract OCR.
5. The API returns JSON, the UI fills the matching form fields, and the temporary
   image is deleted immediately.
6. Every extracted value remains editable. **Upload an image instead** is retained
   for testing and recovery.

## What the Pay & Print button does

1. The customer selects **Pay $71.00 & Print** on the Payment step.
2. The UI connects to a Stripe Terminal card reader (a simulated one until the
   physical S700 arrives - see `src/payment.ts`) and calls
   `POST /api/payment/create_intent`, then has the reader collect the card, then
   calls `POST /api/payment/capture_intent` once the card is confirmed. This is a
   real Stripe charge, run in Stripe's test mode.
3. Only once the payment succeeds does the UI call `POST /api/print/registration`,
   same as before.
4. After a successful print, the UI calls `POST /api/monday/create_record` to log
   the renewal to the company's Monday.com board (optional - see setup below).
5. If printing fails after a successful charge, retrying reuses that same charge
   instead of charging the card again (`pendingPaymentIntentId` in `App.tsx`).

## 1. Confirm the CSI Arducam works

Shut the Pi down before connecting or changing the ribbon cable. On current
Raspberry Pi OS (Bookworm), first test:

```bash
rpicam-hello --list-cameras
rpicam-still -o arducam-test.jpg --autofocus-mode auto
```

If the camera has fixed focus and the autofocus option causes an error, use:

```bash
export KIOSK_CAMERA_AUTOFOCUS=0
```

Older Raspberry Pi OS releases use `libcamera-still`; the application detects that
name too. Some Arducam models require an Arducam-supplied device-tree overlay or
driver. Apply the instructions for the exact sensor model until it appears in
`--list-cameras`; application software cannot compensate for a camera that the OS
has not detected.

## 2. Install system packages

From the `integrated-kiosk` folder on the Pi:

```bash
sudo apt update
sudo apt install -y nodejs npm python3-venv python3-opencv tesseract-ocr \
  libzbar0 chromium cups-client
```

Create the Python environment. Using system site packages lets the environment use
the Raspberry Pi OS OpenCV build:

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-scanner.txt
```

Install and build the interface:

```bash
sudo npm install -g pnpm
pnpm install --frozen-lockfile
pnpm build
```

`pnpm install` pulls in `@stripe/terminal-js` automatically - it's listed in
`package.json`, no separate install step needed.

## 3. Configure payment (Stripe) and Monday.com

```bash
cp .env.example .env
```

Open `.env` and fill in:

- **`STRIPE_SECRET_KEY`** (required to take payments) - a free test-mode key from
  https://dashboard.stripe.com/test/apikeys (starts with `sk_test_`). Test mode
  needs no business verification or bank account.
- **`MONDAY_API_TOKEN`** and **`MONDAY_BOARD_ID`** (optional - only needed for the
  Monday.com logging step) - get these from whoever manages the board: the token
  from Monday.com's Admin -> API page, and the board ID from the number in the
  board's URL.

Then open `monday_service.py` and replace the placeholder values in
`MONDAY_COLUMN_MAP` with the board's real column IDs (Monday identifies columns by
an internal ID like `text` or `status4`, not the column's display name - find these
in the board's column settings). Until this is done, `/api/monday/create_record`
returns a clear "not configured yet" error instead of failing silently.

The reader itself needs no setup for this demo - `src/payment.ts` uses Stripe's
built-in reader simulator (`discoverReaders({ simulated: true })`), so payments
work with no physical hardware attached. When the physical S700 arrives, that's
the one line in `src/payment.ts` that changes (swap it for a real Stripe Location
ID) - everything else, including every route in `scanner_api.py`, stays the same.

## 4. Run and test

```bash
source .venv/bin/activate
KIOSK_CAMERA_BACKEND=rpicam gunicorn --workers 1 --threads 4 \
  --bind 0.0.0.0:5000 scanner_api:app
```

Use one worker because a physical camera is a single shared device. Open
`http://127.0.0.1:5000` on the Pi. The status endpoint is
`http://127.0.0.1:5000/api/health`; it should show `"backend":"rpicam"`.

Configure and test a CUPS printer before starting the kiosk:

```bash
lpstat -p -d
echo "Kiosk printer test" | lp
```

The application uses the system default printer. To select a named printer,
add `KIOSK_PRINTER_NAME="your-printer-name"` before the Gunicorn command.
Completing payment sends a clearly labeled mock registration sticker/receipt to
that printer. Payment card details are never included in the print request.

For a full-screen kiosk session:

```bash
chromium --kiosk --noerrdialogs --disable-infobars http://127.0.0.1:5000
```

## Camera adjustments

These optional environment variables are read when the service starts:

| Variable | Default | Purpose |
| --- | --- | --- |
| `KIOSK_CAMERA_BACKEND` | `auto` | Use `rpicam` for current Raspberry Pi OS |
| `KIOSK_CAMERA_WIDTH` | `2304` | Capture width |
| `KIOSK_CAMERA_HEIGHT` | `1296` | Capture height |
| `KIOSK_CAMERA_WARMUP_MS` | `1200` | Exposure/focus settling time |
| `KIOSK_CAMERA_AUTOFOCUS` | `1` | Set to `0` for a fixed-focus module |

For barcode reliability, mount the camera square to the document, avoid glare, and
make the PDF417 barcode occupy a large part of the frame. Insurance OCR likewise
needs even light and sharp small text. A fixed document tray and diffuse LEDs make
a larger difference than increasing resolution indefinitely.

## Development mode

During UI development, run the Python service on port 5000 and `pnpm dev` in a
second terminal. Vite proxies `/api` to Python. For the deployed kiosk, use the
single-server build described above.

## Windows quick start (Start-Kiosk)

On a Windows machine (not the Pi), double-click `Start-Kiosk.cmd`. It now checks
for and launches both halves of the app if they aren't already running: the Flask
API (`scanner_api.py`, preferring a `.venv` in the project folder if one exists,
otherwise whatever `python`/`py` is on PATH) and the Vite dev server, then opens
Chrome to the kiosk URL. `.env` still needs to be filled in first (see step 3
above) - the script doesn't create it for you. Logs land in `kiosk-api.log` /
`kiosk-api-error.log` and `kiosk-ui.log` / `kiosk-ui-error.log` in the project
folder if something doesn't start.

## Important scope notes

- Driver licenses are read from the PDF417 barcode on the back.
- The supplied insurance parser is tuned for Texas liability-card layouts and may
  need new rules for other carriers or states.
- Captured document images are temporary and are deleted after each request.
- Payment runs against Stripe's test mode and a simulated card reader - no real
  card or bank account is involved until a physical S700 is connected and
  `src/payment.ts` is pointed at a real Stripe Location. Chrome 142+ requires an
  explicit local-network permission for the Terminal SDK to reach a physical
  reader; not relevant for the simulated reader used here.
- Do not expose port 5000 directly to the public internet; keep it on the kiosk or
  a trusted local network.
