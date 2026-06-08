"""
HMM postmortem on the current paper-trade book.

For each closed and open trade, this:
  1. Loads the symbol's hourly bars via the live data pipeline
  2. Attaches HMM + Kalman-smoothed regime
  3. Reads HMM state at entry, and (closed) at exit OR (open) at current bar
  4. Classifies the trade by 4 quality grades:
       A — HMM aligned with direction at entry, trade WON
       B — HMM aligned with direction at entry, trade LOST (structural failure)
       C — HMM fighting direction at entry, trade WON (lucky / chop edge)
       D — HMM fighting direction at entry, trade LOST (avoidable)
  5. For losers: did HMM flip between entry and exit? (Part 8.8 thesis)
  6. For open positions: current HMM agreement status (heads-up on whether
     to expect mean-reversion or trend-continuation from here)

Output is a per-trade audit plus an aggregate finding:
  "what would have happened if we'd filtered out D-grade entries"
"""
from __future__ import annotations
import warnings, logging, json
warnings.filterwarnings("ignore")
logging.getLogger("statsmodels").setLevel(logging.ERROR)
try:
    from statsmodels.tools.sm_exceptions import ConvergenceWarning
    warnings.simplefilter("ignore", ConvergenceWarning)
except Exception: pass

import pandas as pd
from datetime import datetime, timezone

from config.settings import PULLBACK, TRENDCARRY
from core.data_loader import load_symbol
from main_portfolio import prepare_dual


BULL_LABELS = {"bull", "1", "up", "high"}
BEAR_LABELS = {"bear", "0", "down", "low"}

def _is_bull(s): return str(s).lower() in BULL_LABELS
def _is_bear(s): return str(s).lower() in BEAR_LABELS


def asof(series, t):
    try: return series.asof(pd.Timestamp(t))
    except Exception: return None


def analyze_trade(trade: dict, df: pd.DataFrame, closed: bool):
    side = int(trade["side"])
    entry_t = pd.Timestamp(trade["entry_time"])
    exit_t = pd.Timestamp(trade["exit_time"]) if closed else df.index[-1]

    state_col = "HMM_state_kalman" if "HMM_state_kalman" in df.columns else "HMM_state"
    raw_state_col = "HMM_state"
    p_col = "P_bull_kalman" if "P_bull_kalman" in df.columns else "P_bull"

    hmm_entry = asof(df[state_col], entry_t) if state_col in df.columns else "?"
    hmm_exit  = asof(df[state_col], exit_t)  if state_col in df.columns else "?"
    hmm_entry_raw = asof(df[raw_state_col], entry_t) if raw_state_col in df.columns else "?"
    p_entry = asof(df[p_col], entry_t) if p_col in df.columns else None
    p_exit  = asof(df[p_col], exit_t)  if p_col in df.columns else None

    long_side = (side == 1)
    aligned = (long_side and _is_bull(hmm_entry)) or ((not long_side) and _is_bear(hmm_entry))
    fighting = (long_side and _is_bear(hmm_entry)) or ((not long_side) and _is_bull(hmm_entry))
    flipped = str(hmm_entry).lower() != str(hmm_exit).lower()

    grade = "?"
    if closed:
        won = trade["pnl"] > 0
        if aligned and won:        grade = "A"
        elif aligned and not won:  grade = "B"
        elif fighting and won:     grade = "C"
        elif fighting and not won: grade = "D"
        else:                       grade = "N"   # neutral/range entry

    return {
        "hmm_entry_kalman": str(hmm_entry),
        "hmm_entry_raw":    str(hmm_entry_raw),
        "hmm_exit_kalman":  str(hmm_exit),
        "p_bull_entry":     float(p_entry) if p_entry is not None and pd.notna(p_entry) else None,
        "p_bull_exit":      float(p_exit)  if p_exit  is not None and pd.notna(p_exit)  else None,
        "aligned":          aligned,
        "fighting":         fighting,
        "flipped":          flipped,
        "grade":            grade,
    }


