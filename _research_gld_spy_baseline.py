"""
Clean symmetric ex-boom-year baseline — GLD + SPY 1h, current production engine.

The most important number on the page is GLD ex-2025 PF and CAGR. 2025 was
gold's once-per-decade boom year; including it overstates the realistic edge.
This run produces the honest non-boom baseline alongside SPY for comparison.

Windows:
  - Full available             (yfinance hourly cap = ~2 years on both)
  - Ex-2025                    (trade-level re-walk excluding 2025 trades)
  - 2024 only                  (single clean non-boom year)
  - 2026 YTD                   (out-of-sample forward check)
  - Pre-2024                   (whatever yfinance has before 2024 — usually
                                ~5 months from mid-2023)

For each window we report: n / WR / PF / CAGR / DD / Sharpe / final equity,
plus long-vs-short split, avg hold bars, and exit-type breakdown.

Output:
  - Tables to stdout
  - data/research_gld_spy_exboom.png (equity curves, subplots per window)

Notes / honest caveats:
  - yfinance hourly cap means "pre-2024" is structurally thin on GLD/SPY
  - "Ex-2025" uses the same trade-walk trick as _backtest_gld_ex2025.py:
    drop 2025 trades from the timeline and recompound the rest. This is
    NOT equivalent to retraining/refitting the engine without 2025 —
    just measures what the same trades would have done across the
    non-boom timeline.
"""
from __future__ import annotations
import warnings
import os
import pandas as pd

warnings.filterwarnings("ignore")
import logging; logging.getLogger("statsmodels").setLevel(logging.ERROR)
try:
    from statsmodels.tools.sm_exceptions import ConvergenceWarning
    warnings.simplefilter("ignore", ConvergenceWarning)
except Exception:
    pass

from config.settings import PULLBACK, TRENDCARRY
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from strategies.pullback import exit_profile_for as pb_exit
from strategies.trend_carry import exit_profile_for as tc_exit
from execution.portfolio import run_portfolio, StrategySpec
from backtest.metrics import sharpe_daily, max_drawdown


SYMBOLS = ["GLD", "SPY"]
START_CAP = 100_000


def _metrics(eq: pd.Series, tr: pd.DataFrame, *, days: int | None = None) -> dict:
    final = float(eq.iloc[-1])
    if days is None:
        days = max((eq.index[-1] - eq.index[0]).days, 1)
    cagr = (final / START_CAP) ** (365.25 / max(days, 1)) - 1 if final > 0 else -1.0
    dd = max_drawdown(eq)
    pf = (tr.loc[tr.pnl > 0, "pnl"].sum() /
          max(1e-9, -tr.loc[tr.pnl < 0, "pnl"].sum())) if (tr.pnl < 0).any() else float("inf")
    wr = float((tr.pnl > 0).mean()) if not tr.empty else 0.0
    n_long = int((tr.side > 0).sum()) if not tr.empty else 0
    n_short = int((tr.side < 0).sum()) if not tr.empty else 0
    avg_hold = float(tr.bars_held.mean()) if not tr.empty else 0.0
    exit_breakdown = (tr.exit_reason.value_counts().to_dict()
                      if not tr.empty else {})
    return dict(
        n=int(len(tr)), wr=wr, pf=pf, cagr=cagr, dd=dd,
        sharpe=sharpe_daily(eq), final=final,
        n_long=n_long, n_short=n_short, avg_hold_bars=avg_hold,
        exit_breakdown=exit_breakdown, eq=eq,
        worst=float(tr.pnl.min()) if not tr.empty else 0.0,
        best=float(tr.pnl.max()) if not tr.empty else 0.0,
    )


def _exboom_metrics(eq_full: pd.Series, tr_full: pd.DataFrame, exclude_year: int) -> dict:
    """Re-walk the trade tape skipping any trade whose entry was in exclude_year."""
    if tr_full.empty:
        return _metrics(eq_full, tr_full)
    tr = tr_full.copy().sort_values("entry_time")
    tr["year"] = tr.entry_time.dt.year
    tr["eq_at_entry"] = tr.entry_time.map(lambda t: eq_full.asof(t))
    tr["ret"] = tr.pnl / tr.eq_at_entry
    tr_kept = tr[tr.year != exclude_year].copy()

    cap = START_CAP
    eq_rows: list[tuple[pd.Timestamp, float]] = []
    if not tr_kept.empty:
        eq_rows.append((tr_kept.entry_time.min(), cap))
    for _, row in tr_kept.iterrows():
        cap = cap * (1.0 + row.ret)
        eq_rows.append((row.exit_time, cap))
    if not eq_rows:
        return _metrics(eq_full, tr_full)
    eq_kept = pd.Series(dict(eq_rows)).sort_index()
    days = max((eq_kept.index[-1] - eq_kept.index[0]).days, 1)
    return _metrics(eq_kept, tr_kept, days=days)


