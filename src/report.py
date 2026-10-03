"""
Evidence report generation: JSON (full machine-readable record) and PDF (human-readable).

Both reports contain the same deterministic content. The PDF always carries the
disclaimer and the provenance block, and never presents AI commentary as recovered data.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from xml.sax.saxutils import escape as xml_escape

import numpy as np

from . import config, evidence

PDF_AVAILABLE = True
try:  # reportlab is optional - JSON export must always work
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Image as RLImage,
    )
    from reportlab.platypus import (
        KeepTogether,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )
except Exception:  # noqa: BLE001 - optional ReportLab import must not disable JSON export
    PDF_AVAILABLE = False


STATUS_COLOR_HEX = {
    config.STATUS_RECOVERED: "#2e7d32",
    config.STATUS_PARTIAL: "#b26a00",
    config.STATUS_UNRECOVERABLE: "#b3261e",
}


def to_json_bytes(document: evidence.EvidenceDocument) -> bytes:
    """Serialise the evidence document to pretty-printed JSON bytes."""
    return json.dumps(document.to_dict(), indent=2, ensure_ascii=False).encode("utf-8")


def _safe_paragraph_text(value: object) -> str:
    """Escape untrusted content before inserting it into ReportLab paragraph markup."""
    return xml_escape(str(value))


def _bgr_to_png_bytes(image: np.ndarray) -> bytes:
    import cv2

    ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("Could not encode image for the PDF report.")
    return buf.tobytes()


def to_pdf_bytes(
    document: evidence.EvidenceDocument,
    original_bgr: np.ndarray | None,
    annotated_bgr: np.ndarray | None,
    annotation_for: str = "all",
) -> bytes:
    """Build the PDF evidence report. Raises RuntimeError when reportlab is unavailable."""
    if not PDF_AVAILABLE:
        raise RuntimeError("PDF export requires reportlab (pip install reportlab).")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"DisasterDoc evidence report - {document.document_id[:24]}",
        author="DisasterDoc",
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=18, spaceAfter=2, textColor=colors.HexColor("#12263a"))
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=12.5, spaceBefore=10, spaceAfter=4,
                        textColor=colors.HexColor("#12263a"))
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9.2, leading=12.4)
    small = ParagraphStyle("small", parent=styles["BodyText"], fontSize=7.8, leading=10, textColor=colors.HexColor("#555555"))

    story: list = []
    story.append(Paragraph("DisasterDoc - evidence report", h1))
    story.append(Paragraph("Evidence-first document recovery. <b>If the document doesn't show it, "
                           "DisasterDoc doesn't claim it.</b>", body))
    story.append(Paragraph(
        "RECOVERED means the current deterministic checks passed; it is not independent verification, "
        "authenticity, or a probability that the value is correct. OCR confidence is an engine score.",
        small,
    ))
    story.append(Spacer(1, 6))

    summary = document.summary
    meta_rows = [
        ["Document ID", document.document_id],
        ["Processed at (UTC)", document.processed_at],
        ["Template", document.template],
        ["OCR engine", str(document.audit.get("ocr_engine"))],
        [
            "AI commentary",
            (
                f"{'used - commentary only' if document.audit.get('ai_used') else 'not used'}"
                f"{' (' + str(document.audit.get('ai_model')) + ')' if document.audit.get('ai_used') else ''}"
            ),
        ],
        [
            "Fields",
            (
                f"{summary.get('recovered', 0)} recovered / {summary.get('partial', 0)} partial / "
                f"{summary.get('unrecoverable', 0)} unrecoverable"
            ),
        ],
        ["Requires human verification", f"{summary.get('needs_verification', 0)} of {summary.get('total', 0)} fields"],
        ["Code version", str(document.audit.get("code_version") or config.CODE_VERSION)],
        ["Evidence schema", str(config.EVIDENCE_SCHEMA_VERSION)],
        ["Configuration version", config.CONFIG_VERSION],
        ["Template version", config.TEMPLATE_VERSION],
    ]
    meta = Table(
        [[Paragraph(f"<b>{_safe_paragraph_text(k)}</b>", body), Paragraph(_safe_paragraph_text(v), body)]
         for k, v in meta_rows],
        colWidths=[42 * mm, 132 * mm],
    )
    meta.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#e2e6ea")),
    ]))
    story.append(meta)
    story.append(Spacer(1, 8))

    # ---- document images ----
    if original_bgr is not None:  # `annotation_for` kept for signature clarity in the UI
        for title, image in (("Original document (as received)", original_bgr),
                             ("Annotated evidence regions", annotated_bgr)):
            if image is None:
                continue
            story.append(Paragraph(title, h2))
            png = _bgr_to_png_bytes(image)
            iw, ih = image.shape[1], image.shape[0]
            width = 174 * mm
            # Keep image bytes in memory; do not leave named temporary files behind.
            story.append(RLImage(io.BytesIO(png), width=width, height=width * ih / iw))
            story.append(Spacer(1, 6))

    story.append(PageBreak())
    story.append(Paragraph("Field-level findings", h2))
    story.append(Paragraph(
        "Every field below is classified by deterministic code from OCR evidence only. "
        "For PARTIAL and UNRECOVERABLE fields no value is reported, by design.", body))
    story.append(Spacer(1, 4))

    header = [Paragraph(f"<b>{h}</b>", body) for h in ("Field", "Status", "Observed", "Claimed", "Evidence")]

    for f in document.fields:
        status_colour = STATUS_COLOR_HEX.get(f.status, "#333333")
        observed_text = _safe_paragraph_text(f.observed_value or "No readable text")
        claimed_text = _safe_paragraph_text(f.claimed_value if f.claimed_value is not None else "Not claimed")
        observed_cell = Paragraph(observed_text, body)
        claimed_cell = Paragraph(f"<b>{claimed_text}</b>", body)
        ev_bits = []
        for item in f.evidence:
            ev_bits.append(
                f"{_safe_paragraph_text(item.get('observation_id', ''))}: "
                f"bbox {_safe_paragraph_text(item.get('bbox', ''))} "
                f"OCR conf {_safe_paragraph_text(item.get('ocr_confidence', ''))} "
                f"\u201c{_safe_paragraph_text(item.get('text', ''))}\u201d"
            )
        damage = f.damage_evidence.to_dict()
        if damage["evaluated"]:
            ev_bits.append(
                "Damage: field-zone obscuration "
                f"{_safe_paragraph_text(damage['obscuration_ratio'])}; adjacent obscuration "
                f"{_safe_paragraph_text(damage['adjacent_obscuration_ratio'])}."
            )
        else:
            ev_bits.append("Local damage ratios were not evaluated because usable OCR evidence was absent.")
        if f.validation_evidence:
            ev_bits.append(
                f"Validation result: {_safe_paragraph_text(f.validation_result)}."
            )
            ev_bits.append(
                "Validation checks: "
                + ", ".join(
                    f"{_safe_paragraph_text(item.validator)}={_safe_paragraph_text(item.result)} "
                    f"({_safe_paragraph_text(item.reason)})"
                    for item in f.validation_evidence
                )
            )
        ev_bits.append(f"Heuristic evidence bucket: {_safe_paragraph_text(f.confidence_bucket)}.")
        evidence_cell = Paragraph("<br/>".join(ev_bits), small)

        contract = f.to_dict()
        code_lines = [
            f"<b>{_safe_paragraph_text(code)}</b>: {_safe_paragraph_text(description)}"
            for code, description in zip(contract["reason_codes"], contract["reason_descriptions"])
        ]
        detail_lines = [_safe_paragraph_text(reason) for reason in f.reasons]
        reason_content = "<b>Reason codes</b><br/>" + (
            "<br/>".join(code_lines) if code_lines else "None recorded."
        )
        if detail_lines:
            reason_content += "<br/><b>Decision details</b><br/>" + "<br/>".join(detail_lines)
        reason_cell = Paragraph(reason_content, small)

        rows = [
            header,
            [
                Paragraph(f"<b>{_safe_paragraph_text(f.label)}</b>", body),
                Paragraph(f"<font color='{status_colour}'><b>{_safe_paragraph_text(f.status)}</b></font>", body),
                observed_cell,
                claimed_cell,
                evidence_cell,
            ],
            [reason_cell, "", "", "", ""],
        ]
        table = Table(rows, colWidths=[22 * mm, 23 * mm, 39 * mm, 37 * mm, 53 * mm])
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dfe4e8")),
            ("SPAN", (0, 2), (-1, 2)),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f5f7")),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(KeepTogether([table, Spacer(1, 5)]))

        if f.ai_commentary:
            ai_rows = [
                [Paragraph("<b>AI ASSISTANCE (advisory only \u2014 not document evidence)</b>",
                           ParagraphStyle("aih", parent=body, textColor=colors.HexColor("#6a4b00")))],
            ]
            interpretations = f.ai_commentary.get("possible_interpretations", [])
            if isinstance(interpretations, list) and interpretations:
                safe_interpretations = ", ".join(_safe_paragraph_text(item) for item in interpretations)
                ai_rows.append([Paragraph("Possible interpretations: " + safe_interpretations, small)])
            if f.ai_commentary.get("commentary"):
                ai_rows.append([Paragraph(_safe_paragraph_text(f.ai_commentary["commentary"]), small)])
            if f.ai_commentary.get("verification_instruction"):
                ai_rows.append([
                    Paragraph(
                        "Suggested verification: "
                        + _safe_paragraph_text(f.ai_commentary["verification_instruction"]),
                        small,
                    )
                ])
            ai_table = Table(ai_rows, colWidths=[174 * mm])
            ai_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fff8e6")),
                ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#e0b64a")),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            story.append(KeepTogether([ai_table, Spacer(1, 6)]))

    story.append(Paragraph("Human verification checklist", h2))
    checklist_rows = [[Paragraph("Done", body), Paragraph("Task", body)]]
    for task in document.verification_tasks:
        checklist_rows.append([Paragraph("\u2610", body), Paragraph(_safe_paragraph_text(task.get("text", "")), body)])
    checklist = Table(checklist_rows, colWidths=[14 * mm, 160 * mm])
    checklist.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dfe4e8")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f5f7")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(checklist)

    story.append(Paragraph("Raw OCR observations (machine-observed evidence, unmodified)", h2))
    raw_rows = [[Paragraph(f"<b>{h}</b>", small) for h in ("ID", "bbox [x0,y0,x1,y1]", "OCR conf.", "Text (verbatim)")]]
    for observation in document.ocr_observations_raw:
        raw_rows.append([
            Paragraph(_safe_paragraph_text(observation.get("observation_id", "")), small),
            Paragraph(_safe_paragraph_text(observation.get("bbox", "")), small),
            Paragraph(_safe_paragraph_text(observation.get("ocr_confidence", "")), small),
            Paragraph(_safe_paragraph_text(observation.get("text", "")), small),
        ])
    if len(raw_rows) == 1:
        raw_rows.append([Paragraph("no OCR observations available", small), "", "", ""])
    raw_table = Table(raw_rows, colWidths=[14 * mm, 40 * mm, 18 * mm, 102 * mm])
    raw_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e5e9ec")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f5f7")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(raw_table)

    if document.audit.get("ai_guardrail_notes"):
        story.append(Paragraph("AI guardrail log", h2))
        for note in document.audit["ai_guardrail_notes"]:
            story.append(Paragraph(f"\u2022 {_safe_paragraph_text(note)}", small))

    story.append(Spacer(1, 8))
    story.append(Paragraph("Provenance and disclaimer", h2))
    story.append(Paragraph(
        "The document ID is the SHA-256 digest of the exact file that was processed. It identifies the "
        "processed file for integrity and provenance purposes; it does <b>not</b> prove that the document "
        "is authentic or legally valid.", small))
    story.append(Paragraph(_safe_paragraph_text(document.disclaimer), small))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f"Generated by DisasterDoc ({config.CODE_VERSION}) at {datetime.now(timezone.utc).isoformat(timespec='seconds')} "
        "\u00b7 Synthetic demo documents only \u00b7 No government database was queried.",
        small))

    doc.build(story)
    return buf.getvalue()


__all__ = ["PDF_AVAILABLE", "STATUS_COLOR_HEX", "to_json_bytes", "to_pdf_bytes"]
