# Quant IA Trading System — Session Log

A chronological record of every strategy variant attempted in this session, with backtest metrics, the issue each one revealed, and the fix that followed. Drop into your second brain as a single-file reference.

**Asset / Timeframe**: SPY 1H bars · ~147.7 weeks of yfinance data (Jul 2023 → May 2026)
**Capital**: $100,000 starting · single-symbol single-asset
**HMM training**: rolling 6-month train / 1-month predict / monthly refit

---

## Master comparison table

| # | Configuration | Trades/wk | Legs | WR | PF | CAGR | DD | Sharpe | Final $ |
|---|---|---|---|---|---|---|---|---|---|
| 0 | **Baseline** (no filters) | 0.86 | 191 | 70.7% | **2.55** | **17.2%** | 14.8% | 0.37 | $156,622 |
| 1 | + RVOL hard filter | 0.65 | 137 | 64.2% | 1.60 | 7.1% | 13.5% | 0.21 | $121,409 |
| 2 | + RVOL + VWAP hard filters | 0.41 | 88 | 67.1% | 1.77 | 5.8% | 8.7% | 0.23 | $117,397 |
| 3 | + RVOL + VWAP + HMM hard gate | 0.07 | 13 | 46.2% | 0.92 | −0.1% | 6.8% | −0.01 | $99,626 |
| 3b | Phase 3 + loosened entries | 0.24 | 43 | 37.2% | 0.58 | −2.6% | 20.3% | −0.15 | $92,789 |
| 2a | VWAP-only diagnostic | 0.68 | 149 | 66.4% | 1.73 | 8.8% | 14.7% | 0.23 | $126,976 |
| 4 | Dual-strategy v1 (initial spec) | 0.92 | 206 | 68.0% | 2.50 | 8.2% | **7.5%** | **0.44** | $125,024 |
| 5 | Dual-strategy v2 (HMM hard gates) | 0.24 | 51 | 62.8% | 1.41 | 1.4% | 7.5% | 0.15 | $103,978 |
| 6 | Pullback-only + HMM meta layer | 0.66 | 149 | 71.1% | 1.86 | 5.3% | 7.6% | 0.30 | $115,812 |
| 7 | + Path 1: asymmetric sizing | 0.66 | 149 | **71.1%** | 1.94 | 5.9% | 8.0% | 0.31 | $117,559 |
| 8 | **+ Path 2: pullback + mean-rev-extremes (current)** | 0.72 | 161 | 69.6% | 1.91 | 6.1% | **7.6%** | **0.32** | $118,171 |

---

## #0 — Baseline single pullback engine

**Setup**
* Strategy: pullback into bullish structure (EMA(50) > SMA(130), slope > 0, recent HH).
* Entry filters: `|Close − EMA| / EMA ≤ 0.007`, `(EMA − SMA)/SMA ≥ 0.0003`, momentum re-accelerating.
* Sizing: 27% notional per entry · pyramid up to 10 stacks during growth/slowdown regimes.
* Exits: stop −2.5% · TP1 +4% (close 50%, trail to BE) · TP2 +10% · 390-bar time stop.

**Result**
* 191 legs / 127 entries / 0.86 trades/wk · WR 70.7% · PF 2.55 · CAGR 17.2% · DD 14.8% · Sharpe 0.37 · Final $156,622.

**Issue**
* Frequency below the user's ~5/week target. DD high relative to single-symbol capacity.

**Insight that emerged later**
* Despite all subsequent additions, this remained the highest CAGR / highest PF run of the session — the deterministic structure model is the source of edge.

---

## #1 — Phase 1: RVOL hard filter

**Change**
* Required `RVOL > 1.1` for longs, `RVOL > 1.2` for shorts. Computed RVOL as `Volume / SMA(Volume, 20)`.

**Result**
* 137 legs · WR 64.2% · PF 1.60 · CAGR 7.1% · Final $121,409.

**Issue (key learning of the day)**
* **Pullback strategies fire on lower-volume bars by definition** — buyers step aside, sellers light. Demanding `RVOL > 1.1` *inverts* the logic. WR dropped from 70.7% → 64.2%, PF nearly halved.

**Fix**
* RVOL belongs in a breakout (buy-strength) strategy, not a pullback (buy-the-dip) strategy. RVOL was later moved out of pullback and into the breakout module.

---

## #2 — Phase 2: RVOL + VWAP hard filters

**Change**
* Added: longs require `Close > VWAP`, shorts require `Close < VWAP`. Session-anchored daily-reset VWAP.

**Result**
* 88 legs · WR 67.1% · PF 1.77 · CAGR 5.8% · DD 8.7% · Final $117,397.

**Issue**
* Same structural conflict — pullbacks often dip *below* intraday VWAP because price is consolidating below the session average. `Close > VWAP` rejects the deepest, juiciest pullback bars.
* DD did fall (14.8% → 8.7%) because fewer trades = less exposure, but at the cost of throughput.

**Fix**
* VWAP demoted to a diagnostic (still computed, no longer an entry gate).

---

## #2a — Bonus: VWAP-only

**Setup**
* RVOL off, VWAP on, no HMM gate.

**Result**
* 149 legs · WR 66.4% · PF 1.73 · CAGR 8.8% · DD 14.7%.

**Insight**
* VWAP alone is the *least destructive* of the three institutional filters — it almost matches baseline frequency but degrades quality. Confirmed VWAP fights the pullback premise less than RVOL does.

---

## #3 — Phase 3: RVOL + VWAP + HMM hard gate

**Change**
* Added Hidden Markov Model trained on (log_returns, vol_ratio) with 3 states (bull/bear/range), rolling 6-month train / 1-month predict, monthly refit.
* Hard gate: longs require `P_bull > 0.6`, shorts require `P_bear > 0.6`.

**Result**
* 13 legs · WR 46.2% · PF 0.92 · CAGR −0.1% · Final $99,626 (lost money).

**Issue**
* Triple-stacked filters reduced trade count by 86% (191 → 13) and turned a 70.7% WR profitable system into a 46% WR loser.
* HMM warmup eats ~6 months (1100 bars), then `P_bull > 0.6` is rarely true mid-pullback because returns look bear-like during retracements.
* **Confirmed: stacking confirmation filters on a pullback strategy compounds the conflict, doesn't dilute it.**

