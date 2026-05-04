from __future__ import annotations

from pathlib import Path

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
