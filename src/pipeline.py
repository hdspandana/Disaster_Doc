"""
The DisasterDoc pipeline.

    damaged document
        -> hash (SHA-256 provenance)
        -> preprocessing
        -> OCR (raw observations: text + bbox + engine confidence)
        -> surface-damage map
        -> deterministic field mapping
        -> deterministic classification (RECOVERED / PARTIAL / UNRECOVERABLE)
        -> human verification queue
        -> [optional] AI commentary  <-- advisory only, cannot change facts
        -> evidence document (JSON / PDF)

Every stage is observable: `run_pipeline` reports per-stage status and timing, and the
UI renders those statuses directly instead of inventing progress theatre.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from . import ai_commentary, classifier, config, damage, evidence, hashing, ocr
from . import fields as field_mapping
from .preprocessing import InvalidImageError


@dataclass
class StageStatus:
    key: str
    label: str
    ok: bool
    detail: str = ""
    seconds: float = 0.0


@dataclass
class PipelineResult:
    """Everything the UI and the report generators need."""

    document_id: str
    document: evidence.EvidenceDocument
    stages: list[StageStatus]
    original_bgr: np.ndarray | None = None
    preprocessed_bgr: np.ndarray | None = None
    observations: list[ocr.Observation] = field(default_factory=list)
    damage_map: damage.DamageMap | None = None
    annotation: np.ndarray | None = None
    ai_status: str = ""
    ai_notes: list[str] = field(default_factory=list)
    ai_events: list[dict] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _stage(
    stages: list[StageStatus],
    key: str,
    label: str,
    started: float,
    ok: bool = True,
    detail: str = "",
    on_stage: Callable[[StageStatus], None] | None = None,
) -> None:
    """Record a stage result (and notify the caller, so the UI can show real progress)."""
    stage = StageStatus(key=key, label=label, ok=ok, detail=detail, seconds=round(time.time() - started, 3))
    stages.append(stage)
    if on_stage is not None:
        try:
            on_stage(stage)
        except Exception:  # a broken progress callback must never break the pipeline
            pass


def run_pipeline(
    image_bytes: bytes,
    use_ai: bool = False,
    on_stage: Callable[[StageStatus], None] | None = None,
) -> PipelineResult:
    """Run the full evidence pipeline on uploaded document bytes.

    Never raises for bad input or failing sub-systems: structural problems surface as
    `PipelineResult.error` (with a user-facing message) and partial failures degrade to
    UNRECOVERABLE fields rather than guesses.

    `on_stage` is called after each stage completes, so the UI can display the real
    pipeline sequence instead of a scripted animation.
    """
    stages: list[StageStatus] = []
    started = time.time()
    doc_id = hashing.document_id(image_bytes)
    _stage(stages, "hash", "Document hashed (SHA-256)", started, True, doc_id, on_stage)

    # ---- Decode/preprocess and OCR (timed as separate stages) --------------------------
    image_started = time.time()
    image_stage_recorded = False

    def _record_preprocessed(shape: tuple[int, int], steps: list[str]) -> None:
        nonlocal image_stage_recorded
        if image_stage_recorded:
            return
        height, width = shape
        _stage(
            stages,
            "image",
            "Image decoded and preprocessed",
            image_started,
            True,
            f"{width}x{height} px; " + ", ".join(steps),
            on_stage,
        )
        image_stage_recorded = True

    try:
        ocr_result, original_bgr, preprocessed_bgr = ocr.extract_evidence(
            image_bytes, on_preprocessed=_record_preprocessed
        )
    except InvalidImageError as exc:
        if not image_stage_recorded:
            _stage(stages, "image", "Image processed", image_started, False, str(exc), on_stage)
        return PipelineResult(
            document_id=doc_id,
            document=evidence.new_document(
                doc_id,
                [],
                [],
                {"ocr_engine": config.OCR_ENGINE_NAME, "ai_used": False, "code_version": config.CODE_VERSION},
            ),
            stages=stages,
            error=str(exc),
        )
    except Exception:  # unexpected decode/OCR failure - still must not crash
        if not image_stage_recorded:
            _stage(
                stages,
                "image",
                "Image processed",
                image_started,
                False,
                "The image could not be safely processed.",
                on_stage,
            )
        _stage(
            stages,
            "ocr",
            "OCR completed",
            time.time(),
            False,
            "The OCR stage could not be completed safely.",
            on_stage,
        )
        return PipelineResult(
            document_id=doc_id,
            document=evidence.new_document(
                doc_id, [], [],
                {"ocr_engine": config.OCR_ENGINE_NAME, "ai_used": False, "code_version": config.CODE_VERSION},
            ),
            stages=stages,
            error=(
                "Unable to obtain sufficient OCR evidence. The document could not be analysed, so no field "
                "values are reported."
            ),
        )

    if not image_stage_recorded:
        _record_preprocessed(original_bgr.shape[:2], ocr_result.preprocess_steps)

    ocr_started = time.time() - max(0.0, ocr_result.processing_seconds)
    if ocr_result.error:
        _stage(stages, "ocr", "OCR completed", ocr_started, False, ocr_result.error, on_stage)
    else:
        _stage(
            stages,
            "ocr",
            "OCR completed",
            ocr_started,
            True,
            f"{len(ocr_result.observations)} raw observations ({ocr_result.engine})",
            on_stage,
        )

    shape = original_bgr.shape[:2]

    # ---- Surface-damage map -----------------------------------------------------------
    started = time.time()
    damage_map = damage.build_damage_map(original_bgr)
    obscured_fraction = float((damage_map.obscured > 0).mean())
    _stage(stages, "damage", "Damaged regions identified", started, True,
           f"{obscured_fraction * 100:.1f}% of the page has no legible structure", on_stage)

    # ---- Field mapping ----------------------------------------------------------------
    started = time.time()
    mapped = field_mapping.map_fields(ocr_result.observations, shape)
    located = sum(1 for m in mapped.values() if m.has_evidence)
    _stage(stages, "mapping", "Fields mapped", started, True,
           f"{located}/{len(mapped)} fields have candidate evidence regions", on_stage)

    # ---- Deterministic classification -------------------------------------------------
    started = time.time()
    field_results = classifier.classify_all(mapped, damage_map, shape, ocr_available=not ocr_result.error)
    summary = evidence.summarize(field_results)
    _stage(stages, "classify", "Verification status generated", started, True,
           f"{summary['recovered']} recovered, {summary['partial']} partial, "
           f"{summary['unrecoverable']} unrecoverable", on_stage)

    # ---- Optional AI commentary (advisory only) ---------------------------------------
    ai_used = False
    ai_status = ""
    ai_notes: list[str] = []
    ai_events: list[dict] = []
    if use_ai:
        started = time.time()
        context = {
            "template": config.TEMPLATE_NAME,
            "document_id": doc_id,
            "page_obscured_fraction": round(obscured_fraction, 3),
        }
        commentary, notes, ai_error = ai_commentary.generate_commentary(field_results, context)
        if commentary:
            ai_events, merged_notes = evidence.attach_ai_commentary(field_results, commentary, notes)
            ai_notes = merged_notes
            ai_used = True
            ai_status = f"AI commentary generated for {len(commentary)} field(s)."
            _stage(stages, "ai", "AI commentary attached (advisory only)", started, True, ai_status, on_stage)
        else:
            ai_status = ai_error or "AI commentary unavailable."
            ai_notes = notes
            _stage(stages, "ai", "AI commentary skipped", started, False, ai_status, on_stage)
    else:
        ai_status = "AI commentary disabled for this run."
        _stage(stages, "ai", "AI commentary disabled", started, False, ai_status, on_stage)

    annotation = evidence.render_annotation(
        original_bgr, field_results, None, True, [o.to_dict() for o in ocr_result.observations]
    )

    document = evidence.new_document(
        document_id=doc_id,
        fields=field_results,
        observations=[o.to_dict() for o in ocr_result.observations],
        audit={
            "ocr_engine": ocr_result.engine,
            "ai_used": ai_used,
            "ai_model": config.GEMINI_MODEL if ai_used else None,
            "ai_role": "commentary_only",
            "ai_events": ai_events,
            "ai_guardrail_notes": ai_notes,
            "code_version": config.CODE_VERSION,
            "deterministic_layer": "src/classifier.py + src/damage.py + src/fields.py",
        },
        processing={
            "ocr_seconds": round(ocr_result.processing_seconds, 2),
            "preprocess_steps": ocr_result.preprocess_steps,
            "page_obscured_fraction": round(obscured_fraction, 4),
            "stages": [s.__dict__ for s in stages],
            "thresholds": {
                "min_usable_ocr_conf": config.MIN_USABLE_OCR_CONF,
                "high_conf": config.HIGH_CONF,
                "low_conf": config.LOW_CONF,
                "obscured_ratio_limit": config.OBSCURED_RATIO_LIMIT,
                "adjacent_obscuration_limit": config.ADJACENT_OBSCURATION_LIMIT,
            },
        },
    )

    return PipelineResult(
        document_id=doc_id,
        document=document,
        stages=stages,
        original_bgr=original_bgr,
        preprocessed_bgr=preprocessed_bgr,
        observations=ocr_result.observations,
        damage_map=damage_map,
        annotation=annotation,
        ai_status=ai_status,
        ai_notes=ai_notes,
        ai_events=ai_events,
    )


__all__ = ["PipelineResult", "StageStatus", "run_pipeline"]
