"""
Central configuration for DisasterDoc: paths, template definition, thresholds and
environment-driven settings.

Everything a reviewer needs to audit the deterministic rules lives here. No magic
numbers are buried in the pipeline; no API keys are hardcoded.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=False)

DEMO_DIR = ROOT / "demo"
OUTPUT_DIR = ROOT / "outputs"
ASSET_DIR = ROOT / "assets"

CODE_VERSION = "disasterdoc-0.1.0"
EVIDENCE_SCHEMA_VERSION = 2
# Increment this when a decision-relevant rule or configuration changes.
CONFIG_VERSION = "disasterdoc-config-v3"

# --------------------------------------------------------------------------------------
# OCR
# --------------------------------------------------------------------------------------
OCR_ENGINE_NAME = os.environ.get("DD_OCR_ENGINE", "EasyOCR").strip()
EASYOCR_LANGS = ["en"]

# Downloaded OCR weights (~94 MB) live in a cache directory rather than the project tree, so
# a fresh clone starts clean and the workspace snapshot stays small. On a cold start EasyOCR
# re-downloads them once, and the app shows a "loading OCR model" notice while it does.
os.environ.setdefault("EASYOCR_MODULE_PATH", str(Path(os.environ.get("DD_MODEL_CACHE", Path.home() / ".cache" / "easyocr"))))

# --------------------------------------------------------------------------------------
# Upload/resource limits (enforced before OCR)
# --------------------------------------------------------------------------------------
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # keep aligned with Streamlit server.maxUploadSize
MAX_IMAGE_PIXELS = 25_000_000  # supports high-resolution scans while bounding decoded memory
MAX_IMAGE_DIMENSION = 10_000
MAX_SESSION_RESULTS = 1  # one active document per session; results contain full-resolution arrays

# --------------------------------------------------------------------------------------
# Preprocessing
# --------------------------------------------------------------------------------------
MAX_OCR_WIDTH = 1600  # downscale very large uploads before OCR (speed), never upscale
CLAHE_CLIP = 2.0
CLAHE_GRID = (8, 8)
DENOISE_STRENGTH = 5

# --------------------------------------------------------------------------------------
# Evidence quality thresholds (HEURISTIC - not statistically validated)
# --------------------------------------------------------------------------------------
MIN_USABLE_OCR_CONF = 0.25  # below this an observation is not treated as usable evidence
MIN_USABLE_CHARS = 2  # fewer alphanumeric characters than this is not usable evidence
LOW_CONF = 0.50  # evidence-confidence bucket boundaries
HIGH_CONF = 0.75
OBSCURED_RATIO_LIMIT = 0.35  # damaged-area fraction above which completeness is doubtful
ADJACENT_OBSCURATION_LIMIT = 0.30  # damage immediately after a value => truncation risk
ADJACENT_GAP_FRAC = 0.035  # width of the "immediately after" probe, as a fraction of width
OBSCURATION_PAPER_FRAC = 0.72  # luminance below this fraction of paper level = surface damage
OBSCURATION_LOCAL_STD = 9.0  # local contrast below this = no legible structure

TRUNCATION_MARKERS = (".", ",", ";", ":", "-", "_", "~", "|", "/")

# --------------------------------------------------------------------------------------
# Template definition.
# DisasterDoc supports ONE synthetic template with ~5 fields (hackathon scope).
# Regions are normalised (0..1) against the uploaded image so any scan size works.
# --------------------------------------------------------------------------------------
TEMPLATE_NAME = "synthetic_id_v1"
# Increment when geometry, field definitions, patterns, or field-specific validation rules change.
TEMPLATE_VERSION = "2"

TEMPLATE = {
    "name": TEMPLATE_NAME,
    "description": "Fictional 'State Relief Registry' beneficiary card (synthetic demo document only).",
    "fields": {
        "name": {
            "label": "NAME",
            "row_y": 0.248,  # vertical centre of the value row
            "row_height": 0.075,
            "expected_type": "string",
            "pattern": r"^[A-Z][A-Z\.\'\- ]{3,40}$",
            "min_chars": 5,
            "min_tokens": 2,
            "verification_instruction": "Confirm the full name against the applicant's oral statement or an independent record.",
        },
        "id_number": {
            "label": "ID NUMBER",
            "row_y": 0.352,
            "row_height": 0.075,
            "expected_type": "string",
            "pattern": r"^[A-Z]{2}-[0-9]{5}$",
            "id_components": {
                "prefix_length": 2,
                "separator": "-",
                "serial_length": 5,
                "fixed_prefix_required": False,
            },
            "min_chars": 7,  # 2 letters + 5 digits; the '-' is not a character of the value
            "min_tokens": 1,
            "verification_instruction": "Ask the applicant to state the ID number; cross-check against the issuing office register.",
        },
        "dob": {
            "label": "DATE OF BIRTH",
            "row_y": 0.455,
            "row_height": 0.075,
            "expected_type": "date",
            "pattern": r"^[0-3][0-9][ /-][0-1][0-9][ /-](19|20)[0-9]{2}$",
            "dob_components": {
                "day_pattern": r"[0-3][0-9]",
                "month_pattern": r"[0-1][0-9]",
                "separator_pattern": r"[ /-]",
                "year_representation_pattern": r"(19|20)[0-9]{2}",
                "year_representation_description": "1900 through 2099 (existing template pattern)",
            },
            "min_chars": 8,
            "min_tokens": 1,
            "verification_instruction": "Verify the date of birth from a second source; never infer digits from a damaged field.",
        },
        "district": {
            "label": "DISTRICT",
            "row_y": 0.558,
            "row_height": 0.075,
            "expected_type": "string",
            "pattern": r"^[A-Z][A-Z\.\'\- ]{3,30}$",
            "min_chars": 5,
            "min_tokens": 1,
            "verification_instruction": "Confirm the district directly with the applicant or through an independent supporting record.",
        },
        "address": {
            "label": "ADDRESS",
            "row_y": 0.677,
            "row_height": 0.080,
            "expected_type": "string",
            "pattern": r"^[0-9A-Z][0-9A-Z,\.\'\-/ ]{8,80}$",
            "min_chars": 12,
            "min_tokens": 3,
            "verification_instruction": "Re-read the address from the original document, or confirm the surviving part with the applicant.",
        },
    },
    # Where values are printed (normalised x-range) and where labels sit relative to the row.
    "value_x_range": (0.050, 0.630),
    "label_anchor": {"x": 0.050, "width": 0.190, "y_offset": -0.058, "height": 0.038},
}

FIELD_ORDER = ["name", "id_number", "dob", "district", "address"]

FIELD_DISPLAY = {
    "name": "Name",
    "id_number": "ID Number",
    "dob": "Date of Birth",
    "district": "District",
    "address": "Address",
}

# --------------------------------------------------------------------------------------
# AI (Gemini) - optional commentary layer only. Never decides field facts.
# --------------------------------------------------------------------------------------
GEMINI_MODEL = os.environ.get("DD_GEMINI_MODEL", "gemini-2.5-flash").strip()
GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
AI_TIMEOUT_SECONDS = float(os.environ.get("DD_AI_TIMEOUT", "25"))
AI_MAX_INTERPRETATIONS = 4

# Fields where a complete AI-suggested reading is treated as a forbidden guess.
# For identifiers and dates, a "plausible" completion IS the dangerous case: every digit
# is unverifiable, so suggesting a complete pattern-conformant value would invite someone
# to copy it into a form. Descriptive fields (name/district/address) may carry clearly
# labelled possibilities, because a caseworker can immediately sanity-check a place name.
AI_PATTERN_GUESS_BLOCKLIST = ("id_number", "dob")

STATUS_RECOVERED = "RECOVERED"
STATUS_PARTIAL = "PARTIAL"
STATUS_UNRECOVERABLE = "UNRECOVERABLE"

DISCLAIMER = (
    "Confidence levels are heuristic and derived from OCR engine output and field-pattern "
    "matching. This tool does not perform statistical validation and its output requires human "
    "verification before use. DisasterDoc identifies the exact file that was processed; a hash "
    "does not prove that a document is authentic. This MVP does not establish legal identity, "
    "does not verify documents against any registry, and does not produce replacement documents."
)


def ensure_output_dir() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR
