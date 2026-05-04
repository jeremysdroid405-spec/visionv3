"""
SSOT Tier F #4 — ScoreDocument schema parity guard.
====================================================

This test is the structural lock that prevents the gap that
required Tier F #4 in the first place: an adapter adds a new
field to `_SCORE_OUTPUT_FIELDS` but forgets to declare it on
`ScoreDocument` (or vice-versa), and the field starts silently
flowing through `extra="allow"`.

After Tier F #4 the schema is `extra="forbid"`, so any drift
fails at write time. This test also catches drift in CI before
the recompute pipeline runs.

Invariants:
  1. Every field in `_IDENTITY_FIELDS ∪ _SCORE_OUTPUT_FIELDS ∪
     _UNIVERSAL_POOL_FIELDS ∪ {version_tag, computed_at,
     scored_at}` MUST be declared on `ScoreDocument`.
  2. `ScoreDocument` MAY declare a small set of extras that the
     projector does not stamp (currently 9 fields — see
     comments in score_document_schema.py). These exist for
     adapters that compute the value but where the projection
     allowlist hasn't been refreshed yet, OR for diagnostic
     fields the writer chose to drop. We track the count
     explicitly so it cannot grow unnoticed.
  3. The model_config MUST be `extra="forbid"`.
"""
from __future__ import annotations

import os
import asyncio
from typing import Set

import pytest

from services.scoring.score_document_schema import ScoreDocument
from services.scoring.prop_scores_store import (
    _IDENTITY_FIELDS,
    _SCORE_OUTPUT_FIELDS,
    _UNIVERSAL_POOL_FIELDS,
)


_VERSIONING = {"version_tag", "computed_at", "scored_at"}

_PROJECTED: Set[str] = (
    set(_IDENTITY_FIELDS)
    | set(_SCORE_OUTPUT_FIELDS)
    | set(_UNIVERSAL_POOL_FIELDS)
    | _VERSIONING
)

# Tracked extras — fields declared on the schema that the projector
# allowlist does NOT stamp. Each one is intentional; growing this set
# requires a CHANGELOG note. If the count of declared-but-not-projected
# fields drifts up, the test fails.
_ALLOWED_DECLARED_EXTRAS: Set[str] = {
    "consistency_band",
    "half_line_variance",
    "hetero_sigma_multiplier",
    "hit_distance_from_line",
    "miss_distance_from_line",
    "l5_rate",
    "l10_rate",
    "l20_rate",
    "stability_half_line",
    # 2026-05-04: `momentum_data` is declared on ScoreDocument so the
    # `extra="forbid"` write contract accepts it, but it is NOT
    # stamped by the recompute projector — the writer is
    # `services/master_sync.py::_enrich_nba_momentum`, which
    # bulk-writes `$set: momentum_data` AFTER recompute completes.
    # Field ownership is registered at
    # `field_ownership/registry.py:momentum_data`.
    "momentum_data",
}


def test_strict_extras_forbid():
    """Schema MUST be `extra="forbid"`. Tier F #4 lockdown."""
    extra_setting = ScoreDocument.model_config.get("extra")
    assert extra_setting == "forbid", (
        f"ScoreDocument.model_config.extra must be 'forbid' (Tier F #4); "
        f"got {extra_setting!r}. Reverting this without updating the parity "
        f"guard reopens the silent-drift bug class."
    )


def test_every_projected_field_is_declared():
    declared = set(ScoreDocument.model_fields.keys())
    missing = sorted(_PROJECTED - declared)
    assert not missing, (
        f"{len(missing)} field(s) projected by `_project_score_doc` "
        f"are NOT declared on ScoreDocument. With `extra='forbid'` LIVE "
        f"this would hard-fail every recompute write batch.\n"
        f"Missing: {missing}\n"
        f"Fix: add `Optional[...] = None` declarations on "
        f"`services/scoring/score_document_schema.py::ScoreDocument`."
    )


def test_no_unaccounted_declared_extras():
    declared = set(ScoreDocument.model_fields.keys())
    extras = sorted(declared - _PROJECTED)
    unaccounted = sorted(set(extras) - _ALLOWED_DECLARED_EXTRAS)
    assert not unaccounted, (
        f"{len(unaccounted)} field(s) declared on ScoreDocument but NOT in "
        f"the projector allowlist (and NOT in `_ALLOWED_DECLARED_EXTRAS`). "
        f"Either add them to `_SCORE_OUTPUT_FIELDS` so the writer stamps "
        f"them, or add them to `_ALLOWED_DECLARED_EXTRAS` here with a "
        f"CHANGELOG entry explaining why.\n"
        f"Unaccounted: {unaccounted}"
    )


def test_required_identity_fields_are_required():
    """Identity + versioning fields must be NON-optional on the schema."""
    required_on_schema = {
        "canonical_key",
        "sport",
        "stat_type",
        "line",
        "version_tag",
        "computed_at",
        "scored_at",
    }
    for name in required_on_schema:
        info = ScoreDocument.model_fields[name]
        assert info.is_required(), (
            f"Field `{name}` MUST be declared as required (no default) "
            f"on ScoreDocument; identity/versioning fields are the "
            f"contract anchor."
        )


def test_live_db_has_zero_undeclared_fields():
    """Every field on every live `*_prop_scores` doc must be declared.

    Skips when MONGO_URL is unreachable (so the test still runs in
    contributor environments without a Mongo handle)."""
    from dotenv import load_dotenv
    load_dotenv()
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        pytest.skip("MONGO_URL/DB_NAME not set")

    try:
        from motor.motor_asyncio import AsyncIOMotorClient
    except ImportError:
        pytest.skip("motor not installed")

    declared = set(ScoreDocument.model_fields.keys())
    declared.add("_id")  # Mongo's own field; never on the schema.

    async def _scan() -> Set[str]:
        client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=2000)
        try:
            db = client[db_name]
            seen: Set[str] = set()
            for coll in ("nba_prop_scores", "mlb_prop_scores"):
                async for d in db[coll].find({}).limit(2000):
                    seen.update(d.keys())
            return seen
        finally:
            client.close()

    try:
        seen = asyncio.get_event_loop().run_until_complete(_scan())
    except RuntimeError:
        # In case an event loop is already running (e.g. pytest-asyncio).
        seen = asyncio.run(_scan())
    except Exception as exc:
        pytest.skip(f"Mongo unreachable: {exc}")
        return

    undeclared = sorted(seen - declared)
    assert not undeclared, (
        f"{len(undeclared)} field(s) found on live *_prop_scores docs "
        f"that are NOT declared on ScoreDocument. With `extra='forbid'` "
        f"these would fail writes.\nUndeclared: {undeclared}"
    )
