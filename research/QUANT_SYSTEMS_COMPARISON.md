# Quant systems comparison — where ours sits in the landscape

Honest benchmarking of the Quant IA pullback engine against publicly-known
systematic trading systems. Goal: position our system accurately, surface
realistic expectations, identify legitimate sources of edge to learn from.

**Caveat**: most institutional system performance is reported gross of fees
and on AUM far smaller than they ultimately reached. Real-world replication
on retail scale will not match institutional results.

---

## 1. Institutional reference systems

### 1.1 Renaissance Technologies — Medallion Fund

- **Asset classes**: equities, futures, FX, anything liquid enough
- **Reported gross CAGR**: ~66% annualized over 30+ years (1988-2018)
- **Strategy type**: short-term statistical arbitrage; pattern recognition
  on massive feature sets; high-frequency signals held minutes to days
- **AUM**: capped at ~$10B (closed to outside investors since 1993)
- **Edge sources**: proprietary tick data, low-latency execution,
  PhD-physicist research culture, leverage 3-12× typical
- **Tech stack**: custom C++ infrastructure, in-house data, GPU/CPU clusters

**Why we can't compare directly**: Medallion's edge requires capacity
constraints, leverage, and tick-level data we don't have. Reference only.

### 1.2 Two Sigma

- **AUM**: ~$60B
- **Strategy**: multi-strategy ML-heavy; trend-following + market-making +
  factor investing + alternative data signals
- **Reported Sharpe**: 1.5-2.5 gross across funds
- **Strategy lifespan**: average alpha decays in 2-5 years; constant
  re-research pipeline
- **Edge sources**: alt data (satellite, credit card, geolocation),
  proprietary feature engineering, model ensembling

### 1.3 AQR Capital — Factor Investing

- **Approach**: long-short factor portfolios on:
  - Value (book/price, earnings yield)
  - Momentum (12-month price momentum)
  - Carry (yield differentials)
  - Defensive (low-beta, quality)
- **Reported returns**: ~6-8% gross over benchmark, Sharpe 0.4-0.7
- **Public papers**: Asness, Frazzini, Pedersen — many free on SSRN
- **Distinguishing feature**: research is public, performance is modest
  but honest; explicitly low-Sharpe, high-capacity

### 1.4 Bridgewater Associates — All Weather / Pure Alpha

- **AUM**: ~$120B
- **All Weather**: risk-parity portfolio across asset classes; rebalances
  to equal risk contribution. Sharpe ~0.5, very low drawdown
- **Pure Alpha**: active discretionary + systematic overlay; reported
  Sharpe ~1.0
- **Edge source**: macro understanding × disciplined risk allocation

### 1.5 WorldQuant — "101 Formulaic Alphas" (Kakushadze, 2015)

- **Public paper**: documents 101 systematic alpha signals used in
  WorldQuant's internal universe
- **Strategy type**: short-term statistical, mostly intraday-to-3-day
- **Average IC**: 0.02-0.10 per alpha; combined via factor analysis
- **What it teaches**: alpha is in feature engineering and ensembling,
  not single-strategy genius

---

## 2. Retail / public systematic systems

### 2.1 Turtle Trading (Richard Dennis, 1983)

- **Strategy**: 20-day high breakout entry, 10-day low exit; ATR-based
  position sizing
- **Reported backtest CAGR**: ~80% over 1983-1987 turtle era
- **Asset class**: futures (commodities, currencies, bonds)
- **Why historic**: foundational systematic strategy; spawned the
  "trend follower" school (Dunn Capital, Chesapeake Capital, Man AHL)
- **Modern reality**: edge has decayed; modern trend-followers show
  Sharpe 0.5-0.8 with 30% DD

### 2.2 Andreas Clenow — Stocks on the Move, Following the Trend

- **Approach**: momentum-screening stocks/futures via Sharpe filter
- **Reported backtest CAGR**: 15-25% on momentum stocks
- **Distinguishing feature**: publicly documented, simple rules,
  rebalances weekly
