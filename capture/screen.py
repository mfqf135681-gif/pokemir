"""Screen region capture using mss — with window tracking.

ROIs are stored as window-relative coordinates. The capturer finds
the poker client window at startup and applies its screen position
as an offset, so the ROIs stay valid even when the window moves.
"""

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class ROIRegion:
    """A rectangular region of interest, relative to window origin."""
    name: str
    left: int
    top: int
    width: int
    height: int

    def to_mss(self) -> dict:
        return {"left": self.left, "top": self.top, "width": self.width, "height": self.height}


class ScreenCapturer:
    """Captures screen regions, automatically tracking a target window's position."""

    def __init__(self):
        self._sct = None
        self._window_rect: Optional[dict] = None   # {left, top, width, height}
        self._monitor: Optional[dict] = None

    def _get_sct(self):
        if self._sct is None:
            import mss
            self._sct = mss.mss()
        return self._sct

    # ── Window tracking ──────────────────────────────────

    def __init__(self):
        self._sct = None
        self._window_rect: Optional[dict] = None   # {left, top, width, height}
        self._monitor: Optional[dict] = None
        self._found_title: str = ""

    def find_window_by_title(self, title_substring: str) -> bool:
        """Locate a visible window whose title contains the given string.

        Stores the window's screen rect so all ROI captures are offset
        relative to it. Returns False if no matching window found.
        """
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
            result = []

            @WNDENUMPROC
            def enum_callback(hwnd, _lparam):
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length == 0:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
                if title_substring.lower() in title.lower():
                    rect = wintypes.RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(rect))
                    result.append({
                        "hwnd": hwnd,
                        "title": title,
                        "left": rect.left,
                        "top": rect.top,
                        "width": rect.right - rect.left,
                        "height": rect.bottom - rect.top,
                    })
                    return False  # stop at first match
                return True

            user32.EnumWindows(enum_callback, 0)

            if result:
                w = result[0]
                self._found_title = w["title"]
                self._window_rect = {
                    "left": w["left"], "top": w["top"],
                    "width": w["width"], "height": w["height"],
                }
                logger.info(f"Found window: {w['title']!r} at "
                            f"({w['left']},{w['top']}) {w['width']}x{w['height']}")
                return True
            else:
                logger.warning(f"No window found matching {title_substring!r}")
                return False
        except Exception:
            logger.warning("Window-finding not available on this platform", exc_info=True)
            return False

    @property
    def window_offset_left(self) -> int:
        if self._window_rect:
            return self._window_rect["left"]
        if self._monitor:
            return self._monitor["left"]
        return 0

    @property
    def window_offset_top(self) -> int:
        if self._window_rect:
            return self._window_rect["top"]
        if self._monitor:
            return self._monitor["top"]
        return 0

    # ── Monitor selection (fallback) ─────────────────────

    def list_monitors(self) -> list[dict]:
        return self._get_sct().monitors

    def select_monitor(self, index: int = 1):
        monitors = self._get_sct().monitors
        if index < 0 or index >= len(monitors):
            raise IndexError(f"Monitor {index} out of range (0–{len(monitors)-1})")
        self._monitor = monitors[index]

    # ── Capture ──────────────────────────────────────────

    def capture(self) -> np.ndarray:
        """Capture the full window or monitor."""
        region = self._window_rect or self._monitor
        if region is None:
            raise RuntimeError("No window or monitor selected.")
        img = self._get_sct().grab(region)
        return np.array(img)

    def capture_roi(self, roi: ROIRegion) -> np.ndarray:
        """Capture a ROI relative to the current window/monitor origin."""
        offset_x = self.window_offset_left
        offset_y = self.window_offset_top
        region = {
            "left": offset_x + roi.left,
            "top": offset_y + roi.top,
            "width": roi.width,
            "height": roi.height,
        }
        img = self._get_sct().grab(region)
        return np.array(img)

    def capture_raw(self, left: int, top: int, width: int, height: int) -> np.ndarray:
        """Capture an absolute screen region directly."""
        region = {"left": left, "top": top, "width": width, "height": height}
        img = self._get_sct().grab(region)
        return np.array(img)
