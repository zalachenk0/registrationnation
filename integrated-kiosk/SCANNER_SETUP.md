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

## 3. Run and test

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

## Important scope notes

- Driver licenses are read from the PDF417 barcode on the back.
- The supplied insurance parser is tuned for Texas liability-card layouts and may
  need new rules for other carriers or states.
- Captured document images are temporary and are deleted after each request.
- Do not expose port 5000 directly to the public internet; keep it on the kiosk or
  a trusted local network.
