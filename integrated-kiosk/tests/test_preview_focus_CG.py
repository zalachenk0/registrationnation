# ============================================================================
# File:         test_preview_focus_CG.py
# Written by:   CG (Cristian Gonzalez)
# What it does: Tests for refocusing the USB camera's preview as soon as a
#               document is placed (camera_service_BV_SL.py, [CG] lines).
# Team-written: Logic written by CG (Cristian Gonzalez). Outside code is used
#               only through the third-party sources listed below.
# Third-party:  OpenCV; NumPy; pytest
# ============================================================================
"""The preview refocuses on its own once a document is placed and held still. A simulated lens stands in for the camera:
the box floor and the document are sharp at different lens positions, like the floor and a phone lying in the tray."""
import time
from unittest.mock import patch

import cv2
import numpy as np
import pytest

import camera_service_BV_SL as camera_service
from camera_service_BV_SL import Camera, Settings, sharpness

rng = np.random.default_rng(1)
FLOOR = np.full((300, 400, 3), 22, np.uint8)
FLOOR[::40, :] = 34                          # faint seams, like the box floor
PAGE = cv2.resize((rng.random((30, 40)) * 255).astype(np.uint8), (160, 120), interpolation=cv2.INTER_NEAREST)
PAGE = cv2.cvtColor(PAGE, cv2.COLOR_GRAY2BGR)
FLOOR_SHARP_AT = 590


def blurred(image, position, sharp_at):
    blur = abs((position or 0) - sharp_at) / 15
    return image if blur < 0.3 else cv2.GaussianBlur(image, (0, 0), blur)


class Box:
    """The scanner box seen through a manual-focus lens. Put a document in with place(), and wave a hand with moving."""

    def __init__(self):
        self.position, self.moves, self.page_sharp_at, self.moving, self.reads = None, [], None, False, 0
        self.noise = np.random.default_rng(2)

    def place(self, sharp_at):
        self.page_sharp_at = sharp_at

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
        self.reads += 1
        frame = blurred(FLOOR, self.position, FLOOR_SHARP_AT).copy()
        if self.page_sharp_at is not None:
            x = 40 + (self.reads % 2) * 160 if self.moving else 120   # a hand moving it back and forth
            frame[90:210, x:x + 160] = blurred(PAGE, self.position, self.page_sharp_at)
        jitter = self.noise.integers(-2, 3, frame.shape, dtype=np.int16)   # sensor noise: must never start a refocus
        return True, np.clip(frame.astype(np.int16) + jitter, 0, 255).astype(np.uint8)

    def release(self):
        pass


def camera(**overrides):
    settings = dict(backend='opencv', index=0, width=400, height=300, warmup=0, idle_seconds=5, fps=60, rotation=0,
                    focus=590, exposure=78, gain=0, focus_sweep=True, focus_range=100, focus_step=20,
                    focus_settle=0.01, preview_focus=True, scene_change=6, scene_still=0.1)
    settings.update(overrides)
    return Camera(Settings(**settings))


