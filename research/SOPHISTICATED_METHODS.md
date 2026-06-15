# Sophisticated trading methods + the maths — research for Quant IA

Market research on more advanced quant techniques, the specific mathematics
behind each, and which ones are worth implementing for *our* system given
what the HMM paper-trade postmortem just revealed.

Grounding fact (2026-06-13 paper book): **7 open positions, all SHORT,
across GLD/SLV/SPY/^NDX.** Correlation: GLD-SLV = 0.79, SPY-^NDX = 0.95.
Effective independent bets ≈ 2, not 7. Diversification ratio 0.77. This is
a portfolio-construction problem, and it points directly at which maths
deliver the most value.

---

## TIER 1 — Highest value for us right now (portfolio-level risk maths)

### 1.1 Volatility targeting (position sizing)

**The problem it solves**: our `base_size_pct` is fixed at 30%. That means
we take the same dollar risk on a calm GLD bar as on a wild GC=F bar. Wrong.

**The maths**:
```
position_size = (target_vol / realized_vol) × base_size
```
where `realized_vol` is the trailing N-bar annualized stdev of returns and
`target_vol` is your chosen annual risk budget (e.g. 15%).

Refinement (what AQR/Man AHL actually run): EWMA vol so it adapts faster:
```
σ²_t = λ·σ²_{t-1} + (1-λ)·r²_t        (λ ≈ 0.94 for daily, ~0.97 for hourly)
size_t = target_vol / (σ_t·√periods_per_year)
```

**Effort**: low. ~30 LOC. We already compute ATR; this is the principled
version. **Expected lift**: smoother equity curve, lower DD, better
risk-adjusted return. This is the single cheapest upgrade.

---

### 1.2 Kelly criterion (optimal bet sizing)

**The problem**: how much to bet given an edge? Too little = leave money on
table; too much = ruin. We currently guess (30%).

**The maths** (discrete, win/loss):
```
f* = (p·b − q) / b
```
p = win prob, q = 1−p, b = win/loss payoff ratio.

For continuous returns (more appropriate for us):
```
f* = μ / σ²
```
μ = mean return, σ² = variance. **Always use FRACTIONAL Kelly** (¼ to ½ f*)
— full Kelly has ~50% drawdowns and is too sensitive to μ estimation error.

**Our data**: realized WR ≈ 71%, payoff ratio from the paper book
(avg win ≈ $745 / avg loss ≈ $740 ≈ 1.0):
```
f* = (0.71·1.0 − 0.29) / 1.0 = 0.42  → quarter-Kelly ≈ 10% per position
```
Interesting — that's LOWER than our current 30%. Kelly says we may be
over-betting per position (which is exactly why 7 correlated shorts is
scary).

**Effort**: low. **Caveat**: Kelly assumes independent bets — which ours
are NOT (see correlation). So Kelly per-position must be discounted by the
correlation, which leads to…

---

### 1.3 Risk parity / correlation-aware sizing

**The problem (THE big one for us)**: 7 positions but 2 real bets. We should
size so each *independent risk source* contributes equally, not each ticker.

**The maths** — equal risk contribution (ERC):
```
RC_i = w_i · (Σw)_i / (wᵀΣw)        want RC_i = 1/N for all i
```
Σ = covariance matrix. Solved by convex optimization (cvxpy) or a simple
iterative algorithm:
```
w_i ← w_i · (target_RC / RC_i),  renormalize, repeat to convergence
```

**Naive risk parity (good enough to start)**: weight inversely to vol:
```
w_i = (1/σ_i) / Σ(1/σ_j)
```
then halve the weight of any position whose correlation to an existing
position exceeds 0.7 (would auto-catch GLD/SLV and SPY/^NDX).

**Effort**: medium. **Expected lift**: this is the direct fix for the
concentration the paper book exposed. Highest *structural* value.

---

### 1.4 Hierarchical Risk Parity (HRP) — López de Prado, 2016

**The problem**: classic mean-variance (Markowitz) needs to invert the
covariance matrix, which is numerically unstable with few/correlated assets
(exactly our case). HRP avoids inversion entirely.

**The maths** (3 steps):
1. **Tree clustering** — hierarchically cluster assets by correlation
   distance `d_ij = √(½(1−ρ_ij))`.
2. **Quasi-diagonalization** — reorder covariance matrix so similar assets
   are adjacent (GLD/SLV cluster, SPY/^NDX cluster).
3. **Recursive bisection** — split allocation top-down, inversely to
   cluster variance.

**Why it fits us**: with 4-6 correlated symbols it produces far more stable
weights than Markowitz, and it would automatically treat "metals" and
"equities" as the two real bets. This is the institutional answer to our
exact problem.

