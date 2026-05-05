from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
import re
from time import sleep

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

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


@dataclass(frozen=True)
class OcrWord:
    raw_text: str
    normalized_text: str
    confidence: float
    bbox: BoundingBox


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

        message = (
            f"Could not locate {self.options.target!r} "
            f"after {self.options.attempts} attempts."
        )
        if last_error is not None:
            message = f"{message} Last error: {last_error}"
        raise GroundingError(message)

    def _locate_in_image(self, image: Image.Image, screenshot_path: Path) -> GroundingResult:
        ocr_result = self._locate_with_ocr(image, screenshot_path)
        if ocr_result is not None:
            return ocr_result

        candidate_ocr_result = self._locate_with_candidate_ocr(image, screenshot_path)
        if candidate_ocr_result is not None:
            return candidate_ocr_result

        template_result = self._locate_with_template(image, screenshot_path)
        if template_result is not None:
            return template_result

        uia_result = self._locate_with_windows_uia(screenshot_path)
        if uia_result is not None:
            return uia_result

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

        target = normalize_text(self.options.target)
        matches: list[GroundingResult] = []

        for ocr_image, scale, variant in iter_ocr_images(image):
            try:
                data = pytesseract.image_to_data(
                    ocr_image,
                    output_type=pytesseract.Output.DICT,
                    config="--psm 11",
                )
            except Exception:
                continue

            for word in iter_ocr_words(data, scale=scale):
                if word.confidence < 35:
                    continue

                match_score = target_match_score(target, word.normalized_text)
                if match_score == 0:
                    continue

                bbox, center = infer_desktop_item_from_label(word.bbox)
                matches.append(
                    GroundingResult(
                        target=self.options.target,
                        bbox=bbox,
                        center=center,
                        confidence=(word.confidence / 100.0) * match_score,
                        method="ocr",
                        screenshot_path=screenshot_path,
                        note=(
                            f"OCR matched label text {word.raw_text!r} with {variant} "
                            "and inferred the icon above it."
                        ),
                    )
                )

        if not matches:
            return None

        return max(matches, key=lambda result: result.confidence)

    def _locate_with_candidate_ocr(
        self, image: Image.Image, screenshot_path: Path
    ) -> GroundingResult | None:
        try:
            import pytesseract
        except ImportError:
            return None

        target = normalize_text(self.options.target)
        image_width, image_height = image.size
        matches: list[GroundingResult] = []

        for candidate in self.detect_icon_candidates(image):
            search_box = expand_box(
                candidate,
                image_width=image_width,
                image_height=image_height,
                left=36,
                top=10,
                right=36,
                bottom=64,
            )
            crop = image.crop(
                (search_box.left, search_box.top, search_box.right, search_box.bottom)
            )

            for ocr_image, scale, variant in iter_ocr_images(crop):
                for page_segmentation_mode in ("6", "11"):
                    try:
                        data = pytesseract.image_to_data(
                            ocr_image,
                            output_type=pytesseract.Output.DICT,
                            config=f"--psm {page_segmentation_mode}",
                        )
                    except Exception:
                        continue

                    words = [
                        translate_ocr_word(word, search_box.left, search_box.top)
                        for word in iter_ocr_words(data, scale=scale)
                        if word.confidence >= 18
                    ]
                    if not words:
                        continue

                    word_match = best_word_match(target, words)
                    if word_match is not None:
                        match_words, match_score = word_match
                    else:
                        combined = "".join(word.normalized_text for word in words)
                        match_score = target_match_score(target, combined)
                        if match_score == 0:
                            continue
                        match_words = words

                    label_bbox = union_boxes([word.bbox for word in match_words])
                    item_bbox = union_boxes([candidate, label_bbox])
                    confidence = (average_confidence(match_words) / 100.0) * match_score
                    result = GroundingResult(
                        target=self.options.target,
                        bbox=item_bbox,
                        center=candidate.center,
                        confidence=confidence,
                        method="candidate",
                        screenshot_path=screenshot_path,
                        note=(
                            "Localized OCR matched the label near an icon candidate "
                            f"with {variant}, psm {page_segmentation_mode}: "
                            f"{' '.join(word.raw_text for word in match_words)!r}."
                        ),
                    )
                    if confidence >= 0.50:
                        return result
                    matches.append(result)

        if not matches:
            return None

        return max(matches, key=lambda result: result.confidence)

    def _locate_with_windows_uia(self, screenshot_path: Path) -> GroundingResult | None:
        try:
            from pywinauto import Desktop
        except ImportError:
            return None

        target = normalize_text(self.options.target)
        matches: list[GroundingResult] = []

        try:
            elements = Desktop(backend="uia").descendants(control_type="ListItem")
        except Exception:
            return None

        for element in elements:
            try:
                text = element.window_text() or element.element_info.name
            except Exception:
                continue

            match_score = target_match_score(target, normalize_text(text))
            if match_score == 0:
                continue

            try:
                rectangle = element.rectangle()
            except Exception:
                continue

            width = max(1, int(rectangle.right - rectangle.left))
            height = max(1, int(rectangle.bottom - rectangle.top))
            if width > 300 or height > 220:
                continue

            bbox = BoundingBox(
                left=int(rectangle.left),
                top=int(rectangle.top),
                width=width,
                height=height,
            )
            matches.append(
                GroundingResult(
                    target=self.options.target,
                    bbox=bbox,
                    center=bbox.center,
                    confidence=0.70 * match_score,
                    method="uia",
                    screenshot_path=screenshot_path,
                    note=(
                        "Windows UI Automation matched the desktop item after "
                        "visual OCR/template matching did not."
                    ),
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
    return re.sub(r"[^a-z0-9]+", "", str(value).casefold())


def iter_ocr_images(image: Image.Image):
    yield image, 1.0, "original screenshot"

    grayscale = ImageOps.grayscale(image)
    contrasted = ImageOps.autocontrast(grayscale)
    contrasted = ImageEnhance.Contrast(contrasted).enhance(2.0)
    sharpened = contrasted.filter(ImageFilter.SHARPEN)
    upscaled = sharpened.resize((image.width * 2, image.height * 2), Image.Resampling.LANCZOS)
    yield upscaled, 2.0, "enhanced screenshot"


def iter_ocr_words(data: dict[str, list[object]], scale: float) -> list[OcrWord]:
    words: list[OcrWord] = []
    texts = data.get("text", [])

    for index, raw_text in enumerate(texts):
        normalized = normalize_text(raw_text)
        if not normalized:
            continue

        confidence = parse_confidence(get_ocr_value(data, "conf", index, "0"))
        left = round(parse_confidence(get_ocr_value(data, "left", index, 0)) / scale)
        top = round(parse_confidence(get_ocr_value(data, "top", index, 0)) / scale)
        width = round(parse_confidence(get_ocr_value(data, "width", index, 0)) / scale)
        height = round(parse_confidence(get_ocr_value(data, "height", index, 0)) / scale)
        if width <= 0 or height <= 0:
            continue

        words.append(
            OcrWord(
                raw_text=str(raw_text).strip(),
                normalized_text=normalized,
                confidence=confidence,
                bbox=BoundingBox(left=left, top=top, width=width, height=height),
            )
        )

    return words


def get_ocr_value(
    data: dict[str, list[object]], key: str, index: int, default: object
) -> object:
    values = data.get(key)
    if values is None or index >= len(values):
        return default
    return values[index]


def translate_ocr_word(word: OcrWord, offset_x: int, offset_y: int) -> OcrWord:
    return OcrWord(
        raw_text=word.raw_text,
        normalized_text=word.normalized_text,
        confidence=word.confidence,
        bbox=BoundingBox(
            left=word.bbox.left + offset_x,
            top=word.bbox.top + offset_y,
            width=word.bbox.width,
            height=word.bbox.height,
        ),
    )


def best_word_match(target: str, words: list[OcrWord]) -> tuple[list[OcrWord], float] | None:
    matches: list[tuple[list[OcrWord], float]] = []
    for word in words:
        match_score = target_match_score(target, word.normalized_text)
        if match_score > 0:
            matches.append(([word], match_score))

    if not matches:
        return None

    return max(matches, key=lambda match: average_confidence(match[0]) * match[1])


def average_confidence(words: list[OcrWord]) -> float:
    if not words:
        return 0.0
    return sum(word.confidence for word in words) / len(words)


def union_boxes(boxes: list[BoundingBox]) -> BoundingBox:
    left = min(box.left for box in boxes)
    top = min(box.top for box in boxes)
    right = max(box.right for box in boxes)
    bottom = max(box.bottom for box in boxes)
    return BoundingBox(left=left, top=top, width=right - left, height=bottom - top)


def expand_box(
    box: BoundingBox,
    image_width: int,
    image_height: int,
    left: int,
    top: int,
    right: int,
    bottom: int,
) -> BoundingBox:
    expanded_left = max(0, box.left - left)
    expanded_top = max(0, box.top - top)
    expanded_right = min(image_width, box.right + right)
    expanded_bottom = min(image_height, box.bottom + bottom)
    return BoundingBox(
        left=expanded_left,
        top=expanded_top,
        width=max(1, expanded_right - expanded_left),
        height=max(1, expanded_bottom - expanded_top),
    )


def target_match_score(target: str, observed: str) -> float:
    if observed == target:
        return 1.0
    if observed.startswith(target):
        return 0.72
    if len(observed) < min(4, len(target)):
        return 0.0
    if target in observed or observed in target:
        return 0.55
    similarity = SequenceMatcher(None, target, observed).ratio()
    if similarity >= 0.84:
        return 0.60
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
