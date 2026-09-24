from __future__ import annotations

"""Camera capture support for Raspberry Pi CSI Arducam and USB cameras."""

import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2


class CameraError(RuntimeError):
    """A camera was not available or could not produce an image."""


@dataclass(frozen=True)
class CameraSettings:
    backend: str = os.getenv("KIOSK_CAMERA_BACKEND", "auto").lower()
    index: int = int(os.getenv("KIOSK_CAMERA_INDEX", "0"))
    width: int = int(os.getenv("KIOSK_CAMERA_WIDTH", "2304"))
    height: int = int(os.getenv("KIOSK_CAMERA_HEIGHT", "1296"))
    warmup_ms: int = int(os.getenv("KIOSK_CAMERA_WARMUP_MS", "1200"))
    autofocus: bool = os.getenv("KIOSK_CAMERA_AUTOFOCUS", "1").lower() not in {"0", "false", "no"}


class KioskCamera:
    """Selects the best installed camera backend and serializes captures."""

    def __init__(self, settings: CameraSettings | None = None) -> None:
        self.settings = settings or CameraSettings()
        self._lock = threading.Lock()

    def backend(self) -> str | None:
        requested = self.settings.backend
        if requested in {"rpicam", "libcamera", "opencv"}:
            return requested
        if requested != "auto":
            return None
        if shutil.which("rpicam-still"):
            return "rpicam"
        if shutil.which("libcamera-still"):
            return "libcamera"
        # OpenCV is retained as an automatic fallback for USB Arducam models.
        return "opencv"

    def status(self) -> dict[str, object]:
        backend = self.backend()
        executable = {
            "rpicam": "rpicam-still",
            "libcamera": "libcamera-still",
            "opencv": f"/dev/video{self.settings.index}",
        }.get(backend or "")
        return {
            "configured": backend is not None,
            "backend": backend,
            "device": executable,
            "resolution": [self.settings.width, self.settings.height],
        }

    def capture(self, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            backend = self.backend()
            if backend in {"rpicam", "libcamera"}:
                self._capture_raspberry_pi(output_path, backend)
            elif backend == "opencv":
                self._capture_opencv(output_path)
            else:
                raise CameraError(
                    "Unknown camera backend. Set KIOSK_CAMERA_BACKEND to auto, "
                    "rpicam, libcamera, or opencv."
                )

        if not output_path.exists() or output_path.stat().st_size < 1024:
            raise CameraError("The camera did not produce a usable image.")

    def _capture_raspberry_pi(self, output_path: Path, backend: str) -> None:
        executable = "rpicam-still" if backend == "rpicam" else "libcamera-still"
        if not shutil.which(executable):
            raise CameraError(f"{executable} is not installed or not on PATH.")
        command = [
            executable,
            "--nopreview",
            "--timeout", str(self.settings.warmup_ms),
            "--width", str(self.settings.width),
            "--height", str(self.settings.height),
            "--quality", "95",
            "--encoding", "jpg",
            "--output", str(output_path),
        ]
        if self.settings.autofocus:
            command.extend(["--autofocus-mode", "auto"])
        try:
            subprocess.run(command, check=True, capture_output=True, text=True, timeout=20)
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "camera command failed").strip()
            raise CameraError(detail[-500:]) from exc
        except subprocess.TimeoutExpired as exc:
            raise CameraError("Camera capture timed out.") from exc

    def _capture_opencv(self, output_path: Path) -> None:
        camera = cv2.VideoCapture(self.settings.index, cv2.CAP_V4L2)
        if not camera.isOpened():
            camera.release()
            raise CameraError(
                f"Could not open USB camera index {self.settings.index}. "
                "For a CSI Arducam, use the rpicam backend."
            )
        try:
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.settings.width)
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.settings.height)
            deadline = time.monotonic() + max(self.settings.warmup_ms / 1000, 0.5)
            frame = None
            while time.monotonic() < deadline:
                ok, candidate = camera.read()
                if ok:
                    frame = candidate
            if frame is None or not cv2.imwrite(str(output_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 95]):
                raise CameraError("USB camera opened, but no image could be captured.")
        finally:
            camera.release()
