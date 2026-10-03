"""
DisasterDoc - evidence-first recovery interface for damaged documents.

    If the document doesn't show it, DisasterDoc doesn't claim it.

Run with:
    streamlit run app.py

This file is presentation only. Every factual statement on screen comes from the
deterministic pipeline in src/; the AI layer can only add clearly-labelled commentary.
"""

from __future__ import annotations

import hashlib
import sys
from html import escape as html_escape
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src import config, evidence, report
from src.pipeline import PipelineResult, run_pipeline

st.set_page_config(
    page_title="DisasterDoc - evidence-first document recovery",
    page_icon="\U0001F9FE",
    layout="wide",
    initial_sidebar_state="expanded",
)

TAGLINE = "If the document doesn't show it, DisasterDoc doesn't claim it."

CSS = """
<style>
  :root {
    --dd-ink:#101a26; --dd-muted:#5a6672; --dd-line:#dde3e9; --dd-bg:#f6f8fa;
    --dd-green:#1f7a33; --dd-amber:#9a6100; --dd-red:#a52218; --dd-navy:#12263a;
  }
  #MainMenu, footer {visibility:hidden;}
  .block-container {padding-top: 1.1rem; padding-bottom: 2.5rem; max-width: 1400px;}
  .dd-header {
    background: linear-gradient(105deg, #0f2033 0%, #17395c 55%, #1d4d74 100%);
    border-radius: 14px; padding: 20px 24px 18px 24px; color:#fff; margin-bottom: 14px;
    box-shadow: 0 6px 22px rgba(18,38,58,.20);
  }
  .dd-header h1 {margin:0; font-size: 30px; letter-spacing:-0.5px; color:#fff; font-weight:700;}
  .dd-header .sub {font-size: 14px; color:#bcd3e8; margin-top:2px;}
  .dd-tagline {
    margin-top:12px; padding:10px 14px; border-left:4px solid #d69e2e; background:rgba(255,255,255,.09);
    border-radius:6px; font-size:15px; font-style:italic; color:#fff;
  }
  .dd-kicker {text-transform:uppercase; letter-spacing:1.4px; font-size:11px; font-weight:700; color:var(--dd-muted);}
  .dd-card {border:1px solid var(--dd-line); border-radius:10px; padding:7px 9px; background:#fff;}
  .dd-field {
    border:1px solid var(--dd-line); border-left-width:5px; border-radius:10px; background:#fff;
    padding:12px 14px; margin-bottom:8px; box-shadow:0 1px 3px rgba(16,26,38,.05);
  }
  .dd-field.r {border-left-color:var(--dd-green);} .dd-field.p {border-left-color:#d69e2e;}
  .dd-field.u {border-left-color:#b3261e;}
  .dd-field.sel {box-shadow:0 0 0 2px #17395c inset, 0 2px 8px rgba(16,26,38,.12); background:#fbfdff;}
  .dd-flabel {font-size:11.5px; font-weight:700; letter-spacing:1.1px; color:#41505e; text-transform:uppercase;}
  .dd-pill {font-size:10.5px; font-weight:700; letter-spacing:.6px; padding:2px 8px; border-radius:20px; margin-left:6px;}
  .dd-pill.r {background:#e6f4ea; color:var(--dd-green); border:1px solid #b7dfc2;}
  .dd-pill.p {background:#fdf3dc; color:var(--dd-amber); border:1px solid #edd9a5;}
  .dd-pill.u {background:#fbeae8; color:var(--dd-red); border:1px solid #eec3bd;}
  .dd-value {font-size:19px; font-weight:700; color:var(--dd-ink); margin-top:6px; letter-spacing:.2px;}
  .dd-value.muted {font-size:14px; font-weight:600; color:var(--dd-muted);}
  .dd-mono {font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size:12.5px;}
  .dd-raw {font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size:16px;
           background:#f4f6f8; border:1px dashed #c9d2da; border-radius:6px; padding:6px 9px; display:inline-block;}
  .dd-note {font-size:12.5px; color:var(--dd-muted);}
  .dd-warn {background:#fdf3dc; border:1px solid #e6c98a; border-radius:8px; padding:9px 12px; font-size:12.5px; color:#6b4700;}
  .dd-block {background:#f4f6f8; border:1px solid var(--dd-line); border-radius:8px; padding:10px 12px;}
  .dd-sec {font-size:19px; font-weight:700; color:var(--dd-ink); margin:6px 0 2px 0;}
  .dd-summary {display:flex; gap:10px; flex-wrap:wrap; margin:2px 0 10px 0;}
  .dd-chip {border:1px solid var(--dd-line); border-radius:20px; padding:4px 12px; background:#fff; font-size:12.5px; color:#33414f;}
  .dd-chip b {color:var(--dd-ink);}
  .dd-stage {font-size:13.5px; padding:3px 0; color:#33414f;}
  .dd-stage .tick {color:var(--dd-green); font-weight:700; margin-right:6px;}
  .dd-stage .fail {color:#b3261e; font-weight:700; margin-right:6px;}
  .dd-stage .det {color:var(--dd-muted); font-size:12px;}
  .dd-sep {height:1px; background:var(--dd-line); margin:14px 0;}
  .dd-guard {font-size:11.5px; color:#6b4700; background:#fff8e6; border:1px solid #e0b64a;
             border-radius:6px; padding:2px 7px; display:inline-block; margin-top:6px;}
  .dd-foot {font-size:11.5px; color:var(--dd-muted); border-top:1px solid var(--dd-line); margin-top:18px; padding-top:10px;}
  .stButton>button {border-radius:8px;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

STATUS_CLASS = {config.STATUS_RECOVERED: "r", config.STATUS_PARTIAL: "p", config.STATUS_UNRECOVERABLE: "u"}
STATUS_ICON = {
    config.STATUS_RECOVERED: "\U0001F7E2",
    config.STATUS_PARTIAL: "\U0001F7E1",
    config.STATUS_UNRECOVERABLE: "\U0001F534",
}


# --------------------------------------------------------------------------------------
# small render helpers
# --------------------------------------------------------------------------------------
def _safe_html(value: object) -> str:
    """Escape untrusted OCR, AI, filename, and exception text before HTML rendering."""
    return html_escape(str(value), quote=True)


def header() -> None:
    st.markdown(
        f"""
        <div class="dd-header">
          <h1>DisasterDoc</h1>
          <div class="sub">Evidence-first document recovery &nbsp;\u00b7&nbsp; deterministic classification,
          explicit uncertainty, human verification</div>
          <div class="dd-tagline">{TAGLINE}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def status_pill(status: str) -> str:
    css_class = STATUS_CLASS.get(status, "u")
    icon = STATUS_ICON.get(status, "\u26aa")
    return f'<span class="dd-pill {css_class}">{icon} {_safe_html(status)}</span>'


