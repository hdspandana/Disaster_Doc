"""
Evidence assembly: the structured evidence document, the annotated image, the
human-verification queue and guardrailed AI merging.

This module is the only place where the AI layer may attach field commentary, and it
can only write `ai_commentary` (never an observation, claim, status, reason code,
OCR/damage/validation evidence, or confidence).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import cv2
import numpy as np

from . import config
from .classifier import FieldResult

# Status colours (BGR) - green / amber / red
STATUS_COLOURS = {
    config.STATUS_RECOVERED: (94, 197, 34),
    config.STATUS_PARTIAL: (11, 158, 245),
    config.STATUS_UNRECOVERABLE: (68, 68, 239),
}
STATUS_ICONS = {
    config.STATUS_RECOVERED: "\U0001F7E2",  # 🟢
    config.STATUS_PARTIAL: "\U0001F7E1",  # 🟡
    config.STATUS_UNRECOVERABLE: "\U0001F534",  # 🔴
}
STATUS_LABELS = {
    config.STATUS_RECOVERED: "RECOVERED",
    config.STATUS_PARTIAL: "PARTIAL",
    config.STATUS_UNRECOVERABLE: "UNRECOVERABLE",
}

# AI output keys that are ignored and logged: the AI may not decide or rewrite evidence.
FORBIDDEN_AI_KEYS = (
    "value",
    "claimed_value",
    "observed_value",
    "raw_ocr_text",
    "status",
    "reason_codes",
    "reason_descriptions",
    "deterministic_reasons",
    "reasons",
    "evidence",
    "ocr_evidence",
    "validation_evidence",
    "validation_result",
    "validation_status",
    "validation_reason_codes",
    "validator_results",
    "validators",
    "dob_calendar",
    "validation",
    "damage_evidence",
    "confidence_bucket",
    "ocr_confidence",
    "bbox",
    "evidence_bbox",
    "field_region_bbox",
    "damage_bbox",
    "obscuration_in_zone",
    "adjacent_obscuration",
    "needs_verification",
    "evidence_schema_version",
    "config_version",
    "template_version",
    "code_version",
    "metadata",
    "ground_truth",
)


@dataclass
class EvidenceDocument:
    """The complete, exportable evidence record for one processed document."""

    document_id: str
    processed_at: str
    template: str
    fields: list[FieldResult]
    ocr_observations_raw: list[dict]
    audit: dict[str, Any]
    summary: dict[str, int] = field(default_factory=dict)
    verification_tasks: list[dict] = field(default_factory=list)
    disclaimer: str = config.DISCLAIMER
    processing: dict[str, Any] = field(default_factory=dict)

    def field(self, name: str) -> FieldResult:
        return next(f for f in self.fields if f.field_name == name)

    def to_dict(self) -> dict:
        return {
            "evidence_schema_version": config.EVIDENCE_SCHEMA_VERSION,
            "metadata": {
                "code_version": self.audit.get("code_version") or config.CODE_VERSION,
                "config_version": config.CONFIG_VERSION,
                "template_version": config.TEMPLATE_VERSION,
                "template": self.template,
                "ocr_engine": self.audit.get("ocr_engine"),
            },
            "document_id": self.document_id,
            "processed_at": self.processed_at,
            "template": self.template,
            "summary": self.summary,
            "fields": [f.to_dict() for f in self.fields],
            "verification_tasks": self.verification_tasks,
            "ocr_observations_raw": self.ocr_observations_raw,
            "audit": self.audit,
            "processing": self.processing,
            "disclaimer": self.disclaimer,
        }


def summarize(fields: list[FieldResult]) -> dict[str, int]:
    """Count fields per status."""
    return {
        "total": len(fields),
        "recovered": sum(f.status == config.STATUS_RECOVERED for f in fields),
        "partial": sum(f.status == config.STATUS_PARTIAL for f in fields),
        "unrecoverable": sum(f.status == config.STATUS_UNRECOVERABLE for f in fields),
        "needs_verification": sum(f.needs_verification for f in fields),
    }


def build_verification_tasks(fields: list[FieldResult]) -> list[dict]:
    """Human-verification checklist derived ONLY from deterministic statuses."""
    tasks: list[dict] = []
    for f in fields:
        if f.status == config.STATUS_UNRECOVERABLE:
            tasks.append(
                {
                    "task_id": f"verify_{f.field_name}_insufficient",
                    "field_name": f.field_name,
                    "text": f"{f.label}: insufficient evidence on the document - {f.verification_instruction}",
                    "priority": "high",
                }
            )
        elif f.status == config.STATUS_PARTIAL:
            observed = f.raw_ocr_text or "no readable text"
            tasks.append(
                {
                    "task_id": f"verify_{f.field_name}_partial",
                    "field_name": f.field_name,
                    "text": (
                        f"{f.label}: document shows only \u201c{observed}\u201d - "
                        f"{f.verification_instruction}"
                    ),
                    "priority": "high",
                }
            )
    tasks.append(
        {
            "task_id": "verify_manual_inspection",
            "field_name": None,
            "text": "Manually inspect the original document under good lighting before any decision is made.",
            "priority": "medium",
        }
    )
    tasks.append(
        {
            "task_id": "verify_no_auto_accept",
            "field_name": None,
            "text": "Do not treat any PARTIAL or UNRECOVERABLE field as established; DisasterDoc output is a recovery aid, not a determination.",
            "priority": "medium",
        }
    )
    return tasks


def attach_ai_commentary(
    fields: list[FieldResult], commentary: dict[str, dict], guardrail_notes: list[str] | None = None
) -> list[dict]:
    """Merge guardrailed AI commentary into fields.

    Only the `ai_commentary` slot is written. Observations, claims, status, reason
    codes, OCR/damage/validation evidence, confidence and geometry are never touched.
    Attempts to return those keys are recorded by name (not value) so the report can
    show that deterministic ownership was enforced at runtime.
    """
    events: list[dict] = []
    notes = list(guardrail_notes or [])
    for f in fields:
        entry = commentary.get(f.field_name)
        if not entry:
            continue
        if f.status == config.STATUS_RECOVERED:
            events.append(
                {
                    "field_name": f.field_name,
                    "event": "ai_commentary_skipped",
                    "detail": "Field is RECOVERED; AI commentary is only used for PARTIAL/UNRECOVERABLE fields.",
                }
            )
            continue
        raw = entry.get("_raw", {}) or {}
        for key in FORBIDDEN_AI_KEYS:
            if key in raw:
                notes.append(
                    f"AI response for '{f.field_name}' tried to return '{key}'; it was ignored - the AI layer "
                    "may only contribute commentary."
                )
        f.ai_commentary = {
            "possible_interpretations": entry.get("possible_interpretations", []),
            "verification_instruction": entry.get("verification_instruction") or f.verification_instruction,
            "commentary": entry.get("commentary", ""),
            "ai_generated": True,
            "advisory_only": True,
            "guardrail": "Possibilities only - NOT document evidence. Never used to set status or value.",
        }
        events.append(
            {"field_name": f.field_name, "event": "ai_commentary_attached", "detail": f.ai_commentary["commentary"]}
        )
    return events, notes


def _dashed_rect(canvas: np.ndarray, pt1: tuple[int, int], pt2: tuple[int, int], colour, thickness: int,
                 dash: int = 12) -> None:
    """Draw a dashed rectangle (used for template regions that yielded no usable evidence)."""
    x0, y0 = pt1
    x1, y1 = pt2
    for x in range(x0, x1, dash * 2):
        cv2.line(canvas, (x, y0), (min(x + dash, x1), y0), colour, thickness)
        cv2.line(canvas, (x, y1), (min(x + dash, x1), y1), colour, thickness)
    for y in range(y0, y1, dash * 2):
        cv2.line(canvas, (x0, y), (x0, min(y + dash, y1)), colour, thickness)
        cv2.line(canvas, (x1, y), (x1, min(y + dash, y1)), colour, thickness)


def render_annotation(
    bgr: np.ndarray,
    fields: list[FieldResult],
    selected_field: str | None = None,
    show_ocr_boxes: bool = True,
    observations: list[dict] | None = None,
) -> np.ndarray:
    """Draw evidence regions, OCR boxes and status colours onto a copy of the document."""
    canvas = bgr.copy()
    h, w = canvas.shape[:2]
    thickness = max(2, int(min(h, w) / 320))

    if show_ocr_boxes and observations:
        overlay = canvas.copy()
        for obs in observations:
            x0, y0, x1, y1 = [int(v) for v in obs["bbox"]]
            cv2.rectangle(overlay, (x0, y0), (x1, y1), (255, 255, 255), -1)
        canvas = cv2.addWeighted(overlay, 0.18, canvas, 0.82, 0)
        for obs in observations:
            if not obs.get("usable", True):
                continue
            x0, y0, x1, y1 = [int(v) for v in obs["bbox"]]
            cv2.rectangle(canvas, (x0, y0), (x1, y1), (200, 205, 210), max(1, thickness // 2))

    for f in fields:
        if not f.evidence_bbox:
            # No observation was accepted as evidence here. Showing the examined template
            # region (dashed) makes the negative result inspectable instead of invisible:
            # "we looked here and there is nothing usable" is itself evidence.
            if f.status == config.STATUS_UNRECOVERABLE and f.field_region_bbox:
                x0, y0, x1, y1 = [int(v) for v in f.field_region_bbox]
                _dashed_rect(canvas, (x0, y0), (x1, y1), STATUS_COLOURS[f.status], max(1, thickness))
                scale = max(0.38, min(h, w) / 2000.0)
                cv2.putText(canvas, f"{f.label} \u2014 no usable evidence", (x0 + 4, y0 + int(13 * scale * 2)),
                            cv2.FONT_HERSHEY_SIMPLEX, scale, STATUS_COLOURS[f.status],
                            max(1, thickness // 2), cv2.LINE_AA)
            continue
        colour = STATUS_COLOURS[f.status]
        selected = f.field_name == selected_field
        x0, y0, x1, y1 = [int(v) for v in f.evidence_bbox]
        pad = int(min(h, w) * 0.006)
        box = (max(0, x0 - pad), max(0, y0 - pad), min(w - 1, x1 + pad), min(h - 1, y1 + pad))
        cv2.rectangle(canvas, box[:2], box[2:], colour, thickness * (3 if selected else 2))

        if selected:
            cv2.rectangle(canvas, (box[0] - thickness, box[1] - thickness),
                          (box[2] + thickness, box[3] + thickness), (255, 255, 255), thickness)
            if f.status != config.STATUS_RECOVERED and f.damage_bbox:
                dx0, dy0, dx1, dy1 = [int(v) for v in f.damage_bbox]
                overlay = canvas.copy()
                cv2.rectangle(overlay, (dx0, dy0), (dx1, dy1), (60, 60, 235), -1)
                canvas = cv2.addWeighted(overlay, 0.30, canvas, 0.70, 0)
                cv2.rectangle(canvas, (dx0, dy0), (dx1, dy1), (60, 60, 235), max(1, thickness // 2))

        if f.status == config.STATUS_PARTIAL and f.evidence_bbox:
            # Always shown for PARTIAL fields: marks the exact column where the surviving
            # evidence stops, so a reviewer can see why the value is not complete.
            ex = int(f.evidence_bbox[2]) + int(w * 0.004)
            y_from = max(0, int(f.evidence_bbox[1]) - int(h * 0.025))
            y_to = min(h - 1, int(f.evidence_bbox[3]) + int(h * 0.025))
            for yy in range(y_from, y_to, max(8, thickness * 5)):
                cv2.line(canvas, (ex, yy), (ex, min(y_to, yy + max(4, thickness * 3))), (60, 60, 235),
                         thickness + 1)
            cv2.putText(canvas, "evidence ends here", (ex + int(w * 0.006), min(h - 4, y_to + int(h * 0.035))),
                        cv2.FONT_HERSHEY_SIMPLEX, max(0.34, min(h, w) / 2200.0), (60, 60, 235),
                        max(1, thickness // 2), cv2.LINE_AA)

        label = STATUS_LABELS[f.status]
        font_scale = max(0.4, min(h, w) / 1400.0)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, max(1, thickness // 2))
        chip_y = max(th + 6, box[1])
        cv2.rectangle(canvas, (box[0], chip_y - th - 6), (box[0] + tw + 12, chip_y), colour, -1)
        cv2.putText(canvas, label, (box[0] + 6, chip_y - 4), cv2.FONT_HERSHEY_SIMPLEX, font_scale,
                    (20, 20, 20), max(1, thickness // 2), cv2.LINE_AA)
    return canvas


def new_document(
    document_id: str,
    fields: list[FieldResult],
    observations: list[dict],
    audit: dict[str, Any],
    processing: dict[str, Any] | None = None,
) -> EvidenceDocument:
    """Assemble the evidence document from the deterministic pipeline output."""
    return EvidenceDocument(
        document_id=document_id,
        processed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        template=config.TEMPLATE_NAME,
        fields=fields,
        ocr_observations_raw=observations,
        audit=audit,
        summary=summarize(fields),
        verification_tasks=build_verification_tasks(fields),
        processing=processing or {},
    )


__all__ = [
    "STATUS_COLOURS",
    "STATUS_ICONS",
    "STATUS_LABELS",
    "EvidenceDocument",
    "attach_ai_commentary",
    "build_verification_tasks",
    "new_document",
    "render_annotation",
    "summarize",
]
