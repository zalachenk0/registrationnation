# ============================================================================
# File:         ui_SL.py
# Written by:   SL (Seth Leija)
# What it does: On-screen drawing for the scanner windows.
# Team-written: Logic written by SL (Seth Leija). Outside code is used only
#               through the third-party sources listed below.
# Third-party:  OpenCV; NumPy; Pillow
# ============================================================================
"""
Screen drawing for the scanners: theme, fonts, and the shared layout pieces
(header, footer, status pills, scan guide, busy spinner, review screen).

Shapes are drawn with OpenCV; text is drawn with Pillow so real fonts
(Segoe UI) can be used. A Screen collects text and renders it in one pass.
"""

from __future__ import annotations

import os
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple, TypeVar

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

Color = Tuple[int, int, int]
T = TypeVar("T")


def _bgr(hex_color: str) -> Color:
    h = hex_color.lstrip("#")
    return int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16)


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------

# Orange #FD5D00 and near-black accents on white (team color scheme)
BACKGROUND = _bgr("#F3F4F6")      # screen background
BAR = _bgr("#FFFFFF")             # header / footer
CARD = _bgr("#FFFFFF")
CARD_LIGHT = _bgr("#F1F2F4")      # keycaps, inactive steps
BORDER = _bgr("#E1E4E8")
TEXT = _bgr("#0B0F14")            # body text (brand near-black)
MUTED = _bgr("#6B7280")
WHITE = _bgr("#FFFFFF")           # text/icons on colored fills
ACCENT = _bgr("#FD5D00")          # brand orange
ACCENT_SOFT = _bgr("#FFEFE5")     # orange tint behind icons
VIDEO_BG = _bgr("#1B1F24")        # around the camera picture
INK = _bgr("#11161C")             # dark pill behind text over the camera picture
SUCCESS = _bgr("#16A34A")
WARNING = _bgr("#F59E0B")
ERROR = _bgr("#DC2626")
DARK_TEXT = _bgr("#1A1A1A")

HEADER_H = 76
FOOTER_H = 64

_PILL_COLORS = {"ok": (SUCCESS, WHITE), "warn": (WARNING, DARK_TEXT),
                "error": (ERROR, WHITE), "info": (INK, WHITE)}


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

KEY_NONE = -1
KEY_CLOSED = -2          # the window's X button
KEY_ESC = 27
KEY_SPACE = 32
KEYS_ENTER = (10, 13)


def is_cancel(key: int) -> bool:
    return key in (KEY_ESC, ord("q"), ord("Q"), KEY_CLOSED)


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

_FONT_FILES = {"regular": "segoeui.ttf", "semibold": "seguisb.ttf", "bold": "segoeuib.ttf"}


