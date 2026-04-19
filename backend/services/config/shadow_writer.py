"""
ShadowWriter — thin Motor-collection fan-out adapter.

Used during Wave 1 shadow-writes of the NBA rebuild to mirror every mutation
against a `primary` collection to one or more `shadow` collections, while
keeping ALL read operations pinned to the primary.

Design rules
------------
- Mutating methods (`insert_*`, `update_*`, `replace_one`, `delete_*`,
  `find_one_and_*`, `bulk_write`, index ops, `drop`) fan out to
  [primary, *shadows] concurrently via `asyncio.gather`.
- The return value is ALWAYS the primary's result — shadows are advisory
  mirrors and must never alter call-site semantics.
- Shadow failures are logged but never raised. A shadow that silently
  drifts shows up in `board_drift_ledger` via the divergence monitor,
  which is the single source of truth for shadow health.
- Read methods (`find`, `find_one`, `count_documents`,
  `estimated_document_count`, `aggregate`, `distinct`, `list_indexes`,
  attribute access, ...) are delegated to the primary directly.

This module has zero awareness of registry concepts — it only knows about
Motor collection handles. The registry (`collection_names.COLL.handle`) is
responsible for deciding when to construct a ShadowWriter.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Iterable, List

logger = logging.getLogger(__name__)

# Every name in this set is fanned out to [primary, *shadows].
# Kept conservative: only methods that actually mutate state.
_MUTATION_METHODS = frozenset({
    "insert_one",
    "insert_many",
    "update_one",
    "update_many",
    "replace_one",
    "delete_one",
    "delete_many",
    "find_one_and_update",
    "find_one_and_replace",
    "find_one_and_delete",
    "bulk_write",
    "create_index",
    "create_indexes",
    "drop_index",
    "drop_indexes",
    "drop",
    "rename",
})


class ShadowWriter:
    """Fan-out wrapper for multi-collection writes with primary-only reads."""

    __slots__ = ("_primary", "_shadows", "_label")

    def __init__(self, primary, shadows: Iterable) -> None:
        self._primary = primary
        self._shadows: List[Any] = list(shadows)
        # Stable label for logs (primary collection name).
        try:
            self._label = getattr(primary, "name", None) or repr(primary)
        except Exception:
            self._label = "<primary>"

    def __repr__(self) -> str:
        shadow_names = [getattr(s, "name", repr(s)) for s in self._shadows]
        return f"ShadowWriter(primary={self._label!r}, shadows={shadow_names!r})"

    # -------------------------------------------------------------------
    # Introspection helpers (used by the divergence monitor)
    # -------------------------------------------------------------------
    @property
    def primary(self):
        return self._primary

    @property
    def shadows(self) -> List[Any]:
        return list(self._shadows)

    # -------------------------------------------------------------------
    # Attribute routing
    # -------------------------------------------------------------------
    def __getattr__(self, name: str):
        # Mutations → fan out; reads → primary.
        if name in _MUTATION_METHODS:
            return self._make_fanout(name)
        return getattr(self._primary, name)

    def __getitem__(self, key):
        # Sub-collections via `coll["sub"]` — pass through to primary only.
        # We do NOT attempt to shadow sub-collection access during Wave 1.
        return self._primary[key]

    # -------------------------------------------------------------------
    # Fan-out machinery
    # -------------------------------------------------------------------
    def _make_fanout(self, method_name: str):
        primary_method = getattr(self._primary, method_name)
        shadow_methods = [getattr(s, method_name) for s in self._shadows]
        label = self._label

        async def _fanned(*args, **kwargs):
            coros = [primary_method(*args, **kwargs)]
            coros.extend(m(*args, **kwargs) for m in shadow_methods)
            results = await asyncio.gather(*coros, return_exceptions=True)
            primary_result = results[0]
            for i, r in enumerate(results[1:], start=0):
                if isinstance(r, BaseException):
                    shadow_name = getattr(self._shadows[i], "name", f"shadow[{i}]")
                    logger.error(
                        "[SHADOW_WRITER] %s.%s failed on shadow=%s: %r",
                        label, method_name, shadow_name, r,
                    )
            if isinstance(primary_result, BaseException):
                raise primary_result
            return primary_result

        return _fanned
