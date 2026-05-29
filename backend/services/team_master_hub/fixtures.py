"""
Phase 1.A.3.2 — offline fixture loader + sanitization checker.

Pure file I/O. NO SGO calls. NO network. NO Mongo. Used by Tier 3
tests to replay a sanitized SGO payload through
`TeamOddsIngestWorker.run_pass()` without ever hitting the network.

Public API:
    SANITIZATION_VERSION    — bump when rules change
    REDACTION_TOKEN         — sentinel string used by the recorder
    assert_sanitized(...)   — raises FixtureSanitizationError on violation
    load_fixture(path)      — returns (payload_dict, meta_dict)
    list_fixtures(root)     — discovery for the test loader

`FixtureSanitizationError` carries an explicit `rule` attribute so
tests can assert on the rule code, not the message string.

The companion CLI recorder (`scripts/team_odds_fixture_record.py`)
is a stub in Phase 1.A.3.2 and only becomes functional in 1.A.3.3.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

SANITIZATION_VERSION = 1
REDACTION_TOKEN = "<REDACTED>"

# Forbidden field names (substring match, case-insensitive).
_FORBIDDEN_FIELD_PATTERN = re.compile(
    r"(email|phone|ssn|address|password)", re.IGNORECASE,
)

# Patterns that suggest an unredacted API key leaked into the payload
# or meta. The recorder MUST strip these before write.
_LEAKED_KEY_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"api[_-]?key\s*=\s*[A-Za-z0-9_\-]{12,}", re.IGNORECASE),
    re.compile(r"x[_-]?rapidapi[_-]?key", re.IGNORECASE),
    re.compile(r"authorization", re.IGNORECASE),
    # Defence-in-depth: bearer tokens (SGO error responses sometimes
    # echo header examples). 12-char minimum avoids matching
    # the literal word "Bearer" in unrelated copy.
    re.compile(r"bearer\s+[A-Za-z0-9._\-]{12,}", re.IGNORECASE),
)

# Endpoint allow-list (host part only). Lowered before compare.
_ALLOWED_HOSTS = frozenset({"api.sportsgameodds.com"})

# Required top-level keys on a payload (must match the shape
# normalize_sgo_payload expects).
_REQUIRED_PAYLOAD_KEYS = ("events",)

# Required meta keys (Phase 1.A.3.2 contract — kept minimal so the
# Phase 1.A.3.3 recorder has room to add fields without rev'ing
# SANITIZATION_VERSION).
_REQUIRED_META_KEYS = (
    "fixture_version", "sanitization_version", "recorded_at",
    "sport", "sgo_endpoint", "event_id", "commence_time",
    "checksum_sha256",
)


class FixtureSanitizationError(ValueError):
    """Raised when a fixture or its meta-file violates a sanitization
    rule. The `rule` attribute identifies which rule fired so tests
    can assert against a stable code rather than the message text.
    """

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(f"[{rule}] {message}")
        self.rule = rule


@dataclass(frozen=True)
class LoadedFixture:
    """Container returned by `load_fixture` — convenience wrapper
    when tests want both payload + meta without unpacking.
    """
    payload: Dict[str, Any]
    meta:    Dict[str, Any]
    path:    Path
    meta_path: Path


# ── Pure rule helpers ────────────────────────────────────────────────
def _scan_for_leaked_keys(text: str) -> str | None:
    """Return the matched pattern name on first hit, else None."""
    for pat in _LEAKED_KEY_PATTERNS:
        if pat.search(text):
            return pat.pattern
    return None


def _scan_for_forbidden_field_names(obj: Any) -> str | None:
    """Walk an arbitrarily-nested dict/list and return the first
    forbidden field name encountered, else None.
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and _FORBIDDEN_FIELD_PATTERN.search(k):
                return k
            inner = _scan_for_forbidden_field_names(v)
            if inner is not None:
                return inner
    elif isinstance(obj, list):
        for item in obj:
            inner = _scan_for_forbidden_field_names(item)
            if inner is not None:
                return inner
    return None


def _endpoint_host_allowed(endpoint: str) -> bool:
    """Tolerant host check — accepts bare paths (`/v2/events…`)
    AND full URLs (`https://api.sportsgameodds.com/v2/events…`).
    """
    if not endpoint:
        return False
    if endpoint.startswith("/"):
        return True   # bare path — host is implied
    for host in _ALLOWED_HOSTS:
        if endpoint.lower().startswith(f"https://{host}"):
            return True
        if endpoint.lower().startswith(f"http://{host}"):
            return True
    return False


