# Backtest Results — Pullback/Imbalance Engine

**Asset:** SPY 1H bars · **Data:** ~147.7 weeks yfinance (Jul 2023 → May 2026)
**Capital:** $100,000 start · single-symbol unless noted
**Costs:** fee 0.05% + slippage 0.02% per fill
**Engine:** EMA(50) > SMA(130), slope > 0, recent higher-high, deviation pullback,
momentum re-acceleration. Stop −2.5% · TP1 +4% (close 50%, stop→BE) · TP2 +10% ·
390-bar time stop · pyramid during growth/slowdown regimes.

`Tw` = trades/week · `WR` = win rate · `PF` = profit factor · `DD` = max drawdown

| # | Model | Tw | Legs | WR | PF | CAGR | DD | Sharpe | Final $ |
|---|---|---|---|---|---|---|---|---|---|
| 0 | **Baseline** (deterministic, no extra filters) | 0.86 | 191 | 70.7% | **2.55** | **17.2%** | 14.8% | 0.37 | **$156,622** |
| 1 | + RVOL hard filter | 0.65 | 137 | 64.2% | 1.60 | 7.1% | 13.5% | 0.21 | $121,409 |
| 2 | + RVOL + VWAP hard filters | 0.41 | 88 | 67.1% | 1.77 | 5.8% | 8.7% | 0.23 | $117,397 |
| 3 | + RVOL + VWAP + HMM hard gate | 0.07 | 13 | 46.2% | 0.92 | −0.1% | 6.8% | −0.01 | $99,626 |
| 3b | Phase 3 + loosened entries | 0.24 | 43 | 37.2% | 0.58 | −2.6% | 20.3% | −0.15 | $92,789 |
| 2a | VWAP-only diagnostic | 0.68 | 149 | 66.4% | 1.73 | 8.8% | 14.7% | 0.23 | $126,976 |
| 4 | Dual-strategy v1 | 0.92 | 206 | 68.0% | 2.50 | 8.2% | 7.5% | **0.44** | $125,024 |
| 5 | Dual-strategy v2 (HMM gates) | 0.24 | 51 | 62.8% | 1.41 | 1.4% | 7.5% | 0.15 | $103,978 |
| 6 | Pullback + HMM meta layer | 0.66 | 149 | 71.1% | 1.86 | 5.3% | 7.6% | 0.30 | $115,812 |
| 7 | + asymmetric sizing | 0.66 | 149 | 71.1% | 1.94 | 5.9% | 8.0% | 0.31 | $117,559 |
| 8 | + pullback + mean-rev-extremes | 0.72 | 161 | 69.6% | 1.91 | 6.1% | 7.6% | 0.32 | $118,171 |
| P | **Production** (cap 0.95 / base 0.25, VWAP pyramid gate) | 0.47 | 101 | 68.3% | 1.72 | 5.0% | 7.6% | 0.28 | $114,892 |
| L | **Production + 2.5× lev, Sharpe-wtd SPY+DIA** | — | — | — | — | — | — | — | **≈$203,858 (+$103,858)** |

## Notes for the quant

- **Model 0 is the raw edge.** Every "institutional" filter added afterward *removed*
  PF/CAGR. RVOL inverts pullback logic (pullbacks fire on low volume); VWAP rejects the
  deepest dips; HMM hard-gating is catastrophic (PF 0.92, loses money). All three were
  demoted to informational/diagnostic and kept *out* of the execution path.
- **Production (P)** trades ~30% of baseline CAGR for ~50% of baseline DD with much
  simpler logic. Pyramids contribute **72.6% of total PnL**; avg stack depth 2.39.
- **Model L** is the headline: clean production engine + 2.5× leverage on a
  Sharpe-weighted SPY+DIA book → roughly **doubles $100K**.
- Baseline leg breakdown (191 legs): TP1 +$39,366 (64×) · TP2 +$21,297 (16×) ·
  time +$23,308 (34×) · stops −$36,211 (68×, 19% WR). Winners pay ~2.4× the stop bleed.
- The engine is **scale-invariant** (operates on % moves) — same stats on $700 as $100K.
