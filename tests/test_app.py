"""
End-to-end UI tests using Streamlit's headless AppTest.

    pytest -q tests/test_app.py

These exercise the real app script: the demo flow, field rendering, field selection,
the verification queue and both report exports. OCR is the slow step (~10 s per
document), so the timeout is raised deliberately.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(ROOT / "app.py")
TIMEOUT = 240


def _fresh() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    return at


def test_app_loads_without_exception():
    at = _fresh()
    assert not at.exception, at.exception
    # The evidence-first promise must be visible immediately.
    text = " ".join(m.value for m in at.markdown)
    assert "DisasterDoc" in text
    assert "If the document doesn't show it, DisasterDoc doesn't claim it." in text


def test_main_demo_flow_end_to_end():
    """Load demo 2, run the pipeline, verify statuses and the evidence panel."""
    at = _fresh()
    at.button(key="demo_partial_damage.png").click().run()
    assert not at.exception, at.exception

    raw_text = "\n".join(m.value for m in at.markdown)
    # verified statuses from the deterministic pipeline
    assert "ANANYA RAO" in raw_text  # name RECOVERED
    assert "DX-48291" in raw_text  # id RECOVERED
    assert "BENG" in raw_text  # district PARTIAL shows the fragment, not a completion
    assert "BENGALURU" not in raw_text.replace("BENGALURU", "BENGALURU", 1) or "BENGALURU" not in raw_text
    assert "No sufficient evidence" in raw_text  # DOB UNRECOVERABLE
    assert "Verification required" in raw_text
    assert "RECOVERED" in raw_text and "PARTIAL" in raw_text and "UNRECOVERABLE" in raw_text

    # with no API key the deterministic results must still be presented, with a clear note
    notes = [i.value for i in at.info] + [s.value for s in at.success]
    assert any(("AI commentary" in n) for n in notes), notes


def test_field_selection_shows_evidence_detail():
    at = _fresh()
    at.button(key="demo_partial_damage.png").click().run()
    at.button(key="inspect_district").click().run()
    assert not at.exception, at.exception
    text = "\n".join(m.value for m in at.markdown)
    assert "Evidence detail" in text
    assert "DISTRICT" in text
    assert "Why PARTIAL?" in text
    assert "BENG" in text  # the observed fragment, verbatim
    assert "null (not reported)" in text  # PARTIAL never carries a value
    # without an API key the AI slot is explicitly absent rather than silently empty
    if at.session_state.get("doc", {}).get("demo"):
        assert "AI commentary is not available for this field" in text or "NOT DOCUMENT EVIDENCE" in text
    assert at.session_state["selected"] == "district"


def test_zero_value_invariant_holds_in_the_ui():
    """PARTIAL/UNRECOVERABLE fields must render 'null', never an invented value."""
    at = _fresh()
    at.button(key="demo_partial_damage.png").click().run()
    at.button(key="inspect_dob").click().run()
    assert not at.exception
    text = "\n".join(m.value for m in at.markdown)
    assert "null (not reported)" in text
    assert "No sufficient evidence" in text


def test_download_buttons_are_offered():
    at = _fresh()
    at.button(key="demo_mild_damage.png").click().run()
    labels = [d.label for d in at.get("download_button")]
    assert any("JSON evidence report" in label for label in labels)
    has_pdf = any("PDF evidence report" in label for label in labels)
    pdf_lib = pytest.importorskip("reportlab", reason="reportlab not installed")
    assert has_pdf, "PDF export button missing although reportlab is available"


def test_verification_checklist_is_rendered():
    at = _fresh()
    at.button(key="demo_severe_damage.png").click().run()
    assert not at.exception
    checkboxes = at.checkbox
    assert len(checkboxes) >= 4
    assert any("Manually inspect the original document" in c.label for c in checkboxes)


def test_severe_demo_reports_fragments_not_guesses():
    at = _fresh()
    at.button(key="demo_severe_damage.png").click().run()
    text = "\n".join(m.value for m in at.markdown)
    assert "ANA" in text  # surviving fragment of the name
    assert "ANANYA RAO" not in text  # the destroyed part is never restored
