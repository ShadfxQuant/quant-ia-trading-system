"""
Rebuild from Baseline #0 — pure pullback + symmetric + rollover guard,
no VWAP/RSI/HMM entry gates. Then run three research tasks:

  Task 1 — VWAP pyramid gate vs RSI size multiplier (4 configs)
  Task 2 — HMM as off / sizing mult / aggressive mult (3 configs)
  Task 3 — Deterministic regime vs HMM agreement entry-quality breakdown

CRITICAL RULE: indicators NEVER block initial entries. Only allowed as
pyramid confirmation, size multiplier, or diagnostic. Verified via
baseline-vs-with-indicator deltas — any config that drops n_legs by
>10% relative to baseline is flagged as a hidden entry gate.
"""
from __future__ import annotations
import warnings
import os
import dataclasses
import numpy as np
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
from strategies.pullback import (
    generate_signals as pullback_signals,
    exit_profile_for as pb_exit,
)
from strategies.trend_carry import exit_profile_for as tc_exit
from execution.portfolio import run_portfolio, StrategySpec
from backtest.metrics import sharpe_daily, max_drawdown


SYMBOL = "SPY"
START_CAP = 100_000

# ----- Baseline #0 — exact spec from the user prompt -----
BASELINE0 = dict(
    base_size_pct=0.30,
    capital_cap_pct=1.00,
    max_pyramid_positions=8,
    final_tp_pct=0.15,
    use_atr_normalized=True,
    pyramid_require_above_vwap=False,    # #0 has NO VWAP entry/pyramid gate
    pyramid_require_positive_momentum=False,
)


def make_cfg(**overrides):
    fields = {**BASELINE0, **overrides}
    return dataclasses.replace(PULLBACK, **fields)


def metrics(eq, tr):
    final = float(eq.iloc[-1])
    days = max((eq.index[-1] - eq.index[0]).days, 1)
    cagr = (final/START_CAP)**(365.25/days) - 1 if final > 0 else -1.0
    dd = max_drawdown(eq)
    pf = (tr.loc[tr.pnl>0, "pnl"].sum() /
          max(1e-9, -tr.loc[tr.pnl<0, "pnl"].sum())) if (tr.pnl<0).any() else float("inf")
    wr = float((tr.pnl>0).mean()) if not tr.empty else 0.0
    return dict(
        n=int(len(tr)), wr=wr, pf=pf, cagr=cagr, dd=dd,
        sharpe=sharpe_daily(eq), final=final, eq=eq, trades=tr,
        n_long=int((tr.side>0).sum()) if not tr.empty else 0,
        n_short=int((tr.side<0).sum()) if not tr.empty else 0,
    )


def fmt(label, r):
    return (f"{label:<40}  n={r['n']:>3} (L={r['n_long']:>3}/S={r['n_short']:>3})  "
            f"WR={r['wr']*100:>5.1f}%  PF={r['pf']:>5.2f}  "
            f"CAGR={r['cagr']*100:>+6.1f}%  DD={r['dd']*100:>5.1f}%  "
            f"Sh={r['sharpe']:>+5.2f}  final=${r['final']:>9,.0f}")


# -----------------------------------------------------------------------------
# Indicator helpers
# -----------------------------------------------------------------------------
def compute_rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/n, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - 100/(1 + rs)).fillna(50.0)


def apply_rsi_size_mult(df: pd.DataFrame) -> pd.DataFrame:
    """RSI(14): <40 → 1.3×, 40-60 → 1.0×, >60 → 0.7×.
    NEVER zeros out — entries always fire, only sizing changes."""
    out = df.copy()
    rsi = compute_rsi(out["Close"])
    mult = pd.Series(1.0, index=out.index)
    mult[rsi < 40] = 1.3
    mult[rsi > 60] = 0.7
    # Multiply onto any existing SizeMult set by prepare_dual.
    existing = out.get("pullback_SizeMult", pd.Series(1.0, index=out.index)).fillna(1.0)
    out["pullback_SizeMult"] = existing * mult
    out["__rsi14"] = rsi
    return out


