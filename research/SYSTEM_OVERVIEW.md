# Quant IA — complete system structure, strategy & roadmap

Master reference (Part 8.45, 2026-06-19). Synthesises the architecture, the
trading logic, what's validated, what failed, and the tweaks to perfect it.

---

## PART 1 — SYSTEM STRUCTURE (architecture)

### 1.1 The proxy-signal architecture (the core design)
Signals are computed on **clean, liquid NYSE-hours data** (ETFs / cash indices)
and **executed on MT5 CFDs** that track the same underlying within basis points.

```
yfinance signal symbol   →  MT5 execution instrument   (TRADING_LABEL_MAP)
  SPY   (S&P 500 ETF)     →  US500
  ^NDX  (Nasdaq-100 cash) →  US100
  GLD   (gold ETF, spot)  →  XAUUSD
  GC=F  (gold futures)    →  XAUUSD  (23/5 cross-confirm)
```
Why: the engine was tuned on clean bar structure; CFDs add overnight/weekend
gaps + spread the backtest doesn't model. Computing on the clean series and
relabeling for execution keeps signal quality high. (SLV being dropped — it
has no MT5 instrument.)

### 1.2 Layered pipeline
```
DATA          core/data_loader.py        yfinance hourly bars, cache
   │
FEATURES      core/indicators.py         EMA50 / SMA130 / ATR / RSI / momentum / VWAP / RVOL
              core/hmm_regime.py         3-state Gaussian HMM (growth/slowdown/crash...)
              core/kalman.py             Kalman smoothing of HMM posterior (P_bull_kalman)
              core/regime_score.py       composite regime score
              core/vix.py / vol_targeting.py   vol context (informational)
   │  (assembled by main_portfolio.prepare_dual)
   │
STRATEGY      strategies/pullback.py     primary sleeve — pullback-to-trend continuation
              strategies/trend_carry.py  runner sleeve — rides extended trends
              strategies/pullback_vwap.py  challenger (VWAP entry, GC=F)
   │  → emits  {name}_Signal / _SizeMult / _PyramidOK / _PyramidCap
   │
EXECUTION     execution/portfolio.py     run_portfolio() — shared-capital book,
              strategies/exit_profile.py   exit ladder, pyramiding, capital cap
   │
LIVE          worker.py                  cron: build_state → signals per symbol
              core/paper_trader.py       paper fills + open/close management
              core/notifier.py           Discord signal cards (MT5 labels)
              cron_trigger/ + Cloudflare worker   5-min schedule
   │
VALIDATION    _montecarlo_final.py       10k-path bootstrap, leverage sweep, P(ruin)
              _walk_forward_analysis.py  chronological out-of-sample chunks
              research/edge_lab.py       per-bar edge mining (46 edges)
   │
SURFACES      dashboard.py (Streamlit)   live stats + equity
              research/*.pine            TradingView overlays
```

### 1.3 Capital model
One **$100k pooled account**. Each sleeve sizes ~30% per position
(`base_size_pct`), reuses capital as positions close, can pyramid up to a
`capital_cap_pct` ceiling. Pooling across 4 symbols + capital velocity is what
turns modest single-asset edges into the combined return (Part 8.44: +248% vs
+90% buy-hold).

---

## PART 2 — THE STRATEGY (trading logic)

### 2.1 Universe (post-decisions)
SPY (US500), ^NDX (US100), GLD (XAUUSD), GC=F (XAUUSD cross). SLV dropped.

### 2.2 Regime detection
- **3-state Gaussian HMM** fit on return distributions → posteriors
  P_bull / P_bear / P_range (and named regimes growth/slowdown/crash/
  stabilization/distribution).
