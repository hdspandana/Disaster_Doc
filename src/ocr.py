"""
OCR evidence extraction.

Contract: this module reports what the OCR engine observed, verbatim. It never
corrects, completes or "cleans" text into a value that looks more plausible.

    extract_evidence(image_bytes) -> OcrEvidence(text + bbox + confidence per observation)

Raw OCR text is machine-observed evidence, NOT truth.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from . import config
from .preprocessing import (
    InvalidImageError,
    load_image,
    preprocess,
    to_original_bbox,
    to_original_points,
)


@dataclass
class Observation:
    """A single OCR observation, preserved exactly as returned by the engine."""

    observation_id: str
    text: str
    bbox: list[int]  # [x0, y0, x1, y1] in ORIGINAL image coordinates
    polygon: list[list[int]]
    confidence: float  # OCR engine confidence (not a probability of truth)

    def to_dict(self) -> dict:
        return {
            "observation_id": self.observation_id,
            "text": self.text,
            "bbox": self.bbox,
            "polygon": self.polygon,
            "ocr_confidence": round(float(self.confidence), 4),
        }

    @property
    def alnum_count(self) -> int:
        return sum(ch.isalnum() for ch in self.text)


@dataclass
class OcrResult:
    """Everything the OCR stage produced, including failure information."""

    observations: list[Observation] = field(default_factory=list)
    engine: str = config.OCR_ENGINE_NAME
    error: str | None = None
    processing_seconds: float = 0.0
    preprocess_steps: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.observations)

    def text_lines(self) -> list[str]:
        return [o.text for o in self.observations]


_READER = None
_READER_LOCK = threading.Lock()


def _get_reader():
    """Lazily create the EasyOCR reader (model load is slow, so it is done once)."""
    global _READER
    with _READER_LOCK:
        if _READER is None:
            import easyocr  # imported lazily: heavy dependency

            _READER = easyocr.Reader(config.EASYOCR_LANGS, gpu=False, verbose=False)
    return _READER


def extract_evidence(
    image_bytes: bytes,
    on_preprocessed: Callable[[tuple[int, int], list[str]], None] | None = None,
) -> tuple[OcrResult, np.ndarray, np.ndarray]:
    """Run the OCR stage.

    Returns (result, original_bgr, preprocessed_bgr). The OCR result always carries raw
    text, bounding boxes and engine confidence; on failure it carries `error` and no
    observations, and the caller must classify fields as UNRECOVERABLE rather than guess.
    ``on_preprocessed`` is an optional timing/progress notification fired after safe image
    decode and preprocessing, before the OCR engine runs. Callback failures are ignored.
    """
    img = load_image(image_bytes)  # raises InvalidImageError -> handled by the caller
    pre = preprocess(img)
    if on_preprocessed is not None:
        try:
            on_preprocessed(img.shape[:2], list(pre.steps_applied))
        except Exception:  # progress instrumentation must never break evidence extraction
            pass

    result = OcrResult(preprocess_steps=pre.steps_applied)
    started = time.time()
    try:
        reader = _get_reader()
        raw = reader.readtext(pre.image, detail=1, paragraph=False)
    except Exception:  # pragma: no cover - depends on the OCR runtime
        # Do not expose engine exceptions, file paths, or runtime details to the browser.
        result.error = "OCR engine failed. No field values are reported."
        result.processing_seconds = time.time() - started
        return result, img, pre.color

    observations: list[Observation] = []
    for i, item in enumerate(raw, start=1):
        points, text, conf = item[0], item[1], float(item[2])
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        pre_bbox = [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))]
        observations.append(
            Observation(
                observation_id=f"obs_{i:03d}",
                text=text,  # verbatim - no stripping, no correction
                bbox=to_original_bbox(pre_bbox, pre.scale, pre.original_shape),
                polygon=to_original_points(points, pre.scale),
                confidence=conf,
            )
        )

    observations.sort(key=lambda o: (o.bbox[1], o.bbox[0]))
    for i, obs in enumerate(observations, start=1):
        obs.observation_id = f"obs_{i:03d}"

    result.observations = observations
    result.processing_seconds = time.time() - started
    if not observations:
        result.error = "Unable to obtain sufficient OCR evidence."
    return result, img, pre.color


__all__ = ["InvalidImageError", "Observation", "OcrResult", "extract_evidence"]