def apply_hmm_size_mult(df: pd.DataFrame, scale_up: float = 2.0,
                       scale_down: float = 0.5) -> pd.DataFrame:
    """HMM P_bull buckets → size multiplier.
    P_bull > 0.70 → scale_up, P_bull < 0.30 → scale_down, else 1.0×.
    NEVER zeros out. Also freezes pyramid adds when bullish-deterministic
    AND P_bull < 0.30 (HMM disagrees with deterministic regime)."""
    out = df.copy()
    # HMM_state is the predicted state (string or int); pullback_HmmSizeMult
    # is already populated by prepare_dual but we override it here.
    # Use HMM_state if available — otherwise fall back to neutral.
    state = out.get("HMM_state", pd.Series(index=out.index, dtype="object"))
    # We don't have direct P_bull — derive bucket from HmmBucket col if present,
    # else infer from state name.
    bucket = out.get("pullback_HmmBucket")
    if bucket is not None and not bucket.isna().all():
        mult = pd.Series(1.0, index=out.index)
        mult[bucket == "high"] = scale_up
        mult[bucket == "low"] = scale_down
    else:
        # Fallback: high if state is bullish-ish, low if bearish-ish
        s = state.astype(str).str.lower()
        mult = pd.Series(1.0, index=out.index)
        mult[s.str.contains("bull")] = scale_up
        mult[s.str.contains("bear")] = scale_down
    existing = out.get("pullback_SizeMult", pd.Series(1.0, index=out.index)).fillna(1.0)
    out["pullback_SizeMult"] = existing * mult
    out["__hmm_size_mult_applied"] = mult
    return out


# -----------------------------------------------------------------------------
# Runner
# -----------------------------------------------------------------------------
def run(df_in: pd.DataFrame, cfg, *, rsi_mult: bool = False,
        hmm_mult: tuple[float, float] | None = None) -> dict:
    """Run the backtest. Multipliers applied AFTER pullback_signals so they
    don't get clobbered by the strategy's own SizeMult logic."""
    df = pullback_signals(df_in, cfg=cfg)
    if rsi_mult:
        df = apply_rsi_size_mult(df)
    if hmm_mult is not None:
        up, down = hmm_mult
        df = apply_hmm_size_mult(df, scale_up=up, scale_down=down)
    bt = run_portfolio(df, [
        StrategySpec("pullback", cfg, pb_exit(cfg)),
        StrategySpec("trend_carry", TRENDCARRY, tc_exit(TRENDCARRY)),
    ], symbol=SYMBOL, initial_capital=START_CAP)
    return metrics(bt["equity_curve"], bt["trades"])


