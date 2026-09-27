"""
Generate synthetic (fictional) damaged document images for the DisasterDoc demo.

These documents are entirely invented. They do not depict, imitate or reproduce any
real identity document, and they contain no real personal data. Every generated card
carries an explicit SPECIMEN watermark.

Usage:
    python tools/make_demo_docs.py

Outputs:
    demo/mild_damage.png
    demo/partial_damage.png
    demo/severe_damage.png
    demo/gt/*_occlusion.png   (ground-truth damage masks - DEV VALIDATION ONLY)
"""

from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = ROOT / "demo"
GT_DIR = DEMO_DIR / "gt"

CARD_W, CARD_H = 1000, 630
S = 2  # internal render scale: draw at 2x for crisp glyphs, damage, then downsample
SEED = 20250926
OUT_W, OUT_H = CARD_W * S, CARD_H * S

FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"

LABELS = {
    "name": "NAME",
    "id_number": "ID NUMBER",
    "dob": "DATE OF BIRTH",
    "district": "DISTRICT",
    "address": "ADDRESS",
    "issuer": "ISSUED BY",
}
VALUES = {
    "name": "ANANYA RAO",
    "id_number": "DX-48291",
    "dob": "14 08 1991",
    "district": "BENGALURU",
    "address": "42 LAKEVIEW STREET, WARD 7",
    "issuer": "DISTRICT RELIEF OFFICE",
}
ROW_Y = {"name": 0.222, "id_number": 0.325, "dob": 0.428, "district": 0.531, "address": 0.650, "issuer": 0.750}
VALUE_FS = {"name": 30, "id_number": 30, "dob": 30, "district": 30, "address": 26, "issuer": 22}
ORDER = ["name", "id_number", "dob", "district", "address", "issuer"]
LEFT_X = 0.062

# Damage tuning knobs: how many glyphs of a value survive the damage in each demo.
# Overridable via env (e.g. DD_SEVERE_NAME_KEEP=6) - used by tools/tune_demo_docs.py.
TUNING = {
    "mild_address_keep": 20,
    "partial_district_keep": 4,
    "partial_district_pad": 3,  # px (2x space) of damage overlap into the surviving fragment
    "partial_address_keep": 20,
    "severe_name_keep": 4,
    "severe_address_keep": 15,
}


def keep(key: str) -> int:
    """Number of surviving leading glyphs for a tuned damage cut."""
    return int(os.environ.get("DD_" + key.upper(), TUNING[key]))


def _font(path, size):
    try:
        return ImageFont.truetype(path,           size * S)
    except OSError:
        return ImageFont.load_default(size=max(12, int(size * S)))


def _measure(text: str, size: int, bold: bool = True) -> float:
    """Width of `text` in 2x pixel space."""
    return ImageDraw.Draw(Image.new("RGB", (10, 10))).textlength(text, font=_font(FONT_BOLD if bold else FONT_REG, size))