**Effort**: medium (the algorithm is ~80 LOC; `scipy.cluster.hierarchy`
does the clustering). **Reference**: López de Prado, *Advances in Financial
Machine Learning*, Ch. 16.

---

## TIER 2 — Fixes our failed ML attempt (signal-quality maths)

### 2.1 Meta-labeling (López de Prado) — the right way to do the loser-classifier

**Why our Part 8.30 LightGBM failed**: we trained on raw "will this lose?"
with naive labels and filtered on raw probability. AUC OOS was 0.556
(random). Meta-labeling is the correct architecture:

- **Primary model** (our pullback engine) decides DIRECTION (long/short).
- **Secondary model** (ML) decides SIZE / whether-to-act — it only ever
  says "take it / skip it", never the direction. This is a much easier,
  more learnable problem and it preserves the primary model's recall.

**The maths — triple-barrier labeling** (replaces our naive pnl<0 label):
For each entry, set three barriers:
```
upper = entry·(1 + tp·σ)      profit-take
lower = entry·(1 − sl·σ)      stop
vert  = entry_time + max_hold  time barrier
label = +1 if upper hit first, 0 if lower/vertical first
```
Then the secondary model predicts P(label=1 | features). Bet size:
```
size = base · P(meta=1)        (or a step function on the probability)
```

**Sample-uniqueness weighting** (critical, we skipped it): overlapping
trades share information and inflate effective sample size. Weight each
sample by the inverse of how many concurrent trades overlap it:
```
w_i = 1 / (avg concurrent labels over trade i's life)
```

**Effort**: medium-high. **Why retry**: our classifier didn't fail because
ML can't help — it failed because we used the wrong labeling + no sample
weighting + filtered the wrong thing. Meta-labeling is the documented fix.

---

### 2.2 Fractional differentiation (stationary-but-memory features)

**The problem**: price is non-stationary (bad for ML), but returns throw
away all memory (level information). Fractional differencing keeps maximum
memory while passing a stationarity test.

**The maths**:
```
(1−B)^d X_t,  with 0 < d < 1  (B = lag operator)
```
expanded as a weighted sum of past values with binomial weights
`w_k = −w_{k-1}·(d−k+1)/k`. Pick the smallest d that makes the series pass
ADF stationarity. Typical d ≈ 0.3–0.5.

**Effort**: low-medium. Feeds better features into 2.1. **Reference**:
AFML Ch. 5.

---

## TIER 3 — Upgrades to our regime engine (state-detection maths)

### 3.1 Hidden Semi-Markov Models (HSMM)

**The limit of our HMM**: a plain HMM assumes regime duration is
geometric (memoryless) — P(stay) is constant each bar. Real regimes have
*characteristic durations* (a bull market doesn't have constant per-bar
death probability).

**The maths**: HSMM adds an explicit duration distribution `d_j(u)` per
state (e.g. negative-binomial). The state survives a sampled duration, then
transitions. Inference via the modified forward-backward (Guédon's
algorithm).

**Effort**: high. **Expected lift**: more stable regimes, fewer spurious
flips — directly attacks the "54% of losers had a regime flip" finding
(Part 8.8). But heavy; consider 3.2 first.

### 3.2 Statistical Jump Models (Nystrup et al., 2020) — cheaper, more stable

**The pitch**: a modern alternative to HMM that adds an explicit *jump
penalty* λ to discourage frequent regime switches. Often MORE stable than
HMM + Kalman and cheaper to fit.

**The maths** — minimize:
```
Σ_t ‖x_t − μ_{s_t}‖²  +  λ·Σ_t 𝟙[s_t ≠ s_{t−1}]
```
First term = fit data to regime centroids; second = penalty per jump.
Solved by coordinate descent (k-means-like + dynamic programming for the
state path). λ is the single knob controlling regime persistence.

**Effort**: medium. **Why attractive**: it's basically "k-means with a
stickiness penalty", far simpler than HSMM, and the λ knob is exactly the
control we hand-rolled with Kalman smoothing — but principled. **Strong
candidate to A/B against our current Kalman-HMM.**

### 3.3 Bayesian Online Changepoint Detection (BOCPD)

**The maths**: maintain a probability distribution over "run length" (bars
since last regime change), updated each bar:
```
P(r_t | x_{1:t}) ∝ Σ P(r_t | r_{t−1})·P(x_t | r_{t−1})·P(r_{t−1} | x_{1:t−1})
```
with a hazard function H(r) for the prior changepoint rate.

**Effort**: medium. **Use**: a fast early-warning that the regime is
breaking — could trigger our regime-flip exit earlier than the HMM
confirms. Complementary to (not replacement for) the HMM.

---

## TIER 4 — New strategy families (different edge, different maths)

