"""
Continuous portfolio scanner — runs the FULL production strategy on every
yfinance-loadable symbol in the candidate universe, ranks by realized
backtest, and surfaces the best candidates for universe expansion.

Different from the Edge Lab (research/edge_lab.py):
  - Edge Lab tests individual edges (RSI, CVD, etc.) per bar
  - Portfolio Scanner tests the FULL production strategy (pullback +
    trend_carry + Kalman + regime-flip) as a single backtest per symbol

What it outputs:
  - research/results/portfolio_scan_<ts>.csv : per-symbol metrics
  - research/results/portfolio_scan_latest.csv : rolling pointer
  - console leaderboard sorted by edge-score (composite of PF, CAGR, DD, n)

Usage:
    python3 -m research.portfolio_scanner
    python3 -m research.portfolio_scanner --quick  (subset for fast iteration)

Schedule via cron / GitHub Actions to run weekly so we can track:
  - Which symbols are improving / degrading over time
  - When a new candidate clears the promotion gate
  - When a live symbol crosses below the demotion threshold
"""
from __future__ import annotations
import argparse, warnings, logging, os, json, sys
sys.stdout.reconfigure(line_buffering=True)  # so progress streams even when piped
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed
warnings.filterwarnings("ignore")
logging.getLogger("statsmodels").setLevel(logging.ERROR)
try:
    from statsmodels.tools.sm_exceptions import ConvergenceWarning
    warnings.simplefilter("ignore", ConvergenceWarning)
except Exception: pass

import pandas as pd

from config.settings import PULLBACK, TRENDCARRY
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from strategies.pullback import exit_profile_for as pb_exit
from strategies.trend_carry import exit_profile_for as tc_exit
from execution.portfolio import run_portfolio, StrategySpec

