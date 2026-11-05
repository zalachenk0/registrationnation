# ============================================================================
# File:         test_usb_camera_and_notice_SL.py
# Written by:   SL (Seth Leija)
# What it does: Tests for the USB camera settings and the renewal notice scan.
# Team-written: Logic written by SL (Seth Leija). Outside code is used only
#               through the third-party sources listed below.
# Third-party:  OpenCV; NumPy; pytest
# ============================================================================
"""USB document camera settings and the optional renewal notice scan."""
from unittest.mock import patch

import cv2
import numpy as np
import pytest

import camera_service_BV_SL as camera_service
import extraction_service_BV_SL as extraction_service
from camera_service_BV_SL import Camera, Settings


class FakeCapture:
    """Records every property set, and returns frames (black first, like the real camera)."""

    def __init__(self, frames):
        self.sets, self.frames = [], list(frames)

    def isOpened(self):
        return True

    def set(self, prop, value):
        self.sets.append((prop, value))
        return True

    def get(self, prop):
        return 0

    def read(self):
        return True, self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]

    def release(self):
        pass


def test_defaults_are_the_scanner_box_usb_camera(monkeypatch):
    for name in ('BACKEND', 'INDEX', 'WIDTH', 'HEIGHT', 'ROTATION', 'FOCUS', 'EXPOSURE', 'GAIN'):
        monkeypatch.delenv('KIOSK_CAMERA_' + name, raising=False)
    s = Settings()
    assert (s.backend, s.width, s.height, s.rotation, s.focus, s.gain) == ('opencv', 4000, 3000, 180, 590, 0)
    monkeypatch.setenv('KIOSK_CAMERA_FOCUS', 'auto')
    monkeypatch.setenv('KIOSK_CAMERA_EXPOSURE', 'auto')
    monkeypatch.setenv('KIOSK_CAMERA_INDEX', '/dev/v4l/by-id/usb-camera-video-index0')
    s = Settings()
    assert s.focus is None and s.exposure is None and s.index == '/dev/v4l/by-id/usb-camera-video-index0'


def test_usb_open_sets_mjpg_size_manual_focus_and_exposure_then_refocuses(monkeypatch):
    black, page = np.zeros((30, 40, 3), np.uint8), np.full((30, 40, 3), 200, np.uint8)
    fake = FakeCapture([black, page, page, page])
    monkeypatch.setattr(camera_service.platform, 'system', lambda: 'Linux')
    with patch.object(camera_service.cv2, 'VideoCapture', return_value=fake) as opened:
        _, read, _ = Camera(Settings(backend='opencv', index=0, focus=590, exposure=78, gain=0))._open()
    assert opened.call_args.args == (0, cv2.CAP_V4L2)
    props = [p for p, _ in fake.sets]
    assert (cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG')) in fake.sets
    # Height before width; focus and exposure after the resolution
    assert props.index(cv2.CAP_PROP_FRAME_HEIGHT) < props.index(cv2.CAP_PROP_FRAME_WIDTH) < props.index(cv2.CAP_PROP_FOCUS)
    assert (cv2.CAP_PROP_AUTOFOCUS, 0) in fake.sets and fake.sets[-4:] == [
        (cv2.CAP_PROP_FOCUS, 590), (cv2.CAP_PROP_AUTO_EXPOSURE, 1), (cv2.CAP_PROP_EXPOSURE, 78), (cv2.CAP_PROP_GAIN, 0)]
    fake.sets.clear()
    assert read() is None                     # black start-up frame is skipped
    assert read() is not None and read() is not None
    assert [v for p, v in fake.sets if p == cv2.CAP_PROP_FOCUS] == [540, 590, 590]
    fake.sets.clear()
    read()
    assert fake.sets == []                    # refocus happens once per open


def test_usb_camera_frames_are_turned_180(monkeypatch):
    frame = np.zeros((30, 40, 3), np.uint8)
    frame[0, 0] = 255                         # top-left marker
    camera = Camera(Settings(warmup=0, idle_seconds=1, fps=30, rotation=180))
    with patch.object(camera, '_open', return_value=(None, lambda: frame.copy(), lambda: None)):
        captured = camera.get(timeout=3)
        camera.close()
    assert captured[-1, -1].tolist() == [255, 255, 255] and captured[0, 0].tolist() == [0, 0, 0]


def test_notice_scan_returns_form_fields(monkeypatch):
    from kiosk_scanners_SL.results_SL import ScanResult
    fields = {k: '' for k in extraction_service.REGISTRATION_KEYS}
    fields.update(license_plate='GX52CL', vehicle_vin='JN1EV7EK3KM231838', owner_names='ROBERT L LEIJA')
    with patch.object(extraction_service, 'configure_tesseract'), \
         patch.object(extraction_service, 'registration_ocr'), \
         patch.object(extraction_service.documents, 'read',
                      return_value=ScanResult('registration_renewal', fields, notes=['Orientation uncertain — check'])):
        result = extraction_service.scan_registration(np.zeros((20, 20, 3), np.uint8))
    assert result['success'] and result['fields']['license_plate'] == 'GX52CL'
    assert result['notes'] == ['Orientation uncertain - check']
    assert 'total_fee' in result['missing']


def test_unreadable_notice_is_a_422_style_error():
    from kiosk_scanners_SL.results_SL import ScanResult
    with patch.object(extraction_service, 'configure_tesseract'), \
         patch.object(extraction_service, 'registration_ocr'), \
         patch.object(extraction_service.documents, 'read', return_value=ScanResult('registration_renewal', {})):
        with pytest.raises(ValueError, match='optional'):
            extraction_service.scan_registration(np.zeros((20, 20, 3), np.uint8))