@lru_cache(maxsize=None)
def font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name in (_FONT_FILES.get(weight, "segoeui.ttf"), "segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(str(fonts_dir / name), size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def text_width(text: str, size: int, weight: str = "regular") -> float:
    return font(size, weight).getlength(text)


def fit_text(text: str, max_width: float, size: int, weight: str = "regular",
             min_size: int = 12) -> Tuple[str, int]:
    """Shrinks the font until the text fits, then truncates with an ellipsis."""
    while size > min_size and text_width(text, size, weight) > max_width:
        size -= 1
    if text_width(text, size, weight) <= max_width:
        return text, size
    while text and text_width(text + "…", size, weight) > max_width:
        text = text[:-1]
    return text + "…", size


def wrap_text(text: str, max_width: float, size: int, weight: str = "regular") -> List[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and text_width(trial, size, weight) > max_width:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + ([line] if line else [])


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------

def _round_mask(w: int, h: int, r: int) -> np.ndarray:
    mask = np.zeros((h, w), np.uint8)
    r = int(max(0, min(r, w // 2, h // 2)))
    if r == 0:
        mask[:] = 255
        return mask
    cv2.rectangle(mask, (r, 0), (w - 1 - r, h - 1), 255, -1)
    cv2.rectangle(mask, (0, r), (w - 1, h - 1 - r), 255, -1)
    for cx, cy in ((r, r), (w - 1 - r, r), (r, h - 1 - r), (w - 1 - r, h - 1 - r)):
        cv2.circle(mask, (cx, cy), r, 255, -1, cv2.LINE_AA)
    return mask


class Screen:
    """One frame being composed. Call finish() to get the final image."""

    def __init__(self, image: np.ndarray) -> None:
        self.img = image
        self.h, self.w = image.shape[:2]
        self._texts: list = []

    @classmethod
    def blank(cls, size: Tuple[int, int], color: Color = BACKGROUND) -> "Screen":
        w, h = size
        img = np.empty((h, w, 3), np.uint8)
        img[:] = color
        return cls(img)

    def text(self, xy: Tuple[float, float], text: str, size: int = 20, color: Color = TEXT,
             weight: str = "regular", anchor: str = "la") -> None:
        """Queue text. anchor is Pillow's: 'la' left/ascender, 'mm' middle/middle, 'rm' right/middle..."""
        self._texts.append(((float(xy[0]), float(xy[1])), text, font(size, weight), color, anchor))

    def rect(self, x0: float, y0: float, x1: float, y1: float, color: Color,
             alpha: float = 1.0, radius: int = 0, border: int = 0) -> None:
        """Filled (border=0) or outlined rounded rectangle, optionally translucent."""
        x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
        cx0, cy0, cx1, cy1 = max(0, x0), max(0, y0), min(self.w, x1), min(self.h, y1)
        if cx1 <= cx0 or cy1 <= cy0:
            return
        mask = _round_mask(x1 - x0, y1 - y0, radius)
        if border:
            inner = np.zeros_like(mask)
            inner[border:-border, border:-border] = _round_mask(
                x1 - x0 - 2 * border, y1 - y0 - 2 * border, max(0, radius - border))
            mask = cv2.subtract(mask, inner)
        mask = mask[cy0 - y0: cy1 - y0, cx0 - x0: cx1 - x0]
        roi = self.img[cy0:cy1, cx0:cx1]
        a = (mask.astype(np.float32) * (alpha / 255.0))[..., None]
        roi[:] = (roi * (1.0 - a) + np.array(color, np.float32) * a).astype(np.uint8)

    def paste(self, image: np.ndarray, x: int, y: int) -> None:
        h, w = image.shape[:2]
        self.img[y:y + h, x:x + w] = image

    def finish(self) -> np.ndarray:
        if self._texts:
            pil = Image.fromarray(self.img)       # BGR stays BGR: colors are BGR too
            draw = ImageDraw.Draw(pil)
            for xy, text, fnt, color, anchor in self._texts:
                draw.text(xy, text, font=fnt, fill=color, anchor=anchor)
            self.img = np.array(pil)
            self._texts = []
        return self.img


# ---------------------------------------------------------------------------
# Layout pieces
# ---------------------------------------------------------------------------

def header(s: Screen, title: str, subtitle: str = "", steps: Sequence[str] = (),
           current: int = 0) -> None:
    """Top bar: title/subtitle beside an orange accent bar, and a step indicator on the right."""
    s.rect(0, 0, s.w, HEADER_H, BAR)
    s.rect(0, HEADER_H - 3, s.w, HEADER_H, ACCENT)          # accent stripe
    s.rect(24, 18, 30, HEADER_H - 18, ACCENT, radius=3)      # accent bar beside the title
    tx = 44

    if subtitle:
        s.text((tx, 14), title, 24, TEXT, "semibold")
        s.text((tx, 46), subtitle, 15, MUTED)
    else:
        s.text((tx, HEADER_H / 2), title, 24, TEXT, "semibold", anchor="lm")

    # Steps, right-aligned: (1) Front — (2) Back — (3) Confirm
    x = s.w - 24
    cy = HEADER_H // 2
    for i in reversed(range(len(steps))):
        label = steps[i]
        lw = text_width(label, 15, "semibold")
        x -= lw
        done, active = i < current, i == current
        s.text((x, cy), label, 15, TEXT if (active or done) else MUTED, "semibold", anchor="lm")
        x -= 34
        color = SUCCESS if done else ACCENT if active else CARD_LIGHT
        cv2.circle(s.img, (int(x + 13), cy), 13, color, -1, cv2.LINE_AA)
        if done:
            cv2.polylines(s.img, [np.int32([[x + 7, cy], [x + 11, cy + 5], [x + 19, cy - 5]])],
                          False, WHITE, 2, cv2.LINE_AA)
        else:
            s.text((x + 13, cy), str(i + 1), 14, WHITE if active else MUTED, "bold", anchor="mm")
        if i > 0:
            cv2.line(s.img, (int(x - 30), cy), (int(x - 8), cy), BORDER, 2, cv2.LINE_AA)
            x -= 38


def keycap(s: Screen, x: float, cy: float, key: str) -> float:
    """Draws a keyboard key label with its left edge at x; returns its width."""
    w = max(34, text_width(key, 14, "semibold") + 20)
    s.rect(x, cy - 16, x + w, cy + 16, CARD_LIGHT, radius=7)
    s.rect(x, cy - 16, x + w, cy + 16, BORDER, radius=7, border=1)
    s.text((x + w / 2, cy), key, 14, TEXT, "semibold", anchor="mm")
    return w


def footer(s: Screen, hints: Sequence[Tuple[str, str]], left_text: str = "") -> None:
    """Bottom bar with key hints on the right: [ENTER] Confirm  [R] Retake ..."""
    y0 = s.h - FOOTER_H
    s.rect(0, y0, s.w, s.h, BAR)
    s.rect(0, y0, s.w, y0 + 1, BORDER)
    cy = y0 + FOOTER_H / 2
    widths = [max(34, text_width(k, 14, "semibold") + 20) + 10 + text_width(label, 16) for k, label in hints]
    x = s.w - 24 - sum(widths) - 28 * (len(hints) - 1)
    for (key, label), w in zip(hints, widths):
        kw = keycap(s, x, cy, key)
        s.text((x + kw + 10, cy), label, 16, TEXT, anchor="lm")
        x += w + 28
    if left_text:
        s.text((24, cy), left_text, 14, MUTED, anchor="lm")


def pill(s: Screen, cx: float, y: float, text: str, kind: str = "info", size: int = 19) -> None:
    """Rounded status label centered on cx with its top at y. kind: ok | warn | error | info."""
    bg, fg = _PILL_COLORS[kind]
    w = text_width(text, size, "semibold") + 44
    h = size + 22
    s.rect(cx - w / 2, y, cx + w / 2, y + h, bg, alpha=0.95, radius=int(h / 2))
    s.text((cx, y + h / 2), text, size, fg, "semibold", anchor="mm")


def dim_outside(img: np.ndarray, rect: Tuple[int, int, int, int], factor: float = 0.45) -> None:
    x, y, w, h = rect
    inside = img[y:y + h, x:x + w].copy()
    img[:] = (img * factor).astype(np.uint8)
    img[y:y + h, x:x + w] = inside


def guide_frame(img: np.ndarray, rect: Tuple[int, int, int, int], color: Color) -> None:
    """Corner brackets around the scan area plus a faint outline."""
    x, y, w, h = rect
    overlay = img.copy()
    cv2.rectangle(overlay, (x, y), (x + w, y + h), color, 1, cv2.LINE_AA)
    cv2.addWeighted(overlay, 0.5, img, 0.5, 0, img)
    n = int(min(w, h) * 0.16)
    t = max(4, int(min(img.shape[:2]) / 160))
    for (cx, cy), (dx, dy) in (((x, y), (1, 1)), ((x + w, y), (-1, 1)),
                               ((x, y + h), (1, -1)), ((x + w, y + h), (-1, -1))):
        cv2.line(img, (cx, cy), (cx + dx * n, cy), color, t, cv2.LINE_AA)
        cv2.line(img, (cx, cy), (cx, cy + dy * n), color, t, cv2.LINE_AA)


def spinner(img: np.ndarray, center: Tuple[int, int], radius: int, t: float) -> None:
    cv2.circle(img, center, radius, BORDER, 5, cv2.LINE_AA)
    start = int(t * 360) % 360
    cv2.ellipse(img, center, (radius, radius), 0, start, start + 100, ACCENT, 5, cv2.LINE_AA)


def place_video(canvas_size: Tuple[int, int], video: np.ndarray, top: int = HEADER_H,
                bottom: int = FOOTER_H) -> Tuple[Screen, Tuple[float, float, float]]:
    """
    Fits a video frame into the area between header and footer.
    Returns the screen and (x offset, y offset, scale) for mapping video
    coordinates onto the screen.
    """
    w, h = canvas_size
    area_h = h - top - bottom
    scale = min(w / video.shape[1], area_h / video.shape[0])
    vw, vh = int(video.shape[1] * scale), int(video.shape[0] * scale)
    s = Screen.blank(canvas_size, VIDEO_BG)
    ox, oy = (w - vw) // 2, top + (area_h - vh) // 2
    s.paste(cv2.resize(video, (vw, vh), interpolation=cv2.INTER_AREA), ox, oy)
    return s, (ox, oy, scale)


def starting_screen(size: Tuple[int, int], title: str, t: float = 0.0) -> np.ndarray:
    s = Screen.blank(size)
    header(s, title)
    cx, cy = s.w // 2, s.h // 2 - 20
    spinner(s.img, (cx, cy), 28, t)
    s.text((cx, cy + 60), "Starting camera…", 22, TEXT, "semibold", anchor="mm")
    return s.finish()


# ---------------------------------------------------------------------------
# Busy overlay (runs work in a thread so the window stays responsive)
# ---------------------------------------------------------------------------

def busy_screen(base: np.ndarray, title: str, detail: str, t: float) -> np.ndarray:
    s = Screen((base * 0.35).astype(np.uint8))
    cw, ch = 560, 210
    x0, y0 = (s.w - cw) // 2, (s.h - ch) // 2
    s.rect(x0, y0, x0 + cw, y0 + ch, CARD, radius=18)
    s.rect(x0, y0, x0 + cw, y0 + ch, BORDER, radius=18, border=1)
    spinner(s.img, (s.w // 2, y0 + 62), 24, t)
    s.text((s.w / 2, y0 + 122), title, 24, TEXT, "semibold", anchor="mm")
    if detail:
        s.text((s.w / 2, y0 + 160), detail, 16, MUTED, anchor="mm")
    return s.finish()


def run_busy(window: "Window", base: np.ndarray, title: str, detail: str,
             fn: Callable[..., T], *args) -> T:
    """Runs fn(*args) in a worker thread while showing an animated busy screen."""
    box: dict = {}

    def work() -> None:
        try:
            box["value"] = fn(*args)
        except BaseException as e:          # re-raised on the UI thread
            box["error"] = e

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    t0 = time.time()
    while thread.is_alive():
        window.show(busy_screen(base, title, detail, time.time() - t0), wait_ms=30)
    if "error" in box:
        raise box["error"]
    return box["value"]


# ---------------------------------------------------------------------------
# Review screen
# ---------------------------------------------------------------------------

Row = Tuple[str, str, bool]                       # label, value, found
Section = Tuple[Optional[str], Sequence[Row]]     # optional title, rows


def review_screen(size: Tuple[int, int], title: str, subtitle: str,
                  images: Sequence[Tuple[str, np.ndarray]], sections: Sequence[Section],
                  steps: Sequence[str] = (), current: int = 0, note: str = "",
                  image_frac: float = 0.5,
                  hints: Sequence[Tuple[str, str]] = (("ENTER", "Confirm"), ("R", "Retake"),
                                                      ("ESC", "Cancel"))) -> np.ndarray:
    """Captured image(s) on the left, extracted fields on the right."""
    s = Screen.blank(size)
    header(s, title, subtitle, steps, current)
    footer(s, hints)
    pad = 24
    top, bottom = HEADER_H + pad, s.h - FOOTER_H - pad
    if note:
        bottom -= 56
    has_fields = any(rows for _, rows in sections)
    left_w = int((s.w - 3 * pad) * image_frac) if has_fields else s.w - 2 * pad

    # --- Images, stacked in a card ---
    s.rect(pad, top, pad + left_w, bottom, CARD, radius=16)
    s.rect(pad, top, pad + left_w, bottom, BORDER, radius=16, border=1)
    gap = 12
    slot_h = (bottom - top - 2 * gap - (len(images) - 1) * gap) / max(1, len(images))
    for i, (caption, image) in enumerate(images):
        sy = top + gap + i * (slot_h + gap)
        cap_h = 28 if caption else 0
        avail_w, avail_h = left_w - 2 * gap, slot_h - cap_h
        k = min(avail_w / image.shape[1], avail_h / image.shape[0])
        iw, ih = max(1, int(image.shape[1] * k)), max(1, int(image.shape[0] * k))
        ix = int(pad + (left_w - iw) / 2)
        iy = int(sy + cap_h + (avail_h - ih) / 2)
        s.paste(cv2.resize(image, (iw, ih), interpolation=cv2.INTER_AREA), ix, iy)
        if caption:
            s.text((pad + gap + 4, sy + 4), caption, 15, MUTED, "semibold")

    # --- Fields, one line each, grouped in sections ---
    if has_fields:
        fx0 = pad * 2 + left_w
        fx1 = s.w - pad
        s.rect(fx0, top, fx1, bottom, CARD, radius=16)
        s.rect(fx0, top, fx1, bottom, BORDER, radius=16, border=1)
        n_rows = sum(len(rows) for _, rows in sections)
        n_titles = sum(1 for t, rows in sections if t and rows)
        row_h = min(58.0, (bottom - top - 24) / (n_rows + 1.1 * n_titles))
        label_size = int(max(12, min(16, row_h * 0.36)))
        value_size = int(max(13, min(22, row_h * 0.46)))
        inner_w = fx1 - fx0 - 40
        label_w = inner_w * 0.40
        y = top + 12
        for sec_title, rows in sections:
            if not rows:
                continue
            if sec_title:
                s.text((fx0 + 20, y + row_h * 0.55), sec_title.upper(), max(12, label_size - 1),
                       ACCENT, "bold", anchor="lm")
                y += row_h * 1.1
            for label, value, found in rows:
                cy = y + row_h / 2
                s.text((fx0 + 20, cy), label, label_size, MUTED, anchor="lm")
                vx = fx0 + 20 + label_w
                icon_r = int(max(7, value_size * 0.42))
                value_w = fx1 - 20 - 2 * icon_r - 12 - vx
                shown, vsize = fit_text(value if found else "Not found", value_w, value_size,
                                        "semibold")
                s.text((vx, cy), shown, vsize, TEXT if found else ERROR, "semibold", anchor="lm")
                icx = int(fx1 - 20 - icon_r)
                if found:
                    cv2.circle(s.img, (icx, int(cy)), icon_r, SUCCESS, -1, cv2.LINE_AA)
                    k = icon_r / 7
                    pts = np.int32([[icx - 3.5 * k, cy], [icx - 1 * k, cy + 2.5 * k],
                                    [icx + 3.5 * k, cy - 2.5 * k]])
                    cv2.polylines(s.img, [pts], False, WHITE, max(1, int(k * 1.6)), cv2.LINE_AA)
                else:
                    cv2.circle(s.img, (icx, int(cy)), icon_r, ERROR, -1, cv2.LINE_AA)
                    s.text((icx, cy), "!", int(icon_r * 1.5), WHITE, "bold", anchor="mm")
                y += row_h
                if y < bottom - 14:
                    cv2.line(s.img, (fx0 + 20, int(y)), (fx1 - 20, int(y)), BORDER, 1)

    if note:
        pill(s, s.w / 2, s.h - FOOTER_H - pad - 44, note, "warn", 16)
    return s.finish()


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------

class Window:
    """The OpenCV window all scanners draw into."""

    def __init__(self, title: str, size: Tuple[int, int], fullscreen: bool = False) -> None:
        self.title, self.size, self.fullscreen = title, size, fullscreen
        self._open = False

    def open(self) -> None:
        if self._open:
            return
        cv2.namedWindow(self.title, cv2.WINDOW_NORMAL)
        if self.fullscreen:
            cv2.setWindowProperty(self.title, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
        else:
            cv2.resizeWindow(self.title, *self.size)
        self._open = True

    def show(self, image: np.ndarray, wait_ms: int = 1) -> int:
        """Shows the image; returns the key pressed, KEY_NONE, or KEY_CLOSED."""
        self.open()
        cv2.imshow(self.title, image)
        key = cv2.waitKey(wait_ms)
        try:
            closed = cv2.getWindowProperty(self.title, cv2.WND_PROP_VISIBLE) < 1
        except cv2.error:
            closed = True
        if closed:
            self._open = False
            return KEY_CLOSED
        return KEY_NONE if key == -1 else key & 0xFF

    def close(self) -> None:
        if self._open:
            cv2.destroyWindow(self.title)
            cv2.waitKey(1)
            self._open = False
