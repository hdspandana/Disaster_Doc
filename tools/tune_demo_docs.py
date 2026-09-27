"""Sweep a damage-cut parameter and report what OCR actually sees.

    python tools/tune_demo_docs.py severe_name_keep 3 4 5 6

DEV TOOL ONLY - it regenerates demo images in a temp dir; it never ships with the app.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    param = sys.argv[1]
    values = sys.argv[2:] or ["3", "4", "5", "6"]
    import easyocr

    reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    src = ROOT / "tools" / "make_demo_docs.py"
    for v in values:
        env = dict(os.environ, **{"DD_" + param.upper(): v})
        code = (
            "import sys, os; sys.path.insert(0, 'tools'); import make_demo_docs as G;"
            "G.DEMO_DIR = __import__('pathlib').Path(os.environ['OUTDIR']);"
            f"os.environ['DD_{param.upper()}']='{v}';"
            "import numpy as np; rng = np.random.default_rng(G.SEED + 2*977);"
            "img, gt = G.build_severe(rng); img.save(G.DEMO_DIR / 'severe_damage.png')"
        )
        outdir = Path(tempfile.mkdtemp())
        subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=dict(env, OUTDIR=str(outdir)), check=True)
        img = cv2.imread(str(outdir / "severe_damage.png"))
        res = reader.readtext(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), detail=1, paragraph=False)
        rows = [f"{t!r}({c:.2f})@y{int(min(p[1] for p in b))}" for b, t, c in res if int(min(p[1] for p in b)) > 100]
        print(f"{param}={v}: {rows}")


if __name__ == "__main__":
    main()