**Fix**
* All three filters subsequently removed from pullback entries. RVOL+VWAP moved exclusively to breakout. HMM repurposed as a meta layer (sizing/pyramiding) rather than a gate.

---

## #3b — Phase 3 + loosened entries (failed rescue attempt)

**Change**
* Loosened `pullback_band` 0.007 → 0.014 and `imbalance_min` 0.0003 → 0.00015 on top of the Phase 3 filter stack to try to recover frequency.

**Result**
* 43 legs · WR 37.2% · PF 0.58 · CAGR −2.6% · DD 20.3%.

**Issue / lesson**
* **Loosening entries on top of mismatched filters compounds the damage** — when filters fight the strategy, no amount of entry relaxation rescues quality, it just lets in worse bars.

**Fix**
* Validated the rule: fix the filter mismatch first, *then* talk about loosening entries.

---

## #4 — Dual-strategy v1 (pullback + breakout, initial implementation)

**Architecture change**
* Created `strategies/pullback.py` and `strategies/breakout.py` as fully independent modules.
* Created `execution/portfolio.py` — generic portfolio backtester running N strategies in parallel.
* Capital caps: 70% pullback / 30% breakout.

**Pullback** (no HMM gate, HMM as sizing only)
* 179 legs · WR 68.2% · PF 2.41 · +$22,222 contribution.

**Breakout** (initial spec — vol_ratio > 1.4, no HMM gate, EMA(50) trail after final TP)
* 27 legs · WR 66.7% · PF **3.98** · +$2,802 contribution.

**Combined**
* 0.92 trades/wk · WR 68.0% · PF **2.50** · CAGR 8.2% · **DD 7.5%** · **Sharpe 0.44** · Final $125,024.

**Insight**
* Breakout PF of 3.98 standalone proved RVOL+VWAP+vol-expansion *do* add edge — but only paired with breakout entry logic, never with pullback entry logic.
* Drawdown halved vs baseline (14.8% → 7.5%) and Sharpe rose 19% — diversification doing exactly what it should.
* CAGR lower because capital caps + lower per-strategy sizing (0.27 / 0.20) constrain stacking vs the unconstrained baseline.

**Issue**
* CAGR fell from 17.2% → 8.2% — the trade-off was real but the user wanted more.

---

## #5 — Dual-strategy v2 (full institutional spec with HMM hard gates)

**Changes per "institutional upgrade phase" spec**
* Pullback: added `P_bull > 0.50` soft entry filter, `P_bear > 0.60` for shorts.
* Pullback pyramiding: required `regime in {growth} AND P_bull > 0.70`.
* Breakout: relaxed `vol_ratio_min` 1.4 → 1.2; added HMM gates; trailing stop "starts immediately."
* Shared `ExitProfile` dataclass.
* Per-strategy DD, return correlation, avg holding period.

**Pullback**
* 41 legs · WR 68.3% · PF 1.51 · +$4,179.

**Breakout**
* 10 legs · WR 40% · PF 0.86 · −$201.

**Combined**
* 0.24 trades/wk · WR 62.8% · PF 1.41 · CAGR 1.4% · DD 7.5% · Sharpe 0.15.
* **Inter-strategy correlation: 0.076** (genuinely uncorrelated ✓).

**Issues**
* `P_bull > 0.50` plus the HMM warmup blocked pullback during the first 6 months entirely → frequency collapsed (121 → 27 entries).
* Breakout's "immediate trailing stop" cut winners short — 5 of 10 legs exited as `trail` before reaching TP1.
* Loosened breakout `vol_ratio` from 1.4 → 1.2 admitted noisier expansions, dropping breakout WR 67% → 40%.

**Fix paths considered**
* Treat NaN HMM as a pass during warmup → recovers warmup bars.
* Move breakout trail to "after_partial" → lets runner reach TP1.
* Restore `vol_ratio > 1.4` if breakout gets re-enabled.

---

## #6 — Pullback-only + HMM as meta layer (the design decision that stuck)

**User design pivot**
> "HMM should help manage uncertainty / exposure / aggressiveness, not replace the edge generator."

**Architectural revert**
* Removed all hard HMM/RVOL/VWAP entry gates from pullback.
* Disabled breakout entirely (kept file dormant).
* HMM repurposed for three things and three things only:
  1. **Sizing**: `P_bull > 0.7 → 1.4×`, `P_bull < 0.3 → 0.5×`, else `1.0×`.
  2. **Confidence score**: per-bar direction-aware probability column.
  3. **Pyramiding aggressiveness**: disagreement → cap = 0; otherwise → full cap.
* Added "regime disagreement" flag: `bullish deterministic regime AND P_bull < 0.3` (or symmetric for bearish).

**Result**
* 149 legs · 97 entries · **0.66 trades/wk** · **WR 71.1%** · PF 1.86 · CAGR 5.3% · **DD 7.6%** · Sharpe 0.30 · Final $115,812.

**Diagnostics**
* HMM coverage: 84.7% of bars (rest is warmup).
* 33% of all bars in disagreement.
* **43.4% of signal bars in disagreement** — the HMM and structural model diverge often, mostly during pullbacks.
* Sizing distribution at signals: 157 × 0.5×, 136 × 1.0×, 57 × 1.4× — HMM actively reducing exposure on 45% of trades.

**Insight (core finding of the session)**
* Frequency **76% of baseline restored** while DD remained halved.
* HMM works as a *real defensive risk dampener* exactly as designed — when deterministic and probabilistic models disagree, size shrinks and pyramiding stops.
* The drag on CAGR is the *cost of insurance* against regime-change overstays.

---

## #7 — Path 1: asymmetric sizing (current)

**Change**
* `size_mult_high: 1.4 → 2.0`. Single number tweak.
* Rationale: high-confidence trades pay for the 0.5× drag on disagreement bars.

**Result**
* 149 legs · 97 entries · 0.66 trades/wk · **WR 71.1%** · **PF 1.94** · **CAGR 5.9%** · DD 8.0% · Sharpe 0.31 · Final $117,559.

**Why the gain was modest**
* Only 57 of 350 signals (16%) qualify for `P_bull > 0.70` on SPY 1h — most pullback bars sit in HMM-neutral or HMM-bear zones.
* Doubling 16% of trades only added ~$1,750 in total PnL.

