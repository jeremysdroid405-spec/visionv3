"""
Universal probability-translator layer (2026-04-24).

Sport-agnostic Empirical-CDF probability translator. Converts a
(projection, line) pair from ANY sport's projection model into a
calibrated over/under probability using the empirical residual
distribution learnt from historical (projection, actual) pairs.

Design invariants
-----------------
- The ECDF engine carries zero sport-specific knowledge. Sport code
  is responsible only for mapping its native stats to a `stat_family`
  string and supplying (projection, actual) training records.
- Artifacts persist at:
      models/probability/ecdf/{sport}/{stat_family}.pkl
  one pkl per (sport, stat_family).
- Fit = bin projection into quantile-derived buckets, store sorted
  residuals per bucket. Predict = digitize projection, `searchsorted`
  for P(ε > line − projection).
- NO Gaussian assumption. NO projection override. Returns None when
  the artifact is missing / too small so the caller can fall back.

Artifact schema (pickled dict)
------------------------------
  sport                       : str lower ("nba", "mlb", "nfl", ...)
  stat_family                 : str lower ("pts", "pra", "hits", ...)
  version                     : str    ("UNIVERSAL_ECDF_v1")
  trained_at                  : iso timestamp
  source_model_version        : Optional[str] — tag of the projection
                                 model whose residuals were used, e.g.
                                 "NBA_VK_v2_5yr_weighted_pruned52"
  projection_bucket_edges     : np.ndarray, length n_buckets+1,
                                 first = -inf, last = +inf
  sorted_residuals_by_bucket  : Dict[int, np.ndarray] — ascending-sorted
                                 residual = actual - projection per bucket
  bucket_ns                   : Dict[int, int]
  sample_count                : int — total residual pairs used
  min_bucket_n                : int
  n_buckets                   : int
"""
from __future__ import annotations

import logging
import os
import pickle
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

VERSION = "UNIVERSAL_ECDF_v1"
DEFAULT_N_BUCKETS = 10
DEFAULT_MIN_BUCKET_N = 20          # predict-time safety floor
DEFAULT_MIN_BUCKET_N_WARN = 200    # fit-time warning threshold
DEFAULT_ROOT = "/app/backend/models/probability/ecdf"


@dataclass
class ECDFPrediction:
    p_over: float
    p_under: float
    bucket: int
    bucket_n: int
    version: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "p_over": self.p_over,
            "p_under": self.p_under,
            "bucket": self.bucket,
            "bucket_n": self.bucket_n,
            "version": self.version,
        }