def field_card(field, selected: bool) -> str:
    cls = STATUS_CLASS.get(field.status, "u")
    if field.status == config.STATUS_RECOVERED:
        value_html = f'<div class="dd-value">{_safe_html(field.value)}</div>'
    elif field.status == config.STATUS_PARTIAL:
        shown = field.raw_ocr_text or "no readable text"
        value_html = (
            f'<div class="dd-raw">{_safe_html(shown)}</div>'
            f'<div class="dd-note" style="margin-top:5px;">No value reported \u2014 the document does not '
            f'fully show this field.</div>'
        )
    else:
        value_html = '<div class="dd-value muted">No sufficient evidence</div>'
    sel = " sel" if selected else ""
    return (
        f'<div class="dd-field {cls}{sel}">'
        f'<span class="dd-flabel">{_safe_html(field.label)}</span>{status_pill(field.status)}'
        f"{value_html}</div>"
    )


def stage_html(stage) -> str:
    mark = '<span class="tick">\u2713</span>' if stage.ok else '<span class="fail">\u2715</span>'
    detail = f' <span class="det">{_safe_html(stage.detail)}</span>' if stage.detail else ""
    return f'<div class="dd-stage">{mark}{_safe_html(stage.label)}{detail}</div>'


def bbox_label(field) -> str:
    if not field.evidence:
        return "no evidence region"
    ids = ", ".join(_safe_html(e["observation_id"]) for e in field.evidence)
    box = field.evidence_bbox
    return f"{ids} \u00b7 bbox [{box[0]}, {box[1]}, {box[2]}, {box[3]}]" if box else ids


