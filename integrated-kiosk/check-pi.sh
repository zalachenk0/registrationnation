#!/usr/bin/env bash
set -u
cd -- "$(dirname -- "$0")"
echo 'Operating system:'
cat /etc/os-release
uname -m
echo 'Detected cameras (close the kiosk first):'
rpicam-hello --list-cameras
echo 'OCR languages:'
tesseract --list-langs
echo 'Printer queues:'
lpstat -p -d
echo 'Python imports:'
.venv/bin/python -c 'import picamera2, cv2, numpy, pytesseract, zxingcpp, flask, reportlab, pypdf, requests, stripe, dotenv; print("Runtime imports OK")'
echo 'Payment configuration (does not contact Stripe or expose the secret):'
.venv/bin/python -c 'from pathlib import Path; from dotenv import load_dotenv; load_dotenv(Path(".env")); from payment_service import configuration; c = configuration(); print("Stripe test card entry; amount:", c["amount"] / 100)'
