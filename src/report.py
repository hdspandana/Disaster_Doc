"""
Evidence report generation: JSON (full machine-readable record) and PDF (human-readable).

Both reports contain the same deterministic content. The PDF always carries the
disclaimer and the provenance block, and never presents AI commentary as recovered data.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone

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
        KeepTogether,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )
except Exception:  # pragma: no cover - depends on environment
    PDF_AVAILABLE = False


STATUS_COLOR_HEX = {
    config.STATUS_RECOVERED: "#2e7d32",
    config.STATUS_PARTIAL: "#b26a00",
    config.STATUS_UNRECOVERABLE: "#b3261e",
}


def to_json_bytes(document: evidence.EvidenceDocument) -> bytes:
    """Serialise the evidence document to pretty-printed JSON bytes."""
    return json.dumps(document.to_dict(), indent=2, ensure_ascii=False).encode("utf-8")


def _png_path(png_bytes: bytes) -> str:
    """Write encoded PNG bytes to a temp file and return the path (reportlab-safe)."""
    import tempfile

    handle = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    handle.write(png_bytes)
    handle.close()
    return handle.name


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
    mono = ParagraphStyle("mono", parent=styles["BodyText"], fontName="Courier", fontSize=7.6, leading=9.6)
    banner = ParagraphStyle("banner", parent=styles["BodyText"], fontSize=11, leading=14,
                            textColor=colors.HexColor("#12263a"), alignment=1)

    story: list = []
    story.append(Paragraph("DisasterDoc - evidence report", h1))
    story.append(Paragraph("Evidence-first document recovery. <b>If the document doesn't show it, "
                           "DisasterDoc doesn't claim it.</b>", body))
    story.append(Spacer(1, 6))

    summary = document.summary
    meta_rows = [
        ["Document ID", document.document_id],
        ["Processed at (UTC)", document.processed_at],
        ["Template", document.template],
        ["OCR engine", str(document.audit.get("ocr_engine"))],
        ["AI commentary", f"{'used - commentary only' if document.audit.get('ai_used') else 'not used'}"
                          f"{' (' + str(document.audit.get('ai_model')) + ')' if document.audit.get('ai_used') else ''}"],
        ["Fields", f"{summary.get('recovered', 0)} recovered / {summary.get('partial', 0)} partial / "
                   f"{summary.get('unrecoverable', 0)} unrecoverable"],
        ["Requires human verification", f"{summary.get('needs_verification', 0)} of {summary.get('total', 0)} fields"],
        ["Code version", str(document.audit.get("code_version"))],
    ]
    meta = Table([[Paragraph(f"<b>{k}</b>", body), Paragraph(str(v), body)] for k, v in meta_rows], colWidths=[42 * mm, 132 * mm])
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
            story.append(RLImage(_png_path(png), width=width, height=width * ih / iw))
            story.append(Spacer(1, 6))

    story.append(PageBreak())
    story.append(Paragraph("Field-level findings", h2))
    story.append(Paragraph(
        "Every field below is classified by deterministic code from OCR evidence only. "
        "For PARTIAL and UNRECOVERABLE fields no value is reported, by design.", body))
    story.append(Spacer(1, 4))

    header = [Paragraph(f"<b>{h}</b>", body) for h in ("Field", "Status", "Value / observed evidence", "Evidence")]

    for f in document.fields:
        status_colour = STATUS_COLOR_HEX.get(f.status, "#333333")
        if f.status == config.STATUS_RECOVERED:
            value_cell = Paragraph(f"<b>{f.value}</b>", body)
        else:
            value_cell = Paragraph(f"no value reported<br/><font color='#555555'>observed: "
                                   f"{f.raw_ocr_text or 'no usable evidence'}</font>", body)
        ev_bits = []
        for e in f.evidence:
            ev_bits.append(f"{e['observation_id']}: bbox {e['bbox']} conf {e['ocr_confidence']:.2f} \u201c{e['text']}\u201d")
        evidence_cell = Paragraph("<br/>".join(ev_bits) if ev_bits else "no observation mapped", small)
        rows = [header,
                [Paragraph(f"<b>{f.label}</b>", body),
                 Paragraph(f"<font color='{status_colour}'><b>{f.status}</b></font>", body),
                 value_cell,
                 evidence_cell],
                ["", Paragraph(f"confidence bucket: {f.confidence_bucket}", small),
                 Paragraph("<b>Rule outcome:</b> " + "<br/>".join(f.reasons), small),
                 Paragraph(f"needs verification: {'yes' if f.needs_verification else 'no'}", small)]]
        table = Table(rows, colWidths=[26 * mm, 26 * mm, 62 * mm, 60 * mm])
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dfe4e8")),
            ("SPAN", (0, 2), (0, 2)),
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
            if f.ai_commentary.get("possible_interpretations"):
                ai_rows.append([Paragraph("Possible interpretations: " + ", ".join(
                    f.ai_commentary["possible_interpretations"]), small)])
            if f.ai_commentary.get("commentary"):
                ai_rows.append([Paragraph(f.ai_commentary["commentary"], small)])
            if f.ai_commentary.get("verification_instruction"):
                ai_rows.append([Paragraph("Suggested verification: " + f.ai_commentary["verification_instruction"], small)])
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
    for t in document.verification_tasks:
        checklist_rows.append([Paragraph("\u2610", body), Paragraph(t["text"], body)])
    checklist = Table(checklist_rows, colWidths=[14 * mm, 160 * mm])
    checklist.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dfe4e8")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f5f7")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(checklist)

    story.append(Paragraph("Raw OCR observations (machine-observed evidence, unmodified)", h2))
    raw_rows = [[Paragraph(f"<b>{h}</b>", small) for h in ("ID", "bbox [x0,y0,x1,y1]", "OCR conf.", "Text (verbatim)")]]
    for o in document.ocr_observations_raw:
        raw_rows.append([
            Paragraph(o["observation_id"], small),
            Paragraph(str(o["bbox"]), small),
            Paragraph(f"{o['ocr_confidence']:.2f}", small),
            Paragraph(str(o["text"]), small),
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
            story.append(Paragraph(f"\u2022 {note}", small))

    story.append(Spacer(1, 8))
    story.append(Paragraph("Provenance and disclaimer", h2))
    story.append(Paragraph(
        "The document ID is the SHA-256 digest of the exact file that was processed. It identifies the "
        "processed file for integrity and provenance purposes; it does <b>not</b> prove that the document "
        "is authentic or legally valid.", small))
    story.append(Paragraph(document.disclaimer, small))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f"Generated by DisasterDoc ({config.CODE_VERSION}) at {datetime.now(timezone.utc).isoformat(timespec='seconds')} "
        "\u00b7 Synthetic demo documents only \u00b7 No government database was queried.",
        small))

    doc.build(story)
    return buf.getvalue()


__all__ = ["PDF_AVAILABLE", "to_json_bytes", "to_pdf_bytes", "STATUS_COLOR_HEX"]