# --------------------------------------------------------------------------------------
# Renderer
# --------------------------------------------------------------------------------------
def render_clean_card() -> tuple[Image.Image, dict[str, dict]]:
    """Render the undamaged synthetic card + per-field geometry (all in 2x pixel space)."""
    img = Image.new("RGB", (OUT_W, OUT_H), (250, 247, 240))
    d = ImageDraw.Draw(img, "RGBA")

    # guilloche-style background
    for i in range(0, OUT_W, 18):
        d.line([(i, 0), (i + 240, OUT_H)], fill=(226, 234, 240, 90), width=2)
    for i in range(0, OUT_H + OUT_W, 28):
        d.line([(0, i), (OUT_W, i - OUT_W)], fill=(232, 240, 232, 70), width=2)
    img = img.filter(ImageFilter.GaussianBlur(1.2))
    d = ImageDraw.Draw(img, "RGBA")

    # header band
    d.rectangle([0, 0, OUT_W, int(0.165 * OUT_H)], fill=(20, 58, 96, 255))
    d.rectangle([0, int(0.165 * OUT_H), OUT_W, int(0.165 * OUT_H) + 10], fill=(214, 158, 46, 255))
    d.text((LEFT_X * OUT_W, 0.034 * OUT_H), "STATE RELIEF REGISTRY", font=_font(FONT_BOLD, 33), fill=(255, 255, 255))
    d.text(
        (LEFT_X * OUT_W, 0.100 * OUT_H),
        "BENEFICIARY ID CARD   \u00b7   SPECIMEN \u2014 SYNTHETIC DOCUMENT",
        font=_font(FONT_REG, 15),
        fill=(178, 205, 230),
    )

    # stylised (non-photographic) photo placeholder
    px0, py0, px1, py1 = int(0.735 * OUT_W), int(0.205 * OUT_H), int(0.945 * OUT_W), int(0.615 * OUT_H)
    d.rectangle([px0, py0, px1, py1], fill=(236, 233, 226, 255), outline=(150, 148, 140, 255), width=4)
    cx = (px0 + px1) // 2
    d.ellipse([cx - 80, py0 + 80, cx + 80, py0 + 240], fill=(198, 200, 202, 255))
    d.ellipse([cx - 156, py0 + 256, cx + 156, py1 + 80], fill=(198, 200, 202, 255))
    d.text(((px0 + px1) // 2, py1 + 20), "PHOTO", font=_font(FONT_REG, 13), fill=(130, 130, 130), anchor="ma")

    layout: dict[str, dict] = {}
    for key in ORDER:
        fs = VALUE_FS[key]
        lx, ly = LEFT_X * OUT_W, ROW_Y[key] * OUT_H
        d.text((lx, ly - 52), LABELS[key], font=_font(FONT_REG, 14), fill=(96, 104, 112))
        d.text((lx - 4, ly), VALUES[key], font=_font(FONT_BOLD, fs), fill=(26, 30, 36))
        layout[key] = {
            "label_bbox": (int(lx), int(ly - 58), int(lx + _measure(LABELS[key], 14, False) + 20), int(ly - 12)),
            "value_bbox": (int(lx), int(ly - 10), int(lx + _measure(VALUES[key], fs) + 8), int(ly + fs * S + 12)),
            "text_width": _measure(VALUES[key], fs),
            "x0": lx,
            "y0": ly,
            "fs": fs,
        }

    # separators / microtext / footer
    d.line([(LEFT_X * OUT_W, 0.700 * OUT_H), (0.945 * OUT_W, 0.700 * OUT_H)], fill=(196, 200, 204), width=3)
    d.text(
        (LEFT_X * OUT_W, 0.845 * OUT_H),
        "SPECIMEN \u00b7 SYNTHETIC DEMO DOCUMENT \u00b7 NOT A REAL IDENTITY DOCUMENT \u00b7 NO REAL PERSONAL DATA",
        font=_font(FONT_REG, 13),
        fill=(140, 140, 140),
    )
    d.text((LEFT_X * OUT_W, 0.900 * OUT_H), "DD-SYN-001", font=_font(FONT_MONO, 15), fill=(120, 124, 130))
    d.text((0.795 * OUT_W, 0.900 * OUT_H), "SPECIMEN", font=_font(FONT_BOLD, 15), fill=(120, 124, 130))

    wm = Image.new("RGBA", (OUT_W, OUT_H), (0, 0, 0, 0))
    ImageDraw.Draw(wm).text(
        (OUT_W // 2, OUT_H // 2), "SPECIMEN", font=_font(FONT_BOLD, 96), fill=(90, 100, 115, 30), anchor="mm"
    )
    wm = wm.rotate(-22, resample=Image.BICUBIC, center=(OUT_W // 2, OUT_H // 2))
    img = Image.alpha_composite(img.convert("RGBA"), wm).convert("RGB")
    return img, layout


def px_after(key: str, layout: dict, n_chars: int) -> int:
    """2x-pixel x position immediately after the first `n_chars` of a field's value."""
    return int(layout[key]["x0"] + _measure(VALUES[key][:n_chars], layout[key]["fs"]))


# --------------------------------------------------------------------------------------
# Damage primitives (all coordinates in 2x pixel space)
# --------------------------------------------------------------------------------------
def _ragged_rect(shape, rect, rng, ragged="left", jitter=9, feather=5) -> np.ndarray:
    """Rectangle mask whose `ragged` edge is torn/jittered -> float32 0..1 mask."""
    h, w = shape
    x0, y0, x1, y1 = [int(v) for v in rect]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return np.zeros((h, w), np.float32)
    m = np.zeros((h, w), np.float32)
    if ragged in ("left", "right"):
        steps = max(24, (y1 - y0) // 6)
        edge, pts = (x0, []) if ragged == "left" else (x1, [])
        for i in range(steps + 1):
            y = int(y0 + (y1 - y0) * i / steps)
            offset = int(rng.integers(-jitter, jitter + 1))
            pts.append((edge + offset, y))
        inner = (x1, y0) if ragged == "left" else (x0, y0)
        poly = pts + [(x1, y1), inner][:1] + [(inner[0], inner[1]), (x1 if ragged == "left" else x0, y1)]
        if ragged == "left":
            poly = pts + [(x1, y1), (x1, y0)]
        else:
            poly = pts + [(x0, y1), (x0, y0)]
        cv2.fillPoly(m, [np.array(poly, np.int32)], 1.0)
    else:
        m[y0:y1, x0:x1] = 1.0
    return cv2.GaussianBlur(m, (0, 0), feather)


def _blob(shape, center, radius, rng, wobble=0.5, points=22, feather=0.22) -> np.ndarray:
    h, w = shape
    ang = np.linspace(0, 2 * np.pi, points, endpoint=False)
    radii = radius * (1.0 + rng.uniform(-wobble, wobble, size=points))
    pts = np.stack([center[0] + radii * np.cos(ang), center[1] + radii * np.sin(ang)], axis=1).astype(np.int32)
    m = np.zeros((h, w), np.float32)
    cv2.fillPoly(m, [pts], 1.0)
    return cv2.GaussianBlur(m, (0, 0), max(2.0, radius * feather))


def _limit(mask: np.ndarray, rect, pad_x: int = 60, pad_y: int = 40) -> np.ndarray:
    """Hard-clip a damage mask to a padded band around `rect`.

    Without this, organic blobs can bleed into neighbouring rows and destroy fields
    (e.g. a burn aimed at DATE OF BIRTH also eating ID NUMBER). Real damage spreads,
    but for a controlled demo each field's fate must be deliberate.
    """
    h, w = mask.shape
    limit = np.zeros((h, w), np.float32)
    x0, y0, x1, y1 = [int(v) for v in rect]
    limit[max(0, y0 - pad_y): min(h, y1 + pad_y), max(0, x0 - pad_x): min(w, x1 + pad_x)] = 1.0
    return mask * limit


def _solidify(mask: np.ndarray, level: float = 0.995) -> np.ndarray:
    """Push the interior of a damage mask to near-total opacity.

    Without this, a mask core of ~0.97 leaves ~3% of the original ink showing through,
    which is faintly legible when a judge zooms into the image. Edges stay soft.
    """
    core = (mask > 0.5).astype(np.float32) * level
    return np.maximum(mask, core)


def _composite(img: Image.Image, mask: np.ndarray, colour) -> Image.Image:
    a = np.array(img).astype(np.float32)
    m = np.clip(mask, 0, 1)[..., None]
    out = a * (1 - m) + np.array(colour, np.float32) * m
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def destroy(img: Image.Image, rect, rng, strength=0.97, colour=(150, 118, 70), ragged="left", jitter=8,
            pad_x=60, pad_y=40):
    """Wash a region out to illegibility (brown flood-stain look). Returns image + mask."""
    m = _ragged_rect(img.size[::-1], rect, rng, ragged=ragged, jitter=jitter, feather=6) * strength
    x0, y0, x1, y1 = rect
    span = max(x1 - x0, 60)
    for _ in range(3):
        cx = rng.uniform(x0, x1)
        cy = rng.uniform(y0, y1)
        r = rng.uniform(0.20, 0.35) * span
        m = np.maximum(m, _blob(img.size[::-1], (cx, cy), r, rng) * strength * 0.9)
    m = _limit(np.clip(m, 0, 1), rect, pad_x=pad_x, pad_y=pad_y)
    if strength >= 0.9:
        m = _solidify(m)
    return _composite(img, m, colour), (np.clip(m, 0, 1) * 255).astype(np.uint8)


def char(img: Image.Image, rect, rng, strength=0.98, jitter=10, pad_x=60, pad_y=40):
    """Charred / burned band: near-black, mottled, uneven edges."""
    m = _ragged_rect(img.size[::-1], rect, rng, ragged="left", jitter=jitter, feather=5) * strength
    x0, y0, x1, y1 = rect
    span = max(x1 - x0, 70)
    for _ in range(4):
        cx = rng.uniform(x0, x1)
        cy = rng.uniform(y0, y1)
        r = rng.uniform(0.25, 0.4) * span
        m = np.maximum(m, _blob(img.size[::-1], (cx, cy), r, rng) * strength)
    m = _limit(np.clip(m, 0, 1), rect, pad_x=pad_x, pad_y=pad_y)
    if strength >= 0.9:
        m = _solidify(m)
    out = _composite(img, m, (34, 27, 23))
    a = np.array(out).astype(np.float32)
    a += rng.normal(0, 22, a.shape).astype(np.float32) * m[..., None]
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)), (m * 255).astype(np.uint8)


def wash(img: Image.Image, rect, rng, strength=0.6, colour=(158, 128, 80), blobs=6, pad_x=60, pad_y=40):
    m = np.zeros(img.size[::-1], np.float32)
    x0, y0, x1, y1 = rect
    span = max(x1 - x0, 160)
    for _ in range(blobs):
        cx = rng.uniform(x0, x1)
        cy = rng.uniform(y0, y1)
        r = rng.uniform(0.10, 0.24) * span
        m = np.maximum(m, _blob(img.size[::-1], (cx, cy), r, rng) * rng.uniform(0.55, 1.0))
    m = _limit(cv2.GaussianBlur(m, (0, 0), 14) * strength, rect, pad_x=pad_x, pad_y=pad_y)
    return _composite(img, m, colour), (np.clip(m, 0, 1) * 255).astype(np.uint8)


def tear(img: Image.Image, rect, rng, jitter=14):
    """Ragged missing-paper tear (paper-coloured)."""
    m = _ragged_rect(img.size[::-1], rect, rng, ragged="left", jitter=jitter, feather=3)
    return _composite(img, m, (252, 250, 245)), (np.clip(m, 0, 1) * 255).astype(np.uint8)


def crease(img: Image.Image, x: int, rng, strength=0.18):
    a = np.array(img).astype(np.float32)
    h, w = a.shape[:2]
    band = 18
    prof = (1 - np.abs(np.arange(-band, band + 1)) / band) ** 1.5 * strength
    for dx, f in zip(range(-band, band + 1), prof):
        xx = x + dx
        if 0 <= xx < w:
            a[:, xx] *= (1 - f)
    msk = np.zeros((h, w), np.uint8)
    cv2.line(msk, (x, 0), (x + int(rng.integers(-3, 4)), h), 255, 6)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)), msk


def finish(img: Image.Image, rng, blur=0.9, noise=4.0) -> Image.Image:
    img = img.filter(ImageFilter.GaussianBlur(blur * S))
    a = np.array(img).astype(np.float32)
    a += rng.normal(0, noise, a.shape).astype(np.float32)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def _down(img: Image.Image) -> Image.Image:
    return img.resize((CARD_W, CARD_H), Image.LANCZOS)


def _down_mask(m: np.ndarray) -> np.ndarray:
    return cv2.resize(m, (CARD_W, CARD_H), interpolation=cv2.INTER_AREA)


# --------------------------------------------------------------------------------------
# Demo documents
# --------------------------------------------------------------------------------------
def build_mild(rng):
    """Demo 1 - mild damage. Values intact; trailing part of ADDRESS is obscured."""
    img, L = render_clean_card()
    masks = []

    # ADDRESS row: everything after "42 LAKEVIEW STREET," is washed out -> PARTIAL
    ax0, ay0, ax1, ay1 = L["address"]["value_bbox"]
    cut = px_after("address", L, keep("mild_address_keep"))
    img, m = destroy(img, (cut, ay0 - 6, ax1 + 40, ay1 + 6), rng, strength=0.96, jitter=6, pad_y=20)
    masks.append(m)

    # footer + bottom-edge flood staining (does not touch any field row)
    img, m = wash(img, (0, int(0.80 * OUT_H), OUT_W, OUT_H), rng, strength=0.50, blobs=9, pad_x=0, pad_y=0)
    masks.append(m)
    img, m = wash(img, (int(0.66 * OUT_W), int(0.18 * OUT_H), OUT_W, int(0.70 * OUT_H)), rng,
                  strength=0.35, blobs=6, pad_x=0, pad_y=0)
    masks.append(m)

    img, m = crease(img, int(0.72 * OUT_W), rng, strength=0.14)
    masks.append(m)
    img = finish(img, rng, blur=0.7, noise=3.5)
    gt = np.zeros((CARD_H, CARD_W), np.uint8)
    for m in masks:
        gt = np.maximum(gt, _down_mask(m))
    return _down(img), gt


def build_partial(rng):
    """Demo 2 - MAIN demo. DISTRICT truncated after 'BENG'; DOB fully destroyed."""
    img, L = render_clean_card()
    masks = []

    # --- DATE OF BIRTH: entire value band destroyed -> UNRECOVERABLE ---
    dx0, dy0, dx1, dy1 = L["dob"]["value_bbox"]
    img, m = char(img, (dx0 - 4, dy0 - 10, dx1 + 40, dy1 + 12), rng, strength=1.0, pad_y=26)
    masks.append(m)
    img, m = destroy(img, (dx0 - 14, dy0 - 18, dx1 + 80, dy1 + 22), rng, strength=0.99, pad_y=30)
    masks.append(m)

    # --- DISTRICT: torn + washed immediately after 'BENG' -> PARTIAL ---
    cx0, cy0, cx1, cy1 = L["district"]["value_bbox"]
    cut = px_after("district", L, keep("partial_district_keep")) + int(os.environ.get("DD_PARTIAL_DISTRICT_PAD", TUNING["partial_district_pad"]))
    # pad_x=0 clips the damage mask at `cut`, so the surviving fragment keeps its glyphs
    # intact while everything to the right of them is destroyed.
    img, m = char(img, (cut, cy0 + 2, cx1 + 60, cy1 + 16), rng, strength=1.0, jitter=3, pad_x=0, pad_y=30)
    masks.append(m)
    img, m = destroy(img, (cut + 55, cy0 + 2, cx1 + 140, cy1 + 30), rng, strength=0.97, jitter=12, pad_x=0,
                     pad_y=38)
    masks.append(m)

    # --- ADDRESS: right-hand half washed out -> PARTIAL ---
    ax0, ay0, ax1, ay1 = L["address"]["value_bbox"]
    acut = px_after("address", L, keep("partial_address_keep"))
    img, m = destroy(img, (acut, ay0 - 12, ax1 + 80, ay1 + 14), rng, strength=0.96, pad_y=24)
    masks.append(m)

    # --- corner tear over the photo area (visually damaged, no field impact) ---
    img, m = tear(img, (int(0.86 * OUT_W), 0, OUT_W, int(0.30 * OUT_H)), rng, jitter=16)
    masks.append(m)

    # a fold line in the left margin: visible damage that does not disturb any value glyph
    img, m = crease(img, int(0.028 * OUT_W), rng, strength=0.16)
    masks.append(m)
    img = finish(img, rng, blur=0.85, noise=5.0)
    gt = np.zeros((CARD_H, CARD_W), np.uint8)
    for m in masks:
        gt = np.maximum(gt, _down_mask(m))
    return _down(img), gt


def build_severe(rng):
    """Demo 3 - severe damage. NAME fragment only; ID/DOB/DISTRICT gone; ADDRESS partial."""
    img, L = render_clean_card()
    masks = []

    def kill(key: str, pad_y: int = 32) -> None:
        nonlocal img
        x0, y0, x1, y1 = L[key]["value_bbox"]
        img, m1 = char(img, (x0 - 10, y0 - 12, x1 + 50, y1 + 14), rng, strength=1.0, pad_y=pad_y)
        img, m2 = destroy(img, (x0 - 26, y0 - 20, x1 + 100, y1 + 26), rng, strength=1.0, jitter=12, pad_y=pad_y + 6)
        masks.extend([m1, m2])

    # NAME: keep only the first 3 glyphs -> "ANA..." PARTIAL
    nx0, ny0, nx1, ny1 = L["name"]["value_bbox"]
    ncut = px_after("name", L, keep("severe_name_keep")) + 6
    img, m = char(img, (ncut, ny0 - 14, nx1 + 60, ny1 + 16), rng, strength=1.0, pad_y=28)
    masks.append(m)
    img, m = destroy(img, (ncut + 40, ny0 - 24, nx1 + 110, ny1 + 28), rng, strength=0.99, pad_y=32)
    masks.append(m)

    kill("id_number")
    kill("dob")
    kill("district")

    # ADDRESS: only the first ~16 characters survive
    ax0, ay0, ax1, ay1 = L["address"]["value_bbox"]
    acut = px_after("address", L, keep("severe_address_keep"))
    img, m = destroy(img, (acut, ay0 - 12, ax1 + 80, ay1 + 14), rng, strength=0.97, jitter=8, pad_y=24)
    masks.append(m)

    # right-hand side torn away entirely + general flood grime
    img, m = tear(img, (int(0.66 * OUT_W), int(0.10 * OUT_H), OUT_W, OUT_H), rng, jitter=22)
    masks.append(m)
    img, m = wash(img, (0, 0, OUT_W, OUT_H), rng, strength=0.28, blobs=12, pad_x=0, pad_y=0)
    masks.append(m)
    img, m = crease(img, int(0.24 * OUT_W), rng, strength=0.22)
    masks.append(m)

    img = finish(img, rng, blur=0.95, noise=6.0)
    gt = np.zeros((CARD_H, CARD_W), np.uint8)
    for m in masks:
        gt = np.maximum(gt, _down_mask(m))
    return _down(img), gt


def main() -> None:
    os.makedirs(DEMO_DIR, exist_ok=True)
    os.makedirs(GT_DIR, exist_ok=True)
    builders = {"mild_damage": build_mild, "partial_damage": build_partial, "severe_damage": build_severe}
    for i, (name, fn) in enumerate(builders.items()):
        rng = np.random.default_rng(SEED + i * 977)
        img, gt = fn(rng)
        img.save(DEMO_DIR / f"{name}.png")
        cv2.imwrite(str(GT_DIR / f"{name}_occlusion.png"), gt)
        print(f"wrote demo/{name}.png  {img.size[0]}x{img.size[1]}  occluded={gt.mean() / 255:.3f}")


if __name__ == "__main__":
    main()
