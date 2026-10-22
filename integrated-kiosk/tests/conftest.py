"""Every test writes the office problem log to a throwaway file, never to the kiosk's real runtime/kiosk-problems.log."""
import pytest

import scanner_api


@pytest.fixture(autouse=True)
def problem_log(tmp_path, monkeypatch):
    path = tmp_path / 'kiosk-problems.log'
    monkeypatch.setattr(scanner_api, 'PROBLEM_LOG', path)
    return path
