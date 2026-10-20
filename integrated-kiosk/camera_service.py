"""One camera owner shared by the web preview and full-resolution captures."""
from __future__ import annotations
import os
import platform
import threading
import time
from dataclasses import dataclass, field

import cv2


class CameraError(RuntimeError):
    pass


@dataclass
class Settings:
    backend: str = field(default_factory=lambda: os.getenv('KIOSK_CAMERA_BACKEND', 'picamera2'))
    index: int = field(default_factory=lambda: int(os.getenv('KIOSK_CAMERA_INDEX', '0')))
    width: int = field(default_factory=lambda: int(os.getenv('KIOSK_CAMERA_WIDTH', '2304')))
    height: int = field(default_factory=lambda: int(os.getenv('KIOSK_CAMERA_HEIGHT', '1296')))
    fps: int = 8
    warmup: float = 1.5
    idle_seconds: float = 15
    rotation: int = field(default_factory=lambda: int(os.getenv('KIOSK_CAMERA_ROTATION', '0')))


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
        backend = cv2.CAP_DSHOW if platform.system() == 'Windows' else cv2.CAP_V4L2
        device = cv2.VideoCapture(s.index, backend)
        if not device.isOpened():
            device.release()
            raise CameraError('USB camera could not open. Check the camera index and close other camera programs.')
        device.set(cv2.CAP_PROP_FRAME_WIDTH, s.width)
        device.set(cv2.CAP_PROP_FRAME_HEIGHT, s.height)
        device.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        def read():
            ok, frame = device.read()
            if not ok:
                raise CameraError('The camera stopped delivering frames. Reconnect it and retry.')
            return frame
        return device, read, device.release

    def _run(self):
        close = None
        try:
            _, read, close = self._open()
            ready_at = time.monotonic() + self.settings.warmup
            while not self.stop.is_set():
                with self.condition:
                    if time.monotonic() - self.last_use > self.settings.idle_seconds:
                        break
                started = time.monotonic()
                frame = read()
                if frame is None:
                    raise CameraError('No camera image was received.')
                if self.settings.rotation in {90, 180, 270}:
                    rotations = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
                    frame = cv2.rotate(frame, rotations[self.settings.rotation])
                if time.monotonic() < ready_at:
                    continue
                h, w = frame.shape[:2]
                preview = cv2.resize(frame, (960, max(1, round(h * 960 / w))))
                ok, jpg = cv2.imencode('.jpg', preview, [cv2.IMWRITE_JPEG_QUALITY, 75])
                if not ok:
                    raise CameraError('Could not encode camera preview.')
                with self.condition:
                    self.frame, self.jpeg = frame, jpg.tobytes()
                    self.frame_time = time.monotonic()
                    self.condition.notify_all()
                self.stop.wait(max(0, 1 / self.settings.fps - (time.monotonic() - started)))
        except Exception as exc:
            with self.condition:
                self.error = str(exc) if isinstance(exc, CameraError) else 'Camera could not start. Check rpicam-hello --list-cameras and close other camera programs.'
                self.condition.notify_all()
        finally:
            if close:
                try:
                    close()
                except Exception:
                    pass
            with self.condition:
                self.frame = self.jpeg = None
                self.thread = None
                self.condition.notify_all()

    def get(self, preview=False, timeout=10):
        with self.condition:
            self.last_use = time.monotonic()
            if self.thread is None:
                self.error = ''
                self.stop.clear()
                self.thread = threading.Thread(target=self._run, daemon=True)
                self.thread.start()
            deadline = time.monotonic() + timeout
            # Captures require a frame produced after the button was pressed.
            required_time = 0 if preview else time.monotonic()
            while self.frame is None or self.frame_time < required_time or time.monotonic() - self.frame_time > 2:
                if self.error:
                    raise CameraError(self.error)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CameraError('Camera timed out. Check the ribbon cable, camera detection, and focus.')
                self.condition.wait(min(remaining, 0.5))
            return self.jpeg if preview else self.frame.copy()

    def close(self):
        self.stop.set()
        thread = self.thread
        if thread:
            thread.join(timeout=3)
