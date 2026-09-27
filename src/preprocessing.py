"""
Image preprocessing for OCR.

Deliberately minimal and transparent: decode, optional downscale, grayscale,
contrast enhancement (CLAHE), mild denoise. No learned models, no inpainting, no
"restoration" - nothing that could invent document content.

The preprocessed image is used ONLY to feed the OCR engine. Every bounding box the
OCR returns is mapped back to original-image coordinates by the caller, so all
evidence always refers to the document as uploaded.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from . import config


@dataclass
class PreprocessedImage:
    """Preprocessed pixels plus the geometry needed to map boxes back to the original."""

    image: np.ndarray  # grayscale, uint8, OCR input
    color: np.ndarray  # BGR original-scale image
    scale: float  # preprocessed_size / original_size
    original_shape: tuple[int, int]  # (h, w) of the uploaded image
    steps_applied: list[str]


class InvalidImageError(ValueError):
    """Raised when the uploaded bytes cannot be decoded into a usable image."""


def load_image(data: bytes) -> np.ndarray:
    """Decode uploaded bytes into a BGR image, raising a user-facing error if impossible."""
    if not data:
        raise InvalidImageError("The uploaded file is empty.")
    buf = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None:
        raise InvalidImageError(
            "This file could not be read as an image. Please upload a JPG or PNG scan or "
            "photograph of the document."
        )
    h, w = img.shape[:2]
    if h < 40 or w < 40:
        raise InvalidImageError(
            f"The image is too small to analyse ({w}x{h} px). Please upload a scan of at least 400x250 px."
        )
    return img


def preprocess(img: np.ndarray) -> PreprocessedImage:
    """Run the minimal preprocessing chain and report what was applied."""
    steps: list[str] = []
    color = img.copy()
    h, w = img.shape[:2]

    scale = 1.0
    if w > config.MAX_OCR_WIDTH:
        scale = config.MAX_OCR_WIDTH / float(w)
        color = cv2.resize(color, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        steps.append(f"downscaled to {int(w * scale)}px wide")

    gray = cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)
    steps.append("grayscale")

    clahe = cv2.createCLAHE(clipLimit=config.CLAHE_CLIP, tileGridSize=config.CLAHE_GRID)
    gray = clahe.apply(gray)
    steps.append("CLAHE contrast")

    gray = cv2.bilateralFilter(gray, config.DENOISE_STRENGTH, 60, 60)
    steps.append("bilateral denoise")

    return PreprocessedImage(
        image=gray,
        color=color,
        scale=scale,
        original_shape=(h, w),
        steps_applied=steps,
    )


def to_original_bbox(bbox: list[int], scale: float, original_shape: tuple[int, int]) -> list[int]:
    """Convert a [x0, y0, x1, y1] box from preprocessed space back to original coordinates."""
    if scale == 1.0:
        return [int(v) for v in bbox]
    x0, y0, x1, y1 = [v / scale for v in bbox]
    h, w = original_shape
    return [
        int(max(0, min(w - 1, x0))),
        int(max(0, min(h - 1, y0))),
        int(max(0, min(w - 1, x1))),
        int(max(0, min(h - 1, y1))),
    ]


def to_original_points(points, scale: float) -> list[list[int]]:
    """Convert OCR polygon points to original-image integer coordinates."""
    return [[int(round(p[0] / scale)), int(round(p[1] / scale))] for p in points]