def wait_until(check, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def box(monkeypatch):
    monkeypatch.setattr(camera_service.platform, 'system', lambda: 'Linux')
    box = Box()
    with patch.object(camera_service.cv2, 'VideoCapture', return_value=box):
        yield box


def watch(cam, seconds):
    """The kiosk screen polls the preview the whole time, which also keeps the camera open."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        cam.get(preview=True, timeout=5)
        time.sleep(0.02)


def settled(cam):
    cam.get(preview=True, timeout=5)
    return cam.focus_fresh and not cam.focus_requested


def test_placing_a_document_focuses_the_preview_without_pressing_scan(box):
    cam = camera()
    cam.get(preview=True, timeout=5)
    assert wait_until(lambda: settled(cam))                            # first, the empty box once the camera opens
    box.place(650)                                                      # a phone goes in the tray, nearer the lens
    assert wait_until(lambda: settled(cam) and cam.focus == 650)       # sharp, and nobody pressed Scan
    preview = cv2.imdecode(np.frombuffer(cam.get(preview=True), np.uint8), cv2.IMREAD_COLOR)
    cam.close()
    assert box.position == 650 and sharpness(preview) > 0


def test_a_moving_hand_never_starts_a_refocus_and_it_runs_once_the_hand_stops(box):
    cam = camera()
    cam.get(preview=True, timeout=5)
    assert wait_until(lambda: settled(cam))
    box.place(650)
    box.moving = True
    box.moves.clear()
    watch(cam, 0.6)                                                     # six times longer than the stillness needed
    assert box.moves == [] and cam.focus_fresh is False                 # still moving: the lens stays put
    box.moving = False
    assert wait_until(lambda: settled(cam) and cam.focus == 650)
    cam.close()


def test_noise_alone_never_moves_the_lens(box):
    cam = camera()
    cam.get(preview=True, timeout=5)
    box.place(650)
    assert wait_until(lambda: settled(cam) and cam.focus == 650)
    box.moves.clear()
    watch(cam, 0.6)
    cam.close()
    assert box.moves == []                                              # one focus per document, not a hunting lens


def test_scan_after_the_preview_focused_skips_the_sweep_and_is_still_sharp(box):
    cam = camera()
    box.place(650)
    cam.get(preview=True, timeout=5)
    assert wait_until(lambda: settled(cam) and cam.focus == 650)
    box.moves.clear()
    started = time.monotonic()
    image = cam.get(timeout=5)
    took = time.monotonic() - started
    cam.close()
    assert box.moves == [] and took < 0.5                               # no second search: the photo is taken straight away
    assert sharpness(image[90:210, 120:280]) > 0.9 * sharpness(PAGE)    # and the document in it is sharp


def test_scan_right_after_a_swap_still_sweeps_for_the_new_document(box):
    cam = camera()
    box.place(650)
    cam.get(preview=True, timeout=5)
    assert wait_until(lambda: settled(cam) and cam.focus == 650)
    cam.settings.scene_still = 30                                       # Scan pressed before the preview could refocus
    box.place(610)                                                      # a different document, lying lower
    box.moving = True                                                   # ...placed by a hand: the picture changes
    watch(cam, 0.1)
    box.moving = False
    assert wait_until(lambda: (cam.get(preview=True), cam.focus_fresh is False)[1])
    cam.get(timeout=10)
    cam.close()
    assert cam.focus == 610                                             # the capture's own search found it


def test_turned_off_the_preview_never_moves_the_lens_but_scan_still_focuses(box):
    cam = camera(preview_focus=False)
    box.place(650)
    watch(cam, 0.6)
    assert set(box.moves) == {540, 590} and cam.focus_fresh is False    # open and its nudge only, as before
    cam.get(timeout=5)
    cam.close()
    assert cam.focus == 650


def test_the_ribbon_camera_and_manual_focus_settings_are_left_alone(box):
    cam = camera(focus_sweep=False)
    box.place(650)
    watch(cam, 0.6)
    cam.close()
    assert set(box.moves) == {540, 590} and cam.focus == 590            # no sweep allowed means no preview refocus either


def test_a_closed_camera_forgets_what_it_focused_on(box):
    cam = camera()
    box.place(650)
    cam.get(preview=True, timeout=5)
    assert wait_until(lambda: settled(cam) and cam.focus == 650)
    cam.close()
    assert cam.focus_fresh is False and cam._scene_focused is None      # the next customer's document gets its own focus
    box.moves.clear()
    cam.get(timeout=5)
    cam.close()
    assert box.moves and cam.focus == 650                               # so the next Scan searches again


def test_settings_come_from_the_env_file(monkeypatch):
    for name in ('KIOSK_CAMERA_PREVIEW_FOCUS', 'KIOSK_CAMERA_SCENE_CHANGE', 'KIOSK_CAMERA_SCENE_STILL'):
        monkeypatch.delenv(name, raising=False)
    default = Settings()
    assert default.preview_focus is True and default.scene_change == 6 and default.scene_still == 0.8
    monkeypatch.setenv('KIOSK_CAMERA_SCENE_CHANGE', '9')
    monkeypatch.setenv('KIOSK_CAMERA_SCENE_STILL', '1.5')
    for off in ('0', 'off', 'False', ' no '):
        monkeypatch.setenv('KIOSK_CAMERA_PREVIEW_FOCUS', off)
        assert Settings().preview_focus is False
    assert Settings().scene_change == 9 and Settings().scene_still == 1.5