class UniversalECDFProbability:
    """Sport/stat-agnostic ECDF probability translator.

    The instance caches loaded artifacts in memory; call
    `invalidate()` (or construct a new instance) to force reload.
    """

    def __init__(self, root: str = DEFAULT_ROOT) -> None:
        self.root = root
        self._cache: Dict[Tuple[str, str], Optional[Dict[str, Any]]] = {}
        self._load_attempted: Dict[Tuple[str, str], bool] = {}

    # ---------- paths / io ----------------------------------------------
    def _key(self, sport: str, stat_family: str) -> Tuple[str, str]:
        return sport.strip().lower(), stat_family.strip().lower()

    def artifact_path(self, sport: str, stat_family: str) -> str:
        s, f = self._key(sport, stat_family)
        return os.path.join(self.root, s, f"{f}.pkl")

    # ---------- public api ---------------------------------------------
    def fit(
        self,
        sport: str,
        stat_family: str,
        records: Iterable[Dict[str, float]],
        *,
        n_buckets: int = DEFAULT_N_BUCKETS,
        source_model_version: Optional[str] = None,
        min_bucket_n_warn: int = DEFAULT_MIN_BUCKET_N_WARN,
    ) -> Dict[str, Any]:
        """Fit the ECDF artifact from (projection, actual) records.

        `records` may be any iterable of dicts containing `projection`
        and `actual` (both required) — or numpy arrays — or an object
        with `.projection` / `.actual` attributes. All forms below are
        accepted:
            [{"projection": 10.1, "actual": 12}, ...]
            [(10.1, 12), (...)]                            # tuples
            {"projection": np.array([...]), "actual": np.array([...])}

        Returns the built artifact dict (also persisted to disk via
        `save`).

        Raises ValueError when fewer than n_buckets * 20 usable
        records are supplied (would otherwise produce trivially small
        buckets that the predict-time safety floor would reject).
        """
        projs, acts = _coerce_records(records)
        if len(projs) < n_buckets * 20:
            raise ValueError(
                f"fit requires >= {n_buckets * 20} records "
                f"({n_buckets} buckets × 20 floor); got {len(projs)}"
            )
        residuals = acts - projs

        # Quantile edges on projection. Clamp the outermost edges to
        # ±inf so inference is safe for OOS projections.
        quantiles = np.linspace(0.0, 1.0, n_buckets + 1)
        edges = np.quantile(projs, quantiles).astype(np.float64)

        # De-duplicate inner edges to handle discrete / zero-heavy
        # projection distributions (MLB total_bases, home_runs, walks
        # are the canonical case — many predictions cluster at 0, so
        # multiple quantile edges land on the same value, creating
        # empty buckets downstream). After dedup we may have FEWER
        # than n_buckets effective buckets; that's deliberate.
        inner = edges[1:-1]
        unique_inner = np.unique(inner)
        # Build the final edges with deduped inner set + ±inf guards
        edges = np.concatenate([[-np.inf], unique_inner, [np.inf]]).astype(
            np.float64
        )
        effective_buckets = len(edges) - 1
        if effective_buckets < n_buckets:
            logger.info(
                f"[UNIVERSAL_ECDF] {sport}/{stat_family}: quantile edges "
                f"collapsed from {n_buckets} to {effective_buckets} "
                f"unique buckets (discrete / zero-heavy distribution)."
            )

        bins = np.digitize(projs, edges[1:-1])
        sorted_res: Dict[int, np.ndarray] = {}
        bucket_ns: Dict[int, int] = {}
        for b in range(effective_buckets):
            mask = bins == b
            sorted_res[int(b)] = np.sort(residuals[mask])
            bucket_ns[int(b)] = int(mask.sum())
        min_bucket = min(bucket_ns.values()) if bucket_ns else 0
        if min_bucket < min_bucket_n_warn:
            logger.warning(
                f"[UNIVERSAL_ECDF] {sport}/{stat_family}: smallest "
                f"bucket has {min_bucket} residuals "
                f"(< warn threshold {min_bucket_n_warn}). Fit anyway."
            )

        artifact = {
            "sport": sport.strip().lower(),
            "stat_family": stat_family.strip().lower(),
            "version": VERSION,
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "source_model_version": source_model_version,
            "projection_bucket_edges": edges,
            "sorted_residuals_by_bucket": sorted_res,
            "bucket_ns": bucket_ns,
            "sample_count": int(len(projs)),
            "min_bucket_n": min_bucket,
            "n_buckets": int(effective_buckets),
        }
        self.save(sport, stat_family, artifact)
        # refresh cache with new artifact so immediate predicts hit it
        self._cache[self._key(sport, stat_family)] = artifact
        self._load_attempted[self._key(sport, stat_family)] = True
        return artifact

    def save(
        self, sport: str, stat_family: str, artifact: Dict[str, Any],
    ) -> str:
        """Persist `artifact` to disk. Returns the written path."""
        path = self.artifact_path(sport, stat_family)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(artifact, f)
        return path

    def load(
        self, sport: str, stat_family: str,
    ) -> Optional[Dict[str, Any]]:
        """Lazy-load the artifact for (sport, stat_family). Returns
        None when the artifact is missing / unreadable; subsequent
        calls for the same key won't re-attempt IO."""
        key = self._key(sport, stat_family)
        if self._load_attempted.get(key):
            return self._cache.get(key)
        self._load_attempted[key] = True
        path = self.artifact_path(sport, stat_family)
        if not os.path.exists(path):
            logger.info(
                f"[UNIVERSAL_ECDF] no artifact for {sport}/{stat_family} "
                f"at {path}; caller must fall back."
            )
            self._cache[key] = None
            return None
        try:
            with open(path, "rb") as f:
                artifact = pickle.load(f)
            self._cache[key] = artifact
            logger.info(
                f"[UNIVERSAL_ECDF] loaded {sport}/{stat_family} "
                f"(version={artifact.get('version')}, "
                f"n_buckets={artifact.get('n_buckets')}, "
                f"min_bucket_n={artifact.get('min_bucket_n')}, "
                f"samples={artifact.get('sample_count')})"
            )
            return artifact
        except Exception as exc:
            logger.warning(
                f"[UNIVERSAL_ECDF] failed to load {sport}/{stat_family}: {exc}"
            )
            self._cache[key] = None
            return None

    def is_available(self, sport: str, stat_family: str) -> bool:
        return self.load(sport, stat_family) is not None

    def predict_over_probability(
        self,
        sport: str,
        stat_family: str,
        projection: Optional[float],
        line: Optional[float],
        context: Optional[Dict[str, Any]] = None,   # reserved; unused
    ) -> Optional[ECDFPrediction]:
        """Compute P(actual > line) using the fitted ECDF for
        (sport, stat_family).

        Returns None (caller must fall back) when:
          - projection or line is None,
          - artifact missing,
          - selected bucket has < DEFAULT_MIN_BUCKET_N residuals,
          - any unexpected internal error.

        `context` is a placeholder for a future 2-D lookup (e.g.
        conditioning on minutes_bucket / opportunity_bucket /
        odds_bucket). Today any non-None context is silently ignored
        — this lets callers start populating it without breaking.
        """
        if projection is None or line is None:
            return None
        art = self.load(sport, stat_family)
        if art is None:
            return None
        try:
            edges = art["projection_bucket_edges"]
            inner = edges[1:-1]
            bucket = int(np.digitize(float(projection), inner))
            r = art["sorted_residuals_by_bucket"].get(bucket)
            if r is None or len(r) < DEFAULT_MIN_BUCKET_N:
                return None
            needed = float(line) - float(projection)
            pos = int(np.searchsorted(r, needed, side="right"))
            p_over = 1.0 - pos / len(r)
            p_over = max(0.0, min(1.0, p_over))
            return ECDFPrediction(
                p_over=p_over,
                p_under=1.0 - p_over,
                bucket=bucket,
                bucket_n=int(len(r)),
                version=str(art.get("version") or ""),
            )
        except Exception as exc:
            logger.warning(
                f"[UNIVERSAL_ECDF] lookup failed for "
                f"{sport}/{stat_family}: {exc}"
            )
            return None

    def invalidate(self, sport: Optional[str] = None,
                   stat_family: Optional[str] = None) -> None:
        """Drop cache. Call with no args to flush everything; pass a
        key to flush a single entry."""
        if sport is None and stat_family is None:
            self._cache.clear()
            self._load_attempted.clear()
            return
        if sport is not None and stat_family is not None:
            key = self._key(sport, stat_family)
            self._cache.pop(key, None)
            self._load_attempted.pop(key, None)


