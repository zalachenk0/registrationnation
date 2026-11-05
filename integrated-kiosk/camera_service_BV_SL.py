# ============================================================================
# File:         camera_service_BV_SL.py
# Written by:   BV (camera sharing for the web preview); SL (USB document camera
#               support and focus sweep)
# What it does: Opens the document camera once and shares it between the live
#               preview and full-size captures.
# Team-written: Logic written by BV (camera sharing for the web preview); SL
#               (USB document camera support and focus sweep). Outside code is
#               used only through the third-party sources listed below.
# Third-party:  OpenCV; Picamera2; libcamera; Picamera2 and libcamera (Pi
#               camera, used only on the Pi).
# ============================================================================
"""One camera owner shared by the web preview and full-resolution captures."""
from __future__ import annotations
import logging
import os
import platform
import threading
import time
from dataclasses import dataclass, field

import cv2

LOGGER = logging.getLogger(__name__)
# A USB camera that sends only black frames this long is not working
NO_FRAME_TIMEOUT_S = 15.0


class CameraError(RuntimeError):
    pass


def _index(value):
    """A V4L2/DirectShow index ("0") or a device path ("/dev/v4l/by-id/...")."""
    value = value.strip()
    return int(value) if value.lstrip('-').isdigit() else value


def _manual(name, default):
    """A number from the environment, or None when it is set to "auto"."""
    value = os.getenv(name, default).strip().lower()
    return None if value in {'', 'auto'} else float(value)


# The scanner box's 16MP USB document camera (see Seth's kiosk_scanners_SL
# config_SL.py). DirectShow and V4L2 use different exposure units:
#   DirectShow: log2 seconds, -7 = 1/128 s (from the LED sweep: -6 clipped
#               92% of the paper, -7 clipped none)
#   V4L2:       exposure_time_absolute in 100 us steps, 78 = 7.8 ms ~ 1/128 s
#               (starting point only; re-sweep on the Pi)
_DEFAULT_EXPOSURE = '-7' if platform.system() == 'Windows' else '78'


@dataclass
class Settings:
    # opencv = USB camera (the scanner box); picamera2 = Pi CSI ribbon camera
    backend: str = field(default_factory=lambda: os.getenv('KIOSK_CAMERA_BACKEND', 'opencv'))
    index: object = field(default_factory=lambda: _index(os.getenv('KIOSK_CAMERA_INDEX', '0')))
    # 12MP 4:3 streams at ~7.5 fps; 16MP streams black frames for seconds at ~1 fps
    width: int = field(default_factory=lambda: int(os.getenv('KIOSK_CAMERA_WIDTH', '4000')))
    height: int = field(default_factory=lambda: int(os.getenv('KIOSK_CAMERA_HEIGHT', '3000')))
    fps: int = 8
    warmup: float = 1.5
    idle_seconds: float = 15
    # The USB camera hangs upside down in the scanner box
    rotation: int = field(default_factory=lambda: int(os.getenv('KIOSK_CAMERA_ROTATION', '180')))
    # USB camera only. Manual focus for a document on the box floor (autofocus
    # hunts on the background); "auto" turns autofocus on. Sharp ~560-640, best 590.
    focus: object = field(default_factory=lambda: _manual('KIOSK_CAMERA_FOCUS', '590'))
    # USB camera only. Manual exposure; auto exposure blows out white paper
    # under the LEDs. "auto" turns auto exposure on.
    exposure: object = field(default_factory=lambda: _manual('KIOSK_CAMERA_EXPOSURE', _DEFAULT_EXPOSURE))
    gain: float = field(default_factory=lambda: float(os.getenv('KIOSK_CAMERA_GAIN', '0')))
    # USB camera with manual focus only: before each capture, step the lens
    # around the last sharp position and keep the sharpest (the document is in
    # place by then). KIOSK_CAMERA_FOCUS_SWEEP=0 turns it off.
    focus_sweep: bool = field(default_factory=lambda: os.getenv('KIOSK_CAMERA_FOCUS_SWEEP', '1').strip().lower()
                              not in {'0', 'false', 'no', 'off'})
    # Lens positions searched: KIOSK_CAMERA_FOCUS +/- this range, in steps
    focus_range: int = field(default_factory=lambda: int(os.getenv('KIOSK_CAMERA_FOCUS_RANGE', '100')))
    focus_step: int = field(default_factory=lambda: int(os.getenv('KIOSK_CAMERA_FOCUS_STEP', '20')))
    # Time for the lens motor to move and a new frame to arrive before measuring
    focus_settle: float = field(default_factory=lambda: float(os.getenv('KIOSK_CAMERA_FOCUS_SETTLE', '0.5')))


