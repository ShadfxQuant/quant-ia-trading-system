"""
Systematic exit + entry parameter sweep on SPY 1H.

Baseline to beat (user-specified "#10 MIDDLE"): CAGR 12.5%, PF 2.83,
DD 7.9%, Sharpe 0.52. Note: baseline uses base_size_pct=0.30 +
capital_cap_pct=1.00 (NOT current production 0.75/2.50 with leverage).

Sweep approach:
  1. Prepare df once (expensive — only need indicators once).
  2. For each cfg variant, overwrite pullback_Signal column by calling
     pullback.generate_signals(df, cfg=variant). Same for trend_carry.
  3. Run run_portfolio with StrategySpec using the variant cfg.
  4. Compute metrics; gate by DD ≤ 8.5% AND n ≥ 50.
  5. Report top-N by each ranking metric.

Reduced grid (per user spec for runtime):
  tp1 × tp2 × partial_size × stop × max_hold = 3×3×3×3×3 = 243 combos.

Plus entry sensitivity sweep (5×5×4 = 100), then multi-symbol on the
single best config.
"""
from __future__ import annotations
import warnings
import os
import time
import dataclasses
import pandas as pd

warnings.filterwarnings("ignore")
import logging; logging.getLogger("statsmodels").setLevel(logging.ERROR)
try:
    from statsmodels.tools.sm_exceptions import ConvergenceWarning
    warnings.simplefilter("ignore", ConvergenceWarning)
except Exception: pass

from config.settings import PULLBACK, TRENDCARRY
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from strategies.pullback import generate_signals as pullback_signals, exit_profile_for as pb_exit
from strategies.trend_carry import exit_profile_for as tc_exit
from execution.portfolio import run_portfolio, StrategySpec
from backtest.metrics import sharpe_daily, max_drawdown


START_CAP = 100_000


# ---------------------------------------------------------------------------
# Fixed baseline (kept across all sweeps)
# ---------------------------------------------------------------------------
BASELINE_OVERRIDES = dict(
    base_size_pct=0.30,
    capital_cap_pct=1.00,
    max_pyramid_positions=8,
    use_atr_normalized=True,
    pyramid_require_above_vwap=True,
    pyramid_require_positive_momentum=True,
)


def _make_cfg(**overrides):
    """Build a PULLBACK variant with baseline fixed + sweep overrides."""
    fields = {**BASELINE_OVERRIDES, **overrides}
    return dataclasses.replace(PULLBACK, **fields)


def _metrics(eq, tr):
    final = float(eq.iloc[-1])
    days = max((eq.index[-1] - eq.index[0]).days, 1)
    cagr = (final / START_CAP) ** (365.25 / days) - 1 if final > 0 else -1.0
    dd = max_drawdown(eq)
    pf = (tr.loc[tr.pnl > 0, "pnl"].sum() /
          max(1e-9, -tr.loc[tr.pnl < 0, "pnl"].sum())) if (tr.pnl < 0).any() else float("inf")
    wr = float((tr.pnl > 0).mean()) if not tr.empty else 0.0
    return dict(
        n=int(len(tr)), wr=wr, pf=pf, cagr=cagr, dd=dd,
        sharpe=sharpe_daily(eq), final=final, eq=eq,
        n_long=int((tr.side > 0).sum()) if not tr.empty else 0,
        n_short=int((tr.side < 0).sum()) if not tr.empty else 0,
    )


def _run(df_base, cfg, symbol="SPY"):
    """Inject custom-cfg signals onto base df and run portfolio."""
    df = pullback_signals(df_base, cfg=cfg)
    spec_pb = StrategySpec("pullback", cfg, pb_exit(cfg))
    # trend_carry stays at production cfg — sweep targets pullback knobs.
    spec_tc = StrategySpec("trend_carry", TRENDCARRY, tc_exit(TRENDCARRY))
    bt = run_portfolio(df, [spec_pb, spec_tc],
                       symbol=symbol, initial_capital=START_CAP)
    return _metrics(bt["equity_curve"], bt["trades"])


def _gate(r, dd_max=0.085, n_min=50):
    return r["dd"] <= dd_max and r["n"] >= n_min


def _gate_relaxed(r, dd_max=0.12, n_min=50):
    """Used when the strict DD≤8.5% gate finds nothing — gives the user
    a fallback ranked list one tier wider."""
    return r["dd"] <= dd_max and r["n"] >= n_min


def _fmt(r, label_short):
    return (f"  {label_short:<48}  n={r['n']:>3} "
            f"(L={r['n_long']:>3}/S={r['n_short']:>3})  "
            f"WR={r['wr']*100:>5.1f}%  PF={r['pf']:>5.2f}  "
            f"CAGR={r['cagr']*100:>+6.1f}%  DD={r['dd']*100:>5.1f}%  "
            f"Sh={r['sharpe']:>+5.2f}  final=${r['final']:>9,.0f}")