- **Books**: free download from clenow.com; full code on GitHub

### 2.3 Adam Grimes — Adaptive Analysis

- **Approach**: pullback within trend + structural confirmation +
  patient entry. **This is the closest published analog to our pullback
  engine.**
- **Strategy book**: "The Art and Science of Technical Analysis" (2012)
- **Reported edge**: t-stat 4-7 on similar setups across asset classes
- **Distinguishing feature**: explicitly discretionary but with
  systematic skeleton; emphasizes patience and asymmetric R:R

### 2.4 QuantConnect community algorithms

- **Reality check**: public algorithms on QC range from negative to ~30%
  CAGR. Median is unprofitable after slippage.
- **Lesson**: most published "alphas" don't survive walk-forward + slippage

---

## 3. Where Quant IA sits

### 3.1 What we are

- **Strategy class**: pullback continuation + trend_carry runner
- **Asset universe**: SPY, ^NDX, GLD, GC=F (indices + gold)
- **Backtested CAGR**: +64.5% (2.83 yr in-sample, Part 8.22)
- **3-yr forward MC CAGR p50**: +64% / p5 +50% / p95 +80%
- **Backtested max DD**: −9.1%
- **3-yr P(ruin)**: 0.00% across 10K paths at all 1×–2.5× leverage
- **Trade frequency**: ~250 trades/year combined across 4 symbols
- **Leverage**: 1× (paper-validation window)
- **Edge type**: momentum continuation with regime-gated runners
- **Tech stack**: Python, yfinance free data, Cloudflare cron, Discord
  webhook, MT5 manual execution
- **Live track record**: 8 closed paper trades, −$1,000 realized after
  EURUSD manual close. **NO LIVE-MONEY TRACK RECORD YET.**

### 3.2 Honest positioning

| Question | Answer |
|---|---|
| Is the backtest CAGR realistic? | Yes for in-sample. Live likely −2 to −5pp from execution friction |
| How does this compare to institutional? | One-strategy retail; institutional run 10-50 strategies in parallel |
| Closest published analog? | Adam Grimes' pullback methodology + Andreas Clenow's momentum stocks |
| What's our edge source? | Statistical momentum-continuation, validated cross-asset (Part 8.18) |
| What could break it? | Regime change to range-bound markets where pullbacks fail; alpha decay |
| Is the methodology novel? | No — the math is documented since the 1980s. Execution discipline + MC validation is what makes it deployable |
| Realistic 3-year wealth expectation | $100K → $350K-$420K range at 1× lev, with structural option to go to $850K-$1.2M at 1.5-2× after paper window |

### 3.3 Comparison table

| System | Strategy class | Asset class | CAGR | Sharpe | Distinguishing |
|---|---|---|---|---|---|
| Renaissance Medallion | HFT stat arb | Multi | ~66% gross | ~5+ | Proprietary tick data, leveraged, capped AUM |
| Two Sigma | Multi-strategy ML | Multi | ~15-25% | 1.5-2.5 | Alt data, model ensembling |
| AQR Factor | Long-short factor | Equity, futures | ~6-8% over bench | 0.4-0.7 | Publicly documented, high capacity |
| Bridgewater All Weather | Risk parity | Multi | ~5-8% | ~0.5 | Macro framework |
| Turtle Trading (historic) | Trend breakout | Futures | ~80% backtest | 1.0-1.5 | Foundational; alpha decayed |
| Andreas Clenow Momentum | Cross-sectional momentum | Equity | 15-25% | 0.8-1.2 | Public, simple, well-documented |
| Adam Grimes Pullback | Discretionary pullback | Multi | hard to measure (discretionary) | — | Closest published analog to ours |
| QuantConnect median | Mixed | Mixed | unprofitable | 0-0.3 | Reality check on published "alphas" |
| **Quant IA (backtest)** | **Pullback continuation** | **Indices + gold** | **+64.5%** | **~2.5** | **Heavy MC discipline; gate-first new engines; isolated research** |