### 4.1 Cointegration / statistical-arbitrage pairs

**Directly relevant**: GLD and SLV correlate 0.79 and are BOTH short in our
book. Instead of two correlated directional bets, trade the *spread* — a
market-neutral mean-reversion bet with its own edge.

**The maths**:
- **Engle-Granger**: regress `GLD = α + β·SLV + ε`, test ε for
  stationarity (ADF). If stationary, they're cointegrated.
- Model the spread as **Ornstein-Uhlenbeck**:
  ```
  dS_t = θ(μ − S_t)dt + σ dW_t
  ```
  θ = mean-reversion speed, μ = equilibrium. Half-life = ln(2)/θ.
- **Entry**: z-score of spread > +2 → short spread; < −2 → long spread.
- **Johansen test** generalizes to >2 assets (could find a GLD/SLV/GC=F
  basket).

**Effort**: medium. **Why it fits**: turns our correlation *problem* into a
correlation *edge*. Market-neutral = uncorrelated with the directional book
= real diversification.

### 4.2 GARCH volatility forecasting

**The maths** — GARCH(1,1):
```
σ²_t = ω + α·r²_{t−1} + β·σ²_{t−1}
```
Forecasts next-bar variance; feeds vol-targeting (1.1), regime detection,
and option-like risk sizing. EGARCH adds asymmetry (vol rises more on down
moves — the leverage effect, very real in equities).

**Effort**: low (`arch` library, one-liner fit). **Use**: better
`realized_vol` input everywhere.

---

## What the HMM paper-trade postmortem says (2026-06-13)

```
9 closed · realized −$536 · grades: 4×A, 0×B, 0×C, 2×D, 3×N
D-grade (avoidable, HMM-disagreed) cost −$815
Without D-grade entries: +$279.  A-grade-only: +$2,979
7 open positions — ALL SHORT, mostly HMM-aligned (bear)
  ⚠ SLV pullback SHORT: HMM flipped bear→bull — watch
```

**Three concrete reads:**

1. **Grade-D avoidance is the cheapest alpha we have.** 2 trades flipped
   −$536 into +$279. The HMM *already knew* — it disagreed at entry. This
   is the meta-labeling case (2.1) in miniature: don't change direction
   logic, just add a "should I take this?" gate that respects the HMM.
   Our failed classifier was trying to do this without the right
   architecture.

2. **The book is dangerously concentrated.** 7 shorts, ~2 independent bets,
   diversification ratio 0.77. If metals OR equities rally, half the book
   loses together. Tier-1 maths (risk parity / HRP) is the direct fix.
   Right now we have no portfolio-level risk control — each signal sizes
   in isolation.

3. **Regime flips are our main leak (Part 8.8: 54% of losers flipped).**
   Jump models (3.2) or BOCPD (3.3) attack this at the source. The SLV
   open-position flag is a live example — HMM flipped to bull while we're
   short.

---

## Recommended implementation order (by value ÷ effort)

| # | Method | Tier | Effort | Why first |
|---|--------|------|--------|-----------|
| 1 | Vol targeting (1.1) | 1 | low | Cheapest risk-adjusted-return win |
| 2 | Correlation-aware sizing / naive risk parity (1.3) | 1 | low-med | Direct fix for the 7-short concentration |
| 3 | Fractional-Kelly cap (1.2) | 1 | low | Caps per-position over-betting (says 10% not 30%) |
| 4 | GLD/SLV cointegration sleeve (4.1) | 4 | med | Turns correlation problem into market-neutral edge |
| 5 | Statistical Jump Model A/B vs HMM (3.2) | 3 | med | Principled regime stickiness, attacks flip-leak |
| 6 | Meta-labeling redo of the classifier (2.1) | 2 | med-high | The RIGHT way to do Grade-D avoidance |
| 7 | HRP portfolio overlay (1.4) | 1 | med | Institutional concentration fix |

**Gate-first discipline still applies**: each is built, backtested on the
MT5 tickers, MC-validated, and only shipped if it clears. Same as the
engine attempts in Parts 8.27–8.31.

---

## Libraries / references

- `arch` — GARCH/EGARCH (pip install arch)
- `cvxpy` — convex optimization for risk parity
- `scipy.cluster.hierarchy` — HRP tree clustering
- `statsmodels.tsa.stattools.coint` / `adfuller` — cointegration tests
- `jumpmodels` (Nystrup et al.) or hand-roll the coordinate descent
- López de Prado, *Advances in Financial Machine Learning* — meta-labeling
  (Ch.3), sample weighting (Ch.4), frac-diff (Ch.5), HRP (Ch.16)
- Nystrup, Lindström, Madsen (2020), "Learning hidden Markov models with
  persistent states by penalizing jumps"
