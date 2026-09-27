"""
Generate the README figures and sample report artefacts from the real pipeline.

    python tools/make_figures.py

Outputs (all produced by the same code the app runs - nothing is mocked up):
    assets/demo_montage.png                        the three synthetic demo documents
    assets/annotated_partial_all.png               annotated evidence regions, main demo
    assets/annotated_partial_district_selected.png district selected (highlight + "evidence ends here")
    assets/annotated_severe_all.png                annotated evidence regions, severe demo
    outputs/sample_partial_damage_evidence.json    example JSON evidence report
    outputs/sample_partial_damage_evidence.pdf     example PDF evidence report
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import config, evidence, report  # noqa: E402
from src.pipeline import run_pipeline  # noqa: E402

ASSETS = ROOT / "assets"
OUTPUTS = ROOT / "outputs"


def label_strip(width: int, text: str, height: int = 34) -> np.ndarray:
    strip = np.full((height, width, 3), 255, np.uint8)
    cv2.putText(strip, text, (10, int(height * 0.72)), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (30, 40, 55), 2, cv2.LINE_AA)
    return strip


def montage(paths: list[tuple[str, Path]], out: Path, max_w: int = 760) -> None:
    tiles = []
    for title, path in paths:
        img = cv2.imread(str(path))
        scale = max_w / img.shape[1]
        img = cv2.resize(img, (max_w, int(img.shape[0] * scale)), interpolation=cv2.INTER_AREA)
        tiles.append(np.vstack([label_strip(max_w, title), img]))
    grid = np.hstack(tiles)
    cv2.imwrite(str(out), grid)
    print(f"wrote {out.relative_to(ROOT)}  {grid.shape[1]}x{grid.shape[0]}")


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    OUTPUTS.mkdir(exist_ok=True)

    montage(
        [
            ("1. Mild damage", config.DEMO_DIR / "mild_damage.png"),
            ("2. Partial damage (main demo)", config.DEMO_DIR / "partial_damage.png"),
            ("3. Severe damage", config.DEMO_DIR / "severe_damage.png"),
        ],
        ASSETS / "demo_montage.png",
    )

    for name, selected, out_name in (
        ("partial_damage", None, "annotated_partial_all.png"),
        ("partial_damage", "district", "annotated_partial_district_selected.png"),
        ("severe_damage", None, "annotated_severe_all.png"),
    ):
        data = (config.DEMO_DIR / f"{name}.png").read_bytes()
        result = run_pipeline(data, use_ai=False)
        assert result.ok, result.error
        annotated = evidence.render_annotation(
            result.original_bgr,
            result.document.fields,
            selected,
            True,
            [o.to_dict() for o in result.observations],
        )
        cv2.imwrite(str(ASSETS / out_name), annotated)
        print(f"wrote assets/{out_name}")

        if name == "partial_damage" and selected is None:
            (OUTPUTS / "sample_partial_damage_evidence.json").write_bytes(report.to_json_bytes(result.document))
            print("wrote outputs/sample_partial_damage_evidence.json")
            if report.PDF_AVAILABLE:
                (OUTPUTS / "sample_partial_damage_evidence.pdf").write_bytes(
                    report.to_pdf_bytes(result.document, result.original_bgr, annotated)
                )
                print("wrote outputs/sample_partial_damage_evidence.pdf")

        # print the verdict table so a reader can compare figure with statuses
        print(f"  {' | '.join(f'{f.field_name}={f.status}' for f in result.document.fields)}")


if __name__ == "__main__":
    main()