def sharpness(frame):
    """Laplacian variance of the frame's centre (where the document lies), as in kiosk_scanners_SL."""
    h, w = frame.shape[:2]
    centre = frame[h // 6:h - h // 6, w // 6:w - w // 6]
    scale = 1000 / max(1, centre.shape[1])
    if scale < 1:
        centre = cv2.resize(centre, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(centre, cv2.COLOR_BGR2GRAY) if centre.ndim == 3 else centre
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


class Camera:
    def __init__(self, settings=None):
        self.settings = settings or Settings()
        self.condition = threading.Condition()
        self.thread = None
        self.frame = None
        self.jpeg = None
        self.frame_time = 0.0
        self.last_use = 0.0
        self.error = ''
        self.stop = threading.Event()
        # Manual-focus lens control while the USB camera is open, else None
        self._focus_to = None
        self.focus = self.settings.focus
        self.focus_requested = False
        self.focus_done_at = 0.0
        self.focus_sharpness = 0.0

    def status(self):
        with self.condition:
            return dict(backend=self.settings.backend, running=bool(self.thread and self.thread.is_alive()),
                        ready=self.frame is not None and time.monotonic() - self.frame_time < 2,
                        resolution=[self.settings.width, self.settings.height], error=self.error)

    def _open(self):
        s = self.settings
        if s.backend == 'picamera2':
            try:
                from picamera2 import Picamera2
                from libcamera import controls
            except ImportError as exc:
                raise CameraError('Install python3-picamera2 with apt and use a venv with --system-site-packages.') from exc
            device = Picamera2(s.index)
            try:
                # Picamera2 RGB888 arrays use B,G,R byte order, as OpenCV expects.
                config = device.create_video_configuration(main={'size': (s.width, s.height), 'format': 'RGB888'},
                                                           buffer_count=3, controls={'FrameRate': s.fps})
                device.configure(config)
                if 'AfMode' in device.camera_controls and os.getenv('KIOSK_CAMERA_AUTOFOCUS', '1') != '0':
                    device.set_controls({'AfMode': controls.AfModeEnum.Continuous})
                lens = os.getenv('KIOSK_CAMERA_LENS_POSITION')
                if lens and 'LensPosition' in device.camera_controls:
                    device.set_controls({'AfMode': controls.AfModeEnum.Manual, 'LensPosition': float(lens)})
                device.start()
                return device, lambda: device.capture_array('main'), device.close
            except Exception:
                device.close()
                raise
        if s.backend != 'opencv':
            raise CameraError('KIOSK_CAMERA_BACKEND must be picamera2 (CSI) or opencv (USB).')
        windows = platform.system() == 'Windows'
        device = cv2.VideoCapture(s.index, cv2.CAP_DSHOW if windows else cv2.CAP_V4L2)
        if not device.isOpened():
            device.release()
            raise CameraError('USB camera could not open. Check the camera index and close other camera programs.')
        if not windows:
            # Uncompressed YUYV cannot carry 12MP over USB 2; the camera sends MJPG
            device.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        # Height first: a width-only change can snap to a 16:9 mode
        device.set(cv2.CAP_PROP_FRAME_HEIGHT, s.height)
        device.set(cv2.CAP_PROP_FRAME_WIDTH, s.width)
        device.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        # Focus and exposure go after the resolution: a mode change resets them
        # Start from the last sharp position found (kept between opens)
        self._set_focus(device, self.focus, -50)
        if s.exposure is None:
            device.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.75 if windows else 3)
        else:
            # DirectShow: 0.25 = manual. V4L2: 1 = manual, 3 = aperture priority (auto)
            device.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25 if windows else 1)
            device.set(cv2.CAP_PROP_EXPOSURE, s.exposure)
            device.set(cv2.CAP_PROP_GAIN, s.gain)
        # Read back: drivers silently ignore settings they do not support
        LOGGER.info('USB camera: %dx%d, focus %s, auto exposure %s, exposure %s, gain %s',
                    device.get(cv2.CAP_PROP_FRAME_WIDTH), device.get(cv2.CAP_PROP_FRAME_HEIGHT),
                    device.get(cv2.CAP_PROP_FOCUS), device.get(cv2.CAP_PROP_AUTO_EXPOSURE),
                    device.get(cv2.CAP_PROP_EXPOSURE), device.get(cv2.CAP_PROP_GAIN))
        # The focus sent at open is sometimes dropped while the camera still
        # streams black frames (lens far out of focus), so it is sent again on
        # the first real frames: nudged away, then back (the driver ignores a
        # value equal to the last one).
        refocus = [-50, 0] if s.focus is not None else []
        if s.focus is not None and s.focus_sweep:
            self._focus_to = lambda value: self._set_focus(device, value)

        def read():
            ok, frame = device.read()
            if not ok:
                raise CameraError('The camera stopped delivering frames. Reconnect it and retry.')
            if frame.mean() < 5:
                return None            # still starting up (black frames)
            if refocus:
                self._set_focus(device, self.focus, refocus.pop(0))
            return frame
        return device, read, device.release

    @staticmethod
    def _set_focus(device, focus, offset=0):
        if focus is None:
            device.set(cv2.CAP_PROP_AUTOFOCUS, 1)
            return
        device.set(cv2.CAP_PROP_AUTOFOCUS, 0)
        device.set(cv2.CAP_PROP_FOCUS, focus + offset)
        if offset:
            device.set(cv2.CAP_PROP_FOCUS, focus)

    def _run(self):
        close = None
        try:
            _, read, close = self._open()
            opened = time.monotonic()
            ready_at = opened + self.settings.warmup
            while not self.stop.is_set():
                with self.condition:
                    if time.monotonic() - self.last_use > self.settings.idle_seconds:
                        break
                started = time.monotonic()
                frame = read()
                if frame is None:
                    # USB start-up black frames; give up if they never end
                    if time.monotonic() - opened > NO_FRAME_TIMEOUT_S:
                        raise CameraError('The camera only sent black frames. Check the box lights and the camera index.')
                    continue
                frame = self._rotate(frame)
                if time.monotonic() < ready_at:
                    continue
                with self.condition:
                    sweep = self.focus_requested
                if sweep:
                    self._sweep_focus(read)
                    continue
                self._publish(frame)
                self.stop.wait(max(0, 1 / self.settings.fps - (time.monotonic() - started)))
        except Exception as exc:
            with self.condition:
                self.error = str(exc) if isinstance(exc, CameraError) else 'Camera could not start. Check the camera is plugged in (v4l2-ctl --list-devices) and close other camera programs.'
                self.condition.notify_all()
        finally:
            self._focus_to = None
            if close:
                try:
                    close()
                except Exception:
                    pass
            with self.condition:
                self.frame = self.jpeg = None
                self.thread = None
                self.condition.notify_all()

    def _rotate(self, frame):
        rotations = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
        code = rotations.get(self.settings.rotation)
        return frame if code is None else cv2.rotate(frame, code)

    def _publish(self, frame):
        h, w = frame.shape[:2]
        preview = cv2.resize(frame, (960, max(1, round(h * 960 / w))))
        ok, jpg = cv2.imencode('.jpg', preview, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if not ok:
            raise CameraError('Could not encode camera preview.')
        with self.condition:
            self.frame, self.jpeg = frame, jpg.tobytes()
            self.frame_time = time.monotonic()
            self.condition.notify_all()

    def _measure(self, read, position):
        """Moves the lens, waits for it to settle (keeping the preview live), returns the sharpness."""
        if position != self.focus:
            self._focus_to(position)
            self.focus = position
        settle_until = time.monotonic() + self.settings.focus_settle
        frame = None
        while frame is None or time.monotonic() < settle_until:
            if self.stop.is_set():
                raise CameraError('The camera was closed during focusing.')
            latest = read()
            if latest is not None:
                frame = self._rotate(latest)
                self._publish(frame)
        return sharpness(frame)

    def _sweep_focus(self, read):
        """
        Contrast autofocus for the manual-focus USB camera. The camera's own
        autofocus hunts on the box floor, and one fixed position was not always
        sharp, so before each capture the lens is stepped from its last sharp
        position toward higher sharpness (hill climb), then checked half a step
        either side. A sweep takes about 2-4 s (4-8 positions).
        """
        s = self.settings
        try:
            if self._focus_to is None:
                return
            low, high = s.focus - s.focus_range, s.focus + s.focus_range
            step = max(1, s.focus_step)
            best = min(max(self.focus, low), high)
            best_sharpness = self._measure(read, best)
            tried = {best: best_sharpness}

            def climb(direction, size):
                nonlocal best, best_sharpness
                moved = False
                while low <= best + direction * size <= high:
                    position = best + direction * size
                    if position not in tried:
                        tried[position] = self._measure(read, position)
                    # 2% margin: noise between frames must not walk the lens away
                    if tried[position] <= best_sharpness * 1.02:
                        break
                    best, best_sharpness, moved = position, tried[position], True
                return moved

            if not climb(1, step):
                climb(-1, step)
            half = step // 2
            if half and not climb(1, half):
                climb(-1, half)
            if self.focus != best:
                self._measure(read, best)
            LOGGER.info('Focus sweep: lens %s, sharpness %.0f (%d positions)', best, best_sharpness, len(tried))
            self.focus_sharpness = best_sharpness
        finally:
            with self.condition:
                self.focus_requested = False
                self.focus_done_at = time.monotonic()
                self.condition.notify_all()

    def get(self, preview=False, timeout=10):
        with self.condition:
            self.last_use = time.monotonic()
            if self.thread is None:
                self.error = ''
                self.stop.clear()
                self.thread = threading.Thread(target=self._run, daemon=True)
                self.thread.start()
            # A capture first refocuses on the document (USB camera, manual focus)
            sweep = not preview and self.settings.focus is not None and self.settings.focus_sweep \
                and self.settings.backend == 'opencv'
            if sweep:
                self.focus_requested = True
                timeout += 15
            deadline = time.monotonic() + timeout
            # Captures require a frame produced after the button was pressed
            # (and after the focus sweep).
            required_time = 0 if preview else time.monotonic()
            while self.frame is None or self.frame_time < required_time or time.monotonic() - self.frame_time > 2 \
                    or (sweep and (self.focus_requested or self.frame_time <= self.focus_done_at)):
                if self.error:
                    raise CameraError(self.error)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CameraError('Camera timed out. Check the camera cable and KIOSK_CAMERA_INDEX, and close other camera programs.')
                self.condition.wait(min(remaining, 0.5))
            return self.jpeg if preview else self.frame.copy()

    def close(self):
        self.stop.set()
        thread = self.thread
        if thread:
            thread.join(timeout=3)