**Remaining levers within Path 1**
* Lower `pyramid_aggressive_p_bull` 0.70 → 0.60 to admit more signals to the boosted size tier.
* Push `size_mult_high` to 2.5× or 3.0×.
* Both are one-line changes; both increase exposure during high-confidence bars only.

---

---

## #8 — Path 2: Pullback + Mean-Reversion-on-Extremes

**Setup**
* Created `strategies/mean_reversion_extremes.py` as a third strategy module.
* Thesis: deep dips inside a still-bullish regime are statistically high-probability mean-reversions, fires when pullback doesn't (price far below EMA, not near it).
* Entry filters: `Is_bullish_regime`, `Price_dev ≤ −0.012` (≥1.2% below EMA), `Close < SMA`, intrabar buying response (close in upper 60% of bar range).
* Exit profile: stop −2%, TP1 +2.5%, TP2 +5%, max hold 60 bars (~1.5 weeks). No trailing — fixed targets only.
* Sizing: 20% base · 30% capital cap · max 3 pyramid stacks · same HMM meta layer as pullback.

**Per-strategy attribution**

| | Legs | Entries | Tw | WR | PF | DD | $ |
|---|---|---|---|---|---|---|---|
| pullback | 149 | 97 | 0.66 | 71.1% | 1.94 | 8.1% | +$17,646 (97.1%) |
| **meanrev** | 12 | 9 | 0.06 | 50.0% | **1.44** | **1.0%** | +$525 (2.9%) |

**Combined**
* 161 legs · 106 entries · 0.72 trades/wk · WR 69.6% · PF 1.91 · CAGR 6.1% · **DD 7.6%** · Sharpe 0.32 · Final $118,171.

**THE KEY METRIC**
* **Inter-strategy return correlation: ρ = +0.030** ← essentially zero. Diversification working as designed.

**Why correlation is so low**
* Pullback fires near the EMA (`|Price_dev| ≤ 0.007`).
* Mean-rev fires far below EMA (`Price_dev ≤ −0.012`).
* The two signal sets are mutually exclusive by construction.

**Issue (and the honest take)**
* Mean-rev fires only **9 times in 34 months**. Deep capitulation events on a low-vol index ETF like SPY are rare.
* Diversification is real (DD 8.0% → 7.6%, Sharpe 0.31 → 0.32) but small in magnitude — meanrev contributes 2.9% of total PnL.
* Tested looser threshold (−0.008 instead of −0.012): trade count tripled, but correlation jumped 0.03 → 0.20 and meanrev PF dropped 1.44 → 1.26 — diversification quality lost. Reverted.

**Insight**
* Path 2 *architecturally works* — the `StrategySpec` plug-in pattern absorbed the new strategy in 80 lines of code with no changes to the portfolio backtester.
* Path 2 *quantitatively works* — correlation ≈ 0 confirms genuine alpha-stream independence.
* Path 2 *doesn't close the CAGR gap on a single low-vol asset* because mean-rev events are too rare on SPY 1h to add meaningful magnitude.

**Conclusion**
* The plumbing for diversification is now proven and reusable. The next leverage point is **multi-symbol fanout** or a third strategy with a higher firing rate (e.g., session-anchored opening-hour breakout, VWAP-fade) — both are single-file additions on top of this architecture.

---

## The dilemma named explicitly

> Can the system simultaneously hit baseline PF (2.55), Sharpe (0.44), CAGR (17.2%) **while preserving the halved drawdown (7.6% vs 14.8%)**?

**No, not on a single non-diversified strategy with the same signals.** That's mathematically the impossible trinity for one engine — the DD halving came from the HMM downsizing 45% of trades to 0.5×. Restoring full sizing brings back full CAGR but also full DD.

**Three honest paths out (in order of difficulty):**

1. **Asymmetric sizing** *(implemented — Path 1, ~6% PnL gain)*: closes a fraction of the gap. Easy. One number change.
2. **Add an uncorrelated second strategy** *(implemented — Path 2)*: ρ = +0.030 confirmed genuine independence. Architecture proven. Magnitude small on SPY 1h because deep dips are rare on a low-vol index ETF — needs higher-volatility assets or higher-frequency strategies to scale.
3. **Sharpen the signal itself** (raise entry bar): fewer trades, higher per-trade quality. Compounds with #1.

**The empirical answer to the dilemma**: a single SPY 1h non-diversified strategy cannot get to baseline CAGR/Sharpe with halved DD. The architecture to fix it (multi-strategy portfolio, genuine zero-correlation alpha streams) is now built and validated. The remaining lift comes from running the same plumbing across more symbols and/or adding more strategy types.

---

## Architecture inventory (final state)

```
quant_ia_trading_system/
├── core/
│   ├── data_loader.py          yfinance + intraday-period handling
│   ├── indicators.py           EMA, SMA, slope, momentum, deviation, vol_ratio, RVOL, VWAP
│   ├── regime_model.py         deterministic 5-state classifier (KEPT, source of edge)
│   └── hmm_regime.py           rolling-window HMM, posterior probabilities
├── strategy/
│   └── structure.py            bullish/bearish/neutral structural label
├── strategies/
│   ├── exit_profile.py                shared ExitProfile contract
│   ├── pullback.py                    ACTIVE: deterministic gates + HMM meta layer
│   ├── mean_reversion_extremes.py     ACTIVE: deep-dip mean-reversion (Path 2)
│   └── breakout.py                    DORMANT: ready to re-enable
├── execution/
│   └── portfolio.py            multi-strategy backtester with attribution
├── backtest/
│   ├── backtester.py           legacy single-strategy engine (still works)
│   └── metrics.py              WR, PF, DD, CAGR, Sharpe, expectancy
├── config/settings.py          all tunables (one source of truth)
├── main.py                     legacy single-strategy orchestrator
├── main_portfolio.py           ACTIVE: portfolio orchestrator + HMM diagnostics
├── live.py                     legacy live snapshot
├── live_runner.py              ACTIVE: dual-aware live dashboard
├── STRATEGY.md                 conceptual reference doc
└── SESSION_LOG.md              this file
```

---

## What HMM is doing now (the keep-this-mental-model section)

