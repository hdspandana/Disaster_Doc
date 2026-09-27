"""
Acceptance tests for the DisasterDoc deterministic pipeline.

    pytest -q            (from the project root)

These tests encode the acceptance criteria of the project: expected statuses per demo
document, the null-value invariant, raw-OCR preservation, deterministic-only
classification, AI guardrails, report exports and hash provenance.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import ai_commentary, classifier, config, evidence, hashing, report  # noqa: E402
from src.pipeline import run_pipeline  # noqa: E402

DEMO = ROOT / "demo"
EXPECTED = {
    "mild_damage": {
        "name": config.STATUS_RECOVERED,
        "id_number": config.STATUS_RECOVERED,
        "dob": config.STATUS_RECOVERED,
        "district": config.STATUS_RECOVERED,
        "address": config.STATUS_PARTIAL,
    },
    "partial_damage": {
        "name": config.STATUS_RECOVERED,
        "id_number": config.STATUS_RECOVERED,
        "district": config.STATUS_PARTIAL,
        "dob": config.STATUS_UNRECOVERABLE,
        "address": config.STATUS_PARTIAL,
    },
    "severe_damage": {
        "name": config.STATUS_PARTIAL,
        "id_number": config.STATUS_UNRECOVERABLE,
        "dob": config.STATUS_UNRECOVERABLE,
        "district": config.STATUS_UNRECOVERABLE,
        "address": config.STATUS_PARTIAL,
    },
}


@pytest.fixture(scope="module")
def results() -> dict:
    """Run the pipeline once per demo document (OCR is the slow step)."""
    out = {}
    for name in EXPECTED:
        path = DEMO / f"{name}.png"
        assert path.exists(), f"missing demo document {path}; run: python tools/make_demo_docs.py"
        out[name] = (path.read_bytes(), run_pipeline(path.read_bytes(), use_ai=True))
    return out


@pytest.mark.parametrize("name", list(EXPECTED))
def test_expected_statuses(results, name):
    """Each demo document must reproduce its expected status table."""
    _, res = results[name]
    assert res.ok, res.error
    for field_name, expected_status in EXPECTED[name].items():
        got = res.document.field(field_name)
        assert got.status == expected_status, (
            f"{name}.{field_name}: expected {expected_status}, got {got.status} "
            f"(raw={got.raw_ocr_text!r}, reasons={got.reasons})"
        )


def test_partial_district_is_not_completed(results):
    """The main demo: 'BENG...' must stay a fragment, never become Bengaluru."""
    _, res = results["partial_damage"]
    district = res.document.field("district")
    assert district.status == config.STATUS_PARTIAL
    assert district.value is None
    assert district.raw_ocr_text.upper().startswith("BENG")
    assert len(district.raw_ocr_text.strip()) <= 6, "a fragment must stay a fragment"
    assert "BENGALURU" not in district.raw_ocr_text.upper()


def test_unrecoverable_dob_has_no_value(results):
    """Date of birth is destroyed: no value may be reported for it."""
    _, res = results["partial_damage"]
    dob = res.document.field("dob")
    assert dob.status == config.STATUS_UNRECOVERABLE
    assert dob.value is None
    assert dob.needs_verification is True


@pytest.mark.parametrize("name", list(EXPECTED))
def test_null_value_invariant(results, name):
    """PARTIAL and UNRECOVERABLE fields must never carry a value."""
    _, res = results[name]
    for f in res.document.fields:
        if f.status in (config.STATUS_PARTIAL, config.STATUS_UNRECOVERABLE):
            assert f.value is None, f"{name}.{f.field_name} has a value despite status {f.status}"


@pytest.mark.parametrize("name", list(EXPECTED))
def test_raw_ocr_text_is_preserved(results, name):
    """Every reported field text must appear verbatim in the raw OCR observations."""
    _, res = results[name]
    raw_texts = [o["text"] for o in res.document.ocr_observations_raw]
    for f in res.document.fields:
        for obs in f.evidence:
            assert obs["text"] in raw_texts, f"{name}.{f.field_name}: {obs['text']!r} not in raw OCR output"
        if f.raw_ocr_text:
            for token in f.raw_ocr_text.split(" "):
                assert any(token == t or token in t for t in raw_texts)


@pytest.mark.parametrize("name", list(EXPECTED))
def test_bounding_boxes_are_within_the_image(results, name):
    """Evidence boxes must be real, ordered and inside the processed image."""
    _, res = results[name]
    h, w = res.original_bgr.shape[:2]
    assert res.document.ocr_observations_raw, "no OCR observations at all"
    for o in res.document.ocr_observations_raw:
        x0, y0, x1, y1 = o["bbox"]
        assert 0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h
    for f in res.document.fields:
        if f.evidence_bbox:
            x0, y0, x1, y1 = f.evidence_bbox
            assert 0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h


@pytest.mark.parametrize("name", list(EXPECTED))
def test_statuses_do_not_depend_on_the_ai_layer(results, name):
    """Classification is deterministic: identical with AI disabled."""
    data, with_ai = results[name]
    without_ai = run_pipeline(data, use_ai=False)
    a = [(f.field_name, f.status, f.value, f.raw_ocr_text) for f in with_ai.document.fields]
    b = [(f.field_name, f.status, f.value, f.raw_ocr_text) for f in without_ai.document.fields]
    assert a == b


def test_ai_failure_does_not_break_the_pipeline(results):
    """With no API key the deterministic results and the report must still be produced."""
    data, _ = results["partial_damage"]
    res = run_pipeline(data, use_ai=True)
    assert res.ok
    assert res.document.field("district").status == config.STATUS_PARTIAL
    assert "AI commentary unavailable" in res.ai_status or "disabled" in res.ai_status.lower()
    payload = json.loads(report.to_json_bytes(res.document))
    assert payload["summary"]["partial"] == 2


def test_ai_guardrail_rejects_pattern_valid_guesses():
    """AI may not turn a fragment into a complete, pattern-valid value."""
    field = classifier.FieldResult(
        field_name="district",
        label="DISTRICT",
        expected_type="string",
        status=config.STATUS_PARTIAL,
        value=None,
        raw_ocr_text="BENG...",
        confidence_bucket="medium",
        ocr_confidence=0.71,
    )
    pattern = config.TEMPLATE["fields"]["district"]["pattern"]
    clean, notes = ai_commentary.enforce_guardrails(
        {
            "possible_interpretations": ["Bengaluru", "Other"],
            "verification_instruction": "Confirm district with the applicant.",
            "commentary": "Only a fragment is visible.",
            "_raw": {},
        },
        field,
        pattern,
    )
    assert clean["possible_interpretations"] == ["Bengaluru", "Other"]
    assert notes == []

    # Identifiers and dates are the dangerous case: a complete pattern-valid guess is rejected.
    dob = classifier.FieldResult(
        field_name="dob", label="DATE OF BIRTH", expected_type="date",
        status=config.STATUS_PARTIAL, value=None, raw_ocr_text="14 08 19..",
        confidence_bucket="low", ocr_confidence=0.4,
    )
    dob_pattern = config.TEMPLATE["fields"]["dob"]["pattern"]
    clean2, notes2 = ai_commentary.enforce_guardrails(
        {"possible_interpretations": ["14 08 1991", "Other"], "status": "RECOVERED", "value": "14 08 1991"},
        dob,
        dob_pattern,
    )
    assert clean2["possible_interpretations"] == ["Other"]
    assert any("fully satisfies the expected pattern" in n for n in notes2)


def test_ai_cannot_write_factual_fields():
    """attach_ai_commentary must only fill ai_commentary."""
    field = classifier.FieldResult(
        field_name="district", label="DISTRICT", expected_type="string",
        status=config.STATUS_PARTIAL, value=None, raw_ocr_text="BENG...",
        confidence_bucket="medium", ocr_confidence=0.71,
    )
    before = field.to_dict()
    events, notes = evidence.attach_ai_commentary(
        [field],
        {"district": {"possible_interpretations": ["Bengaluru", "Other"],
                      "verification_instruction": "Ask the applicant.",
                      "commentary": "Fragment only.",
                      "_raw": {"value": "Bengaluru", "status": "RECOVERED"}}},
    )
    after = field.to_dict()
    assert after["value"] is None
    assert after["status"] == config.STATUS_PARTIAL
    assert after["ai_commentary"]["possible_interpretations"] == ["Bengaluru", "Other"]
    assert any("value" in n or "status" in n for n in notes)
    assert events and events[0]["event"] == "ai_commentary_attached"
    for key in ("value", "status", "raw_ocr_text", "confidence_bucket", "evidence"):
        assert after[key] == before[key]


def test_document_hash_is_sha256_of_the_uploaded_file(results):
    """Document ID is the SHA-256 of the exact processed file."""
    data, res = results["mild_damage"]
    assert res.document_id == f"sha256:{hashing.sha256_bytes(data)}"
    assert res.document.document_id == res.document_id


def test_verification_tasks_cover_every_unverified_field(results):
    """Every field needing verification must raise a checklist task."""
    _, res = results["severe_damage"]
    tasks = res.document.verification_tasks
    watched = {t["field_name"] for t in tasks}
    for f in res.document.fields:
        if f.needs_verification:
            assert f.field_name in watched, f"no verification task for {f.field_name}"
    assert any(t["task_id"] == "verify_manual_inspection" for t in tasks)


def test_json_report_roundtrip(results):
    """The JSON evidence report contains the required sections."""
    _, res = results["partial_damage"]
    payload = json.loads(report.to_json_bytes(res.document))
    for key in ("document_id", "processed_at", "template", "fields", "verification_tasks",
                "ocr_observations_raw", "audit", "disclaimer"):
        assert key in payload
    assert payload["audit"]["ai_role"] == "commentary_only"
    assert payload["fields"][0]["evidence"], "fields must carry their evidence boxes"


def test_pdf_report_builds(results):
    """PDF export works and is non-trivial in size."""
    if not report.PDF_AVAILABLE:
        pytest.skip("reportlab not installed")
    _, res = results["partial_damage"]
    pdf = report.to_pdf_bytes(res.document, res.original_bgr, res.annotation)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 5000


def test_invalid_image_is_handled_gracefully():
    """Garbage input must produce a clear error, not a crash."""
    res = run_pipeline(b"this is not an image", use_ai=False)
    assert not res.ok
    assert "could not be read as an image" in (res.error or "")


def test_demo_documents_are_synthetic_and_repeatable(results):
    """Demo docs must be reproducible byte-for-byte from the generator seed."""
    import subprocess

    before = (DEMO / "partial_damage.png").read_bytes()
    subprocess.run([sys.executable, str(ROOT / "tools" / "make_demo_docs.py")], cwd=ROOT, check=True,
                   capture_output=True)
    after = (DEMO / "partial_damage.png").read_bytes()
    assert before == after, "demo document generation is not deterministic"