# --------------------------------------------------------------------------------------
# processing (cached per session so re-renders never re-run OCR)
# --------------------------------------------------------------------------------------
def process(doc_bytes: bytes, use_ai: bool) -> PipelineResult:
    key = (hashlib.sha256(doc_bytes).hexdigest(), bool(use_ai))
    store: dict = st.session_state.setdefault("results", {})
    if key in store:
        return store[key]

    # Keep only a bounded number of full-resolution results in session memory.
    limit = max(1, config.MAX_SESSION_RESULTS)
    while len(store) >= limit:
        store.pop(next(iter(store)))

    progress = st.empty()
    rendered: list[str] = []
    progress.markdown(
        '<div class="dd-note">Running the deterministic pipeline '
        "(the first run also loads the OCR model, which takes a few seconds)\u2026</div>",
        unsafe_allow_html=True,
    )

    def on_stage(stage) -> None:
        rendered.append(stage_html(stage))
        progress.markdown("".join(rendered), unsafe_allow_html=True)

    try:
        result = run_pipeline(doc_bytes, use_ai=use_ai, on_stage=on_stage)
    except Exception:  # absolute safety net: the app must never crash
        progress.empty()
        st.error("Something went wrong while processing this file, so no field values are reported.")
        st.stop()

    payloads: dict[str, bytes | None] = {"json": None, "pdf": None, "pdf_error": None}
    if result.ok:
        payloads["json"] = report.to_json_bytes(result.document)
        if report.PDF_AVAILABLE:
            try:
                payloads["pdf"] = report.to_pdf_bytes(result.document, result.original_bgr, result.annotation)
            except Exception:  # PDF is a convenience export; JSON must always work
                payloads["pdf_error"] = "PDF generation failed; the JSON evidence report remains available."

    store[key] = (result, payloads)
    progress.empty()
    return result, payloads