| Lever | Behaviour |
|---|---|
| **Trade permission** | Never blocked. Pure deterministic structure decides entries. |
| **Sizing** | `P_bull > 0.70` → 2.0×  ·  `0.30 ≤ P_bull ≤ 0.70` → 1.0×  ·  `P_bull < 0.30` → 0.5× |
| **Confidence score** | `pullback_Confidence` column — direction-aware probability |
| **Regime disagreement** | `bullish det. regime AND P_bull < 0.30` (or symmetric for bearish) |
| **Pyramiding aggressiveness** | Disagreement → cap = 0 · otherwise → full cap (10) |
| **Surface** | `live_runner.py` flags ⚠ on every disagreement bar |

> **Regime disagreement, in one line**: deterministic structure says one thing (e.g. "growth") while the return-distribution HMM says another (P_bull near zero) — when they diverge, the system still trades but cuts size and freezes pyramiding.

---

## Single-session laws of motion (the takeaways)

1. **The deterministic edge is the strategy.** Every filter that disagreed with it cost performance.
2. **A pullback strategy and a breakout strategy need different filters.** RVOL + VWAP belong on breakouts. They actively *invert* pullback logic.
3. **Filters compound, not dilute.** Stacking three "good" filters on a strategy whose entries they each contradict produces a 12× collapse in trade count and a profitable system turning into a losing one.
4. **HMM as gate is destructive. HMM as risk-manager is valuable.** Same model, totally different role.
5. **Diversification is the only free lunch.** Two uncorrelated 1.5-PF streams beat one 2.5-PF stream on Sharpe — provided correlation is genuinely near zero (we measured 0.076 in v1).
6. **The impossible trinity (CAGR + low DD + same signals) cannot be tuned away** — it can only be diversified away.

---

## Run cookbook (final)

```bash
# Backtest
python -m main_portfolio SPY     # full attribution + HMM diagnostics
python -m main SPY               # legacy single-strategy path

# Live
python -m live_runner SPY        # confidence + disagreement + pyramid status
python -m live_runner QQQ --refresh

# Tuning levers (config/settings.py → PullbackStratConfig)
size_mult_high               2.0    # boost on P_bull > 0.70
size_mult_low                0.5    # cut on P_bull < 0.30 (disagreement)
pyramid_aggressive_p_bull    0.70   # threshold for full pyramid cap
disagreement_p_bull_threshold 0.30  # disagreement trigger
max_pyramid_positions        10     # full cap (active when no disagreement)
```

---

## Next session (queued)

* **Multi-symbol fanout** *(highest-leverage move now)*: run the proven pullback + meanrev pair on QQQ, IWM, XLK, XLF in parallel. Aggregate equity curves. Each new symbol multiplies the diversification surface area without requiring any new strategy code — the `StrategySpec`/`run_portfolio` pattern is already symbol-agnostic.
* **Test meanrev on IWM specifically**: small-cap volatility means the `Price_dev ≤ −0.012` event will fire much more often than on SPY. Single ticker swap, same code.
* **Add a third strategy with a different temporal pattern**: candidates that should keep low correlation with both pullback and meanrev — session-anchored opening-hour breakout, VWAP-fade (long when Close pulls 1.5σ below daily VWAP in bullish regime), gap-fill on >0.5% opens.
* Try lowering `pyramid_aggressive_p_bull` 0.70 → 0.60 to push more pullback trades into the 2.0× sizing tier.
* Consider re-enabling breakout on IWM where vol_ratio > 1.4 fires more frequently.

---

## #9 / #10 — Re-implanting baseline edge into the production ATR engine

**Question tested:** can we recover Model 0's raw edge inside the *current* production
engine (ATR-normalized thresholds + trend-carry sleeve + +15% runner TP) by un-throttling
size/pyramids/gates — and what does the VWAP/momentum gate actually cost now?

**Setup:** single-symbol SPY · same ~147.7 wks · `python -m _research_edge`.
Two configs, all else production-default (`base_size_pct=0.30`, `final_tp_pct=0.15`,
ATR-normalized ON, trend-carry sleeve active):

| # | Model | Tw | Legs | Entries | WR | PF | Exp/leg | CAGR | DD | Sharpe | Final $ |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 9 | **FULL EDGE** — cap 1.00, pyr 10, **VWAP gate OFF, mom gate OFF** | 0.82 | 197 | 121 | 68.5% | **2.92** | +3.02% | 12.5% | 8.2% | 0.51 | **$139,419** |
| 10 | **MIDDLE** — cap 1.00, pyr 8, **VWAP gate ON, mom gate ON** | 0.67 | 161 | 99 | 69.6% | 2.83 | +3.14% | 12.5% | **7.9%** | **0.52** | $139,366 |

**Result**
* Both land at ~$139K — **statistically the same money and the same ~8% DD**.
* FULL EDGE: +36 legs, slightly higher PF (2.92 vs 2.83).
* MIDDLE: marginally better Sharpe (0.52) and lower DD (7.9%) — the institutional
  discipline is **free** here.

**Key insight (updates the original RVOL/VWAP lesson)**
* The old finding "VWAP gate destroys pullback edge" was specific to the **fixed-threshold**
  engine. Under **ATR-normalized thresholds**, the VWAP + momentum pyramid gates cost
  essentially **nothing** — you keep the drawdown safety net for free.
* Both configs comfortably beat the old single-symbol Production ($114,892). The driver
  is the **+15% runner TP** (`final_tp_pct` 0.10 → 0.15), not the size/gate changes.

