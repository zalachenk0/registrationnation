#!/usr/bin/env bash
# ============================================================================
# File:         start-pi_BV.sh
# Written by:   BV (Blas Vargas)
# What it does: Starts the kiosk server with gunicorn.
# Team-written: Logic written by BV (Blas Vargas). Outside code is used only
#               through the third-party sources listed below.
# Third-party:  gunicorn.
# ============================================================================
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [[ ! -f dist/index.html || ! -x .venv/bin/gunicorn ]]; then
  echo 'Missing built UI or Python environment. Follow START_HERE.md.' >&2
  exit 1
fi
export KIOSK_CAMERA_BACKEND="${KIOSK_CAMERA_BACKEND:-opencv}"
export OMP_THREAD_LIMIT=1
exec .venv/bin/gunicorn --workers 1 --threads 4 --timeout 270 --bind 127.0.0.1:5000 scanner_api_BV_CG:app
