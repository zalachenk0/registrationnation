"""The kiosk's 16MP USB document camera."""

from __future__ import annotations

import time
from typing import Optional

import cv2
import numpy as np

from .config import ScannerConfig
from .results import ScannerError

# No usable frame for this long = the camera is gone (unplugged, or taken by
# another program). Start-up black frames last a few seconds at most.
NO_FRAME_TIMEOUT_S = 15.0


class CameraError(ScannerError):
    """The camera can't be opened or stopped delivering frames."""


class Camera:
    def __init__(self, config: ScannerConfig) -> None:
        self.config = config
        self.cap = cv2.VideoCapture(config.camera_index, cv2.CAP_DSHOW)
        if not self.cap.isOpened():
            raise CameraError(f"Could not open camera index {config.camera_index} "
                              "(is it plugged in, or open in another program?)")
        self._last_frame_at = time.time()
        # Height first: a width-only change can snap to a 16:9 mode
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.capture_res[1])
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.capture_res[0])
        # Focus must be set after the resolution — a mode change resets the lens.
        # The driver ignores a value equal to the one it last had, so nudge first.
        self.cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)
        self.cap.set(cv2.CAP_PROP_FOCUS, config.focus - 50)
        self.set_focus(config.focus)
        self.refocus()

    def set_focus(self, value: int) -> None:
        # The lens motor is slow: a new position takes up to a few seconds to settle
        self.focus = value
        self.cap.set(cv2.CAP_PROP_FOCUS, value)

    def refocus(self) -> None:
        """
        Re-sends the focus on the next two real frames (nudge away, then back).
        The focus set at open is sometimes dropped while the camera is still
        streaming black frames, leaving the lens far out of focus (sharpness
        ~15 vs ~1200) so no document outline is ever found.
        """
        self._refocus_step = 0

    def flush(self, frames: int = 5) -> None:
        """
        Drops frames the driver buffered while nobody was reading. Scans are
        minutes apart (the customer fills in the form in between), and a stale
        buffered frame could briefly show the previous customer's document.
        """
        for _ in range(frames):
            self.cap.grab()
        # Nobody read frames while the camera sat idle; start the timeout fresh
        self._last_frame_at = time.time()

    def read(self) -> Optional[np.ndarray]:
        """
        Full-resolution frame, or None while the camera is still starting
        (black frames). Raises CameraError after NO_FRAME_TIMEOUT_S without one.
        """
        ok, frame = self.cap.read()
        if not ok or frame.mean() < 5:
            if time.time() - self._last_frame_at > NO_FRAME_TIMEOUT_S:
                raise CameraError("The camera stopped sending images (unplugged, or open in "
                                  "another program?)")
            return None
        self._last_frame_at = time.time()
        if self._refocus_step < 2:
            self.set_focus(self.focus + (-50 if self._refocus_step == 0 else 50))
            self._refocus_step += 1
        return frame

    def release(self) -> None:
        self.cap.release()
