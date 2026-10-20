#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [[ ! -f dist/index.html || ! -x .venv/bin/gunicorn ]]; then
  echo 'Missing built UI or Python environment. Follow START_HERE.md.' >&2
  exit 1
fi
export KIOSK_CAMERA_BACKEND="${KIOSK_CAMERA_BACKEND:-picamera2}"
export OMP_THREAD_LIMIT=1
exec .venv/bin/gunicorn --workers 1 --threads 4 --timeout 150 --bind 127.0.0.1:5000 scanner_api:app
