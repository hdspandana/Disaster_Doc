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

import warnings
from dataclasses import dataclass
from io import BytesIO

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError

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


def _format_from_signature(data: bytes) -> str | None:
    """Recognize only image containers explicitly accepted by the upload UI."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if data.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    if data.startswith(b"BM"):
        return "BMP"
    if data.startswith((b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")):
        return "TIFF"
    return None


def _validate_image_container(data: bytes) -> tuple[int, int]:
    """Check signature, decoded container format, and declared resource bounds."""
    magic_format = _format_from_signature(data)
    if magic_format is None:
        raise InvalidImageError(
            "This file could not be read as an image. Upload a JPG, PNG, WebP, BMP, or TIFF image."
        )

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as source:
                detected_format = (source.format or "").upper()
                width, height = source.size
                if detected_format != magic_format:
                    raise InvalidImageError("The file signature does not match its image container.")
                if width < 40 or height < 40:
                    raise InvalidImageError(
                        f"The image is too small to analyse ({width}x{height} px). "
                        "Please upload a larger scan or photograph."
                    )
                if max(width, height) > config.MAX_IMAGE_DIMENSION:
                    raise InvalidImageError(
                        f"The image dimensions exceed the {config.MAX_IMAGE_DIMENSION}px limit."
                    )
                if width * height > config.MAX_IMAGE_PIXELS:
                    raise InvalidImageError(
                        f"The image exceeds the {config.MAX_IMAGE_PIXELS:,}-pixel limit."
                    )
                source.verify()
    except InvalidImageError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ) as exc:
        raise InvalidImageError("The file is not a valid supported image container.") from exc

    return width, height


def load_image(data: bytes) -> np.ndarray:
    """Validate and decode an uploaded image before it can reach OCR."""
    if not data:
        raise InvalidImageError("The uploaded file is empty.")
    if len(data) > config.MAX_UPLOAD_BYTES:
        max_mb = config.MAX_UPLOAD_BYTES // (1024 * 1024)
        raise InvalidImageError(f"The file is too large. Upload an image no larger than {max_mb} MB.")

    _validate_image_container(data)
    buf = np.frombuffer(data, dtype=np.uint8)
    try:
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    except cv2.error as exc:
        raise InvalidImageError("The supported image could not be decoded safely.") from exc
    if img is None:
        raise InvalidImageError("The supported image could not be decoded safely.")

    h, w = img.shape[:2]
    if h < 40 or w < 40:
        raise InvalidImageError("The image is too small to analyse. Upload a larger scan or photograph.")
    if max(w, h) > config.MAX_IMAGE_DIMENSION or w * h > config.MAX_IMAGE_PIXELS:
        raise InvalidImageError("The decoded image exceeds the configured resource limits.")
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
    return [[round(p[0] / scale), round(p[1] / scale)] for p in points]