# ── Sanitization gatekeeper ──────────────────────────────────────────
def assert_sanitized(
    payload: Dict[str, Any],
    meta:    Dict[str, Any],
) -> None:
    """Run every sanitization rule. Raises `FixtureSanitizationError`
    with a stable `rule` code on the first violation.

    Rules (in evaluation order):
        meta_shape          — required meta keys present
        sanitization_version — meta matches current version
        endpoint_allow_list — sgo_endpoint host is permitted
        leaked_key_meta     — no API key pattern in meta JSON
        leaked_key_payload  — no API key pattern in payload JSON
        forbidden_field     — no PII-shape field names anywhere
        payload_shape       — required payload keys present
        recorded_at_present — meta.recorded_at non-empty
    """
    # ── meta_shape ──
    missing = [k for k in _REQUIRED_META_KEYS if k not in meta]
    if missing:
        raise FixtureSanitizationError(
            "meta_shape",
            f"meta is missing required keys: {missing}",
        )

    # ── sanitization_version ──
    if int(meta.get("sanitization_version", -1)) != SANITIZATION_VERSION:
        raise FixtureSanitizationError(
            "sanitization_version",
            f"meta.sanitization_version="
            f"{meta.get('sanitization_version')!r}, expected "
            f"{SANITIZATION_VERSION}",
        )

    # ── endpoint_allow_list ──
    if not _endpoint_host_allowed(str(meta.get("sgo_endpoint", ""))):
        raise FixtureSanitizationError(
            "endpoint_allow_list",
            f"sgo_endpoint host not permitted: "
            f"{meta.get('sgo_endpoint')!r}",
        )

    # ── leaked_key_meta ──
    meta_blob = json.dumps(meta, ensure_ascii=False)
    hit = _scan_for_leaked_keys(meta_blob)
    if hit is not None:
        raise FixtureSanitizationError(
            "leaked_key_meta",
            f"meta contains a leaked-key pattern: {hit!r}",
        )

    # ── leaked_key_payload ──
    payload_blob = json.dumps(payload, ensure_ascii=False)
    hit = _scan_for_leaked_keys(payload_blob)
    if hit is not None:
        raise FixtureSanitizationError(
            "leaked_key_payload",
            f"payload contains a leaked-key pattern: {hit!r}",
        )

    # ── forbidden_field ──
    bad = _scan_for_forbidden_field_names(payload)
    if bad is not None:
        raise FixtureSanitizationError(
            "forbidden_field",
            f"payload contains forbidden field name: {bad!r}",
        )

    # ── payload_shape ──
    missing = [k for k in _REQUIRED_PAYLOAD_KEYS if k not in payload]
    if missing:
        raise FixtureSanitizationError(
            "payload_shape",
            f"payload is missing required keys: {missing}",
        )

    # ── recorded_at_present ──
    if not str(meta.get("recorded_at", "")).strip():
        raise FixtureSanitizationError(
            "recorded_at_present",
            "meta.recorded_at is empty",
        )


# ── Loader ───────────────────────────────────────────────────────────
def _meta_path_for(payload_path: Path) -> Path:
    return payload_path.with_suffix(".meta.json")


def load_fixture(payload_path: str | Path) -> LoadedFixture:
    """Load a sanitized fixture from disk.

    Verifies:
      - sibling `.meta.json` exists
      - `assert_sanitized()` passes
      - `meta.checksum_sha256` matches the payload bytes

    Raises:
      - FileNotFoundError if either file is missing
      - FixtureSanitizationError on any rule violation
      - ValueError on checksum mismatch
    """
    payload_path = Path(payload_path)
    if not payload_path.is_file():
        raise FileNotFoundError(
            f"fixture payload not found: {payload_path}")
    meta_path = _meta_path_for(payload_path)
    if not meta_path.is_file():
        raise FileNotFoundError(
            f"fixture meta not found: {meta_path}")

    payload_bytes = payload_path.read_bytes()
    payload = json.loads(payload_bytes.decode("utf-8"))
    meta    = json.loads(meta_path.read_text(encoding="utf-8"))

    assert_sanitized(payload, meta)

    actual_sha = hashlib.sha256(payload_bytes).hexdigest()
    if actual_sha != meta["checksum_sha256"]:
        raise ValueError(
            f"checksum mismatch for {payload_path.name}: "
            f"meta says {meta['checksum_sha256']!r}, file is "
            f"{actual_sha!r}"
        )

    return LoadedFixture(
        payload=payload, meta=meta,
        path=payload_path, meta_path=meta_path,
    )


def list_fixtures(root: str | Path) -> List[Path]:
    """Return absolute paths of every `*.json` payload under `root`,
    excluding `*.meta.json` sidecars. Sorted for stable test
    iteration order.
    """
    root_p = Path(root)
    if not root_p.is_dir():
        return []
    out: List[Path] = []
    for p in root_p.rglob("*.json"):
        if p.name.endswith(".meta.json"):
            continue
        if p.name == "README.md":
            continue
        out.append(p.resolve())
    return sorted(out)


# ── Sanitization helpers reusable by the future recorder ─────────────
def sanitize_response_bytes(
    raw_bytes: bytes,
    *,
    api_key_to_strip: str | None = None,
) -> bytes:
    """Pure transform — strips the literal API key from a response
    body (string-level) and returns the sanitized bytes. The recorder
    will call this in Phase 1.A.3.3 BEFORE writing to disk.

    The current implementation handles:
      - exact literal-string occurrences of `api_key_to_strip`
      - `api_key=…` query-param patterns (case-insensitive)

    Header/cookie scrubbing happens at the HTTP-client level (the
    recorder never persists headers), so this function does NOT
    look at headers.
    """
    text = raw_bytes.decode("utf-8", errors="replace")
    if api_key_to_strip:
        text = text.replace(api_key_to_strip, REDACTION_TOKEN)
    text = re.sub(
        r"(api[_-]?key\s*=\s*)[A-Za-z0-9_\-]{12,}",
        rf"\1{REDACTION_TOKEN}",
        text, flags=re.IGNORECASE,
    )
    # Bearer-token defence-in-depth — strip the value while leaving
    # the surrounding "Bearer " prefix intact for readability.
    text = re.sub(
        r"(bearer\s+)[A-Za-z0-9._\-]{12,}",
        rf"\1{REDACTION_TOKEN}",
        text, flags=re.IGNORECASE,
    )
    return text.encode("utf-8")


def compute_checksum(payload_bytes: bytes) -> str:
    """sha256 of the exact bytes written to disk."""
    return hashlib.sha256(payload_bytes).hexdigest()


__all__ = [
    "FixtureSanitizationError",
    "LoadedFixture",
    "REDACTION_TOKEN",
    "SANITIZATION_VERSION",
    "assert_sanitized",
    "compute_checksum",
    "list_fixtures",
    "load_fixture",
    "sanitize_response_bytes",
]
