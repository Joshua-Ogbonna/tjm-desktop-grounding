from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import sleep

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from tjm_desktop_grounding.models import BoundingBox, GroundingResult
from tjm_desktop_grounding.screenshot import capture_screen


class GroundingError(RuntimeError):
    """Raised when a target cannot be grounded on the current screen."""


@dataclass(frozen=True)
class GroundingOptions:
    target: str
    attempts: int = 3
    retry_delay_seconds: float = 1.0
    screenshot_dir: Path = Path("screenshots/runtime")
    template_path: Path | None = None
    annotate_path: Path | None = None
    min_template_confidence: float = 0.72


class DesktopGrounder:
    def __init__(self, options: GroundingOptions) -> None:
        self.options = options

    def locate(self) -> GroundingResult:
        last_error: Exception | None = None
        for attempt in range(1, self.options.attempts + 1):
            screenshot_path = self.options.screenshot_dir / f"desktop_attempt_{attempt}.png"
            image = capture_screen(screenshot_path)

            try:
                result = self._locate_in_image(image, screenshot_path)
                if self.options.annotate_path is not None:
                    self.annotate(image, result, self.options.annotate_path)
                return result
            except GroundingError as exc:
                last_error = exc
                if attempt < self.options.attempts:
                    sleep(self.options.retry_delay_seconds)

        message = f"Could not locate {self.options.target!r} after {self.options.attempts} attempts."
        if last_error is not None:
            message = f"{message} Last error: {last_error}"
        raise GroundingError(message)

    def _locate_in_image(self, image: Image.Image, screenshot_path: Path) -> GroundingResult:
        ocr_result = self._locate_with_ocr(image, screenshot_path)
        if ocr_result is not None:
            return ocr_result

        template_result = self._locate_with_template(image, screenshot_path)
        if template_result is not None:
            return template_result

        candidates = self.detect_icon_candidates(image)
        if candidates:
            ocr_note = self._ocr_debug_note(image)
            raise GroundingError(
                f"Found {len(candidates)} icon-like regions, but none matched "
                f"{self.options.target!r}. Screenshot: {screenshot_path}. {ocr_note}"
            )

        raise GroundingError(f"No icon-like regions were found. Screenshot: {screenshot_path}.")

    def _locate_with_ocr(
        self, image: Image.Image, screenshot_path: Path
    ) -> GroundingResult | None:
        try:
            import pytesseract
        except ImportError:
            return None

        try:
            data = pytesseract.image_to_data(
                image,
                output_type=pytesseract.Output.DICT,
                config="--psm 11",
            )
        except Exception:
            return None

        target = normalize_text(self.options.target)
        matches: list[GroundingResult] = []

        for index, raw_text in enumerate(data.get("text", [])):
            text = normalize_text(raw_text)
            if not text:
                continue

            confidence = parse_confidence(data.get("conf", ["0"])[index])
            if confidence < 35:
                continue

            match_score = target_match_score(target, text)
            if match_score == 0:
                continue

            label_bbox = BoundingBox(
                left=int(data["left"][index]),
                top=int(data["top"][index]),
                width=int(data["width"][index]),
                height=int(data["height"][index]),
            )
            bbox, center = infer_desktop_item_from_label(label_bbox)
            matches.append(
                GroundingResult(
                    target=self.options.target,
                    bbox=bbox,
                    center=center,
                    confidence=(confidence / 100.0) * match_score,
                    method="ocr",
                    screenshot_path=screenshot_path,
                    note=f"OCR matched label text {raw_text!r} and inferred the icon above it.",
                )
            )

        if not matches:
            return None

        return max(matches, key=lambda result: result.confidence)

    @staticmethod
    def _ocr_debug_note(image: Image.Image) -> str:
        try:
            import pytesseract
        except ImportError:
            return "OCR is unavailable because pytesseract is not installed."

        try:
            data = pytesseract.image_to_data(
                image,
                output_type=pytesseract.Output.DICT,
                config="--psm 11",
            )
        except Exception as exc:
            return f"OCR failed: {exc}"

        words: list[str] = []
        for index, raw_text in enumerate(data.get("text", [])):
            text = str(raw_text).strip()
            if not text:
                continue
            confidence = parse_confidence(data.get("conf", ["0"])[index])
            if confidence < 20:
                continue
            words.append(f"{text!r}:{confidence:.0f}")
            if len(words) >= 12:
                break

        if not words:
            return "OCR saw no readable text."
        return f"OCR saw: {', '.join(words)}."

    def _locate_with_template(
        self, image: Image.Image, screenshot_path: Path
    ) -> GroundingResult | None:
        if self.options.template_path is None:
            return None

        template_path = self.options.template_path
        if not template_path.exists():
            raise GroundingError(f"Template image does not exist: {template_path}")

        screenshot = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY)
        template = cv2.imread(str(template_path), cv2.IMREAD_GRAYSCALE)
        if template is None:
            raise GroundingError(f"Could not read template image: {template_path}")

        if template.shape[0] > screenshot.shape[0] or template.shape[1] > screenshot.shape[1]:
            raise GroundingError("Template image is larger than the screenshot.")

        result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
        _, max_value, _, max_location = cv2.minMaxLoc(result)
        confidence = float(max_value)

        if confidence < self.options.min_template_confidence:
            return None

        height, width = template.shape[:2]
        bbox = BoundingBox(
            left=int(max_location[0]),
            top=int(max_location[1]),
            width=int(width),
            height=int(height),
        )
        return GroundingResult(
            target=self.options.target,
            bbox=bbox,
            center=bbox.center,
            confidence=confidence,
            method="template",
            screenshot_path=screenshot_path,
            note=f"Template matched {template_path.name}.",
        )

    def detect_icon_candidates(self, image: Image.Image) -> list[BoundingBox]:
        screenshot = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, threshold1=40, threshold2=120)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        candidates: list[BoundingBox] = []
        for contour in contours:
            x, y, width, height = cv2.boundingRect(contour)
            area = width * height
            if not 400 <= area <= 20000:
                continue
            if not 12 <= width <= 180:
                continue
            if not 12 <= height <= 140:
                continue
            candidates.append(BoundingBox(x, y, width, height))

        return sorted(candidates, key=lambda box: (box.top, box.left))

    @staticmethod
    def annotate(image: Image.Image, result: GroundingResult, output_path: Path) -> None:
        annotated = image.copy()
        draw = ImageDraw.Draw(annotated)
        box = result.bbox
        x, y = result.center

        draw.rectangle([box.left, box.top, box.right, box.bottom], outline="red", width=4)
        draw.ellipse([x - 7, y - 7, x + 7, y + 7], fill="yellow", outline="black", width=2)

        label = f"{result.target} {result.confidence:.2f} ({result.method})"
        font = ImageFont.load_default()
        label_box = draw.textbbox((box.left, max(0, box.top - 20)), label, font=font)
        draw.rectangle(label_box, fill="red")
        draw.text((label_box[0], label_box[1]), label, fill="white", font=font)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        annotated.save(output_path)


def normalize_text(value: object) -> str:
    return str(value).strip().casefold().replace(" ", "")


def target_match_score(target: str, observed: str) -> float:
    if observed == target:
        return 1.0
    if observed.startswith(target):
        return 0.72
    if len(observed) < min(4, len(target)):
        return 0.0
    if target in observed or observed in target:
        return 0.55
    return 0.0


def parse_confidence(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def infer_desktop_item_from_label(label: BoundingBox) -> tuple[BoundingBox, tuple[int, int]]:
    label_center_x, _ = label.center
    icon_size = 48
    horizontal_padding = 12
    gap_above_label = 8

    icon_left = label_center_x - icon_size // 2
    icon_top = max(0, label.top - icon_size - gap_above_label)
    item_left = min(label.left, icon_left) - horizontal_padding
    item_top = icon_top
    item_right = max(label.right, icon_left + icon_size) + horizontal_padding
    item_bottom = label.bottom

    bbox = BoundingBox(
        left=max(0, item_left),
        top=max(0, item_top),
        width=max(1, item_right - max(0, item_left)),
        height=max(1, item_bottom - max(0, item_top)),
    )
    center = (label_center_x, icon_top + icon_size // 2)
    return bbox, center
