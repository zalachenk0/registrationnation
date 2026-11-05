# ============================================================================
# File:         test_focus_sweep_SL.py
# Written by:   SL (Seth Leija)
# What it does: Tests for the USB camera focus sweep.
# Team-written: Logic written by SL (Seth Leija). Outside code is used only
#               through the third-party sources listed below.
# Third-party:  OpenCV; NumPy; pytest
# ============================================================================
"""Focus sweep of the USB document camera, on a simulated lens."""
from unittest.mock import patch

import cv2
import numpy as np
import pytest

import camera_service_BV_SL as camera_service
from camera_service_BV_SL import Camera, Settings, sharpness

rng = np.random.default_rng(0)
PAGE = cv2.resize((rng.random((60, 80)) * 255).astype(np.uint8), (400, 300), interpolation=cv2.INTER_NEAREST)
PAGE = cv2.cvtColor(PAGE, cv2.COLOR_GRAY2BGR)


class Lens:
    """A camera whose image blurs with distance from the sharp lens position."""

    def __init__(self, sharp_at):
        self.sharp_at, self.position, self.moves = sharp_at, None, []

    def isOpened(self):
        return True

    def set(self, prop, value):
        if prop == cv2.CAP_PROP_FOCUS:
            self.position = value
            self.moves.append(value)
        return True

    def get(self, prop):
        return 0

    def read(self):
        blur = abs((self.position or 0) - self.sharp_at) / 15
        frame = PAGE if blur < 0.3 else cv2.GaussianBlur(PAGE, (0, 0), blur)
        return True, frame.copy()

    def release(self):
        pass


def camera(**overrides):
    settings = dict(backend='opencv', index=0, width=400, height=300, warmup=0, idle_seconds=5, fps=60,
                    rotation=0, focus=590, exposure=78, gain=0, focus_sweep=True, focus_range=100,
                    focus_step=20, focus_settle=0.01)
    settings.update(overrides)
    return Camera(Settings(**settings))


@pytest.mark.parametrize('sharp_at,expected', [(590, 590), (640, 640), (530, 530), (700, 690)])
def test_capture_sweeps_to_the_sharpest_lens_position(monkeypatch, sharp_at, expected):
    monkeypatch.setattr(camera_service.platform, 'system', lambda: 'Linux')
    lens, cam = Lens(sharp_at), camera()
    with patch.object(camera_service.cv2, 'VideoCapture', return_value=lens):
        image = cam.get(timeout=5)
        cam.close()
    # 700 is outside 590 +/- 100, so the sweep stops at the edge of the range
    assert cam.focus == expected and lens.position == expected
    assert sharpness(image) == pytest.approx(sharpness(lens.read()[1]))
    assert len(set(lens.moves)) <= 12


def test_next_capture_starts_from_the_last_sharp_position(monkeypatch):
    monkeypatch.setattr(camera_service.platform, 'system', lambda: 'Linux')
    lens, cam = Lens(650), camera()
    with patch.object(camera_service.cv2, 'VideoCapture', return_value=lens):
        cam.get(timeout=5)
        lens.moves.clear()
        cam.get(timeout=5)
        cam.close()
    assert cam.focus == 650
    assert set(lens.moves) <= {630, 640, 650, 660, 670}   # only checks around 650


def test_preview_does_not_sweep_and_sweep_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(camera_service.platform, 'system', lambda: 'Linux')
    for cam, call in ((camera(), lambda c: c.get(preview=True, timeout=5)),
                      (camera(focus_sweep=False), lambda c: c.get(timeout=5))):
        lens = Lens(650)
        with patch.object(camera_service.cv2, 'VideoCapture', return_value=lens):
            call(cam)
            cam.close()
        assert cam.focus == 590 and set(lens.moves) == {540, 590}   # open + refocus nudge only


def test_sharpness_prefers_the_focused_image():
    assert sharpness(PAGE) > 3 * sharpness(cv2.GaussianBlur(PAGE, (0, 0), 2))
