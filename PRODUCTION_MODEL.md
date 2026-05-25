# Production Pullback Engine — Spec Card

**Status**: shipped · SPY 1H · single-symbol · single-strategy
**Edge generator**: pure deterministic (EMA(50) > SMA(130), slope > 0, recent HH, deviation pullback, momentum re-acceleration)
**Meta layer**: VWAP gates pyramiding · RVOL is diagnostic-only · HMM is informational-only

---

## Configuration (the numbers that matter)

| Parameter | Value | Why |
|---|---|---|
| `base_size_pct` | **0.25** | institutional-scale base entry |
| `capital_cap_pct` | **0.95** | near-full account allocation when VWAP-confirmed |
| `max_pyramid_positions` | 4 | hard count cap (cap binds before 4) |
| `pullback_band` | 0.007 | `|Close − EMA| / EMA ≤ 0.7%` |
| `imbalance_min` | 0.0003 | `(EMA − SMA)/SMA ≥ 0.03%` |
| `stop_loss_pct` | 0.025 | fixed −2.5% |
| `partial_tp_pct` | 0.04 | TP1 +4%, close 50%, trail to BE |
| `final_tp_pct` | 0.10 | TP2 +10%, runner |
| `max_hold_bars` | 390 | ~3 months on 1h |
| `pyramid_require_above_vwap` | True | institutional confirmation |
| `pyramid_require_positive_momentum` | True | trend continuation only |

---

## Backtest results (SPY 1H · 147.7 weeks)

| Metric | Value |
|---|---|
| Total legs | 101 |
| Unique entries | 70 |
| Trades/week | 0.47 |
| **Win rate** | **68.32%** |
| **Profit factor** | **1.72** |
| Expectancy/leg | +1.88% |
| **Max drawdown** | **7.60%** |
| CAGR | 5.03% |
| Sharpe | 0.28 |
| Final equity | $114,892 from $100,000 |

---

## Pyramid attribution

| Stack tier | Unique entries | Legs | $ contribution | % of total |
|---|---|---|---|---|
| Initial (stack_idx = 0) | 22 | 31 | +$4,083 | 27.4% |
| **Pyramid (stack_idx ≥ 1)** | 48 | 70 | **+$10,809** | **72.6%** |
| of which VWAP-confirmed | 48 | 70 | +$10,809 | 72.6% |

* **Average stack depth: 2.39** · max: 4 (the hard cap)
* **VWAP-confirmed pyramids deliver 72.6% of total PnL** — the institutional scaling is the dominant edge concentrator
* Initial entries (no VWAP requirement) still represent ~27% — first entries on pullbacks below VWAP are valuable; gating them on VWAP would cost edge

---

## RVOL at entry (informational research metric)

| Group | Avg RVOL | n |
|---|---|---|
| Winners | **1.082** | 69 |
| Losers | 0.995 | 32 |

Quartiles across all entries: Q1=0.71 · med=0.92 · Q3=1.32

**Interpretation**: pullbacks fire on lower-than-average volume (median 0.92). RVOL has a small positive winners-vs-losers gap (+0.087) but it's too weak to filter on. **Tracked for the AI/Obsidian context layer; not used in execution.**

---

## Exposure utilisation

* Cap (configured): **95%** of equity
* Max utilisation reached: **97.2%** (102% of cap — fully saturated when conditions allow)
* Avg utilisation when invested: 59.8%
* Bars invested: 77.0% of period

The cap binds. Engine exercises the full exposure budget during VWAP-aligned uptrends.

---

## Architecture rules — the design contract

| Lever | Role | Used by execution? |
|---|---|---|
| **EMA / SMA structure** | edge generator | ✓ entries, ✓ pyramids |
| **Deviation + momentum** | entry trigger | ✓ entries |
| **Deterministic regime** | regime gate | ✓ pyramids |
| **VWAP** | institutional pyramid confirmation | ✓ pyramids only — **never** initial entries |
| **RVOL** | research / diagnostic | ✗ informational — exposed in trades, dashboard, logs |
| **HMM** | context / future research | ✗ informational — exposed in dashboard |
| **Sizing** | fixed at base_size_pct × cap-respecting math | no scaling |

**Hard rules**:
1. RVOL never gates entries.
2. VWAP never gates initial entries.
3. HMM never affects sizing or trade permission.
4. Pyramiding requires all four: bullish structure · regime ∈ {growth, slowdown} · Close > VWAP · Momentum > 0.

---

## Comparison vs prior architectures

| Architecture | Tw | WR | PF | CAGR | DD | Sharpe | Final $ |
|---|---|---|---|---|---|---|---|
| Original baseline (cap=1.00, base=0.27, no VWAP gate) | 0.86 | 70.7% | 2.55 | 17.2% | 14.8% | 0.37 | $156,622 |
| HMM-meta (Path 1: asymmetric sizing) | 0.66 | 71.1% | 1.94 | 5.9% | 8.0% | 0.31 | $117,559 |
| HMM-meta + meanrev (Path 2) | 0.72 | 69.6% | 1.91 | 6.1% | 7.6% | 0.32 | $118,171 |
| Production cap=0.50 / base=0.15 | 0.47 | 68.3% | 1.70 | 2.7% | 4.2% | 0.27 | $107,808 |
| **Production cap=0.95 / base=0.25 (this build)** | **0.47** | **68.3%** | **1.72** | **5.0%** | **7.6%** | **0.28** | **$114,892** |

**The trade-off snapshot**: ~30% of baseline CAGR for ~50% of baseline DD, with substantially simpler execution logic (no HMM gating, no probabilistic scaling, no second strategy to monitor).

---

## What's preserved vs the dual/HMM eras

**Kept**: deterministic regime model · pullback entry rules · pyramid count cap · fixed exit ladder (−2.5% / +4% / +10%) · trailing-after-partial · session-anchored VWAP · HMM probabilities in dataframe · RVOL in dataframe

**Removed from execution**: HMM size scaling · HMM disagreement penalty · HMM pyramid gating · RVOL entry filter · VWAP entry filter · breakout strategy · meanrev strategy

**Added**: VWAP pyramid confirmation gate · momentum-positive pyramid requirement · RVOL win/loss diagnostic · pyramid contribution attribution · exposure utilisation tracker

---

## Run cookbook

```bash
# Backtest
python -m main_portfolio SPY

# Live dashboard (production execution view)
python -m live_runner SPY
python -m live_runner SPY --refresh

# Single-line knobs (config/settings.py · PullbackStratConfig)
base_size_pct           0.25      # ↑ for more risk per entry
capital_cap_pct         0.95      # ↓ for tighter DD, ↑ for more CAGR
max_pyramid_positions   4         # hard count cap
pyramid_require_above_vwap          True   # set False to revert to original baseline pyramiding
pyramid_require_positive_momentum   True
```

---

## What's queued (when you want more juice)

1. **Multi-symbol fanout**: same code, run on QQQ/IWM/XLK/XLF in parallel. Each new symbol multiplies diversification surface area.
2. **Re-enable meanrev on IWM**: small caps dip harder; the `Price_dev ≤ −0.012` event will fire much more often than on SPY.
3. **Lift cap further**: `capital_cap_pct: 0.95 → 1.00` for full unlevered exposure (DD likely ~9-10%, CAGR closer to 6-7%).
4. **Loosen one pyramid gate**: drop `pyramid_require_positive_momentum` → more pyramid stacks, slightly noisier.

The institutional architecture (VWAP gate, RVOL diagnostic, HMM informational) is preserved across all four — these are knob tweaks, not architectural changes.