def _run_window(df: pd.DataFrame, symbol: str, start: pd.Timestamp | None = None,
                end: pd.Timestamp | None = None) -> dict:
    """Slice the prepared df to a time window and run the full backtest."""
    win = df
    if start is not None:
        win = win[win.index >= start]
    if end is not None:
        win = win[win.index < end]
    if len(win) < 50:
        return None
    bt = run_portfolio(win, [
        StrategySpec("pullback", PULLBACK, pb_exit()),
        StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
    ], symbol=symbol, initial_capital=START_CAP)
    return _metrics(bt["equity_curve"], bt["trades"])


def _fmt_row(label: str, r: dict | None) -> str:
    if r is None:
        return f"  {label:<14}  insufficient data"
    thin = " ⚠ thin" if r["n"] < 20 else ""
    return (f"  {label:<14}  n={r['n']:>3} "
            f"(L={r['n_long']:>3}/S={r['n_short']:>3})  "
            f"WR={r['wr']*100:>5.1f}%  PF={r['pf']:>5.2f}  "
            f"CAGR={r['cagr']*100:>+6.1f}%  DD={r['dd']*100:>5.1f}%  "
            f"Sh={r['sharpe']:>+5.2f}  final=${r['final']:>9,.0f}  "
            f"avg_hold={r['avg_hold_bars']:>4.1f}{thin}")


def _exit_breakdown_str(eb: dict) -> str:
    if not eb:
        return "—"
    keys = ["stop", "partial_tp", "final_tp", "time_stop", "trailing_stop",
            "structural_exit", "max_hold"]
    parts = []
    for k in keys:
        v = eb.get(k, 0)
        if v: parts.append(f"{k}={v}")
    # Any extras we didn't enumerate
    for k, v in eb.items():
        if k not in keys: parts.append(f"{k}={v}")
    return ", ".join(parts) if parts else "—"


def _print_engine_config() -> None:
    print("Engine config (PULLBACK sleeve):")
    for attr in ("base_size_pct", "capital_cap_pct", "max_pyramid_positions",
                 "stop_loss_pct", "partial_tp_pct", "partial_tp_size",
                 "final_tp_pct", "final_tp_size",
                 "use_atr_normalized", "pyramid_require_above_vwap",
                 "pyramid_require_positive_momentum",
                 "trailing_stop_enabled", "trailing_logic_type", "max_hold_bars"):
        if hasattr(PULLBACK, attr):
            print(f"  {attr:<35} {getattr(PULLBACK, attr)}")
    print("Engine config (TRENDCARRY sleeve):")
    for attr in ("base_size_pct", "capital_cap_pct", "stop_loss_pct",
                 "partial_tp_pct", "final_tp_pct", "max_hold_bars"):
        if hasattr(TRENDCARRY, attr):
            print(f"  {attr:<35} {getattr(TRENDCARRY, attr)}")
    print()


def run_symbol(symbol: str) -> dict:
    print(f"\n{'='*92}")
    print(f"  {symbol} — windowed backtest")
    print(f"{'='*92}")

    df = prepare_dual(load_symbol(symbol))
    print(f"  Data: {df.index[0]} → {df.index[-1]}  ({len(df):,} bars, "
          f"{(df.index[-1]-df.index[0]).days/365.25:.2f} yrs)\n")

    # Full window
    bt_full = run_portfolio(df, [
        StrategySpec("pullback", PULLBACK, pb_exit()),
        StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
    ], symbol=symbol, initial_capital=START_CAP)
    r_full = _metrics(bt_full["equity_curve"], bt_full["trades"])

    # Ex-2025 (trade-walk re-compound)
    r_ex25 = _exboom_metrics(bt_full["equity_curve"], bt_full["trades"], exclude_year=2025)

    # 2024 only
    r_2024 = _run_window(df, symbol,
                         start=pd.Timestamp("2024-01-01", tz="UTC"),
                         end=pd.Timestamp("2025-01-01", tz="UTC"))

    # 2026 YTD
    r_2026 = _run_window(df, symbol,
                         start=pd.Timestamp("2026-01-01", tz="UTC"))

    # Pre-2024
    r_pre = _run_window(df, symbol,
                        end=pd.Timestamp("2024-01-01", tz="UTC"))

    print(_fmt_row("Full", r_full))
    print(_fmt_row("Ex-2025", r_ex25))
    print(_fmt_row("2024 only", r_2024))
    print(_fmt_row("2026 YTD", r_2026))
    print(_fmt_row("Pre-2024", r_pre))

    # Exit breakdown for full window — useful diagnostic
    print(f"\n  Full-window exit breakdown: {_exit_breakdown_str(r_full['exit_breakdown'])}")
    print(f"  Full-window worst trade: ${r_full['worst']:+,.0f}  "
          f"best trade: ${r_full['best']:+,.0f}")
    if r_2026:
        print(f"  2026 YTD exit breakdown:    {_exit_breakdown_str(r_2026['exit_breakdown'])}")

    return {
        "full": r_full, "ex2025": r_ex25,
        "y2024": r_2024, "y2026": r_2026, "pre2024": r_pre,
    }


