"""Generate and run the Phase 3 synthetic image-level evaluation.

Usage from the repository root:
    .venv/bin/python tools/run_image_level_evaluation.py
    .venv/bin/python tools/run_image_level_evaluation.py --limit 1  # quick end-to-end smoke run

Each case is rendered into an actual synthetic image, then passed as bytes to the
unchanged ``run_pipeline`` entry point with AI disabled. Evaluation-only truth is
consulted only after the pipeline has returned. Images and generated damage masks are
saved under evaluation/image_level_assets for inspection and repeatable reruns.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import config
from src.pipeline import PipelineResult, run_pipeline
from tools import make_demo_docs

MANIFEST_PATH = ROOT / "evaluation" / "image_level_cases.json"
RESULTS_PATH = ROOT / "evaluation" / "image_level_results.json"
ASSETS_PATH = ROOT / "evaluation" / "image_level_assets"
SEVERITY_BUILDERS = {
    "mild": make_demo_docs.build_mild,
    "medium": make_demo_docs.build_partial,
    "severe": make_demo_docs.build_severe,
}
DEFAULT_TUNING = {
    "mild_address_keep": 20,
    "partial_district_keep": 4,
    "partial_district_pad": 3,
    "partial_address_keep": 20,
    "severe_name_keep": 4,
    "severe_address_keep": 15,
}
TUNING_ENV_KEYS = (
    "DD_MILD_ADDRESS_KEEP",
    "DD_PARTIAL_DISTRICT_KEEP",
    "DD_PARTIAL_DISTRICT_PAD",
    "DD_PARTIAL_ADDRESS_KEEP",
    "DD_SEVERE_NAME_KEEP",
    "DD_SEVERE_ADDRESS_KEEP",
)


@contextmanager
def _controlled_generator_state(values: dict[str, str]) -> Iterator[None]:
    """Temporarily set the existing demo generator's text/tuning, restoring all globals."""
    old_values = make_demo_docs.VALUES
    old_tuning = make_demo_docs.TUNING
    saved_env = {key: os.environ.get(key) for key in TUNING_ENV_KEYS}
    try:
        make_demo_docs.VALUES = dict(values)
        make_demo_docs.TUNING = dict(DEFAULT_TUNING)
        for key in TUNING_ENV_KEYS:
            os.environ.pop(key, None)
        yield
    finally:
        make_demo_docs.VALUES = old_values
        make_demo_docs.TUNING = old_tuning
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _safe_asset_path(relative_path: str, asset_root: Path) -> Path:
    """Resolve a manifest asset path below the chosen asset directory."""
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe evaluation asset path: {relative_path!r}")
    # Manifest paths begin with evaluation/image_level_assets/<source>/...
    try:
        parts = relative.parts
        start = parts.index("image_level_assets") + 1
    except ValueError as exc:
        raise ValueError(f"asset path must be below evaluation/image_level_assets: {relative_path!r}") from exc
    path = (asset_root / Path(*parts[start:])).resolve()
    if asset_root.resolve() not in path.parents:
        raise ValueError(f"evaluation asset path escaped its root: {relative_path!r}")
    return path


def _render_and_encode(case: dict[str, Any], source: dict[str, Any]) -> tuple[bytes, np.ndarray]:
    """Render one controlled case via the existing demo renderer and damage builders."""
    # Preserve non-template demo rows such as ISSUED BY while replacing the five
    # evaluation fields with the selected fictional source record.
    values = dict(make_demo_docs.VALUES)
    values.update(source["values"])
    values[case["field_name"]] = case["rendered_target_value"]
    severity = case["severity"]
    height, width = make_demo_docs.CARD_H, make_demo_docs.CARD_W

    with _controlled_generator_state(values):
        if severity == "clean":
            rendered, _ = make_demo_docs.render_clean_card()
            rendered = make_demo_docs._down(rendered)
            gt_mask = np.zeros((height, width), dtype=np.uint8)
        else:
            rng = np.random.default_rng(int(case["seed"]))
            rendered, gt_mask = SEVERITY_BUILDERS[severity](rng)

    image_bgr = cv2.cvtColor(np.asarray(rendered), cv2.COLOR_RGB2BGR)
    image_bgr, gt_mask = _apply_image_effect(case, image_bgr, gt_mask)
    image_bytes = _encode_image(image_bgr, case["image_format"], case.get("jpeg_quality"))
    return image_bytes, gt_mask