# -------------------------------------------------------------------
# Module-level singleton — the scoring adapters use this instance;
# tests can patch ROOT via the `root` kwarg on a fresh instance.
# -------------------------------------------------------------------
_SINGLETON: Optional[UniversalECDFProbability] = None


def get_universal_ecdf() -> UniversalECDFProbability:
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = UniversalECDFProbability()
    return _SINGLETON


def reset_universal_ecdf_singleton() -> None:
    """Test helper — force a fresh singleton on next access."""
    global _SINGLETON
    _SINGLETON = None


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------
def _coerce_records(records) -> Tuple[np.ndarray, np.ndarray]:
    """Accept list[dict], list[tuple], or dict-of-arrays. Return
    (projections, actuals) as float64 arrays with rows dropped when
    either value is None/NaN."""
    if isinstance(records, dict) and "projection" in records and "actual" in records:
        projs = np.asarray(records["projection"], dtype=np.float64)
        acts = np.asarray(records["actual"], dtype=np.float64)
    else:
        projs_l: List[float] = []
        acts_l: List[float] = []
        for rec in records:
            if isinstance(rec, dict):
                p = rec.get("projection"); a = rec.get("actual")
            elif isinstance(rec, (list, tuple)) and len(rec) >= 2:
                p, a = rec[0], rec[1]
            else:
                p = getattr(rec, "projection", None)
                a = getattr(rec, "actual", None)
            if p is None or a is None:
                continue
            projs_l.append(float(p))
            acts_l.append(float(a))
        projs = np.asarray(projs_l, dtype=np.float64)
        acts = np.asarray(acts_l, dtype=np.float64)
    mask = np.isfinite(projs) & np.isfinite(acts)
    return projs[mask], acts[mask]
