#!/usr/bin/env bash
# ============================================================================
# File:         check-pi_BV.sh
# Written by:   BV (Blas Vargas)
# What it does: Prints camera, OCR, printer and payment checks for the kiosk Pi.
# Team-written: Logic written by BV (Blas Vargas). Outside code is used only
#               through the third-party sources listed below.
# Third-party:  Raspberry Pi tools (rpicam-hello), v4l-utils, Tesseract and CUPS
#               command line tools.
# ============================================================================
set -u
cd -- "$(dirname -- "$0")"
echo 'Operating system:'
cat /etc/os-release
uname -m
echo 'Detected cameras (close the kiosk first):'
rpicam-hello --list-cameras
echo 'USB cameras (the scanner box uses the 16MP USB camera; set KIOSK_CAMERA_INDEX):'
v4l2-ctl --list-devices
echo 'OCR languages:'
tesseract --list-langs
echo 'Printer queues:'
lpstat -p -d
echo 'Python imports:'
.venv/bin/python -c 'import picamera2, cv2, numpy, pytesseract, zxingcpp, flask, reportlab, pypdf, requests, stripe, dotenv; print("Runtime imports OK")'
echo 'Payment configuration (does not contact Stripe or expose the secret):'
.venv/bin/python -c 'from pathlib import Path; from dotenv import load_dotenv; load_dotenv(Path(".env")); from payment_service_CG import configuration; c = configuration(); print("Stripe test card entry; amount:", c["amount"] / 100)'