def _print_topN(results, key, N=10, title="", gate_fn=None):
    gate_fn = gate_fn or _gate
    print(f"\n  ── Top {N} by {title or key} ──")
    ranked = sorted([r for r in results if gate_fn(r)],
                    key=lambda r: r[key], reverse=True)[:N]
    if not ranked:
        print("    (no configs passed the gate)")
        return
    for r in ranked:
        print(_fmt(r, r["label"]))


# ---------------------------------------------------------------------------
# SWEEP 1 — Exit ladder (reduced grid)
# ---------------------------------------------------------------------------
def sweep1(df_base):
    grid = [
        ("partial_tp_pct", [0.03, 0.04, 0.05]),
        ("final_tp_pct",   [0.10, 0.15, 0.20]),
        ("partial_tp_size",[0.40, 0.50, 0.60]),
        ("stop_loss_pct",  [0.020, 0.025, 0.030]),
        ("max_hold_bars",  [150, 200, 390]),
    ]
    keys = [g[0] for g in grid]
    vals_lists = [g[1] for g in grid]

    from itertools import product
    combos = list(product(*vals_lists))
    print(f"\n{'='*92}\nSWEEP 1 — Exit ladder ({len(combos)} combos)\n{'='*92}")
    t0 = time.time()
    results = []
    for i, combo in enumerate(combos):
        overrides = dict(zip(keys, combo))
        cfg = _make_cfg(**overrides)
        r = _run(df_base, cfg)
        label = (f"tp1={overrides['partial_tp_pct']:.3f} tp2={overrides['final_tp_pct']:.2f} "
                 f"ps={overrides['partial_tp_size']} sl={overrides['stop_loss_pct']:.3f} "
                 f"mh={overrides['max_hold_bars']}")
        r["label"] = label
        r["overrides"] = overrides
        results.append(r)
        if (i+1) % 30 == 0:
            print(f"  ...{i+1}/{len(combos)} done ({time.time()-t0:.1f}s)")
    print(f"  done in {time.time()-t0:.1f}s")
    return results


# ---------------------------------------------------------------------------
# SWEEP 2 — Entry sensitivity, anchored on best from sweep 1
# ---------------------------------------------------------------------------
def sweep2(df_base, anchor_overrides):
    grid = [
        ("pullback_atr_mult",  [0.6, 0.8, 1.0, 1.2, 1.4]),
        ("imbalance_atr_mult", [0.4, 0.6, 0.8, 1.0, 1.2]),
        ("stop_atr_mult",      [1.5, 2.0, 2.5, 3.0]),
    ]
    keys = [g[0] for g in grid]
    vals_lists = [g[1] for g in grid]

    from itertools import product
    combos = list(product(*vals_lists))
    print(f"\n{'='*92}\nSWEEP 2 — Entry sensitivity ({len(combos)} combos, anchored on best-CAGR exit)\n{'='*92}")
    t0 = time.time()
    results = []
    for i, combo in enumerate(combos):
        overrides = {**anchor_overrides, **dict(zip(keys, combo))}
        cfg = _make_cfg(**overrides)
        r = _run(df_base, cfg)
        label = (f"pb_atr={overrides['pullback_atr_mult']:.1f} "
                 f"imb_atr={overrides['imbalance_atr_mult']:.1f} "
                 f"st_atr={overrides['stop_atr_mult']:.1f}")
        r["label"] = label
        r["overrides"] = overrides
        results.append(r)
        if (i+1) % 25 == 0:
            print(f"  ...{i+1}/{len(combos)} done ({time.time()-t0:.1f}s)")
    print(f"  done in {time.time()-t0:.1f}s")
    return results