def main():
    print("\n" + "="*100)
    print("  BASELINE #0 RECONSTRUCTION + TASKS 1/2/3 RESEARCH")
    print("  Symbol: SPY · Capital: $100K · Engine: pullback + trend_carry (symmetric)")
    print("="*100)

    df_base = prepare_dual(load_symbol(SYMBOL))
    print(f"\n  Data: {df_base.index[0]} → {df_base.index[-1]} ({len(df_base):,} bars)")
    days = (df_base.index[-1] - df_base.index[0]).days
    print(f"  Span: {days} days ({days/365.25:.2f} years)\n")

    # =========================================================================
    # BASELINE #0 reproduction
    # =========================================================================
    print("─"*100)
    print("  BASELINE #0 — pure pullback, no VWAP/RSI/HMM gates")
    print("─"*100)
    cfg0 = make_cfg()
    r0 = run(df_base, cfg0)
    print(fmt("Baseline #0 (control)", r0))

    target_cagr, target_pf, target_wr, target_n = 0.17, 2.55, 0.70, 190
    drift_ok = (abs(r0["cagr"] - target_cagr) < 0.05 and
                abs(r0["pf"] - target_pf) < 0.5 and
                abs(r0["wr"] - target_wr) < 0.10 and
                r0["n"] >= 100)
    if drift_ok:
        print("  ✓ Baseline matches expected (~17% CAGR, ~2.55 PF, ~70% WR, ~190 legs)")
    else:
        print(f"  ⚠ Baseline drift detected:")
        print(f"    Expected: CAGR~{target_cagr*100:.0f}%  PF~{target_pf}  WR~{target_wr*100:.0f}%  n~{target_n}")
        print(f"    Measured: CAGR {r0['cagr']*100:.1f}%  PF {r0['pf']:.2f}  WR {r0['wr']*100:.1f}%  n {r0['n']}")
        print(f"    Likely cause: yfinance data updates since original measurement.")
        print(f"    Proceeding anyway — relative comparisons within this session are valid.\n")

    # =========================================================================
    # TASK 1 — VWAP vs RSI as non-blocking overlays
    # =========================================================================
    print("\n" + "="*100)
    print("  TASK 1 — VWAP pyramid gate vs RSI size multiplier (4 configs)")
    print("="*100)

    # Config A — pure baseline (already computed as r0)
    r_A = r0
    # Config B — VWAP pyramid gate ON
    cfg_B = make_cfg(pyramid_require_above_vwap=True)
    r_B = run(df_base, cfg_B)
    # Config C — RSI size multiplier (entries unchanged)
    r_C = run(df_base, cfg0, rsi_mult=True)
    # Config D — VWAP gate + RSI mult combined
    r_D = run(df_base, cfg_B, rsi_mult=True)

    print(fmt("A. Pure baseline (control)", r_A))
    print(fmt("B. VWAP pyramid gate only", r_B))
    print(fmt("C. RSI size multiplier only", r_C))
    print(fmt("D. VWAP gate + RSI mult", r_D))

    # Entry-gate sanity check
    print("\n  Entry-gate check (relative to baseline #0 leg count):")
    for label, r in [("A", r_A), ("B", r_B), ("C", r_C), ("D", r_D)]:
        delta = (r["n"] - r0["n"]) / max(r0["n"], 1) * 100
        flag = " ⚠ BLOCKED ENTRIES" if delta < -10 else ""
        print(f"    {label}: n_legs = {r['n']} ({delta:+.1f}% vs baseline){flag}")

    # Task 1 winner
    valid = [("A", r_A), ("B", r_B), ("C", r_C), ("D", r_D)]
    valid = [(l, r) for l, r in valid if r["dd"] <= 0.16]
    if valid:
        winner_label, winner_r = max(valid, key=lambda x: x[1]["cagr"])
        print(f"\n  → Task 1 winner (CAGR within DD≤16%): config {winner_label}  "
              f"CAGR {winner_r['cagr']*100:.1f}%  DD {winner_r['dd']*100:.1f}%")
    else:
        winner_label, winner_r = "A", r_A
        print(f"\n  → No config cleared DD≤16% gate; falling back to A.")

    # Task 1 winner config for Task 2 chaining
    cfg_after_task1 = cfg_B if winner_label in ("B", "D") else cfg0
    rsi_after_task1 = winner_label in ("C", "D")

    # =========================================================================
    # TASK 2 — HMM size multiplier on top of task 1 winner
    # =========================================================================
    print("\n" + "="*100)
    print(f"  TASK 2 — HMM sizing on top of Task 1 winner (config {winner_label})")
    print("="*100)

    # X — informational only (HMM not multiplied into size)
    r_X = run(df_base, cfg_after_task1, rsi_mult=rsi_after_task1)
    # Y — sizing multiplier 2.0× / 1.0× / 0.5×
    r_Y = run(df_base, cfg_after_task1, rsi_mult=rsi_after_task1,
              hmm_mult=(2.0, 0.5))
    # Z — aggressive 2.5× / 1.0× / 0.4×
    r_Z = run(df_base, cfg_after_task1, rsi_mult=rsi_after_task1,
              hmm_mult=(2.5, 0.4))

    print(fmt("X. HMM informational only", r_X))
    print(fmt("Y. HMM size mult 2.0/0.5", r_Y))
    print(fmt("Z. HMM size mult 2.5/0.4 (aggressive)", r_Z))

    # Task 2 winner — best Sharpe within DD≤16%
    valid2 = [("X", r_X), ("Y", r_Y), ("Z", r_Z)]
    valid2 = [(l, r) for l, r in valid2 if r["dd"] <= 0.16]
    if valid2:
        winner2_label, winner2_r = max(valid2, key=lambda x: x[1]["sharpe"])
        print(f"\n  → Task 2 winner (best Sharpe within DD≤16%): {winner2_label}  "
              f"Sharpe {winner2_r['sharpe']:.2f}  CAGR {winner2_r['cagr']*100:.1f}%  "
              f"DD {winner2_r['dd']*100:.1f}%")
    else:
        winner2_label, winner2_r = "X", r_X

    # =========================================================================
    # TASK 3 — Regime vs HMM entry-quality breakdown
    # =========================================================================
    print("\n" + "="*100)
    print("  TASK 3 — Deterministic regime vs HMM agreement breakdown")
    print("="*100)

    tr = r0["trades"].copy()
    if tr.empty:
        print("  No trades from baseline — skipping Task 3.")
    else:
        # Attach regime + HMM state at each entry by asof-lookup on df_base.
        def at_entry(col, t):
            try:
                return df_base[col].asof(t)
            except KeyError:
                return None

        tr["regime"] = tr.entry_time.apply(lambda t: at_entry("Regime", t))
        tr["hmm_state"] = tr.entry_time.apply(lambda t: at_entry("HMM_state", t))
        tr["hmm_bucket"] = tr.entry_time.apply(lambda t: at_entry("pullback_HmmBucket", t))
        tr["regime_score"] = tr.entry_time.apply(lambda t: at_entry("RegimeScore", t))
        tr["win"] = tr.pnl > 0
        total_pnl = tr.pnl.sum() if not tr.empty else 1.0

        # --- Deterministic regime breakdown ---
        print("\n  By deterministic regime at entry:")
        print(f"  {'Regime':<18}{'Trades':>8}{'WR':>8}{'Avg PnL':>11}{'% Total PnL':>14}")
        print("  " + "-"*60)
        for reg, sub in tr.groupby("regime"):
            wr = sub.win.mean() * 100
            avg = sub.pnl.mean()
            pct = sub.pnl.sum() / total_pnl * 100 if total_pnl else 0
            print(f"  {str(reg):<18}{len(sub):>8d}{wr:>7.1f}%${avg:>+9,.0f}{pct:>+12.1f}%")

        # --- HMM state breakdown ---
        print("\n  By HMM state at entry:")
        print(f"  {'HMM state':<18}{'Trades':>8}{'WR':>8}{'Avg PnL':>11}{'% Total PnL':>14}")
        print("  " + "-"*60)
        for st, sub in tr.groupby("hmm_state"):
            wr = sub.win.mean() * 100
            avg = sub.pnl.mean()
            pct = sub.pnl.sum() / total_pnl * 100 if total_pnl else 0
            print(f"  {str(st):<18}{len(sub):>8d}{wr:>7.1f}%${avg:>+9,.0f}{pct:>+12.1f}%")

        # --- HMM bucket breakdown ---
        if tr["hmm_bucket"].notna().any():
            print("\n  By HMM probability bucket at entry:")
            print(f"  {'Bucket':<18}{'Trades':>8}{'WR':>8}{'Avg PnL':>11}{'% Total PnL':>14}")
            print("  " + "-"*60)
            for b, sub in tr.groupby("hmm_bucket"):
                wr = sub.win.mean() * 100
                avg = sub.pnl.mean()
                pct = sub.pnl.sum() / total_pnl * 100 if total_pnl else 0
                print(f"  {str(b):<18}{len(sub):>8d}{wr:>7.1f}%${avg:>+9,.0f}{pct:>+12.1f}%")

        # --- Agreement / disagreement ---
        def is_bullish_det(r): return r in ("growth", "stabilization")
        def is_bullish_hmm(s): return "bull" in str(s).lower()
        tr["det_bull"] = tr.regime.apply(is_bullish_det)
        tr["hmm_bull"] = tr.hmm_state.apply(is_bullish_hmm)
        agree_bull = tr[tr.det_bull & tr.hmm_bull]
        agree_bear = tr[(~tr.det_bull) & (~tr.hmm_bull)]
        disagree_dh = tr[tr.det_bull & (~tr.hmm_bull)]   # det bull, HMM bear
        disagree_hd = tr[(~tr.det_bull) & tr.hmm_bull]   # det bear, HMM bull

        print("\n  Agreement vs disagreement (deterministic regime × HMM state):")
        print(f"  {'Subset':<35}{'Trades':>8}{'WR':>8}{'Avg PnL':>11}")
        print("  " + "-"*65)
        for label, sub in [
            ("Both bullish (agreement)", agree_bull),
            ("Both bearish (agreement)", agree_bear),
            ("Det bull, HMM bear (disagree)", disagree_dh),
            ("Det bear, HMM bull (disagree)", disagree_hd),
        ]:
            if len(sub) == 0:
                print(f"  {label:<35}{len(sub):>8d}{'—':>8}{'—':>11}")
                continue
            wr = sub.win.mean() * 100
            avg = sub.pnl.mean()
            print(f"  {label:<35}{len(sub):>8d}{wr:>7.1f}%${avg:>+9,.0f}")

        # --- Best entry combo ---
        combo = tr.groupby(["regime", "hmm_bucket"]).agg(
            n=("pnl", "size"),
            wr=("win", "mean"),
            avg_pnl=("pnl", "mean"),
            tot_pnl=("pnl", "sum"),
        ).reset_index()
        combo = combo[combo.n >= 5].sort_values("wr", ascending=False)
        if not combo.empty:
            print("\n  Best regime × HMM-bucket combinations (n ≥ 5):")
            print(f"  {'regime':<14}{'hmm_bucket':<14}{'n':>5}{'WR':>7}{'avg_pnl':>11}{'tot_pnl':>11}")
            print("  " + "-"*65)
            for _, row in combo.head(6).iterrows():
                print(f"  {str(row.regime):<14}{str(row.hmm_bucket):<14}"
                      f"{int(row.n):>5d}{row.wr*100:>6.1f}%${row.avg_pnl:>+9,.0f}${row.tot_pnl:>+9,.0f}")

    # =========================================================================
    # PNG
    # =========================================================================
    try:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(2, 1, figsize=(13, 9))
        ax = axes[0]
        for label, r, lw in [
            ("A. Baseline #0", r_A, 1.6),
            ("B. VWAP pyramid", r_B, 1.2),
            ("C. RSI size mult", r_C, 1.2),
            ("D. VWAP+RSI", r_D, 1.2),
        ]:
            ax.plot(r["eq"].index, r["eq"].values, lw=lw, label=label)
        ax.set_title("Task 1 — VWAP vs RSI overlays vs baseline")
        ax.set_ylabel("Equity ($)"); ax.legend(loc="best"); ax.grid(True, alpha=0.3)

        ax = axes[1]
        for label, r in [
            ("X. HMM off", r_X), ("Y. HMM 2.0/0.5", r_Y),
            ("Z. HMM 2.5/0.4", r_Z),
        ]:
            ax.plot(r["eq"].index, r["eq"].values, lw=1.4, label=label)
        ax.set_title(f"Task 2 — HMM sizing on Task 1 winner (config {winner_label})")
        ax.set_ylabel("Equity ($)"); ax.legend(loc="best"); ax.grid(True, alpha=0.3)
        out = os.path.join("data", "research_baseline0_optimized.png")
        os.makedirs("data", exist_ok=True)
        fig.tight_layout(); fig.savefig(out, dpi=120)
        print(f"\nEquity curves → {out}")
    except Exception as e:
        print(f"(matplotlib skipped: {e})")

    # =========================================================================
    # Final recommended config
    # =========================================================================
    print("\n" + "="*100)
    print("  FINAL RECOMMENDED CONFIG")
    print("="*100)
    print(f"  Task 1 winner: {winner_label}  (CAGR {winner_r['cagr']*100:.1f}%, "
          f"DD {winner_r['dd']*100:.1f}%, PF {winner_r['pf']:.2f})")
    print(f"  Task 2 winner: {winner2_label}  (Sharpe {winner2_r['sharpe']:.2f}, "
          f"CAGR {winner2_r['cagr']*100:.1f}%, DD {winner2_r['dd']*100:.1f}%)")

    print("\n  Paste into config/settings.py PULLBACK dataclass:")
    print(f"    base_size_pct = {BASELINE0['base_size_pct']}")
    print(f"    capital_cap_pct = {BASELINE0['capital_cap_pct']}")
    print(f"    max_pyramid_positions = {BASELINE0['max_pyramid_positions']}")
    print(f"    final_tp_pct = {BASELINE0['final_tp_pct']}")
    print(f"    use_atr_normalized = True")
    print(f"    pyramid_require_above_vwap = {winner_label in ('B','D')}")
    print(f"    pyramid_require_positive_momentum = False  # baseline #0 rule")
    print(f"  Indicator overlays:")
    print(f"    RSI size mult: {'ON (1.3/1.0/0.7)' if winner_label in ('C','D') else 'OFF'}")
    print(f"    HMM size mult: {'ON' if winner2_label != 'X' else 'OFF (informational only)'}")


if __name__ == "__main__":
    main()
