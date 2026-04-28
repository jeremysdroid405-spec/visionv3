"""
MLB PA-v2 Input Coverage Audit
==============================
Read-only diagnostic. Replays the MLB Total Bases v1 engine in-process
through the candidate-build phase, capturing BOTH selected and rejected
candidates so we can audit what fraction of the pipeline is actually
being powered by real lineup / team-total inputs vs falling back to
the 4.2 PA default.

Outputs (per spec):
  * Coverage rates: % with batting_order / team_implied_total /
    is_home_team / pa_source=lineup / pa_source=fallback
  * Cross-tabs: tier, side, market_type, selected-vs-rejected
  * Top 20 lineup-PA selected picks
  * Top 20 fallback-PA selected picks
  * Expected_PA distribution

Does NOT modify any model logic. Replays the same pipeline used by
`/app/backend/scripts/mlb_propvision_total_bases.py`.
"""
from __future__ import annotations

import asyncio, importlib.util, os, sys, statistics
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

# Import the (locked) engine.
spec = importlib.util.spec_from_file_location(
    "mlb_pv", "/app/backend/scripts/mlb_propvision_total_bases.py")
mlb_pv = importlib.util.module_from_spec(spec); spec.loader.exec_module(mlb_pv)

from services.scoring.tp_engine import compute_tp
from services.scoring.gates import NormalizedMetrics
from services.scoring.gates.thresholds import resolve_target_tier
from services.scoring.tier_evaluator import evaluate_tier_with_overrides


def _pct(num, denom):
    return (num / denom * 100) if denom else 0.0


def _summary(label, values, fmt="{:.2f}"):
    vals = [v for v in values if v is not None]
    if not vals: print(f"  {label:30s}: (n=0)"); return
    vals.sort(); n = len(vals); q = lambda p: vals[min(n-1, int(p*(n-1)))]
    print(f"  {label:30s}: n={n:>5}  min={fmt.format(vals[0])}  "
          f"med={fmt.format(q(.5))}  p75={fmt.format(q(.75))}  "
          f"max={fmt.format(vals[-1])}  avg={fmt.format(sum(vals)/n)}")