# ---------------------------------------------------------------------------
# SWEEP 3 — Multi-symbol with best cfg
# ---------------------------------------------------------------------------
def sweep3(best_overrides):
    cfg = _make_cfg(**best_overrides)
    universes = [
        ["SPY"], ["SPY", "DIA"], ["SPY", "DIA", "QQQ"],
        ["SPY", "DIA", "QQQ", "IWM"],
    ]
    print(f"\n{'='*92}\nSWEEP 3 — Best cfg across multi-symbol universes "
          f"(Sharpe-weighted)\n{'='*92}")
    per_symbol_results = {}
    rows = []
    for universe in universes:
        eq_list = []
        sh_list = []
        contributions = {}
        for sym in universe:
            try:
                df = prepare_dual(load_symbol(sym))
                df = pullback_signals(df, cfg=cfg)
                bt = run_portfolio(df, [
                    StrategySpec("pullback", cfg, pb_exit(cfg)),
                    StrategySpec("trend_carry", TRENDCARRY, tc_exit(TRENDCARRY)),
                ], symbol=sym, initial_capital=START_CAP)
                eq = bt["equity_curve"]
                sh = sharpe_daily(eq)
                eq_list.append(eq)
                sh_list.append(max(sh, 0.01))   # avoid neg-weight degenerate cases
                contributions[sym] = float(eq.iloc[-1] - START_CAP)
                per_symbol_results[sym] = (eq, sh)
            except Exception as e:
                print(f"  ! {sym} failed: {e}")

        if not eq_list: continue
        # Sharpe-weighted equity blend on aligned daily index
        all_idx = sorted(set().union(*[e.index for e in eq_list]))
        daily_idx = pd.DatetimeIndex(all_idx)
        weights = pd.Series(sh_list) / sum(sh_list)
        blended = pd.Series(0.0, index=daily_idx, dtype=float)
        for w, eq in zip(weights, eq_list):
            blended = blended.add(eq.reindex(daily_idx).ffill().bfill() * w,
                                  fill_value=0.0)
        # Normalize so the blend starts at START_CAP
        blended = blended * (START_CAP / blended.iloc[0])
        r = _metrics(blended, pd.DataFrame({
            "pnl": [], "side": []  # PF/WR irrelevant for blended view
        }))
        # Recompute n by summing per-symbol trade counts
        n_total = 0
        for sym in universe:
            df = prepare_dual(load_symbol(sym))
            df = pullback_signals(df, cfg=cfg)
            bt = run_portfolio(df, [
                StrategySpec("pullback", cfg, pb_exit(cfg)),
                StrategySpec("trend_carry", TRENDCARRY, tc_exit(TRENDCARRY)),
            ], symbol=sym, initial_capital=START_CAP)
            n_total += len(bt["trades"])
        r["n"] = n_total
        r["weights"] = {sym: float(w) for sym, w in zip(universe, weights)}
        r["contributions"] = contributions
        r["universe_label"] = " + ".join(universe)
        rows.append(r)
    return rows, per_symbol_results