# --------------------------------------------------------------------------------------
# sidebar
# --------------------------------------------------------------------------------------
def sidebar() -> tuple[bool, bool, bool]:
    with st.sidebar:
        st.markdown('<div class="dd-kicker">Demo documents</div>', unsafe_allow_html=True)
        st.caption("Synthetic, fictional documents generated for this MVP. No real personal data.")
        for label, path, note in (
            ("1 \u00b7 Mild damage", config.DEMO_DIR / "mild_damage.png", "Most fields recoverable"),
            ("2 \u00b7 Partial damage (main demo)", config.DEMO_DIR / "partial_damage.png", "District truncated, DOB gone"),
            ("3 \u00b7 Severe damage", config.DEMO_DIR / "severe_damage.png", "Only fragments survive"),
        ):
            disabled = not path.exists()
            if st.button(label, width="stretch", disabled=disabled, key=f"demo_{path.name}"):
                st.session_state.doc = {"name": path.name, "bytes": path.read_bytes(), "demo": True}
                st.session_state.selected = None
            st.caption(note)

        st.markdown('<div class="dd-sep"></div>', unsafe_allow_html=True)
        st.markdown('<div class="dd-kicker">Analysis options</div>', unsafe_allow_html=True)
        use_ai = st.toggle("AI commentary (advisory)", value=False,
                           help="Off by default. If enabled, incomplete OCR text and deterministic validation/reason evidence "
                                "(including competing readings and their boxes/scores when present), field/status/pattern, "
                                "document hash, and obscured-area fraction are sent to Gemini; no image is sent and no "
                                "deterministic decision can be changed.")
        show_ocr = st.toggle("Show raw OCR boxes", value=True,
                             help="Overlay every observation returned by the OCR engine, exactly as returned.")
        show_pre = st.toggle("Show preprocessed image", value=False,
                             help="The grayscale/contrast/denoise image that is fed to the OCR engine.")

        st.markdown('<div class="dd-sep"></div>', unsafe_allow_html=True)
        st.markdown('<div class="dd-kicker">Status legend</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="dd-note">{STATUS_ICON[config.STATUS_RECOVERED]} <b>RECOVERED</b> \u2014 a complete OCR transcription '
            "passed the current structural/readability checks. It is not independently verified against a source record or identity database.<br><br>"
            f'{STATUS_ICON[config.STATUS_PARTIAL]} <b>PARTIAL</b> \u2014 some evidence exists but the complete value '
            "cannot be established. <b>No value is reported.</b><br><br>"
            f'{STATUS_ICON[config.STATUS_UNRECOVERABLE]} <b>UNRECOVERABLE</b> \u2014 not enough usable evidence. '
            "<b>No value is reported.</b></div>",
            unsafe_allow_html=True,
        )

        st.markdown('<div class="dd-sep"></div>', unsafe_allow_html=True)
        st.markdown('<div class="dd-kicker">Privacy &amp; scope</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="dd-note">Documents are processed in memory for this session and are never stored or '
            "uploaded to a document service. Optional AI commentary is off by default. If enabled, the only outbound "
            "request contains incomplete OCR text, field labels/status/reasons, deterministic validation results/evidence "
            "(including competing readings and their boxes/scores when present), the expected format, a SHA-256 "
            "document ID, and an obscured-area fraction \u2014 never the source image. No government database is "
            "queried, no identity is verified, and no replacement document is generated.</div>",
            unsafe_allow_html=True,
        )
        st.markdown('<div class="dd-sep"></div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="dd-note"><b>UN SDG 16</b> (Peace, Justice and Strong Institutions) \u2014 Target 16.9 legal '
            "identity.<br><b>UN SDG 11</b> (Sustainable Cities and Communities) \u2014 disaster resilience.</div>",
            unsafe_allow_html=True,
        )
        st.markdown('<div class="dd-sep"></div>', unsafe_allow_html=True)
        st.markdown(f'<div class="dd-note">{config.DISCLAIMER}</div>', unsafe_allow_html=True)
    return use_ai, show_ocr, show_pre


