"""
AI commentary layer (Gemini) - OPTIONAL.

Architectural rule:

    OBSERVATION -> DETERMINISTIC FACT CLASSIFICATION -> AI COMMENTARY

Never:

    OBSERVATION -> AI GUESS -> FACT

Gemini is asked for two advisory things only: plausible readings of an incomplete OCR
fragment, and a verification instruction. Its output is passed through
`enforce_guardrails` before it reaches a field, and the app works fully without it.
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import config
from .classifier import FieldResult


class AiUnavailable(Exception):
    """Raised (and caught by the pipeline) when AI commentary cannot be produced."""


def ai_available() -> bool:
    """True when an API key is configured AND the SDK can be imported."""
    if not config.GEMINI_API_KEY:
        return False
    try:
        import google.genai  # noqa: F401
    except Exception:
        return False
    return True


def status_text() -> str:
    if not config.GEMINI_API_KEY:
        return "AI commentary unavailable (no GEMINI_API_KEY configured). Deterministic evidence results are still available."
    if not ai_available():
        return "AI commentary unavailable (google-genai not installed). Deterministic evidence results are still available."
    return f"AI commentary enabled ({config.GEMINI_MODEL})."


_FRAGMENT_SAFE = re.compile(r"^[A-Za-z0-9 .,'\-/&()]{1,60}$")


def enforce_guardrails(entry: dict[str, Any], field: FieldResult, pattern: str) -> tuple[dict[str, Any], list[str]]:
    """Filter one AI response entry so it can only contribute commentary.

    Guardrails applied:
      1. Only commentary keys are read; factual keys are dropped and reported.
      2. Interpretations must be short plain strings and are capped/deduplicated.
      3. For identifier and date fields (config.AI_PATTERN_GUESS_BLOCKLIST) an
         interpretation that fully satisfies the field's expected pattern is rejected:
         a complete, pattern-valid ID or birth date inferred from a fragment is a guess
         dressed as a recovered value, and every digit of it would be unverifiable.
      4. Descriptive fields (name / district / address) may keep pattern-valid
         possibilities, but they are only ever displayed in the labelled
         "possibilities - not document evidence" slot and never written to `value`.
    """
    notes: list[str] = []
    clean: dict[str, Any] = {"_raw": dict(entry)}

    interpretations = entry.get("possible_interpretations") or []
    if not isinstance(interpretations, list):
        interpretations = []
    kept: list[str] = []
    for item in interpretations:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text or not _FRAGMENT_SAFE.match(text):
            notes.append(f"Rejected AI interpretation {text!r} for '{field.field_name}': not a plain short string.")
            continue
        blocks_guesses = field.field_name in config.AI_PATTERN_GUESS_BLOCKLIST
        if blocks_guesses and re.fullmatch(pattern, text.upper()):
            notes.append(
                f"Rejected AI interpretation {text!r} for '{field.field_name}': the suggested reading fully "
                "satisfies the expected pattern, which would present an unverifiable guess as a value."
            )
            continue
        if text.upper() in {k.upper() for k in kept}:
            continue
        kept.append(text)
    clean["possible_interpretations"] = kept[: config.AI_MAX_INTERPRETATIONS]

    instruction = entry.get("verification_instruction")
    clean["verification_instruction"] = (
        instruction.strip()[:240] if isinstance(instruction, str) and instruction.strip() else field.verification_instruction
    )
    commentary = entry.get("commentary")
    clean["commentary"] = commentary.strip()[:400] if isinstance(commentary, str) else ""
    return clean, notes


def _prompt(field: FieldResult, expected_pattern: str, document_context: dict) -> str:
    return f"""You are an assistant to a human caseworker reviewing a DAMAGED document.

You are given a deterministic finding that you must NOT contradict or change.

Field: {field.label}
Deterministic status: {field.status}
Raw OCR text observed on the damaged document: {field.raw_ocr_text!r}
Deterministic reasons: {' | '.join(field.reasons)}
Expected value pattern for this field: {expected_pattern}
Document context: {json.dumps(document_context)}

Rules you MUST follow:
- Never state a complete field value as fact.
- Never output a value that fully matches the expected pattern above.
- Never claim the document says something it does not show.
- Only suggest what the incomplete fragment might be, and how a human could verify it.

Reply with ONLY this JSON object, no markdown, no extra prose:
{{"possible_interpretations": ["<short possibility>", "<another possibility>", "Other"],
  "verification_instruction": "<one sentence a caseworker can act on>",
  "commentary": "<one or two sentences explaining why the field is {field.status.lower()} and what it means>"}}"""


def generate_commentary(
    fields: list[FieldResult],
    document_context: dict,
) -> tuple[dict[str, dict], list[str], str | None]:
    """Ask Gemini for advisory commentary on PARTIAL/UNRECOVERABLE fields.

    Returns (commentary_by_field, guardrail_notes, error_message). Never raises: a
    failure here must leave the deterministic pipeline and report fully intact.
    """
    targets = [f for f in fields if f.status != config.STATUS_RECOVERED]
    if not targets:
        return {}, [], None
    if not ai_available():
        return {}, [], status_text()

    try:
        from google import genai

        client = genai.Client(api_key=config.GEMINI_API_KEY)
        commentary: dict[str, dict] = {}
        notes: list[str] = []
        for f in targets:
            pattern = config.TEMPLATE["fields"][f.field_name]["pattern"]
            try:
                response = client.models.generate_content(
                    model=config.GEMINI_MODEL,
                    contents=_prompt(f, pattern, document_context),
                )
                raw_text = (getattr(response, "text", "") or "").strip()
                parsed = _parse_json(raw_text)
                entry, entry_notes = enforce_guardrails(parsed, f, pattern)
                commentary[f.field_name] = entry
                notes.extend(entry_notes)
            except Exception as exc:  # one field failing must not kill the rest
                notes.append(f"AI commentary for '{f.field_name}' failed: {type(exc).__name__}.")
        if not commentary:
            return {}, notes, f"AI commentary unavailable. Deterministic evidence results are still available. ({notes[-1] if notes else 'no response'})"
        return commentary, notes, None
    except Exception as exc:  # pragma: no cover - SDK/runtime dependent
        return {}, [], (
            "AI commentary unavailable. Deterministic evidence results are still available. "
            f"({type(exc).__name__})"
        )


def _parse_json(text: str) -> dict:
    """Parse the model response, tolerating ```json fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"```\s*$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise AiUnavailable("AI response did not contain a JSON object.")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AiUnavailable(f"AI response was not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise AiUnavailable("AI response was not a JSON object.")
    return data


__all__ = ["ai_available", "status_text", "generate_commentary", "enforce_guardrails"]