# ---------------------------------------------------------------------------
# Overfit checks
# ---------------------------------------------------------------------------
def _flag_overfit(overrides):
    flags = []
    tp1 = overrides.get("partial_tp_pct", PULLBACK.partial_tp_pct)
    tp2 = overrides.get("final_tp_pct", PULLBACK.final_tp_pct)
    sl = overrides.get("stop_loss_pct", PULLBACK.stop_loss_pct)
    mh = overrides.get("max_hold_bars", PULLBACK.max_hold_bars)
    if tp2 <= 1.5 * tp1:
        flags.append(f"TP2 ({tp2:.2%}) ≤ 1.5× TP1 ({tp1:.2%}) — narrow ladder, curve-fit risk")
    if sl <= 0.01:
        flags.append(f"stop {sl:.2%} ≤ 1% — likely curve-fit, will whipsaw live")
    if mh <= 100:
        flags.append(f"max_hold {mh} ≤ 100 bars — too tight, missing real trends")
    return flags


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("\n" + "="*92)
    print("  SPY SYSTEMATIC SWEEP — pullback exit + entry knobs")
    print("  Baseline #10 MIDDLE: CAGR 12.5%, PF 2.83, DD 7.9%, Sharpe 0.52")
    print("  Gate: DD ≤ 8.5%, n ≥ 50")
    print("="*92)

    # Baseline-confirm run (single config matching user's stated baseline)
    print("\n── Baseline confirmation run (#10 MIDDLE config) ──")
    df_spy = prepare_dual(load_symbol("SPY"))
    base_cfg = _make_cfg()
    r_base = _run(df_spy, base_cfg)
    r_base["label"] = "#10 MIDDLE baseline"
    print(_fmt(r_base, r_base["label"]))

    # ----- SWEEP 1 -----
    s1 = sweep1(df_spy)
    _print_topN(s1, "cagr", title="CAGR (within DD gate)")
    _print_topN(s1, "pf", title="PF (within DD gate)")
    _print_topN(s1, "sharpe", title="Sharpe (within DD gate)")

    passed1 = [r for r in s1 if _gate(r)]
    if not passed1:
        print("\n  ! Strict DD≤8.5% gate found no configs. Relaxing to DD≤12% and continuing.")
        _print_topN(s1, "cagr", title="CAGR (relaxed gate DD≤12%)",
                    gate_fn=_gate_relaxed)
        passed1 = [r for r in s1 if _gate_relaxed(r)]
        if not passed1:
            print("\nEven relaxed gate found nothing. Aborting.")
            return
    best1 = max(passed1, key=lambda r: r["cagr"])
    print(f"\n  → Sweep 1 winner by CAGR: {best1['label']}")
    print(f"    CAGR {best1['cagr']*100:.1f}%  PF {best1['pf']:.2f}  "
          f"DD {best1['dd']*100:.1f}%  Sh {best1['sharpe']:.2f}")

    # ----- SWEEP 2 -----
    s2 = sweep2(df_spy, best1["overrides"])
    _print_topN(s2, "cagr", title="CAGR (within DD gate)")

    passed2 = [r for r in s2 if _gate(r)]
    if not passed2:
        # Fall back to relaxed gate, mirroring sweep 1 behavior
        _print_topN(s2, "cagr", title="CAGR (relaxed gate DD≤12%)",
                    gate_fn=_gate_relaxed)
        passed2 = [r for r in s2 if _gate_relaxed(r)]
    if passed2:
        best2 = max(passed2, key=lambda r: r["cagr"])
        best_overrides = {**best1["overrides"], **best2["overrides"]}
        print(f"\n  → Sweep 2 winner by CAGR: {best2['label']}")
        print(f"    Combined with sweep 1 exit: CAGR {best2['cagr']*100:.1f}%  "
              f"PF {best2['pf']:.2f}  DD {best2['dd']*100:.1f}%  "
              f"Sh {best2['sharpe']:.2f}")
    else:
        print("\n  → Sweep 2 had no gate-clearing configs. Using Sweep 1 winner only.")
        best_overrides = best1["overrides"]

    # ----- SWEEP 3 -----
    s3_rows, per_symbol = sweep3(best_overrides)
    print(f"\n{'─'*92}")
    print(f"  Multi-symbol Sharpe-blended (best cfg):")
    print(f"{'─'*92}")
    print(f"  {'Universe':<38}{'n':>5}{'CAGR':>9}{'DD':>9}{'Sharpe':>9}{'Final $':>15}")
    print("  " + "-" * 80)
    for r in s3_rows:
        print(f"  {r['universe_label']:<38}{r['n']:>5}{r['cagr']*100:>+8.1f}%"
              f"{r['dd']*100:>+8.1f}%{r['sharpe']:>+8.2f}  ${r['final']:>12,.0f}")
        # Per-symbol contributions
        contrib_strs = [f"{s}: ${v:+,.0f}" for s, v in r["contributions"].items()]
        print(f"    contributions: {' | '.join(contrib_strs)}")
        if "weights" in r:
            w_strs = [f"{s}: {w:.1%}" for s, w in r["weights"].items()]
            print(f"    Sharpe weights: {' | '.join(w_strs)}")

    # ----- Recommended config -----
    print(f"\n{'='*92}\n  RECOMMENDED CONFIG (paste into config/settings.py)\n{'='*92}")
    rec = {**BASELINE_OVERRIDES, **best_overrides}
    for k, v in rec.items():
        print(f"  {k:<40} = {v}")
    flags = _flag_overfit(best_overrides)
    if flags:
        print(f"\n  ⚠ Overfit flags:")
        for f in flags: print(f"    - {f}")
    else:
        print(f"\n  ✓ No overfit flags triggered.")

    # ----- PNG -----
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(13, 7))
        ax.plot(r_base["eq"].index, r_base["eq"].values,
                lw=1.2, label="#10 MIDDLE baseline", color="grey")
        # Best single-symbol SPY config
        r_best_spy = _run(df_spy, _make_cfg(**best_overrides))
        ax.plot(r_best_spy["eq"].index, r_best_spy["eq"].values,
                lw=1.6, label="Best SPY-only (recommended cfg)", color="tab:blue")
        # Best multi-symbol blend (last row of sweep 3 = full universe)
        if s3_rows:
            best_multi = max(s3_rows, key=lambda r: r["cagr"])
            ax.plot(best_multi["eq"].index, best_multi["eq"].values,
                    lw=1.4, label=f"Best multi: {best_multi['universe_label']}",
                    color="tab:green")
        ax.set_title("SPY sweep — baseline vs optimized single vs Sharpe-blended multi")
        ax.set_ylabel("Equity ($)"); ax.legend(); ax.grid(True, alpha=0.3)
        out_path = os.path.join("data", "research_spy_optimized.png")
        os.makedirs("data", exist_ok=True)
        fig.tight_layout(); fig.savefig(out_path, dpi=120)
        print(f"\nEquity curves saved → {out_path}")
    except Exception as e:
        print(f"\n(matplotlib skipped: {e})")


if __name__ == "__main__":
    main()