- **Kalman smoothing** (q=1e-4, r=1e-2) → `P_bull_kalman`, the load-bearing
  regime signal (validated as the #1 feature in the ML attempt, Part 8.30).
- Used for: sizing context, pyramid disagreement brake, and the GC=F
  regime-flip exit. **Does NOT hard-gate entries in baseline** (the gates we
  added change that — see 2.6).

### 2.3 Sleeve 1 — pullback engine (primary)
Long entry — all required:
- Bullish structure: EMA50 > SMA130
- Pullback proximity: |Close − EMA| ≤ band (ATR-normalized)
- Imbalance: (EMA − SMA)/SMA ≥ min
- Momentum re-acceleration: Δmomentum > 0
- **Rollover guard**: EMA50 3-bar slope > 0 (kills longs into a rolling-over
  trend — added after a −$80k two-bar loss in backtest)

Short entry: symmetric mirror (bearish structure + slope down).

### 2.4 Sleeve 2 — trend_carry (runner)
Rides established trends for longer holds (wider stop, bigger TP2). Gated by
regime conviction. **Weakness: currently has no RSI/HMM/rollover brake** — it
produced the single worst paper trade (SLV −$1,194, shorted RSI 26 into
P_bull 1.00).

### 2.5 Sizing chain
```
base_size_pct (0.30)
  × RSI multiplier        (1.3× oversold, 0.7× overbought)
  × HMM meta bucket       (informational scaling, P_bull buckets)
  × vol-target / VIX      (toggleable, currently off)
capped at capital_cap_pct, pyramiding up to max_pyramid_positions (VWAP-confirmed)
```

### 2.6 Exit ladder
```
stop      −2.5%   (ATR-aware override available)
TP1       +4%     close 50%, move stop → break-even
TP2       +15%    close remainder
time stop 390 bars (~16 days on 1h)
```
Per-symbol overrides: **GLD uses TP1 +5% / TP2 +20%** (gold trends run
longer). **Regime-flip exit** (close on HMM bear→flip) is **gated to GC=F
only**.

### 2.7 The validated gates (this session, Parts 8.41–8.42)
- **RSI gate (global)**: block shorts at RSI < 40, longs at RSI > 60. Keeps
  96% of trades, lifts PF/DD. Catches "shorting oversold" failures.
- **HMM-posterior veto (GC=F only)**: block long if P_bear > 0.6, short if
  P_bull > 0.6. +$16.8k on GC=F; net-negative if applied blanket (removes good
  trades on strong symbols).

### 2.8 Realized performance (gated, common window, after 10bp friction)
- Per asset: SPY +52% (DD −8%), ^NDX +46% (−6%), GLD +138% (−6%), GC=F +35% (−15%)
- **Pooled book: +248% total / +68% CAGR / −9.7% DD** vs buy-hold basket
  +90% / −15.6%.
- MC: ~80% CAGR net at ~1.4× leverage, **P(ruin) 0.00%** to 2.5×.

---

## PART 3 — POTENTIAL IMPLEMENTATION & TWEAKS

### 3.1 Validated & ready to ship (do these)
| Change | Effect | Status |
|---|---|---|
| Drop SLV from `DATA.symbols` | removes un-executable, loss-making symbol | decided, not coded |
| RSI gate in both sleeves (global) | +quality, 96% trade retention | backtested |
| HMM veto as GC=F-only override | +$16.8k on the weak symbol | backtested |
| GLD per-symbol wider exits | already live (+$18k) | shipped |
| Default leverage knob ~1.4× | targets ~80% CAGR net, 0% ruin | MC-validated |

### 3.2 Open tweaks worth testing (not yet validated)
| Idea | Rationale | Risk |
|---|---|---|
| **Port guards to trend_carry** | it caused the worst losses (no RSI/HMM brake) | low — clear hole |
| **Regime suppression** (stand down in `stabilization`/high `P_range`) | whipsaw + oversold-short losses fired in transitional regimes | medium |
| **Post-stop cooldown** (no re-entry N bars after a stop) | kills the SPY long→short whipsaw (−$1.5k) | low |
| **Correlation cap** (limit simultaneous same-direction across correlated symbols) | 4-long / 3-short clusters mass-stopped (Part 8.32) | medium |
| **GC=F purpose-built variant** | it's the chronic weak symbol responding to every patch (VWAP, HMM) | higher effort |
| **Friction reduction** (fewer, higher-conviction trades) | friction costs ~9pp CAGR; less needed leverage | low |
| **Walk-forward monitor** (rolling 12mo) | SPY chunk-3 degraded; catch regime drift early | low |

### 3.3 Tested and REJECTED (don't waste time re-trying)
- **Conviction sizing** (HMM-scaled size) — −2.4pp CAGR, deeper DD (Part 8.43)
- **Blanket HMM veto** — −$34k, removes good trades (Part 8.41)
- **Cross-sectional / continuous TSMOM / tangency** — negative in trending
  regime (Part 8.34)
- **Donchian/Turtle, orderflow-exhaustion, vol-breakout engines** — all failed
  the MT5 gate (Parts 8.27/8.28/8.31)
- **Universal exit retune (5%/20% all symbols)** — regressed portfolio (8.29)
- **Stocks/crypto universe expansion** — 0 of 20 cleared the gate (8.29)
- **ML loser classifier (naive)** — AUC 0.556; needs meta-labeling + N>1500

### 3.4 The honest path to "perfect"
1. **Ship the validated set** (3.1) behind the MC + friction gate.
2. **Close the trend_carry hole** (3.2 first row) — biggest single risk fix.
3. **Add correlation control** — the recurring concentration failure.
4. **Decide GC=F's fate** — purpose-built variant or demote it.
5. **Leverage to taste** (~1.4× for ~80%, MC says safe to 2.5×).
6. **Paper-validate the full config** before real money; keep the walk-forward
   monitor running to catch regime drift.

### 3.5 What the system is (honest positioning)
A disciplined, retail-scale, **trend-continuation engine** on liquid
index/gold instruments, differentiated from the median retail system not by
exotic alpha but by: MC discipline, gate-first new-feature process, regime
awareness, and brain-documented research. Closest public analogs: Adam Grimes
(pullback) + Andreas Clenow (momentum). Its edge is risk-adjusted and
portfolio-level; leverage converts that into raw outperformance.
```
```