**Decision**
* **Run MIDDLE (#10) as the production default**: identical return, lower DD, better
  Sharpe, retains VWAP discipline. Not yet locked into `config/settings.py` — pending
  user confirmation. SPY+DIA Sharpe-weighted two-symbol run also still queued.

---

## #11–#17 — Leverage sweep on the production engine (single-symbol SPY)

**Question tested:** push the production engine toward a higher equity target with
leverage (`capital_cap_pct` acts as the gross-exposure multiple of equity). All runs
single-symbol SPY, same ~147.7 wks, `python -m _research_edge`.

| # | Model | PF | CAGR | DD | Sharpe | Final $ |
|---|---|---|---|---|---|---|
| 11 | A FULL EDGE 1.0x, pyr10, gates OFF | 2.92 | 12.5% | 8.2% | 0.51 | $139,419 |
| 12 | B MIDDLE 1.0x, pyr8, gates ON | 2.83 | 12.5% | 7.9% | 0.52 | $139,366 |
| 13 | C MIDDLE 2.5x, pyr8, gates ON | 2.45 | 17.5% | 13.9% | 0.41 | $157,594 |
| 14 | D MIDDLE 3.5x, pyr8, gates ON | 2.45 | 17.5% | 13.9% | 0.41 | **$157,594 (identical to C)** |
| 15 | E FULL EDGE 3.0x, pyr10, gates OFF | **3.31** | 27.1% | 17.2% | **0.54** | $196,754 |
| 16 | F FULL EDGE 3.5x, pyr14, gates OFF | 2.97 | 30.7% | 20.3% | 0.46 | $213,034 |
| 17 | G FULL EDGE 4.0x, pyr16, gates OFF | 2.80 | 32.2% | 23.2% | 0.44 | $219,871 |

**Why each step changed the result — the mechanics**

* **#12 → #13 (add 2.5x leverage to MIDDLE): +$18K.** Leverage scales every winning
  trade's notional. CAGR jumps 12.5% → 17.5%, but DD also scales ~1.8× (7.9% → 13.9%)
  and PF *drops* (2.83 → 2.45) because losing legs are levered too — leverage amplifies
  variance, it does not improve edge.
* **#13 → #14 (MIDDLE 2.5x → 3.5x): ZERO change.** The critical structural finding:
  with the VWAP + momentum pyramid gates ON and an 8-pyramid cap, **gross exposure
  saturates at ~2.4× equity**. The gates reject most pyramid adds, so the strategy
  never spends the extra leverage budget. `capital_cap_pct` above ~2.5 is dead config
  for MIDDLE. **The gates are an implicit leverage ceiling.**
* **#14 → #15 (drop the gates, FULL EDGE 3.0x): +$39K, and PF *rises* to 3.31.**
  This is the standout. Removing the VWAP/momentum pyramid gates lets the engine
  actually stack into confirmed trends and *use* the leverage budget. Because the
  underlying pullback edge is real, more pyramid legs on winners raised PF from 2.45
  to 3.31 *and* lifted CAGR to 27%. Best Sharpe of the entire sweep (0.54). **The
  pyramid gates were capping the edge, not protecting it — under ATR-normalized
  thresholds the deep-dip adds the VWAP gate rejects are profitable.**
* **#15 → #16 → #17 (push leverage 3.0 → 3.5 → 4.0x, pyr 10 → 14 → 16): +$23K total,
  but quality decays.** Each step adds raw CAGR (27% → 32%) purely from leverage, while
  PF falls (3.31 → 2.80), Sharpe falls (0.54 → 0.44), and DD balloons (17% → 23%).
  Past #15 the extra dollars are leverage stacking, **not the strategy improving** —
  you are buying return with variance.

**Key takeaways**
1. **Weak/no gating + moderate leverage is the sweet spot (#15).** Best PF and Sharpe
   in the whole study. The institutional pyramid gates, useful at 1× for DD control,
   become a hard leverage ceiling and actually *cost* edge once leverage is applied
   under ATR-normalized thresholds.
2. **MIDDLE cannot be levered past ~2.5×** — its own gates block it (#13=#14).
3. **$220K single-symbol is reachable (#17) but not worth it** — 23% DD, worst
   risk-adjusted metrics. The two-symbol SPY+DIA route is the better path to that range.

**Next:** implement **FULL EDGE · 2.5× leverage · weak gating · multi-symbol**
(SPY+DIA, Sharpe-weighted) — the diversified version of the #15 sweet spot.

---

## #18–#20 — FULL EDGE · 2.5× · weak gating · multi-symbol (the production target)

**Config:** pullback gates OFF (`pyramid_require_above_vwap=False`,
`pyramid_require_positive_momentum=False`), `max_pyramid_positions=10`,
`base_size_pct` & `capital_cap_pct` ×2.5, no VIX overlay, trend-carry sleeve active.
Per-symbol equity curves Sharpe-weighted into a $100K book.
Harness: `python -m _research_fulledge_multi`.

| # | Book | Final $ | PnL | CAGR | DD | Sharpe | Sortino | MAR |
|---|---|---|---|---|---|---|---|---|
| 18 | SPY solo | $217,970 | +$117,970 | 31.8% | 19.5% | 1.34 | — | — |
| 19 | DIA solo | $205,834 | +$105,834 | 29.1% | 16.3% | 1.21 | — | — |
| 19q | QQQ solo | $133,067 | +$33,067 | 10.6% | 27.3% | 0.50 | — | — |
| 20a | **SPY+DIA Sharpe-wtd** | **$212,217** | **+$112,217** | **30.5%** | **15.3%** | **1.35** | 1.67 | **2.0** |
| 20b | SPY+DIA+QQQ Sharpe-wtd | $199,165 | +$99,165 | 27.5% | 16.7% | 1.26 | 1.56 | 1.65 |

**Why this beats the single-symbol leverage route**

* Single-symbol path to ~$220K was #17 (4.0× lev, pyr 16): 23.2% DD, Sharpe 0.44,
  PF 2.80 — pure leverage stacking, degrading quality.
* #20a reaches the **same ~$212K with 15.3% DD and Sharpe 1.35** (MAR 2.0). The
  return comes from (a) the no-gate edge unlocking real pyramid stacking under
  ATR-normalized thresholds, and (b) SPY/DIA diversification smoothing the curve so
  the same 2.5× leverage produces far less drawdown than 4× on one symbol.
* The intraday-bar Sharpe is annualised at 252×7 here (hence ~1.3 vs ~0.5 on the
  daily-equity convention used in #11–#17) — compare Sharpe *within* a study, not across.

**Why QQQ drags (#19q / #20b)**

* QQQ: PF 1.32, DD 27.3%, Sharpe 0.50 — the pullback edge is much weaker on the
  Nasdaq-100 (sharper, gappier tech-led moves break the deviation-revert assumption).
  Adding it pulls the book from $212K → $199K and *raises* DD. **Keep the book to
  SPY + DIA.** Confirms the earlier RVOL/IWM lesson: this engine wants broad,
  mean-reverting large-cap indices, not high-beta growth baskets.

**Production conclusion (interim)**

> FULL EDGE · 2.5× · gates OFF · SPY+DIA Sharpe-weighted ≈ $212K · DD 15.3% ·
> Sharpe 1.35 · MAR 2.0. Superseded by #21 below.

---

## #21 — Gate sweep on SPY+DIA · 2.5× (finding the $220K config)

**Question tested:** is *weak* gating (one gate, not zero) better than full-edge
no-gates? Sweep all four gate combinations, SPY+DIA Sharpe-weighted, 2.5× leverage,
pyr 10. `python -c` inline harness.

| Gate config | Final $ | CAGR | DD | Sharpe | MAR | SPY PF | DIA PF |
|---|---|---|---|---|---|---|---|
| No gates (full edge, #20a) | $212,217 | 30.5% | 15.3% | 1.35 | 2.00 | 3.02 | 2.94 |
| Weak: VWAP off, MOM on | $217,018 | 31.5% | 14.3% | 1.40 | 2.20 | 3.43 | 2.68 |
| **Weak: VWAP ON, MOM off** | **$221,244** | **32.4%** | **12.9%** | **1.42** | **2.51** | 2.79 | 3.51 |
| Strict: both on (MIDDLE) | $215,537 | 31.2% | 12.8% | 1.39 | 2.43 | 2.86 | 3.31 |

**Result — the winner: VWAP gate ON, momentum gate OFF.**

* Hits the $220K target ($221,244) **and** posts the best risk metrics of the entire
  session: DD 12.9%, Sharpe 1.42, MAR 2.51.
* **Why it beats no-gates:** the VWAP confirmation rejects pyramid adds made into
  weak/below-average price action — these are the adds that fatten drawdown without
  fattening the right tail. Removing them cuts DD 15.3% → 12.9% with *no* loss of
  return (return actually rises, $212K → $221K, because capital freed from bad adds
  recycles into good ones).
* **Why it beats strict (MIDDLE):** the *momentum* gate was the one truly clipping
  edge — it rejects the decel→re-accel bottom-tick adds that catch the fattest
  pyramids. Dropping it lifts return $215K → $221K at essentially the same DD.
* **Diagnosis confirmed:** of the two institutional gates, **VWAP is protective
  (keep it), the positive-momentum gate is a tax (drop it).** This refines the
  earlier "all gates cost nothing under ATR thresholds" finding (#10) — it's
  asymmetric: VWAP earns its keep, momentum does not.

**SHIPPED PRODUCTION CONFIG (locked into `config/settings.py`)**

> Engine: deterministic pullback + trend-carry sleeve, ATR-normalized thresholds.
> `base_size_pct=0.75` · `capital_cap_pct=2.50` · `max_pyramid_positions=10`
> · `pyramid_require_above_vwap=True` · `pyramid_require_positive_momentum=False`
> · TRENDCARRY base 0.30 / cap 1.25 · `DATA.symbols=["SPY","DIA"]`.
> **SPY+DIA Sharpe-weighted: $221,244 on $100K · CAGR 32.4% · DD 12.9% ·
> Sharpe 1.42 · MAR 2.51.** Scale-invariant → same %-engine runs the live $700
> token (single-leg = SPY profile: $225,202 / DD ~ SPY-only).

---

## #22 — Reconciliation: re-bind HMM meta-layer (#6/#7) + IWM meanrev (#8) on top of #21

**Hypothesis under test (from engineering brief):** the answer to #21's residual
DD is already in the repo — repurposing the HMM as a sizing/pyramid controller
(never as an entry gate) plus adding the validated near-zero-correlation
mean_reversion_extremes stream on IWM should compress DD below 12.9% with no
material CAGR loss, and post a daily-convention Sharpe ≥ 0.60.

**Code changes shipped this round**

| File | Change |
|---|---|
| `backtest/metrics.py` | New `sharpe_intraday_bar` (`sqrt(252*7)`) and `sharpe_daily` (daily-resampled, `sqrt(252)`). `summarize()` now emits both alongside the legacy `sharpe`. Locks the convention for every future run. |
| `config/settings.py` | PULLBACK: HMM keys promoted from deprecated to active. New `use_hmm_meta` (default **False** = #21) and `hmm_warmup_pass_through` (True). Values from #7: `size_mult_high=2.0`, `size_mult_low=0.5`, `size_threshold_high=0.70`, `size_threshold_low=0.30`, `disagreement_p_bull_threshold=0.30`. MEANREV: same `use_hmm_meta` toggle (default True). |
| `strategies/pullback.py` | When `use_hmm_meta`, multiplies `pullback_SizeMult` by an HMM bucket multiplier (low / normal / high) and zeroes the pyramid cap on regime-disagreement bars. NaN P_bull during HMM warmup → pass-through 1.0×. Adds diagnostic cols `pullback_HmmBucket`, `pullback_HmmSizeMult`, `pullback_HmmDisagree`. Never blocks entries. |
| `strategies/mean_reversion_extremes.py` | Honours `MEANREV.use_hmm_meta=False` for the apples-to-apples C/D comparison. |
| `execution/portfolio.py` | Docstring note: per-symbol strategy assignment already supported via the per-call `strategies` argument; harness exploits this for SPY/DIA-pullback + IWM-meanrev. |

**Sweep results** (SPY/DIA/IWM 1h, ~147.7 wks, Sharpe-intraday weighted book, $100K start)

| # | Config | Final $ | CAGR | DD | Sharpe_intraday | Sharpe_daily | MAR | ρ (pullback vs meanrev) |
|---|---|---|---|---|---|---|---|---|
| A | #21 baseline (HMM OFF, SPY+DIA) | **$221,244** | **32.4%** | 12.92% | 1.419 | 1.448 | **2.51** | n/a |
| B | #21 + HMM meta (SPY+DIA) | $193,008 | 26.2% | 14.32% | 1.309 | 1.339 | 1.83 | n/a |
| C | #21 + IWM meanrev, no HMM | $219,017 | 31.8% | **12.75%** | 1.417 | 1.445 | 2.50 | **0.244** |
| D | #21 + HMM meta + IWM meanrev | $191,089 | 25.6% | 14.10% | 1.306 | 1.336 | 1.82 | 0.158 |

**Success-criteria check (config D)**

| Criterion | Threshold | Measured | Verdict |
|---|---|---|---|
| Final equity | ≥ $215K | $191,089 | **FAIL** |
| Max DD | ≤ 11.0% | 14.10% | **FAIL** |
| Sharpe (daily) | ≥ 0.60 | 1.336 | PASS |
| ρ (pullback vs meanrev) | ≤ 0.15 | 0.158 | **FAIL** |

**Falsification — the headline hypothesis is wrong on the #21 engine.**

### Why HMM meta-layer FAILS on the levered ATR-normalized engine (B vs A)

Bucket distribution on SPY pullback signal bars (n=447, config B):
- **38% low (0.5×)** · 27% normal (1.0×) · **35% high (2.0×)**

Expected from #6 reference (n≈350): ~45% low · ~39% normal · ~16% high.

The diagnostic the brief asked to print **proves the mechanism shifted**:
* The #6 reference engine generated ~350 signal bars on the **fixed-threshold**
  pullback engine. The #21 engine generates **447** signal bars on the same
  window — ATR-normalized thresholds + the +15% runner TP + the weak-gate
  pyramid policy produce a different signal-bar distribution. The HMM P_bull
  distribution conditional on *these* signal bars is bull-skewed (35% high vs
  the expected 16%) and only modestly bear-skewed (38% low vs expected 45%).
* In #6's window the 0.5× bucket landed on bars that overlapped with overstay
  risk — derating them helped. On the #21 engine those same low-P_bull bars
  are the **deep-dip pullback signals the strategy is explicitly designed to
  catch**; ATR-normalized thresholds make the entry conditions fire deeper
  into the pullback than the fixed-threshold engine ever did, so the bars
  where HMM disagrees are exactly the bars where the deterministic edge is
  strongest. Cutting size to 0.5× there throws away the right tail.
* The 2.0× bucket (35% of signal bars vs 16% expected) doubles size on
  already-confirmed bull bars — under 2.5× leverage this concentrates risk
  rather than diversifies it. DD ↑ instead of ↓.
* Net: PnL falls $28,236 (-12.8%), DD rises 1.4 pp, MAR collapses 2.51 → 1.83.
  Every dimension worse. The HMM bucket boundaries (0.30/0.70) and multipliers
  (0.5/2.0) were calibrated on a different signal generator. Without
  recalibration on #21 signal bars the layer is net negative.

### Why IWM meanrev FAILS as a diversifier (C vs A)

* IWM meanrev PF on this window = **0.86** (losing money standalone). #8
  measured PF 1.44 on a different window with a different signal generator
  underneath.
* Inter-strategy ρ = **0.244**, well above the 0.15 success threshold. The
  pullback-on-SPY/DIA and meanrev-on-IWM streams co-move more than #8's 0.030
  measurement on a SPY-only book.
* IWM weight in the Sharpe-weighted book is **1.8%** because IWM's standalone
  Sharpe is near zero. Even if it were uncorrelated, allocation is too small
  to meaningfully shift the book.
* DD did improve marginally (12.92% → 12.75%, a 0.17 pp tail-event smoothing)
  but is nowhere near the 11.0% threshold. CAGR drops $2K.

### Why the stack (D) is strictly worse than A

D = B's HMM damage compounded by C's negative-PF IWM allocation. $191,089 is
*lower* than B alone ($193K) because IWM's 0.72 PF on the HMM-suppressed
window bleeds capital while the HMM derates the SPY/DIA winners.

### Two corrections to the brief's stated premise

1. **The brief claimed #21's headline Sharpe 1.42 is intraday-bar and that
   "the comparable number under the daily-equity convention is ~0.55"** — i.e.
   that diversification-as-free-lunch wasn't showing up apples-to-apples.
   The measured daily-equity Sharpe of #21 in this harness is **1.448** —
   essentially identical to the intraday-bar Sharpe (1.419). The naive
   `/sqrt(7)` rescaling assumes iid bar returns; SPY 1h returns have enough
   serial correlation (trending behaviour) that daily aggregation does not
   shrink Sharpe by the iid factor. The "Sharpe isn't really 1.42" premise
   is false — both conventions give a number above 1.3 for #21.
2. **The "$220K with leverage = buying return with variance" framing from
   #16/#17** still holds for *single-symbol* leverage stacking, but the
   #21 SPY+DIA book reaches the same equity with MAR 2.51 and intraday
   Sharpe 1.42 — the diversification is real, not optical. The brief's
   regime-overstay-via-leverage concern is real in principle but is not
   diagnosed by the available metrics — DD on the in-sample window is
   12.9%, lower than any #11–#17 single-symbol configuration above 1.0×.

### Sharpe backfill (Task 4 housekeeping)

The legacy `summarize.sharpe` field for #11–#17 was `sqrt(252) × mean / std`
applied to **1h-bar** returns — neither pure-daily nor pure-intraday-bar.
Approximate conversion to the new conventions: multiply by **sqrt(7) ≈ 2.645**
to recover `sharpe_intraday_bar`. Example: #15's reported 0.54 → 1.43
intraday-bar, which is in the same neighbourhood as #21's measured 1.42. The
brief's implied gap "0.54 vs 1.42" was a unit artefact, not a structural
finding. #18–#21's `sharpe_daily` is approximately equal to their reported
intraday-bar Sharpe (within ~5%) because of the serial correlation noted
above. **From this point onward every row in this log reports both
explicit conventions side-by-side.**

### Decision

* **Production config remains #21 (config A).** Falsification logged in full.
  `PULLBACK.use_hmm_meta` stays **False** by default. `MEANREV.use_hmm_meta`
  remains True (its #8 default) but unused, since MEANREV is not in the
  shipped strategy roster. `DATA.symbols = ["SPY","DIA"]`. No production
  config rollback needed beyond confirming `use_hmm_meta=False`.
* The HMM hooks (`use_hmm_meta`, bucket diagnostic columns) are kept in the
  codebase — they cost nothing when off and a future study can recalibrate
  `size_threshold_*` / `size_mult_*` on the #21 signal-bar distribution.
* IWM meanrev module stays in the tree, dormant. Re-test conditional on
  recalibrating the `deviation_threshold` to IWM's ATR/Close regime
  (currently using SPY's −0.012 fixed threshold — exactly the
  fixed-vs-ATR-normalized portability bug fixed for pullback in #P).

### Open queue (re-prioritised from this round)

1. **Recalibrate HMM bucket boundaries on the #21 signal-bar distribution**
   before retrying the meta-layer. Specifically lower `size_threshold_high`
   below 0.70 so the 2.0× bucket is < 25% of signal bars (matching #6's
   density), and consider per-symbol thresholds — DIA's P_bull distribution
   is different from SPY's.
2. **ATR-normalize meanrev's `deviation_threshold`** the same way pullback
   was normalized (P1). Currently −0.012 was tuned on SPY's daily vol; IWM
   needs ~−0.020 to fire at structurally comparable extremes.
3. **Lower-correlation alternative streams**: session-anchored opening-hour
   breakout / VWAP-fade — both queued from earlier sessions, both expected
   to have lower ρ to pullback than meanrev does on this window.

---

## #23 — HMM threshold recalibration on the #21 signal-bar distribution

**Question tested (from #22 open queue #1):** the #6-calibrated HMM thresholds
(0.30 / 0.70) produced a bull-skewed bucket distribution on the #21 engine
(38% low / 27% normal / 35% high vs #6's ~45/39/16). Can we restore the #6
density by picking new thresholds at the 45th and 84th P_bull percentiles of
the #21 signal-bar distribution, and would that rescue the HMM meta-layer?

**Harness:** `_research_hmm_recal.py`. Same SPY+DIA Sharpe-weighted book.

### Step 1 — P_bull distribution on #21 pullback signal bars (n=741)

| Quantile | P_bull |
|---|---|
| q05 | 0.000 |
| q25 | **0.000** |
| q45 | 0.130 |
| q50 | 0.273 |
| q55 | 0.435 |
| q75 | 0.928 |
| q84 | **1.000** |
| q95 | 1.000 |

**The distribution is bimodal**: ~50% of signal bars sit near P_bull = 0,
~30% near P_bull = 1, and only ~20% of bars carry intermediate posterior
probability. The HMM is effectively making binary regime calls on the bars
the deterministic engine selects for entry — it is almost never uncertain.

Bucket densities at the default 0.30/0.70 thresholds on this distribution:
**50% low / 20% normal / 30% high** (not the 38/27/35 reported in #22; #22's
n=447 was SPY-only — combining SPY+DIA gives n=741 and the cleaner picture).

### Step 2 — Proposed recalibrated thresholds

Targeting #6's reference density (45% low / 39% normal / 16% high):
- `size_threshold_low` = q45 = **0.130**
- `size_threshold_high` = q84 = **1.000**

At these boundaries the buckets land at 45% / 39% / 16% — by construction
matching #6's density.

### Step 3 — Backtest with recalibrated thresholds

| Config | Final $ | CAGR | DD | Sharpe_daily | MAR |
|---|---|---|---|---|---|
| **A** #21 baseline (HMM OFF) | **$221,244** | **32.4%** | **12.92%** | **1.448** | **2.51** |
| B′ recal both (low=0.13, high=1.00) | $190,749 | 25.7% | 13.54% | 1.376 | 1.89 |
| B″ recal high only (high=1.00) | $190,749 | 25.7% | 13.54% | 1.376 | 1.89 |
| B‴ recal low only (low=0.13) | $193,004 | 26.2% | 14.31% | 1.338 | 1.83 |

**Falsification confirmed — recalibration cannot rescue the HMM meta-layer
on the #21 engine.** Every recalibrated variant underperforms A on every
metric. B′ and B″ are identical (matching to the dollar) because between
the old `high=0.70` and the new `high=1.00` lies essentially no mass beyond
P_bull = 0.928 — the 14% of bars that moved from the 2.0× bucket down to
1.0× were the highly-confident-bull bars where leverage was paying off, so
demoting them cuts return one-for-one without helping DD.

### Mechanism — why threshold choice is not the issue

The HMM's bimodal posterior on signal bars means **every signal bar gets
either 0.5× or 2.0× under any reasonable threshold pair**. The "normal"
1.0× zone is structurally empty. So the meta-layer is not a smooth
controller — it is a coin flip between extremes, applied on top of an
already-levered engine. Two consequences:

1. **The 0.5× side clips real edge.** On the #21 engine, P_bull ≈ 0
   signal bars overlap with deep-dip ATR-normalized pullbacks — the
   exact bars the engine is designed to catch. The HMM (a return-variance
   model) calls them bearish; the deterministic engine knows they are
   reversal setups. Derating size there throws away the right tail.
2. **The 2.0× side concentrates risk under leverage.** Doubling size on
   confirmed-bull bars while already at 2.5× leverage stacks correlated
   exposure exactly when the book is already aligned with the regime.
   DD rises rather than falls.

The brief's expected mechanism ("0.5× bucket lands on overstay-risk bars
under leverage; DD compression should come from there") **assumes a
distribution shape the HMM does not produce on this engine**. The
recalibration verifies the diagnosis the brief itself proposed: "the HMM
P_bull distribution looks different on the trend-carry sleeve /
ATR-normalized engine than it did on the legacy fixed-threshold engine."
Different in shape (bimodal vs smoother), not just in location.

### Decision

* **No threshold change shipped.** `PULLBACK.size_threshold_low=0.30`,
  `PULLBACK.size_threshold_high=0.70`, `use_hmm_meta=False` — all restored
  to the #22 defaults.
* Production config remains #21 (config A, $221K / 12.9% DD / MAR 2.51).
* The bimodal-posterior finding is the lesson: any future HMM-on-#21 study
  needs to either (a) re-train the HMM on features other than log-return +
  vol_ratio so the posterior on signal bars is not collapsed to {0, 1}, or
  (b) replace the bucketed step function with a continuous mapping
  (e.g. `size_mult = clip(0.5 + 1.5 × P_bull, 0.5, 2.0)`), which on a
  bimodal distribution would behave nearly identically to the current
  buckets anyway. Verdict: **the HMM-as-sizing-controller pattern is the
  wrong fit for the ATR-normalized engine.** Move on to alternative
  diversification streams (queue items #2 and #3 in #22).