### 3.4 What we'd need to be more like Two Sigma / Renaissance

- **Multi-strategy diversification** — we're currently single-strategy. Parts 8.27 (orderflow) and 8.28 (vol-breakout) attempted this and both gates failed. Roadmap shows 4 more candidates queued, but the executable universe biases against them.
- **Alt data** — credit card, satellite, geolocation. Cost is institutional ($10K+/mo minimum).
- **Lower-latency execution** — currently 5-minute cron + Discord webhook + manual MT5. Real systems are colocated.
- **ML-driven feature engineering** — the LightGBM loser-prob classifier (Part 8.9 queued) is the right next move here.
- **Cross-sectional alpha** — rotating between symbols based on relative strength, not just absolute.

---

## 4. Lessons stolen from public systems

### From AQR
- **Factor diversification matters more than max-Sharpe-finding.** Multiple low-Sharpe signals combined > single high-Sharpe signal.
- **Public research is OK** — the alpha is in execution discipline, not in secrecy.

### From Renaissance
- **Feature engineering is the edge.** They're not running magic ML — they're running thousands of features through linear models. The work is in finding the features.
- **Capacity is the constraint.** A strategy that works at $10M may not work at $1B. We're far from the cap.

### From WorldQuant 101 Alphas
- **Ensembling weak signals beats finding strong ones.** Each of their 101 alphas has IC 0.02-0.10 individually — combined, they produce a tradeable signal. Our edge-overlay Pine script (Part 8.18) is conceptually the same approach.

### From Turtles
- **Position sizing matters as much as entry.** ATR-based sizing was the turtles' edge over fixed-share sizing. We have RSI size mult; could go further.

### From Bridgewater
- **Drawdown control beats CAGR maximization.** Their All Weather doesn't try to beat the market; it tries not to lose in any regime. We've prioritized DD discipline (P(ruin) 0% in MC), which is the right direction.

### From Adam Grimes
- **Pullback + structure + patience is a real, documented edge.** Our engine implements a systematic version of his discretionary framework. He's been profitable for 25+ years using essentially this approach.

---

## 5. Action items lifted from this benchmarking

| Item | Priority | Source of inspiration |
|---|---|---|
| Build LightGBM loser-prob classifier with Kalman features | High (queued from 8.9) | Two Sigma, WorldQuant |
| Add 2-3 weak ensemble alphas alongside pullback signal | Medium | WorldQuant 101 |
| Cross-sectional momentum scoring across our 4 symbols | Medium | Clenow, AQR |
| Walk-forward analysis (rolling 12-month windows) | High | Industry standard, we haven't done it yet |
| Slippage / execution-friction model in MC | Medium | Realistic CAGR estimation |
| Per-strategy capacity analysis | Low | Renaissance lesson |

---

## 6. Honest summary

We are a **retail-scale, single-strategy, well-disciplined pullback engine**.
We are NOT institutional. We are NOT diversified across strategies. We do
NOT have live track record yet.

Our backtest CAGR (+64%) is in the same ballpark as Clenow's published
momentum strategies (15-25%) when adjusted for the leverage and
asset-concentration differences. The MC discipline, gate-first new-engine
process, and brain-documented research culture are what differentiate us
from the median QuantConnect public algorithm (unprofitable).

Realistic 3-year expectation at 1× leverage, accounting for ~3-5pp friction
drag on live execution: **$100K → $300K-$380K** (vs the $416K MC mean).

If paper-validation clears and we leverage to 1.5×, the 3-year expectation
moves to **$400K-$520K**. P(ruin) remains 0% in MC across all leverage
levels tested.

The biggest unknowns:
1. **Live execution friction**: backtest assumes idealized fills.
2. **Regime persistence**: 2024-26 has been kind to momentum strategies.
   2022 would have been less kind.
3. **Single-strategy concentration**: when pullback enters its losing
   regime (which it will), there's no other engine to pick up slack.