def _hist(label, values, bins, lab):
    counts = [0] * (len(bins) + 1)
    for v in values:
        if v is None: continue
        i = 0
        while i < len(bins) and v > bins[i]: i += 1
        counts[i] += 1
    print(f"  {label}")
    for l, c in zip(lab, counts):
        bar = "#" * min(60, c // max(1, max(counts) // 60 or 1))
        print(f"    {l:>10s}  {c:>5,}  {bar}")


# ---------------------------------------------------------------------------
async def main():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db  = cli[os.environ["DB_NAME"]]

    # ---- 1. Replicate the engine candidate-build phase --------------------
    by_name      = await mlb_pv.load_player_logs(db)
    statcast_pd  = await mlb_pv.load_statcast_features(db)
    identity_map = await mlb_pv.load_identity_map(db)
    raw_props    = await mlb_pv.load_total_bases_props(db)
    print(f"[AUDIT] loaded {len(raw_props):,} live Total Bases props")

    # Pivot mirroring engine main()
    bucket: Dict[Any, Dict[str, Any]] = defaultdict(dict)
    for p in raw_props:
        nm = mlb_pv._player_key(p)
        date = mlb_pv._slate_date(p)
        line = mlb_pv._f(p.get("line"))
        side = (p.get("recommendation") or "").upper()
        if not (nm and date and line is not None and side in ("OVER","UNDER")):
            continue
        bk = (p.get("bookmaker") or "").strip().lower()
        prop = bucket[(nm, date, line)]
        prop.setdefault("player", nm); prop.setdefault("date", date)
        prop.setdefault("line", line); prop.setdefault("event_id", p.get("event_id"))
        prop.setdefault("batting_order", mlb_pv._i(p.get("batting_order")))
        prop.setdefault("team", p.get("team"))
        prop.setdefault("opponent", p.get("opponent_team"))
        prop.setdefault("team_implied_total", mlb_pv._f(p.get("team_total")))
        prop.setdefault("is_home_team", bool(p.get("is_home_team")))
        prop["is_alt"] = mlb_pv._is_alt(p) or prop.get("is_alt", False)
        prop["pp_playable"] = bool(p.get("playable_on_pp")) or prop.get("pp_playable", False)
        prop.setdefault("books", {})
        for book in mlb_pv._TP_BOOK_KEYS:
            ln = mlb_pv._f(p.get(f"{book}_line"))
            od = mlb_pv._i(p.get(f"{book}_odds"))
            od_opp = mlb_pv._i(p.get(f"{book}_odds_opp"))
            if ln is not None and abs(ln - line) < 1e-6 and (od is not None or od_opp is not None):
                prop["books"].setdefault(book, {})
                if od is not None:
                    if side == "OVER":
                        prop["books"][book]["over"] = od
                        if od_opp is not None: prop["books"][book]["under"] = od_opp
                    else:
                        prop["books"][book]["under"] = od
                        if od_opp is not None: prop["books"][book]["over"] = od_opp

    candidates: List[Dict[str, Any]] = []
    feat_cache: Dict[Any, Dict[str, Any]] = {}
    for ck, prop in bucket.items():
        nm = prop["player"]; date = prop["date"]; line = prop["line"]
        plogs = by_name.get(nm) or []
        if not plogs: continue
        prior = [lg for lg in plogs if lg["date"] < date]
        if len(prior) < 10: continue
        cache_key = (nm, date)
        ce = feat_cache.get(cache_key)
        if ce is None:
            sc_row, ident = mlb_pv._statcast_for(
                statcast_pd, nm, date, identity_map=identity_map)
            mu, sigma, dbg = mlb_pv.predict_mu_sigma(
                prior_logs=prior, batting_order=prop.get("batting_order"),
                statcast=sc_row,
                team_implied_total=prop.get("team_implied_total"),
                is_home_team=prop.get("is_home_team"),
                matchup_factor_shadow=None,
                pitcher_confidence_flag=None)
            if mu is None: feat_cache[cache_key]={"_skip":True}; continue
            tb_window = prior[-mlb_pv.HISTORY_WINDOW_LONG:]
            tb_vals = mlb_pv._tb_values(tb_window)
            mean_tb = (statistics.mean(tb_vals) if tb_vals else None)
            std_tb = (statistics.stdev(tb_vals) if len(tb_vals)>=2 else None)
            cv = (std_tb / mean_tb) if mean_tb and mean_tb > 0 else None
            ce = {"_skip": False, "mu": mu, "sigma": sigma, "dbg": dbg,
                  "tb_vals": tb_vals, "cv": cv, "ident": ident}
            feat_cache[cache_key] = ce
        elif ce.get("_skip"): continue

        mu, sigma, cv = ce["mu"], ce["sigma"], ce["cv"]
        tb_vals = ce["tb_vals"]
        if not tb_vals: continue
        n = len(tb_vals)
        n_over = sum(1 for v in tb_vals if v > line)
        hr_o = n_over/n*100; hr_u = (n-n_over)/n*100
        ceil_o = sum(1 for v in tb_vals if v >= max(line*1.5,line+0.5))/n*100
        ceil_u = sum(1 for v in tb_vals if v <= min(line*0.5,line-0.5))/n*100
        tp_input = {"line": line}; book_count=0; any_devig=False
        for book, od in prop["books"].items():
            o_over=od.get("over"); o_under=od.get("under")
            if o_over is not None: tp_input[f"{book}_odds"]=o_over; book_count+=1
            if o_under is not None: tp_input[f"{book}_odds_opp"]=o_under
            if o_over is not None and o_under is not None: any_devig=True
        ref_book=ref_odds=None
        for book in mlb_pv._TP_BOOK_KEYS:
            ov = prop["books"].get(book,{}).get("over")
            if ov is not None: ref_book=book; ref_odds=int(ov); break
        if ref_odds is None: continue
        routed = resolve_target_tier("mlb", ref_odds)
        if routed is None: continue
        z = (line - mu)/sigma
        p_over=1.0-mlb_pv._norm_cdf(z); p_under=1.0-p_over
        tp_o=compute_tp(prop=tp_input,side="OVER")
        tp_u=compute_tp(prop=tp_input,side="UNDER")
        is_alt=bool(prop.get("is_alt"))
        market_type = "alternate" if is_alt else "standard"
        for side, p_side, hr_side, ceil_side, tp_res in (
            ("OVER",  p_over,  hr_o, ceil_o, tp_o),
            ("UNDER", p_under, hr_u, ceil_u, tp_u),
        ):
            tp_side = tp_res.get("tp")
            tp_source = tp_res.get("tp_source") or ("devig" if any_devig else "one_sided")
            edge_side = (p_side*100.0 - tp_side) if tp_side is not None else None
            if tp_side is not None:
                fair_prob = tp_side/100.0
                stab = max(0.3, min(1.0, 1.0 - (cv/3.0))) if cv else 0.5
                conf = (1.0 + (1.0 if hr_side>0 else 0.0) + (1.0 if book_count>=2 else 0.5))/3.0
                pos = max(0.0, p_side - fair_prob)
                vision_raw = pos * p_side * stab * conf
            else:
                vision_raw = None
            candidates.append(dict(
                player=nm, date=date, line=line, side=side,
                routed_tier=routed, ref_book=ref_book, ref_odds=ref_odds,
                book_count=book_count, tp=tp_side, tp_source=tp_source,
                edge_pct=edge_side, vision_raw=vision_raw, vision_score=None,
                hit_rate=hr_side, cv=cv, ceiling_rate=ceil_side,
                p_model_pct=p_side*100.0, mu=mu, sigma=sigma,
                is_alt=is_alt, market_type=market_type,
                # PA-v2 inputs
                batting_order=prop.get("batting_order"),
                team_implied_total=prop.get("team_implied_total"),
                is_home_team=prop.get("is_home_team"),
                expected_PA=ce["dbg"].get("pa_proj"),
                pa_source=ce["dbg"].get("pa_source"),
                rate_per_pa=ce["dbg"].get("rate_per_pa"),
                feature_source=ce["ident"]["feature_source"],
            ))

    # PP rules → collapse → vision percentile → gates
    candidates = [c for c in candidates if not (
        c["side"] == "UNDER" and (c["is_alt"]
                                    or c["routed_tier"] != "front_lines"))]
    by_group: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    for c in candidates:
        by_group[(c["player"], c["date"])].append(c)
    deduped = []
    for gk, members in by_group.items():
        we = [m for m in members if m["edge_pct"] is not None]
        if not we: continue
        deduped.append(max(we, key=lambda m: m["edge_pct"]))
    candidates = deduped

    by_slate = defaultdict(list)
    for c in candidates: by_slate[c["date"]].append(c)
    for d, slate in by_slate.items():
        rank = mlb_pv._percentile_rank([c["vision_raw"] for c in slate])
        for c in slate:
            v = c["vision_raw"]
            c["vision_score"] = 0.0 if v is None or v <= 0 else rank.get(v, 0.0)

    selected, rejected = [], []
    for c in candidates:
        m = NormalizedMetrics(
            sport="mlb", tier=c["routed_tier"],
            stat_family="total_bases", side=c["side"],
            reference_book=c["ref_book"], reference_odds=c["ref_odds"],
            book_count=c["book_count"], tp=c["tp"],
            hit_rate=c["hit_rate"], hit_rate_l20=c["hit_rate"],
            hit_rate_l10=None, hit_rate_l5=None, hit_rate_sample_size=20,
            ceiling_rate=c["ceiling_rate"], cv=c["cv"], edge_pct=c["edge_pct"],
            line=c["line"], vision_score=c["vision_score"],
            tp_source=c["tp_source"], is_alt=c["is_alt"],
            p_model_pct=c["p_model_pct"], extras={"cv_cap_override": None})
        if evaluate_tier_with_overrides(m).passed:
            c["tier_final"] = c["routed_tier"]; selected.append(c)
        else:
            c["tier_final"] = None; rejected.append(c)

    # ============== REPORT ==================================================
    print()
    print("#" * 80)
    print("#  MLB PA-v2 INPUT COVERAGE AUDIT")
    print("#  (read-only — no model state changed)")
    print("#" * 80)
    print()

    # ---- Coverage rates ---------------------------------------------------
    def _cov(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        n = len(rows)
        bo  = sum(1 for r in rows if r.get("batting_order") is not None)
        tt  = sum(1 for r in rows if r.get("team_implied_total") is not None)
        h   = sum(1 for r in rows if r.get("is_home_team") is not None)
        ln  = sum(1 for r in rows if r.get("pa_source") == "lineup")
        fb  = sum(1 for r in rows if r.get("pa_source") == "fallback")
        return {"n": n, "bo": bo, "tt": tt, "h": h, "ln": ln, "fb": fb}

    print("=" * 80); print("  COVERAGE — full candidate pool (post-collapse)")
    print("=" * 80)
    cov_all = _cov(candidates)
    cov_sel = _cov(selected)
    cov_rej = _cov(rejected)
    print(f"  {'metric':30s} {'all':>10s} {'selected':>10s} {'rejected':>10s}")
    for label, key in [
        ("total candidates",        "n"),
        ("batting_order present",   "bo"),
        ("team_implied_total present","tt"),
        ("is_home_team present",    "h"),
        ("pa_source=lineup",        "ln"),
        ("pa_source=fallback",      "fb"),
    ]:
        a = cov_all[key]; s = cov_sel[key]; r = cov_rej[key]
        if key == "n":
            print(f"  {label:30s} {a:>10,} {s:>10,} {r:>10,}")
        else:
            print(f"  {label:30s} {a:>5,} ({_pct(a, cov_all['n']):>4.1f}%) "
                  f"{s:>5,} ({_pct(s, cov_sel['n']):>4.1f}%) "
                  f"{r:>5,} ({_pct(r, cov_rej['n']):>4.1f}%)")
    print()

    # ---- Cross-tabs ------------------------------------------------------
    def _xtab(title: str, key_fn):
        groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for c in candidates: groups[key_fn(c)].append(c)
        print("=" * 80); print(f"  {title}"); print("=" * 80)
        print(f"  {'segment':22s} {'n':>5s} {'lineup':>10s} {'fallback':>10s} "
              f"{'BO%':>6s} {'TT%':>6s}")
        for k, rows in sorted(groups.items()):
            n = len(rows)
            ln = sum(1 for r in rows if r.get("pa_source") == "lineup")
            fb = sum(1 for r in rows if r.get("pa_source") == "fallback")
            bo = sum(1 for r in rows if r.get("batting_order") is not None)
            tt = sum(1 for r in rows if r.get("team_implied_total") is not None)
            print(f"  {str(k):22s} {n:>5,} "
                  f"{ln:>4,} ({_pct(ln,n):>4.1f}%) "
                  f"{fb:>4,} ({_pct(fb,n):>4.1f}%) "
                  f"{_pct(bo,n):>5.1f}% {_pct(tt,n):>5.1f}%")
        print()

    _xtab("CROSS-TAB: by routed_tier",   lambda c: c["routed_tier"])
    _xtab("CROSS-TAB: by side",          lambda c: c["side"])
    _xtab("CROSS-TAB: by market_type",   lambda c: c["market_type"])
    _xtab("CROSS-TAB: selected vs rejected",
          lambda c: "selected" if c.get("tier_final") else "rejected")

    # ---- Top 20 lineup PA picks ------------------------------------------
    print("=" * 80)
    print("  TOP 20 SELECTED PICKS — pa_source='lineup' (lineup card present)")
    print("=" * 80)
    print(f"  {'#':>2}  {'player':22s} {'date':10s} {'side':5s}  "
          f"{'tier':12s} {'BO':>3s} {'TIT':>5s} {'home':>4s} "
          f"{'PA':>4s}  {'mu':>4s}  {'edge':>6s}")
    sel_lineup = sorted(
        [c for c in selected if c.get("pa_source") == "lineup"],
        key=lambda x: -(x["edge_pct"] or -999))[:20]
    for i, c in enumerate(sel_lineup, start=1):
        e   = c["edge_pct"]
        bo  = c.get("batting_order")
        tit = c.get("team_implied_total")
        bo_s  = str(bo)        if bo  is not None else "—"
        tit_s = f"{tit:.1f}"   if tit is not None else "—"
        e_s   = f"{e:+.1f}"    if e   is not None else "—"
        home_s = "Y" if c.get("is_home_team") else "n"
        print(f"  {i:>2}  {(c['player'] or '')[:22]:22s} {c['date']:10s} "
              f"{c['side']:5s}  {c['tier_final']:12s} "
              f"{bo_s:>3} {tit_s:>5} {home_s:>4} "
              f"{(c['expected_PA'] or 0):>4.2f}  {c['mu']:>4.2f}  {e_s:>6}")
    if not sel_lineup: print("    (none)")
    print()

    # ---- Top 20 fallback PA picks ----------------------------------------
    print("=" * 80)
    print("  TOP 20 SELECTED PICKS — pa_source='fallback' (no lineup card)")
    print("=" * 80)
    print(f"  {'#':>2}  {'player':22s} {'date':10s} {'side':5s}  "
          f"{'tier':12s} {'BO':>3s} {'TIT':>5s} {'home':>4s} "
          f"{'PA':>4s}  {'mu':>4s}  {'edge':>6s}")
    sel_fb = sorted(
        [c for c in selected if c.get("pa_source") == "fallback"],
        key=lambda x: -(x["edge_pct"] or -999))[:20]
    for i, c in enumerate(sel_fb, start=1):
        e   = c["edge_pct"]
        bo  = c.get("batting_order")
        tit = c.get("team_implied_total")
        bo_s  = str(bo)        if bo  is not None else "—"
        tit_s = f"{tit:.1f}"   if tit is not None else "—"
        e_s   = f"{e:+.1f}"    if e   is not None else "—"
        home_s = "Y" if c.get("is_home_team") else "n"
        print(f"  {i:>2}  {(c['player'] or '')[:22]:22s} {c['date']:10s} "
              f"{c['side']:5s}  {c['tier_final']:12s} "
              f"{bo_s:>3} {tit_s:>5} {home_s:>4} "
              f"{(c['expected_PA'] or 0):>4.2f}  {c['mu']:>4.2f}  {e_s:>6}")
    if not sel_fb: print("    (none)")
    print()

    # ---- expected_PA distribution -----------------------------------------
    print("=" * 80); print("  EXPECTED_PA DISTRIBUTION (all candidates)")
    print("=" * 80)
    _summary("expected_PA — ALL",       [c["expected_PA"] for c in candidates])
    _summary("expected_PA — selected",  [c["expected_PA"] for c in selected])
    _summary("expected_PA — rejected",  [c["expected_PA"] for c in rejected])
    _summary("expected_PA — lineup",
              [c["expected_PA"] for c in candidates
               if c.get("pa_source") == "lineup"])
    _summary("expected_PA — fallback",
              [c["expected_PA"] for c in candidates
               if c.get("pa_source") == "fallback"])
    print()
    _hist("expected_PA histogram (all candidates):",
          [c["expected_PA"] for c in candidates],
          (3.4, 3.7, 4.0, 4.2, 4.4, 4.7, 5.0),
          ["<3.4", "3.4–3.7", "3.7–4.0", "4.0–4.2", "4.2–4.4",
           "4.4–4.7", "4.7–5.0", "5.0+"])
    print()

    # ---- Verdict line -----------------------------------------------------
    print("=" * 80); print("  VERDICT — is PA-v2 actually being powered?")
    print("=" * 80)
    pct_lineup_all = _pct(cov_all["ln"], cov_all["n"])
    pct_lineup_sel = _pct(cov_sel["ln"], cov_sel["n"])
    print(f"  candidate-pool lineup share : {pct_lineup_all:>5.1f}%")
    print(f"  selected-pick lineup share  : {pct_lineup_sel:>5.1f}%")
    if pct_lineup_sel >= 50:
        print(f"  → MAJORITY of selected picks ARE driven by real lineup cards.")
    elif pct_lineup_sel >= 20:
        print(f"  → MIXED: a meaningful minority uses lineup; the rest fall back.")
    else:
        print(f"  → MOSTLY FALLBACK: PA-v2 is effectively running on the 4.2 default.")
    print()
    print("[AUDIT] DONE — read-only.")


if __name__ == "__main__":
    asyncio.run(main())
