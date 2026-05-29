"""
Team Master Hub — canonical team identity (SSOT for team_id).

Public API for Phase 1.A.1 (Master Hub bootstrap/seeder slice):
    - `SEED_PATH`              : canonical seed file location
    - `COLLECTION_NAME`        : `team_master_hub`
    - `load_seed_doc(path)`    : pure I/O — parse the seed JSON
    - `build_upsert_ops(doc)`  : pure transform — JSON → UpdateOne[]
    - `ensure_indexes(db)`     : create unique/sparse indexes
    - `seed_team_master_hub`   : full seed flow (indexes + bulk upsert)
    - `audit_team_master_hub`  : read-only coverage audit
    - `seed_and_audit(db,…)`   : combined runner used by the admin
                                  endpoint + CLI (single SSOT)

Hard limits (preview-only): no SGO API, no historical ingest, no UI.

Architecture: /app/memory/TEAM_PROPS_ARCHITECTURE.md §1.2 / §12.7.
"""
from __future__ import annotations

from .seeder import (
    COLLECTION_NAME,
    SEED_PATH,
    audit_team_master_hub,
    build_upsert_ops,
    diff_seed_vs_collection_docs,
    ensure_indexes,
    load_seed_doc,
    seed_and_audit,
    seed_team_master_hub,
)

__all__ = [
    "COLLECTION_NAME",
    "SEED_PATH",
    "audit_team_master_hub",
    "build_upsert_ops",
    "diff_seed_vs_collection_docs",
    "ensure_indexes",
    "load_seed_doc",
    "seed_and_audit",
    "seed_team_master_hub",
]