# --------------------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------------------
def upload_page() -> None:
    st.markdown('<div class="dd-sec">Upload a damaged document</div>', unsafe_allow_html=True)
    left, right = st.columns([1.25, 1])

    with left:
        uploaded = st.file_uploader(
            "Upload damaged document", type=["jpg", "jpeg", "png", "webp", "bmp", "tif", "tiff"],
            label_visibility="collapsed",
        )
        st.markdown(
            '<div class="dd-note">Accepted: JPG/JPEG, PNG, WebP, BMP, or TIFF; maximum 25 MB, '
            "25 million pixels, and 10,000 px per dimension. File signature/container is validated before OCR. "
            "Demo mode uses synthetic documents.</div>",
            unsafe_allow_html=True,
        )
        if uploaded is not None:
            data = uploaded.getvalue()
            fingerprint = (hashlib.sha256(data).hexdigest(), len(data))
            if st.session_state.get("_upload_fp") != fingerprint:
                st.session_state._upload_fp = fingerprint
                st.session_state.doc = {"name": uploaded.name, "bytes": data, "demo": False}
                st.session_state.selected = None
                st.rerun()

    with right:
        st.markdown(
            """
            <div class="dd-block">
              <div class="dd-kicker">How this works</div>
              <div class="dd-note" style="margin-top:6px;">
                The damaged document is run through a fixed pipeline:
                preprocessing \u2192 OCR \u2192 raw observations (text + bounding box + OCR confidence)
                \u2192 deterministic field mapping \u2192 deterministic classification
                \u2192 human verification \u2192 optional AI commentary.\n
              </div>
              <div class="dd-note" style="margin-top:8px;">
                <b>Deterministic code decides facts. AI decides wording.</b> A field is only called
                RECOVERED only for a complete OCR transcription that passes current checks; this is not source-record verification.
              </div>
              <div class="dd-warn" style="margin-top:10px;">
                AI is never allowed to complete a field, change a status, or edit OCR text or confidence.
                Possible readings of a fragment are shown only as clearly labelled possibilities.
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown('<div class="dd-sep"></div>', unsafe_allow_html=True)
    st.markdown('<div class="dd-kicker">Try the demo documents</div>', unsafe_allow_html=True)
    cols = st.columns(3)
    for col, (label, name, expect) in zip(
        cols,
        (
            ("Mild damage", "mild_damage.png", "Name / ID / DOB / District recovered, address partial"),
            ("Partial damage \u00b7 main demo", "partial_damage.png", "District shows only \u201cBENG\u2026\u201d \u2192 PARTIAL, DOB unrecoverable"),
            ("Severe damage", "severe_damage.png", "Only fragments survive \u2192 mostly unrecoverable"),
        ),
    ):
        path = config.DEMO_DIR / name
        with col:
            if path.exists():
                st.image(str(path), width="stretch")
                st.caption(f"**{label}** \u2014 {expect}")
                if st.button("Analyse \u2192", key=f"upload_page_{name}", width="stretch"):
                    st.session_state.doc = {"name": name, "bytes": path.read_bytes(), "demo": True}
                    st.session_state.selected = None
                    st.rerun()


def results_page(result: PipelineResult, payloads: dict, show_ocr: bool, show_pre: bool) -> None:
    doc = result.document

    if not result.ok:
        st.error(result.error or "Unable to obtain sufficient OCR evidence.")
        st.info(
            "No field values are reported for this document. Damage of this kind is handled by human "
            "verification, not by inference."
        )
        render_stages(result)
        return

    summary = doc.summary
    st.markdown(
        f"""
        <div class="dd-summary">
          <div class="dd-chip"><b>{summary['total']}</b> fields assessed</div>
          <div class="dd-chip">{STATUS_ICON[config.STATUS_RECOVERED]} <b>{summary['recovered']}</b> recovered</div>
          <div class="dd-chip">{STATUS_ICON[config.STATUS_PARTIAL]} <b>{summary['partial']}</b> partial</div>
          <div class="dd-chip">{STATUS_ICON[config.STATUS_UNRECOVERABLE]} <b>{summary['unrecoverable']}</b> unrecoverable</div>
          <div class="dd-chip"><b>{summary['needs_verification']}</b> need human verification</div>
          <div class="dd-chip dd-mono">{doc.document_id[:34]}\u2026</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if doc.audit.get("ai_used"):
        st.success(f"AI commentary attached (advisory only). {result.ai_status}", icon="\u2139\uFE0F")
    elif result.ai_status:
        st.info(
            f"{result.ai_status} All field statuses, evidence regions and confidence buckets below were produced "
            "by deterministic code, without the AI layer.",
            icon="\u2139\uFE0F",
        )

    selected = st.session_state.get("selected")
    annotation = evidence.render_annotation(
        result.original_bgr,
        doc.fields,
        selected,
        show_ocr,
        [o.to_dict() for o in result.observations],
    )

    left, right = st.columns([1.12, 1], gap="large")

    with left:
        st.markdown('<div class="dd-kicker">Annotated evidence regions</div>', unsafe_allow_html=True)
        st.image(annotation, width="stretch",
                 caption="Status-coloured evidence boxes. The selected field is outlined in white; "
                         "the striped/dashed marker shows where the surviving evidence ends.")
        if show_pre and result.preprocessed_bgr is not None:
            st.markdown('<div class="dd-kicker">Preprocessed (OCR input)</div>', unsafe_allow_html=True)
            st.image(result.preprocessed_bgr, width="stretch")
        with st.expander("Original document (as received)"):
            st.image(result.original_bgr, width="stretch")
            st.caption(f"SHA-256 of this exact file: `{doc.document_id}`")

    with right:
        st.markdown('<div class="dd-kicker">Field results</div>', unsafe_allow_html=True)
        st.caption(
            "RECOVERED means an OCR transcription passed current checks; it is not independent verification of identity or source-record correctness."
        )
        for field in doc.fields:
            is_sel = st.session_state.get("selected") == field.field_name
            st.markdown(field_card(field, is_sel), unsafe_allow_html=True)
            if st.button(
                ("Inspecting \u25BE" if is_sel else "Inspect evidence \u25B8"),
                key=f"inspect_{field.field_name}",
                width="stretch",
            ):
                st.session_state.selected = None if is_sel else field.field_name
                st.rerun()

    # ---- evidence detail ---------------------------------------------------------------
    if selected:
        field = doc.field(selected)
        st.markdown('<div class="dd-sep"></div>', unsafe_allow_html=True)
        st.markdown(f'<div class="dd-sec">Evidence detail \u00b7 {_safe_html(field.label)}</div>', unsafe_allow_html=True)
        c1, c2, c3 = st.columns([1, 1.35, 1], gap="large")

        with c1:
            observed = _safe_html(field.raw_ocr_text or "\u2014")
            claimed = _safe_html(field.value if field.value is not None else "null (not reported)")
            claim_label = (
                "OCR transcription (unverified)"
                if field.status == config.STATUS_RECOVERED
                else "Claimed value"
            )
            st.markdown(
                f"""
                <div class="dd-block">
                  <div class="dd-kicker">Field</div><div style="font-size:15px;font-weight:600;">{_safe_html(field.label)}</div>
                  <div class="dd-kicker" style="margin-top:10px;">Status</div>
                  <div>{status_pill(field.status)}</div>
                  <div class="dd-kicker" style="margin-top:10px;">Observed evidence</div>
                  <div class="dd-raw">{observed}</div>
                  <div class="dd-kicker" style="margin-top:10px;">{_safe_html(claim_label)}</div>
                  <div class="dd-mono">{claimed}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        why = "".join(f"• {_safe_html(reason)}<br>" for reason in field.reasons)
        why_display = why or "—"
        conflict_readings: list[str] = []
        for row in field.validation_evidence:
            if row.validator != "ocr_observation_consistency":
                continue
            for conflict in row.details.get("conflicts", []):
                for text, confidence in zip(conflict.get("observed_texts", []), conflict.get("ocr_confidences", [])):
                    conflict_readings.append(
                        f"{_safe_html(text)} (OCR score {float(confidence):.2f})"
                    )
        conflict_html = "<br>".join(conflict_readings)
        conflict_block = (
            f'<div class="dd-warn" style="margin-top:10px;"><b>Unresolved OCR alternatives</b><br>{conflict_html}</div>'
            if conflict_readings
            else ""
        )

        with c2:
            conf = field.ocr_confidence
            conf_display = "—" if conf is None else f"{conf * 100:.0f}%"
            st.markdown(
                f"""
                <div class="dd-block">
                  <div class="dd-kicker">Evidence region</div>
                  <div class="dd-mono">{bbox_label(field)}</div>
                  {conflict_block}
                  <div class="dd-kicker" style="margin-top:10px;">OCR confidence (engine output)</div>
                  <div class="dd-mono">{conf_display}</div>
                  <div class="dd-kicker" style="margin-top:10px;">Evidence confidence bucket</div>
                  <div class="dd-mono">{_safe_html(field.confidence_bucket.upper())}</div>
                  <div class="dd-note" style="margin-top:6px;">{why_display}</div>
                  <div class="dd-note" style="margin-top:6px;">
                    The first is the engine's own score for a box of pixels, the second is a
                    transparent heuristic over the survival of the field. Neither is a probability that the
                    value is correct.
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with c3:
            st.markdown(
                f"""
                <div class="dd-block">
                  <div class="dd-kicker">Why {_safe_html(field.status)}?</div>
                  <div class="dd-note" style="margin-top:6px;">{why_display}</div>
                  <div class="dd-kicker" style="margin-top:10px;">Requires human verification</div>
                  <div class="dd-mono">{"yes" if field.needs_verification else "no"}</div>
                  <div class="dd-note" style="margin-top:6px;">
                    Decided by src/classifier.py — no AI model contributed to this outcome.
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        if field.ai_commentary:
            ai = field.ai_commentary
            st.markdown('<div class="dd-kicker" style="margin-top:14px;">AI assistance</div>', unsafe_allow_html=True)
            a1, a2 = st.columns([1, 1], gap="large")
            with a1:
                interpretations = ai.get("possible_interpretations", [])
                if not isinstance(interpretations, list):
                    interpretations = []
                interps = "".join(f"<li>{_safe_html(item)}</li>" for item in interpretations)
                st.markdown(
                    f"""
                    <div class="dd-warn">
                      <b>Possible interpretations:</b>
                      <ul style="margin:6px 0 6px 18px;">{interps or "<li>none suggested</li>"}</ul>
                      <b>\u26a0 POSSIBILITIES \u2014 NOT DOCUMENT EVIDENCE.</b> These were produced by an AI model
                      and are not present in the document; they can never be promoted to a value.
                      
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            with a2:
                st.markdown(
                    f"""
                    <div class="dd-block">
                      <div class="dd-kicker">Suggested verification</div>
                      <div class="dd-note" style="margin-top:6px;">{_safe_html(ai.get('verification_instruction', ''))}</div>
                      <div class="dd-kicker" style="margin-top:10px;">AI commentary</div>
                      <div class="dd-note">{_safe_html(ai.get('commentary', ''))}</div>
                      <div class="dd-guard">{_safe_html(ai.get('guardrail', ''))}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        elif field.status != config.STATUS_RECOVERED:
            st.markdown(
                '<div class="dd-note" style="margin-top:10px;">AI commentary is not available for this field; '
                "the deterministic evidence above is unaffected.</div>",
                unsafe_allow_html=True,
            )

    # ---- verification queue -----------------------------------------------------------
    st.markdown('<div class="dd-sep"></div>', unsafe_allow_html=True)
    st.markdown('<div class="dd-sec">Verification required</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="dd-note">DisasterDoc does not resolve these items. It produces the queue a human has to '
        "work through, and refuses to hide the gaps it found.</div>",
        unsafe_allow_html=True,
    )
    done = 0
    for task in doc.verification_tasks:
        state_key = f"task_{doc.document_id[:12]}_{task['task_id']}"
        priority = {"high": "\U0001F534", "medium": "\U0001F7E1"}.get(task["priority"], "\u2022")
        # OCR-derived verification text is displayed as plain text, not interpreted as Markdown.
        st.text(f"{priority} {task['text']}")
        if st.checkbox("Acknowledged", key=state_key):
            done += 1
    st.caption(f"{done} of {len(doc.verification_tasks)} verification items acknowledged.")

    # ---- report ----------------------------------------------------------------------
    st.markdown('<div class="dd-sep"></div>', unsafe_allow_html=True)
    st.markdown('<div class="dd-sec">Evidence report</div>', unsafe_allow_html=True)
    r1, r2 = st.columns([1, 1.6], gap="large")
    with r1:
        st.download_button(
            "Download JSON evidence report",
            data=payloads["json"] or b"{}",
            file_name=f"disasterdoc_evidence_{doc.document_id[7:19]}.json",
            mime="application/json",
            width="stretch",
        )
        if payloads["pdf"]:
            st.download_button(
                "Download PDF evidence report",
                data=payloads["pdf"],
                file_name=f"disasterdoc_evidence_{doc.document_id[7:19]}.pdf",
                mime="application/pdf",
                width="stretch",
            )
        else:
            st.caption(f"PDF export unavailable: {payloads.get('pdf_error') or 'reportlab not installed'}")
    with r2:
        st.markdown(
            f"""
            <div class="dd-block dd-mono" style="font-size:12px;">
              document_id &nbsp;: {_safe_html(doc.document_id)}<br>
              processed_at : {_safe_html(doc.processed_at)}<br>
              template &nbsp;&nbsp;&nbsp;&nbsp;: {_safe_html(doc.template)}<br>
              ocr_engine &nbsp;&nbsp;: {_safe_html(doc.audit.get('ocr_engine'))}<br>
              ai_used &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;: {_safe_html(str(doc.audit.get('ai_used')).lower())}
              ({_safe_html(doc.audit.get('ai_role'))})<br>
              code_version : {_safe_html(doc.audit.get('code_version'))}
            </div>
            <div class="dd-note" style="margin-top:6px;">The document ID is the SHA-256 of the exact file that was
            processed. It identifies the processed file; it does not prove the document is authentic.</div>
            """,
            unsafe_allow_html=True,
        )

    render_stages(result, expanded=False)

    if doc.audit.get("ai_guardrail_notes"):
        with st.expander("AI guardrail log (attempted writes that were ignored)"):
            for note in doc.audit["ai_guardrail_notes"]:
                st.text(note)

    with st.expander("Raw OCR observations (unmodified evidence)"):
        st.caption(
            "Every observation the OCR engine returned, verbatim. Field results above are derived from this "
            "table; nothing here is corrected."
        )
        st.dataframe(
            [
                {
                    "observation_id": o["observation_id"],
                    "text": o["text"],
                    "bbox": str(o["bbox"]),
                    "ocr_confidence": o["ocr_confidence"],
                }
                for o in doc.ocr_observations_raw
            ],
            width="stretch",
            hide_index=True,
        )

    st.markdown(
        f'<div class="dd-foot">{_safe_html(config.DISCLAIMER)}</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="dd-foot" style="border-top:none;padding-top:0;"><b>{TAGLINE}</b> '
        "\u00b7 Synthetic demo documents only \u00b7 Not a determination of identity or legal status.</div>",
        unsafe_allow_html=True,
    )


def render_stages(result: PipelineResult, expanded: bool = True) -> None:
    with st.expander("Processing log", expanded=expanded):
        for stage in result.stages:
            st.markdown(stage_html(stage), unsafe_allow_html=True)
        st.caption(
            "Stages marked \u2715 did not complete. A failed AI stage never affects the deterministic evidence "
            "results above."
        )


# --------------------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------------------
def main() -> None:
    header()
    use_ai, show_ocr, show_pre = sidebar()

    doc = st.session_state.get("doc")
    if not doc:
        upload_page()
        st.markdown(
            f'<div class="dd-foot"><b>{TAGLINE}</b> \u00b7 Built for UN SDG 16.9 (legal identity) and SDG 11 '
            "(disaster resilience).</div>",
            unsafe_allow_html=True,
        )
        return

    name = doc["name"]
    kind = "synthetic demo document" if doc.get("demo") else "uploaded document"
    st.markdown(
        f'<div class="dd-note" style="margin-bottom:8px;">Analysing <b>{_safe_html(name)}</b> ({kind}, '
        f"{len(doc['bytes']) / 1024:.0f} KB) \u00b7 template <b>{_safe_html(config.TEMPLATE_NAME)}</b></div>",
        unsafe_allow_html=True,
    )

    result, payloads = process(doc["bytes"], use_ai)
    results_page(result, payloads, show_ocr, show_pre)

    if st.button("\u21ba Analyse a different document"):
        st.session_state.doc = None
        st.session_state.selected = None
        st.rerun()


if __name__ == "__main__":
    main()
