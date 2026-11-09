# ============================================================================
# File:         conftest.py
# Written by:   CG (Cristian Gonzalez)
# What it does: Test setup shared by every test: signs the tests in and keeps
#               logs out of the real folders.
# Team-written: Logic written by CG (Cristian Gonzalez). Outside code is used
#               only through the third-party sources listed below.
# Third-party:  pytest; Stripe, Monday.com and CUPS are replaced by stand-ins in
#               these tests.
# ============================================================================
"""Test setup shared by every test file.

- The kiosk now needs a signed-in session and a kiosk header on every /api/ call (kiosk_auth_BV.py), so the tests
  act as a signed-in operator. Without this every route test fails with 403.
- The office problem log goes to a throwaway file, never to the kiosk's real runtime/kiosk-problems.log.
- Back-of-license photos from scans in any test go to a throwaway folder, never to the kiosk's real runtime/license_back.
- Folders the app creates under runtime/ while the tests run are removed at the end, but only if runtime/
  did not exist before, so a real kiosk's data is never touched.
"""
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUNTIME_EXISTED = (ROOT / 'runtime').exists()


def pytest_sessionfinish(session, exitstatus):
    if not RUNTIME_EXISTED:
        shutil.rmtree(ROOT / 'runtime', ignore_errors=True)


@pytest.fixture(autouse=True)
def problem_log(tmp_path, monkeypatch):
    path = tmp_path / 'kiosk-problems.log'
    monkeypatch.setenv('KIOSK_PROBLEM_LOG', str(path))
    monkeypatch.setenv('KIOSK_LICENSE_BACK_PHOTO_DIR', str(tmp_path / 'license_back'))  # a successful license scan now keeps its picture
    return path


@pytest.fixture(autouse=True)
def operator_session(monkeypatch, problem_log):
    import scanner_api_BV_CG as scanner_api
    monkeypatch.setattr(scanner_api, 'PROBLEM_LOG', problem_log, raising=False)  # only exists in newer versions of the kiosk
    monkeypatch.setattr(scanner_api.auth, 'authorize', lambda token: {'username': 'test-operator', 'role': 'admin', 'mode': 'master'})
    original = scanner_api.app.test_client

    def signed_in_client(*args, **kwargs):
        client = original(*args, **kwargs)
        client.environ_base['HTTP_X_KIOSK_REQUEST'] = '1'
        return client

    monkeypatch.setattr(scanner_api.app, 'test_client', signed_in_client)
