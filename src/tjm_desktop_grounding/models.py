from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


GroundingMethod = Literal["ocr", "template", "candidate", "uia"]


@dataclass(frozen=True)
class BoundingBox:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def center(self) -> tuple[int, int]:
        return (self.left + self.width // 2, self.top + self.height // 2)


@dataclass(frozen=True)
class GroundingResult:
    target: str
    bbox: BoundingBox
    center: tuple[int, int]
    confidence: float
    method: GroundingMethod
    screenshot_path: Path | None = None
    note: str = ""


@dataclass(frozen=True)
class BlogPost:
    id: int
    title: str
    body: str

    @property
    def text(self) -> str:
        return f"Title: {self.title}\n\n{self.body}"