def render_png(results: dict[str, dict]) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"\n(matplotlib unavailable — skipping PNG: {e})")
        return

    windows = [("full", "Full window"), ("ex2025", "Ex-2025"),
               ("y2024", "2024 only"), ("y2026", "2026 YTD"),
               ("pre2024", "Pre-2024")]
    syms = list(results.keys())

    fig, axes = plt.subplots(len(syms), len(windows),
                             figsize=(4*len(windows), 3*len(syms)),
                             squeeze=False)
    for i, sym in enumerate(syms):
        for j, (wk, wlabel) in enumerate(windows):
            ax = axes[i][j]
            r = results[sym].get(wk)
            if r is None or "eq" not in r:
                ax.set_title(f"{sym} · {wlabel}\n(insufficient)", fontsize=9)
                ax.axis("off"); continue
            ax.plot(r["eq"].index, r["eq"].values, lw=1.1)
            cagr = r["cagr"]*100
            dd = r["dd"]*100
            pf = r["pf"]
            ax.set_title(f"{sym} · {wlabel}\nCAGR {cagr:+.1f}%  DD {dd:.1f}%  PF {pf:.2f}",
                         fontsize=9)
            ax.grid(True, alpha=0.3); ax.tick_params(labelsize=7)
    fig.tight_layout()
    out_path = os.path.join("data", "research_gld_spy_exboom.png")
    os.makedirs("data", exist_ok=True)
    fig.savefig(out_path, dpi=120)
    print(f"\nEquity curves saved → {out_path}")


def main() -> None:
    print("\n" + "="*92)
    print("  CLEAN SYMMETRIC EX-BOOM BASELINE — GLD + SPY 1h")
    print("  Current production engine (post 2026-05-27 fixes)")
    print("="*92 + "\n")
    _print_engine_config()

    results = {sym: run_symbol(sym) for sym in SYMBOLS}

    # Side-by-side comparison table
    print(f"\n{'='*92}")
    print("  SIDE-BY-SIDE — GLD vs SPY (current production)")
    print(f"{'='*92}")
    print(f"  {'Window':<14}  {'GLD CAGR':>10}  {'GLD PF':>7}  {'GLD DD':>7}  "
          f"{'SPY CAGR':>10}  {'SPY PF':>7}  {'SPY DD':>7}")
    print("  " + "-" * 80)
    for wk, wlabel in [("full","Full"), ("ex2025","Ex-2025"),
                       ("y2024","2024 only"), ("y2026","2026 YTD"),
                       ("pre2024","Pre-2024")]:
        rg = results.get("GLD", {}).get(wk)
        rs = results.get("SPY", {}).get(wk)
        def cell(r, k, fmt):
            if r is None: return "—"
            return f"{r[k]*100:{fmt}}%" if k in ("cagr","dd") else f"{r[k]:{fmt}}"
        print(f"  {wlabel:<14}  "
              f"{cell(rg,'cagr','>9.1f'):>10}  "
              f"{cell(rg,'pf','>6.2f'):>7}  "
              f"{cell(rg,'dd','>6.1f'):>7}  "
              f"{cell(rs,'cagr','>9.1f'):>10}  "
              f"{cell(rs,'pf','>6.2f'):>7}  "
              f"{cell(rs,'dd','>6.1f'):>7}")

    # Headline summary — the actual deliverable
    g_full = results["GLD"]["full"]; g_ex = results["GLD"]["ex2025"]
    print(f"\n{'='*92}")
    print("  HEADLINE — the number the user asked for")
    print(f"{'='*92}")
    print(f"  GLD full window:  CAGR {g_full['cagr']*100:+.1f}%  "
          f"PF {g_full['pf']:.2f}  DD {g_full['dd']*100:.1f}%  n={g_full['n']}")
    print(f"  GLD ex-2025:      CAGR {g_ex['cagr']*100:+.1f}%  "
          f"PF {g_ex['pf']:.2f}  DD {g_ex['dd']*100:.1f}%  n={g_ex['n']}")
    print(f"\n  → Plan around the ex-2025 numbers, NOT the full-window numbers.")
    print(f"  → 2025 was once-per-decade gold tailwind; real edge is what")
    print(f"    survives outside it.\n")

    render_png(results)


if __name__ == "__main__":
    main()