def main():
    with open("data/paper_account.json") as f:
        acct = json.load(f)

    closed = acct.get("closed_trades", [])
    open_ = acct.get("open_positions", [])

    print("\n" + "="*100)
    print("  HMM POSTMORTEM — Paper Trade Book")
    print(f"  Equity ${acct['equity']:,.0f}  ·  Realized PnL ${acct['equity']-acct['initial_capital']:+,.0f}  ·  "
          f"{len(closed)} closed · {len(open_)} open")
    print("="*100)

    # cache DFs per symbol
    df_cache = {}
    def get_df(sym):
        if sym not in df_cache:
            try:
                df_cache[sym] = prepare_dual(load_symbol(sym))
            except Exception as e:
                df_cache[sym] = None
                print(f"  [warn] {sym}: data load failed: {e}")
        return df_cache[sym]

    # ───── CLOSED TRADES ─────
    print(f"\n  ── CLOSED TRADES ──")
    print(f"  {'symbol':<10}{'strat':<13}{'side':<7}{'pnl $':>10}{'reason':<8}"
          f"{'hmm_entry':<11}{'hmm_exit':<11}{'flip':<6}{'grade':<6}")
    print("  " + "-"*92)
    closed_analyzed = []
    for t in closed:
        df = get_df(t["symbol"])
        if df is None: continue
        an = analyze_trade(t, df, closed=True)
        closed_analyzed.append({**t, **an})
        side_w = "LONG" if t["side"] == 1 else "SHORT"
        print(f"  {t['symbol']:<10}{t['strategy']:<13}{side_w:<7}{t['pnl']:>+9,.0f} "
              f"{t['reason']:<8}{an['hmm_entry_kalman']:<11}{an['hmm_exit_kalman']:<11}"
              f"{'Y' if an['flipped'] else 'N':<6}{an['grade']:<6}")

    # grade summary
    print(f"\n  ── GRADE BREAKDOWN ──")
    grades = pd.Series([t["grade"] for t in closed_analyzed])
    print(f"  A (aligned + won):     {(grades=='A').sum()}")
    print(f"  B (aligned + lost):    {(grades=='B').sum()}  ← structural; entry was right, market moved against")
    print(f"  C (fighting + won):    {(grades=='C').sum()}  ← lucky / chop-edge")
    print(f"  D (fighting + lost):   {(grades=='D').sum()}  ← AVOIDABLE; HMM disagreed and was right")
    print(f"  N (neutral entry):     {(grades=='N').sum()}")

    # ───── OPEN POSITIONS ─────
    print(f"\n  ── OPEN POSITIONS — current HMM read ──")
    print(f"  {'symbol':<10}{'strat':<13}{'side':<7}{'entry$':>11}{'size$':>10}"
          f"{'hmm_entry':<11}{'hmm_now':<10}{'flip?':<7}{'alignment':<10}")
    print("  " + "-"*100)
    flag_warnings = []
    for t in open_:
        df = get_df(t["symbol"])
        if df is None: continue
        an = analyze_trade(t, df, closed=False)
        side_w = "LONG" if t["side"] == 1 else "SHORT"
        align_label = ("aligned" if an["aligned"]
                       else "fighting" if an["fighting"]
                       else "neutral")
        flag = ""
        if an["flipped"] and an["fighting"]:
            flag_warnings.append(f"{t['symbol']} {t['strategy']} {side_w}: entered fighting HMM AND now flipped — high-risk")
        elif an["flipped"]:
            flag_warnings.append(f"{t['symbol']} {t['strategy']} {side_w}: HMM flipped against direction — watch")
        print(f"  {t['symbol']:<10}{t['strategy']:<13}{side_w:<7}{t['entry_price']:>10.2f}"
              f"{t['size']:>10,.0f} {an['hmm_entry_kalman']:<11}"
              f"{an['hmm_exit_kalman']:<10}{'YES' if an['flipped'] else 'no':<7}"
              f"{align_label:<10}")

    if flag_warnings:
        print(f"\n  ⚠️  OPEN-POSITION FLAGS:")
        for w in flag_warnings:
            print(f"     • {w}")

    # ───── COUNTERFACTUAL ─────
    print(f"\n  ── COUNTERFACTUAL: what if we'd filtered out D-grade entries? ──")
    d_pnl = sum(t["pnl"] for t in closed_analyzed if t["grade"] == "D")
    bcd_pnl = sum(t["pnl"] for t in closed_analyzed
                  if t["grade"] in ("B", "C", "D"))
    realized = sum(t["pnl"] for t in closed_analyzed)
    print(f"  Realized P&L:                   ${realized:+,.0f}")
    print(f"  D-grade contribution:            ${d_pnl:+,.0f}  ({(grades=='D').sum()} trades)")
    print(f"  Without D-grade entries:         ${realized - d_pnl:+,.0f}")
    print(f"  Best-case (only A-grade kept):   ${sum(t['pnl'] for t in closed_analyzed if t['grade']=='A'):+,.0f}")

    # ───── HONEST CAVEATS ─────
    print(f"\n  ── CAVEATS ──")
    print(f"  • Sample size {len(closed_analyzed)} closed trades — far below the ~50 needed for grade-mix significance.")
    print(f"  • HMM at entry uses Kalman-smoothed state ({state_col if 'state_col' in dir() else 'HMM_state_kalman'}); "
          f"see Part 8.10 for noise-reduction rationale.")
    print(f"  • Open-position 'HMM flipped' flag uses the Part 8.8 exit-leak finding: 54% of "
          f"losers had regime flip between entry & exit — bias to caution when flipped.")
    print(f"  • Per Part 8.11, the regime-flip exit primitive is GATED to GC=F only in production. "
          f"Other symbols' open positions ride until stop/TP/time even if HMM flips.")


if __name__ == "__main__":
    main()