def _apply_image_effect(
    case: dict[str, Any], image_bgr: np.ndarray, gt_mask: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Apply only image-space transforms; never alter or inject OCR text."""
    effect = case.get("image_effect")
    if effect is None:
        return image_bgr, gt_mask

    height, width = image_bgr.shape[:2]
    if effect == "blur_mild":
        return cv2.GaussianBlur(image_bgr, (3, 3), sigmaX=0.75), gt_mask

    spec = config.TEMPLATE["fields"][case["field_name"]]
    centre_y = int(spec["row_y"] * height)
    half_height = max(8, int(spec["row_height"] * height * 0.45))
    y0, y1 = max(0, centre_y - half_height), min(height, centre_y + half_height)

    if effect == "contrast_scratch_medium":
        image_bgr = cv2.convertScaleAbs(image_bgr, alpha=0.62, beta=78)
        scratch_mask = np.zeros((height, width), dtype=np.uint8)
        start = (int(width * 0.15), y0 + max(2, half_height // 3))
        end = (int(width * 0.56), y1 - max(2, half_height // 3))
        cv2.line(image_bgr, start, end, (112, 91, 72), 2, lineType=cv2.LINE_AA)
        cv2.line(scratch_mask, start, end, 255, 2, lineType=cv2.LINE_AA)
        return image_bgr, np.maximum(gt_mask, scratch_mask)

    if effect == "jpeg_occlusion_severe":
        x0, x1 = int(width * 0.46), int(config.TEMPLATE["value_x_range"][1] * width)
        cv2.rectangle(image_bgr, (x0, y0), (x1 - 1, y1 - 1), (72, 64, 57), thickness=-1)
        occlusion = np.zeros((height, width), dtype=np.uint8)
        cv2.rectangle(occlusion, (x0, y0), (x1 - 1, y1 - 1), 255, thickness=-1)
        return image_bgr, np.maximum(gt_mask, occlusion)

    raise ValueError(f"unknown image effect {effect!r} in case {case['case_id']}")


def _encode_image(image_bgr: np.ndarray, image_format: str, jpeg_quality: int | None) -> bytes:
    """Encode the exact bytes that are both saved and passed to run_pipeline."""
    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    image = Image.fromarray(rgb)
    buffer = BytesIO()
    if image_format == "JPEG":
        image.save(buffer, format="JPEG", quality=int(jpeg_quality or 88), subsampling=0, optimize=False)
    elif image_format == "PNG":
        image.save(buffer, format="PNG", compress_level=6, optimize=False)
    else:
        raise ValueError(f"unsupported evaluation image format {image_format!r}")
    return buffer.getvalue()


def _save_case_assets(
    case: dict[str, Any], image_bytes: bytes, gt_mask: np.ndarray, asset_root: Path
) -> tuple[Path, Path, str, str]:
    image_path = _safe_asset_path(case["image_path"], asset_root)
    mask_path = _safe_asset_path(case["damage_mask_path"], asset_root)
    image_path.parent.mkdir(parents=True, exist_ok=True)
    mask_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(image_bytes)
    mask_buffer = BytesIO()
    Image.fromarray(gt_mask.astype(np.uint8), mode="L").save(mask_buffer, format="PNG", compress_level=6)
    mask_bytes = mask_buffer.getvalue()
    mask_path.write_bytes(mask_bytes)
    return (
        image_path,
        mask_path,
        hashlib.sha256(image_bytes).hexdigest(),
        hashlib.sha256(mask_bytes).hexdigest(),
    )


def _pattern_validation(field_name: str, observed_value: str | None) -> dict[str, Any]:
    """Report current deterministic shape gates without changing production code."""
    spec = config.TEMPLATE["fields"][field_name]
    if not observed_value:
        return {
            "result": "UNKNOWN",
            "pattern": spec["pattern"],
            "pattern_valid": None,
            "alnum_characters": 0,
            "minimum_alnum_characters": spec["min_chars"],
            "tokens": 0,
            "minimum_tokens": spec["min_tokens"],
            "truncation_marker": None,
            "findings": [],
            "note": "No mapped OCR text; semantic correctness is not inferred.",
        }

    pattern_valid = re.fullmatch(spec["pattern"], observed_value.strip().upper()) is not None
    alnum_count = sum(char.isalnum() for char in observed_value)
    token_count = len([token for token in re.split(r"\s+", observed_value.strip()) if token])
    stripped = observed_value.strip()
    marker = stripped[-1] if stripped and stripped[-1] in config.TRUNCATION_MARKERS else None
    findings = []
    if alnum_count < spec["min_chars"]:
        findings.append("MINIMUM_ALNUM_CHARS_FAILED")
    if token_count < spec["min_tokens"]:
        findings.append("MINIMUM_TOKENS_FAILED")
    if not pattern_valid:
        findings.append("PATTERN_FAILED")
    if marker:
        findings.append("TRUNCATION_MARKER_PRESENT")
    return {
        "result": "FAIL" if findings else "PASS",
        "pattern": spec["pattern"],
        "pattern_valid": pattern_valid,
        "alnum_characters": alnum_count,
        "minimum_alnum_characters": spec["min_chars"],
        "tokens": token_count,
        "minimum_tokens": spec["min_tokens"],
        "truncation_marker": marker,
        "findings": findings,
        "note": "These are the current template shape checks, not proof of semantic correctness.",
    }


def _outcome(
    *, status: str | None, claimed_value: str | None, observed_value: str | None, ground_truth: str
) -> str:
    if status is None:
        return "PIPELINE_ERROR"
    if status == config.STATUS_RECOVERED:
        return "CORRECT_RECOVERY" if claimed_value == ground_truth else "FALSE_RECOVERY"
    if claimed_value is not None:
        return "OTHER_AMBIGUOUS"
    return "INCORRECT_REJECTION" if observed_value == ground_truth else "CORRECT_ABSTENTION"


def _mask_coverage(mask: np.ndarray, bbox: list[int] | None) -> dict[str, float | None]:
    page = float((mask > 0).mean()) if mask.size else 0.0
    if not bbox:
        return {"page_fraction_any": round(page, 6), "target_region_fraction_any": None, "target_region_fraction_opaque": None}
    x0, y0, x1, y1 = [int(value) for value in bbox]
    h, w = mask.shape[:2]
    x0, x1 = max(0, min(w, x0)), max(0, min(w, x1))
    y0, y1 = max(0, min(h, y0)), max(0, min(h, y1))
    roi = mask[y0:y1, x0:x1]
    if not roi.size:
        return {"page_fraction_any": round(page, 6), "target_region_fraction_any": None, "target_region_fraction_opaque": None}
    return {
        "page_fraction_any": round(page, 6),
        "target_region_fraction_any": round(float((roi > 0).mean()), 6),
        "target_region_fraction_opaque": round(float((roi >= 128).mean()), 6),
    }


def _field_result_record(
    case: dict[str, Any],
    pipeline_result: PipelineResult,
    gt_mask: np.ndarray,
    runtime_seconds: float,
    image_sha256: str,
    mask_sha256: str,
    image_path: Path,
    mask_path: Path,
) -> dict[str, Any]:
    field_name = case["field_name"]
    field_result = pipeline_result.document.field(field_name)
    observed_value = field_result.raw_ocr_text or None
    claimed_value = field_result.value
    gt = case["ground_truth"]
    linked_observations = {obs.observation_id: obs.to_dict() for obs in pipeline_result.observations}
    ocr_evidence = []
    for item in field_result.evidence:
        linked = linked_observations.get(item.get("observation_id"), {})
        ocr_evidence.append({**item, "polygon": linked.get("polygon")})

    stage_rows = [
        {"key": stage.key, "ok": stage.ok, "seconds": round(float(stage.seconds), 4), "detail": stage.detail}
        for stage in pipeline_result.stages
    ]
    page_obscured = pipeline_result.document.processing.get("page_obscured_fraction")
    return {
        "case_id": case["case_id"],
        "source_id": case["source_id"],
        "field_name": field_name,
        "corruption_type": case["corruption_type"],
        "severity": case["severity"],
        "seed": int(case["seed"]),
        "preprocessing_variant": case["preprocessing_variant"],
        "image": {
            "path": image_path.relative_to(ROOT).as_posix() if ROOT in image_path.parents else str(image_path),
            "sha256": image_sha256,
            "format": case["image_format"],
            "damage_mask_path": mask_path.relative_to(ROOT).as_posix() if ROOT in mask_path.parents else str(mask_path),
            "damage_mask_sha256": mask_sha256,
            "synthetic_damage_mask": _mask_coverage(gt_mask, field_result.field_region_bbox),
        },
        "pipeline": {
            "ok": pipeline_result.ok,
            "error": pipeline_result.error,
            "runtime_seconds": round(runtime_seconds, 4),
            "ocr_runtime_seconds": pipeline_result.document.processing.get("ocr_seconds"),
            "ocr_engine": pipeline_result.document.audit.get("ocr_engine"),
            "preprocess_steps": pipeline_result.document.processing.get("preprocess_steps", []),
            "page_obscured_fraction_detected": page_obscured,
            "stages": stage_rows,
        },
        "field_result": {
            "status": field_result.status,
            "observed_value": observed_value,
            "claimed_value": claimed_value,
            "ocr_confidence": field_result.ocr_confidence,
            "confidence_bucket": field_result.confidence_bucket,
            "needs_verification": field_result.needs_verification,
            "validation": _pattern_validation(field_name, observed_value),
            "damage_evidence": {
                "field_region_bbox": field_result.field_region_bbox,
                "evidence_bbox": field_result.evidence_bbox,
                "damage_probe_bbox": field_result.damage_bbox,
                "obscuration_in_zone": round(float(field_result.obscuration_in_zone), 6),
                "adjacent_obscuration": round(float(field_result.adjacent_obscuration), 6),
            },
            "reasons": list(field_result.reasons),
            "evidence": ocr_evidence,
        },
        "evaluation": {
            "ground_truth": gt,
            "rendered_target_value": case["rendered_target_value"],
            "rendered_value_differs_from_ground_truth": case["rendered_target_value"] != gt,
            "ocr_matches_ground_truth": observed_value == gt,
            "ocr_matches_rendered_target": observed_value == case["rendered_target_value"],
            "outcome": _outcome(
                status=field_result.status,
                claimed_value=claimed_value,
                observed_value=observed_value,
                ground_truth=gt,
            ),
        },
    }


def _failed_case_record(
    case: dict[str, Any],
    error: str,
    runtime_seconds: float,
    image_sha256: str,
    mask_sha256: str,
    image_path: Path,
    mask_path: Path,
) -> dict[str, Any]:
    """Keep case identity and error evidence if the pipeline raises unexpectedly."""
    return {
        "case_id": case["case_id"],
        "source_id": case["source_id"],
        "field_name": case["field_name"],
        "corruption_type": case["corruption_type"],
        "severity": case["severity"],
        "seed": int(case["seed"]),
        "preprocessing_variant": case["preprocessing_variant"],
        "image": {
            "path": image_path.relative_to(ROOT).as_posix() if ROOT in image_path.parents else str(image_path),
            "sha256": image_sha256,
            "format": case["image_format"],
            "damage_mask_path": mask_path.relative_to(ROOT).as_posix() if ROOT in mask_path.parents else str(mask_path),
            "damage_mask_sha256": mask_sha256,
        },
        "pipeline": {"ok": False, "error": error, "runtime_seconds": round(runtime_seconds, 4)},
        "field_result": None,
        "evaluation": {
            "ground_truth": case["ground_truth"],
            "rendered_target_value": case["rendered_target_value"],
            "rendered_value_differs_from_ground_truth": case["rendered_target_value"] != case["ground_truth"],
            "ocr_matches_ground_truth": None,
            "ocr_matches_rendered_target": None,
            "outcome": "PIPELINE_ERROR",
        },
    }


def _repository_state() -> dict[str, Any]:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"], cwd=ROOT, check=True, capture_output=True, text=True
            ).stdout.strip()
        )
        return {"revision": revision, "worktree_dirty": dirty}
    except (OSError, subprocess.CalledProcessError):
        return {"revision": None, "worktree_dirty": None}


def _summarize(results: list[dict[str, Any]], source_count: int) -> dict[str, Any]:
    outcomes = Counter(row["evaluation"]["outcome"] for row in results)
    statuses = Counter(
        row["field_result"]["status"]
        for row in results
        if row.get("field_result") is not None and row["field_result"].get("status") is not None
    )
    recovered = statuses[config.STATUS_RECOVERED]
    correct = outcomes["CORRECT_RECOVERY"]
    false = outcomes["FALSE_RECOVERY"]
    successful_runs = len(results) - outcomes["PIPELINE_ERROR"]
    per_type: dict[str, Counter[str]] = defaultdict(Counter)
    for row in results:
        per_type[row["corruption_type"]][row["evaluation"]["outcome"]] += 1

    def ratio(numerator: int, denominator: int) -> float | None:
        return round(numerator / denominator, 6) if denominator else None

    return {
        "source_count": source_count,
        "case_count": len(results),
        "pipeline_errors": outcomes["PIPELINE_ERROR"],
        "outcomes": dict(sorted(outcomes.items())),
        "statuses": dict(sorted(statuses.items())),
        "correct_recovery_count": correct,
        "false_recovery_count": false,
        "correct_abstention_count": outcomes["CORRECT_ABSTENTION"],
        "incorrect_rejection_count": outcomes["INCORRECT_REJECTION"],
        "recovery_coverage": {
            "numerator_recovered": recovered,
            "denominator_pipeline_success": successful_runs,
            "value": ratio(recovered, successful_runs),
        },
        "exact_match": {
            "numerator_correct_recovery": correct,
            "denominator_pipeline_success": successful_runs,
            "value": ratio(correct, successful_runs),
        },
        "false_recovery_rate_among_recovered": {
            "numerator_false_recoveries": false,
            "denominator_recovered": recovered,
            "value": ratio(false, recovered),
        },
        "false_recovery_case_ids": [row["case_id"] for row in results if row["evaluation"]["outcome"] == "FALSE_RECOVERY"],
        "false_recoveries_by_rendered_content": {
            "ocr_matches_rendered_wrong_value": sum(
                row["evaluation"]["outcome"] == "FALSE_RECOVERY"
                and row["evaluation"]["rendered_value_differs_from_ground_truth"]
                and row["evaluation"]["ocr_matches_rendered_target"]
                for row in results
            ),
            "ocr_differs_from_rendered_value": sum(
                row["evaluation"]["outcome"] == "FALSE_RECOVERY"
                and not row["evaluation"]["ocr_matches_rendered_target"]
                for row in results
            ),
        },
        "outcomes_by_corruption_type": {
            key: dict(sorted(counter.items())) for key, counter in sorted(per_type.items())
        },
    }


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp_path.replace(path)


def run_evaluation(
    manifest: dict[str, Any] | None = None,
    *,
    asset_root: Path = ASSETS_PATH,
    limit: int | None = None,
    case_ids: set[str] | None = None,
    progress: bool = True,
) -> dict[str, Any]:
    """Generate cases, run unchanged end-to-end pipeline, and return evaluation-only results."""
    if manifest is None:
        manifest_bytes = MANIFEST_PATH.read_bytes()
        manifest = json.loads(manifest_bytes)
    else:
        manifest_bytes = json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode("utf-8")
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    cases = list(manifest["cases"])
    sources = {source["source_id"]: source for source in manifest["sources"]}
    ids = [case["case_id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("case_id values must be unique")
    if case_ids is not None:
        unknown = case_ids - set(ids)
        if unknown:
            raise ValueError(f"unknown case IDs: {sorted(unknown)}")
        cases = [case for case in cases if case["case_id"] in case_ids]
    if limit is not None:
        cases = cases[: max(0, limit)]
    if not cases:
        raise ValueError("no image-level evaluation cases selected")

    rows: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        if case["source_id"] not in sources:
            raise ValueError(f"unknown source_id in {case['case_id']}: {case['source_id']}")
        image_bytes, gt_mask = _render_and_encode(case, sources[case["source_id"]])
        image_path, mask_path, image_hash, mask_hash = _save_case_assets(case, image_bytes, gt_mask, asset_root)
        started = time.perf_counter()
        try:
            # No case metadata or truth is passed to production: run_pipeline receives only pixels.
            pipeline_result = run_pipeline(image_bytes, use_ai=False)
            elapsed = time.perf_counter() - started
            if pipeline_result.document.fields:
                row = _field_result_record(
                    case, pipeline_result, gt_mask, elapsed, image_hash, mask_hash, image_path, mask_path
                )
            else:
                row = _failed_case_record(
                    case,
                    pipeline_result.error or "Pipeline returned no field results.",
                    elapsed,
                    image_hash,
                    mask_hash,
                    image_path,
                    mask_path,
                )
        except Exception as exc:  # noqa: BLE001 - preserve each unexpected case failure in the corpus
            elapsed = time.perf_counter() - started
            row = _failed_case_record(
                case,
                f"{type(exc).__name__}: {exc}",
                elapsed,
                image_hash,
                mask_hash,
                image_path,
                mask_path,
            )
        rows.append(row)
        if progress:
            result = row["evaluation"]["outcome"]
            observed = None if row["field_result"] is None else row["field_result"]["observed_value"]
            print(f"[{index}/{len(cases)}] {case['case_id']}: {result}; observed={observed!r}", flush=True)
        if "pipeline_result" in locals():
            del pipeline_result

    source_ids = {case["source_id"] for case in cases}
    repository = _repository_state()
    return {
        "schema_version": 1,
        "evaluation_version": manifest["evaluation_version"],
        "title": manifest["title"],
        "scope": manifest["scope"],
        "ground_truth_policy": manifest["ground_truth_policy"],
        "image_corruption_policy": manifest["image_corruption_policy"],
        "method": "generated_synthetic_images_through_run_pipeline",
        "run_started_at_utc": datetime.now(timezone.utc).isoformat(),
        "versions": {
            "code_version": config.CODE_VERSION,
            "pipeline_version": config.CODE_VERSION,
            "template": config.TEMPLATE_NAME,
            "ocr_engine": config.OCR_ENGINE_NAME,
            "repository_revision": repository["revision"],
            "repository_worktree_dirty": repository["worktree_dirty"],
            "source_sha256": {
                relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
                for relative in ("src/ocr.py", "src/pipeline.py", "src/damage.py", "src/fields.py", "src/classifier.py", "src/config.py", "tools/make_demo_docs.py")
            },
            "manifest_sha256": manifest_sha,
        },
        "summary": _summarize(rows, len(source_ids)),
        "cases": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="run only the first N cases (smoke/debug)")
    parser.add_argument("--case-id", action="append", dest="case_ids", help="select a case by ID; may be repeated")
    parser.add_argument("--results", type=Path, default=RESULTS_PATH, help="JSON results output path")
    parser.add_argument("--assets-dir", type=Path, default=ASSETS_PATH, help="where generated images/masks are saved")
    args = parser.parse_args()

    result = run_evaluation(
        asset_root=args.assets_dir,
        limit=args.limit,
        case_ids=set(args.case_ids) if args.case_ids else None,
    )
    _atomic_json_write(args.results, result)
    print(f"Wrote {len(result['cases'])} image-level case results to {args.results}")
    print(json.dumps(result["summary"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
