from __future__ import annotations

from pathlib import Path
from typing import Any

import mss
from PIL import Image


def capture_screen(output_path: Path | None = None, monitor_index: int = 1) -> Image.Image:
    """Capture the primary monitor and optionally persist it."""
    with mss.mss() as sct:
        monitor = sct.monitors[monitor_index]
        raw = sct.grab(monitor)
        image = Image.frombytes("RGB", raw.size, raw.rgb)

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path)

    return image


def get_screen_diagnostics() -> dict[str, Any]:
    diagnostics: dict[str, Any] = {}

    with mss.mss() as sct:
        diagnostics["mss_monitors"] = [
            {
                "index": index,
                "left": monitor["left"],
                "top": monitor["top"],
                "width": monitor["width"],
                "height": monitor["height"],
            }
            for index, monitor in enumerate(sct.monitors)
        ]

    try:
        import pyautogui

        width, height = pyautogui.size()
        diagnostics["pyautogui_size"] = {"width": width, "height": height}
    except Exception as exc:
        diagnostics["pyautogui_error"] = str(exc)

    return diagnostics