INITIAL = 100_000.0
RESULTS_DIR = os.path.join("research", "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

# ────────── 60-symbol candidate universe (everything our pipeline can load) ──────────
# Drawn from previous mining + new tickers asked for portfolio expansion
UNIVERSE = [
    # Equity index ETFs + index proxies
    "SPY", "QQQ", "DIA", "IWM", "MDY", "^GSPC", "^NDX", "^DJI", "^RUT",
    # Sector ETFs (11 SPDRs)
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC",
    # Theme ETFs
    "ARKK", "SOXX", "SMH", "IBB",
    # Commodity ETFs
    "GLD", "SLV", "USO", "UNG", "DBC", "CPER", "PALL", "PPLT",
    # Index futures
    "ES=F", "NQ=F", "YM=F", "RTY=F",
    # Metal / energy futures
    "GC=F", "SI=F", "HG=F", "CL=F", "NG=F", "RB=F", "HO=F",
    # Bonds
    "TLT", "IEF", "SHY", "HYG", "LQD", "TIP", "AGG",
    # FX
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "USDCHF=X", "AUDUSD=X", "USDCAD=X", "NZDUSD=X",
    # Vol + crypto
    "^VIX", "BTC-USD", "ETH-USD",
    # Individual mega-caps (just to see if any are clean)
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "TSLA",
]

QUICK_UNIVERSE = ["SPY", "^NDX", "GLD", "GC=F", "QQQ", "DIA", "IWM",
                  "ES=F", "NQ=F", "AAPL", "NVDA", "TSLA", "BTC-USD"]


def _safe_bt(symbol: str) -> dict | None:
    """Backtest a single symbol with the full production strategy.
    Catches all errors so one bad symbol can't break the scan."""
    try:
        df = prepare_dual(load_symbol(symbol))
        if len(df) < 200:
            return {"symbol": symbol, "error": "not enough bars"}
        res = run_portfolio(df, [
            StrategySpec("pullback", PULLBACK, pb_exit()),
            StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
        ], symbol=symbol, initial_capital=INITIAL)
        tr = res["trades"]
        if len(tr) == 0:
            return {"symbol": symbol, "error": "no trades"}
        # walk equity
        eq = INITIAL; peak = INITIAL; dd_min = 0.0
        for p in tr["pnl"]:
            eq += p; peak = max(peak, eq); dd_min = min(dd_min, (eq - peak) / peak)
        days = (tr["exit_time"].max() - tr["entry_time"].min()).days
        years = max(days / 365.25, 0.1)
        wins = tr[tr["pnl"] > 0]; losses = tr[tr["pnl"] < 0]
        pf_w = float(wins["pnl"].sum())
        pf_l = float(-losses["pnl"].sum())
        pf = pf_w / pf_l if pf_l > 0 else float("inf")
        cagr = (eq / INITIAL) ** (1.0 / years) - 1.0
        wr = float((tr["pnl"] > 0).mean())
        avg_win = float(wins["pnl"].mean()) if len(wins) else 0.0
        avg_loss = float(losses["pnl"].mean()) if len(losses) else 0.0
        sharpe_proxy = cagr / (abs(dd_min) + 1e-9)

        # Quality gates
        passes_pf  = pf >= 2.0
        passes_dd  = abs(dd_min) <= 0.25
        passes_n   = len(tr) >= 30
        passes_pos = cagr > 0.05
        promo_ready = passes_pf and passes_dd and passes_n and passes_pos

        # Edge score: composite ranking
        # PF carries the most weight; DD penalty; volume bonus
        edge_score = (
            (min(pf, 10.0) - 1.0) * 30 +     # PF: 0-10 mapped to 0-270
            (cagr * 100) -                    # CAGR pct directly
            (abs(dd_min) * 100) * 1.5 +       # DD: penalty
            (min(len(tr), 500) / 500) * 10    # n: small bonus for sample size
        )

        return {
            "symbol":        symbol,
            "n_trades":      int(len(tr)),
            "win_rate":      round(wr, 4),
            "pf":            round(pf, 3),
            "cagr_pct":      round(cagr * 100, 2),
            "max_dd_pct":    round(dd_min * 100, 2),
            "sharpe_proxy":  round(sharpe_proxy, 2),
            "avg_win_usd":   round(avg_win, 1),
            "avg_loss_usd":  round(avg_loss, 1),
            "final_equity":  round(eq, 0),
            "profit_usd":    round(eq - INITIAL, 0),
            "years":         round(years, 2),
            "edge_score":    round(edge_score, 1),
            "promo_ready":   bool(promo_ready),
        }
    except Exception as e:
        return {"symbol": symbol, "error": f"{type(e).__name__}: {str(e)[:80]}"}


def main():
    p = argparse.ArgumentParser(description="Continuous portfolio scanner")
    p.add_argument("--quick", action="store_true",
                   help="Use 13-symbol quick set instead of 60+")
    p.add_argument("--symbols", help="Comma-separated override list")
    p.add_argument("--parallel", type=int, default=4,
                   help="Worker processes (default 4)")
    args = p.parse_args()

    if args.symbols:
        syms = args.symbols.split(",")
    elif args.quick:
        syms = QUICK_UNIVERSE
    else:
        syms = UNIVERSE

    print(f"\n  ── PORTFOLIO SCANNER: {len(syms)} symbols, "
          f"{args.parallel} parallel workers ──\n")

    results = []
    errors = []

    # serial path (parallel-safe but Python's HMM/Kalman are heavy; use processes)
    if args.parallel > 1:
        with ProcessPoolExecutor(max_workers=args.parallel) as ex:
            futures = {ex.submit(_safe_bt, s): s for s in syms}
            done = 0
            for f in as_completed(futures):
                r = f.result()
                done += 1
                if r and "error" not in r:
                    results.append(r)
                    print(f"  [{done:>2}/{len(syms)}] {r['symbol']:<12} "
                          f"PF {r['pf']:>5.2f}  CAGR {r['cagr_pct']:>+6.1f}%  "
                          f"DD {r['max_dd_pct']:>+5.1f}%  WR {r['win_rate']*100:>4.1f}%  "
                          f"n={r['n_trades']:<4} score {r['edge_score']:>+6.1f}")
                else:
                    errors.append(r)
                    print(f"  [{done:>2}/{len(syms)}] {r['symbol']:<12} "
                          f"SKIP: {r.get('error', '?')}")
    else:
        for i, s in enumerate(syms, 1):
            r = _safe_bt(s)
            if r and "error" not in r:
                results.append(r)
                print(f"  [{i:>2}/{len(syms)}] {r['symbol']:<12} "
                      f"PF {r['pf']:>5.2f}  CAGR {r['cagr_pct']:>+6.1f}%  "
                      f"n={r['n_trades']:<4}")
            else:
                errors.append(r)
                print(f"  [{i:>2}/{len(syms)}] {r['symbol']:<12} SKIP")

    if not results:
        print("\n  no usable results")
        return

    df = pd.DataFrame(results).sort_values("edge_score", ascending=False).reset_index(drop=True)

    # ─── HEADLINE LEADERBOARD ───
    print(f"\n{'='*108}")
    print(f"  LEADERBOARD — sorted by composite edge score")
    print("="*108)
    print(f"  {'rank':<5}{'symbol':<12}{'PF':>6}{'CAGR':>9}{'DD':>8}{'WR':>7}"
          f"{'n':>6}{'profit$':>12}{'score':>9}{'promo?':>9}")
    print("  " + "-"*100)
    for i, r in df.head(30).iterrows():
        promo = "✓" if r["promo_ready"] else "—"
        print(f"  {i+1:<5}{r['symbol']:<12}{r['pf']:>6.2f}{r['cagr_pct']:>+8.1f}%"
              f"{r['max_dd_pct']:>+7.1f}%{r['win_rate']*100:>+6.1f}%{r['n_trades']:>6}"
              f"${r['profit_usd']:>+11,.0f}{r['edge_score']:>+9.1f}{promo:>9}")

    # ─── PROMOTION CANDIDATES ───
    promo = df[df["promo_ready"]].sort_values("edge_score", ascending=False)
    print(f"\n{'='*108}")
    print(f"  PROMOTION-READY SYMBOLS ({len(promo)}) — clear PF≥2 AND DD≤25% AND n≥30 AND CAGR≥5%")
    print("="*108)
    if len(promo):
        for _, r in promo.iterrows():
            print(f"  {r['symbol']:<12} PF {r['pf']:>5.2f}  CAGR {r['cagr_pct']:>+6.1f}%  "
                  f"DD {r['max_dd_pct']:>+5.1f}%  profit ${r['profit_usd']:>+10,.0f}")
    else:
        print("  (none cleared this run)")

    # ─── WRITE OUTPUTS ───
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(RESULTS_DIR, f"portfolio_scan_{ts}.csv")
    df.to_csv(csv_path, index=False)
    df.to_csv(os.path.join(RESULTS_DIR, "portfolio_scan_latest.csv"), index=False)
    print(f"\n  wrote → {csv_path}")
    print(f"  latest pointer → research/results/portfolio_scan_latest.csv")

    if errors:
        print(f"\n  {len(errors)} symbols skipped:")
        for e in errors[:10]:
            print(f"    {e.get('symbol', '?'):<12} {e.get('error', '?')}")


if __name__ == "__main__":
    main()
