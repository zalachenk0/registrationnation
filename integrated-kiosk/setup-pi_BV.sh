#!/usr/bin/env bash
# ============================================================================
# File:         setup-pi_BV.sh
# Written by:   BV (Blas Vargas)
# What it does: Installs the system and Python packages for the kiosk Pi.
# Team-written: Logic written by BV (Blas Vargas). Outside code is used only
#               through the third-party sources listed below.
# Third-party:  apt packages (Picamera2, OpenCV, Tesseract, CUPS, Chromium); pip
#               packages in requirements-pi_BV.txt.
# ============================================================================
set -euo pipefail
cd -- "$(dirname -- "$0")"
echo 'Installing runtime packages for Raspberry Pi OS (64-bit)...'
sudo apt update
sudo apt install -y python3-venv python3-picamera2 python3-opencv python3-numpy python3-pil \
  tesseract-ocr tesseract-ocr-eng tesseract-ocr-osd rpicam-apps v4l-utils chromium cups cups-client python3-cups
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-pi_BV.txt
sudo systemctl enable --now cups
echo 'Packages installed. Now check the camera and printer using START_HERE.md.'
echo 'Start the kiosk with: bash start-pi_BV.sh'
