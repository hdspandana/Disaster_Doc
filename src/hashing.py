"""SHA-256 provenance hashing of the uploaded file.

The hash identifies the exact bytes that were processed. It is NOT evidence of
authenticity and must never be presented as such.
"""

from __future__ import annotations

import hashlib


def sha256_bytes(data: bytes) -> str:
    """Return the hex SHA-256 digest of `data`."""
    return hashlib.sha256(data).hexdigest()


def document_id(data: bytes) -> str:
    """Return the provenance identifier used across the app and the reports."""
    return f"sha256:{sha256_bytes(data)}"
