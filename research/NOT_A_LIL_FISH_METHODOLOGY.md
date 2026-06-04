# The "not a lil fish" methodology — practitioner notes

**Status**: archetype synthesis based on broadly-observable retail-prop
methodology in the US intraday futures + tech-equity community. Direct
quotes from primary sources are NOT pulled here; the handle's content
should be referenced live for verification. What's documented below is
the framework as it manifests across the broader tape-reader cohort
that @not_a_lil_fish exemplifies on Twitter/X.

---

## 1. The thesis in one paragraph

Markets are dominated by professional flow that moves price in predictable
mechanical ways: dealer gamma hedging, market-maker stop runs, opening
auction reversion, vwap mean-reversion, trapped-trader liquidations.
Retail edge is NOT in predicting macro direction or building elaborate
indicators. It's in **waiting at the levels where institutional flow
will mechanically appear**, sitting on your hands when the tape is
ambiguous, and taking small surgical entries with high reward:risk when
the level is hit. Most days you don't trade. The days you do, you risk
0.5R for 3-5R. Volume comes from patience, not frequency.

This is the opposite of the "trade every 5-min candle" retail
approach — and the opposite of "find a strategy backtest, automate it,
walk away." It's a discretionary framework that uses systematic levels
+ orderflow confirmation as the trigger.

## 2. The five pillars

### 2.1 Levels-first

You don't trade unless price is AT a pre-marked level. Levels come from:

- **Overnight high/low (ONH/ONL)** — Globex/ETH session extremes that
  RTH cash open will react to
- **Prior day high/low/close** — magnet levels for next-day flow
- **VWAP and anchored VWAP** — institutional reversion target; intraday
  composite VWAP (from RTH open) is the dominant 1-day anchor
- **Opening range high/low (OR)** — first 15-30 min RTH range; breakout
  vs failure is the bread-and-butter setup
- **Volume profile POC + value-area edges** — high-volume nodes (HVNs)
  attract, low-volume nodes (LVNs) repel
- **GEX walls** — largest gamma strikes act as pin/breakout levels
- **Round numbers + algos' typical entries** (e.g. SPX 5000, NDX 18000)

Without one of these levels at the current price, the answer is "no
trade." This single discipline filters out 80%+ of the trades that
account for retail losses.

### 2.2 Tape reads (orderflow at the level)

Once price reaches a level, you watch for confirmation in 4 forms:

1. **Absorption** — large volume hitting the level but price doesn't go
   through. Means a wall of resting orders is defending. Trade WITH the
   defender (fade the move into the level).
2. **Exhaustion** — series of attempts to break the level fail with
   shrinking volume / range. Trade the reversal.
3. **Trapped traders** — sharp move beyond a level followed by immediate
   rejection. The traders who entered on the breakout are now offside;
   they'll cover, fueling the reversal. Trade the reversal.
4. **Stop run + reclaim** — price spikes through a level (running
   protective stops on the other side), then immediately reclaims. The
   spike was engineered to clear liquidity; trade the reclaim direction.

These are **structural patterns**, not pattern-recognition on a chart —
they're consequences of mechanical institutional behavior at known
liquidity pools.

### 2.3 Patience as edge

The single biggest behavior gap between practitioners and the cohort
is willingness to do nothing. Concrete heuristics:

- **One trade per session** is normal. Two is busy. Five is overtrading.
- If the level didn't come to you, you don't go to it.
- The 9:30-10:00 AM ET window is the highest-edge window of the day
  (opening drive + first reaction), worth waiting for.
- The 14:30-16:00 ET window (power hour + close auction) is the second
  window. Lunch lull (12:00-13:30) has the lowest edge and is generally
  skipped.

### 2.4 Reward:risk discipline

- Minimum 2:1 R:R, target 3-5:1.
- Risk per trade is fixed in $: typically 0.25-0.5% of account.
- Position sizing is computed BACKWARDS from the stop level — stop is set
  at structural invalidation (the price level + a few ticks buffer),
  size is whatever puts $risk equal to the dollar distance to stop ×
  position.
- Stops do NOT move against you. Ever. If invalidated, you're out and
  re-assess from flat.
- TP1 (50% off at 2R) → move stop to entry. TP2 (full off at 3-5R).

### 2.5 Process over outcome

- Journal every trade with: pre-trade thesis, level, confirmation
  pattern, entry, stop, target, exit, post-trade audit
- Trade quality is graded independently of P&L. A losing trade at the
  right level with the right confirmation is a Grade-A trade. A winning
  trade you chased is a Grade-D trade.
- Weekly review: count A/B/C/D grades, not P&L. Long-run profit is a
  function of the A/B ratio.

## 3. Specific setups commonly named

### 3.1 Opening Drive Failure (ODF)

- Open in one direction in first 15 min → fade in second 15 min
- Trigger: price returns to RTH open after extending 0.5-1% away
- Entry: at the open level with rejection candle
- Stop: just beyond the extreme of the drive
- Target: opposite OR boundary, then prior day mid
- Best on days with elevated overnight volume followed by a 1-direction
  open

### 3.2 Opening Range Breakout + Pullback (ORB-pullback)

- Define OR over first 15 or 30 min
- Wait for clean break of OR high or low
- DO NOT chase the initial break
- Wait for pullback into the broken OR boundary (now support/resistance)
- Entry: on hold of the prior OR boundary with tape confirmation
- Stop: inside the OR range
- Target: 1× OR range projected from breakout point

### 3.3 VWAP Reclaim

- Price trends one direction off VWAP all morning, then violates VWAP
- Don't trade the violation (often noise)
- Wait for price to reclaim VWAP from the other side with rejection
  of the now-broken VWAP from below/above
- Enter on the reclaim
- Stop just on the wrong side of VWAP
- Target opposite extreme of session

### 3.4 GEX-wall pin

- Identify the largest gamma strike for the session (free proxy:
  largest open-interest strike on SPY/SPX options near current price)
- In a long-gamma regime (VIX1D < VIX3M, contango), expect price to
  pin to the wall through 2-3 PM
- Trade fades AT the wall (sell into wall from above, buy into wall
  from below)
- Stop: 0.3-0.5% beyond wall
- Target: opposite wall or session VWAP

### 3.5 Trapped Move (Trap + Snap)

- Price breaks a key level with strong impulse → instantly reverses on
  high volume
- The reversal candle closes back inside the prior range
- Entry: on the close-back of the reversal candle
- Stop: just beyond the impulse extreme (typically very tight ~0.2%)
- Target: 3-5R, usually opposite side of recent range
- Best R:R setup in the toolkit; lowest frequency (~3-5×/month)

## 4. Why this works empirically — what the lab confirms

The Edge Lab's 44-symbol mining run validated several of the not-a-lil-fish
pillars directly:

| Pillar / setup | Lab edge | Cross-class validation |
|---|---|---|
| RSI overbought = continuation, not reversal | E4 `rsi_extreme_high_80` | 6 tech symbols, mean_t 6.06 |
| Stack: oversold IN uptrend (not just oversold) | E5 `stack_oversold_uptrend` | 9 symbols, mean_t 4.52 |
| Stack: overbought IN downtrend = bearish | E6 `stack_overbought_downtrend` | 15 symbols, mean_t 5.01 |
| Orderflow exhaustion = bounce | E1/E2 `tick_imb_negative`, `cvd_falling_strong` | 23-24 symbols, mean_t ~6.0 |
| Wide-bar close-at-extreme = institutional sweep | E7 `wide_bar_close_high` | persistent across asset classes |
| Volatility compression precedes expansion | E3 `vol_compression_then_expansion` | 25 symbols, mean_t 5.95 |
| Power-hour bias | E9 `tod_power_hour + vol_spike` | 13 symbols, mean_t 4.17 |

The lab does NOT validate (yet) the more discretionary pillars: opening
drive failure detection, level reclaim with tape, trapped moves. These
require:
- Sub-hourly bars (5-min, 1-min)
- Volume-profile per session
- Real options-chain GEX

All three are on the queue.

## 5. Translation to our system

The not-a-lil-fish methodology is fundamentally **discretionary
intraday** — the lab/strategy approach is **systematic multi-day**.
They're not in conflict; they're complementary.

What's transferable to our systematic engine:

1. **Levels-first thinking** → already partially present via pullback to
   EMA50, but could extend to overnight high/low and OR levels
2. **Patience filter** → only fire trades during high-edge windows
   (power hour, opening drive). Lab confirms power hour edge exists.
3. **R:R discipline** → already shipped (stop 2.5% / TP1 4% / TP2 15%
   on pullback engine)
4. **GEX-aware regime** → our Kalman P_bull smoothing + regime-flip
   exit are a poor man's version of this; real GEX integration is queued
5. **Wait for the tape** → our regime-flip exit DOES this in reverse —
   waits for the tape to flip before cutting. Same principle.

What's NOT directly transferable:
- Discretionary trapped-move detection (no clean systematic encoding)
- Multi-timeframe confluence checks (we trade pure 1H bars)
- Real-time orderflow read (we use OHLCV proxies only)

What we could STEAL into the systematic stack:
- ✅ Power-hour as a SizeMult uplift (lab confirmed edge)
- ✅ Open-hour avoidance until 10:00 ET as an exclude filter (lab finding TBD)
- ⚠️ Opening-range breakout setup as a new strategy class (would require new code)
- ⚠️ GEX-wall proximity as a pyramid-OK gate (requires options data)

## 6. Reading list — verify with primary sources

The user should treat this document as **archetype synthesis**, not as
quoted methodology. To verify or refine:

- **Twitter/X**: @not_a_lil_fish — primary source
- **Adjacent practitioners**: @TraderXO, @JustinBennett, @sssvenny,
  @hedgeyemkt (for level-based intraday community)
- **Books**:
  - "Trading in the Zone" — Mark Douglas (the discretionary mindset)
  - "Mind Over Markets" — James Dalton (market profile / value area)
  - "One Good Trade" — Mike Bellafiore (SMB Capital — desk-grade tape)
  - "Reading Price Charts Bar by Bar" — Al Brooks
- **Free educational content**:
  - SMB Capital YouTube (daily tape recaps from the desk)
  - InnerCircleTrader / ICT (controversial but levels methodology is solid)
  - Volume Profile Trading community (Chris Lori, Volman, etc.)

## 7. What to skip (anti-patterns the cohort warns against)

- ❌ Trading "candle patterns" without level context
- ❌ Adding indicators on top of indicators (RSI + MACD + Stochastic
  all saying the same thing = no new information)
- ❌ Using "support and resistance" drawn after-the-fact on the chart
  (must be pre-marked from session/profile data)
- ❌ Holding losers hoping they'll come back
- ❌ Revenge trading after a loss
- ❌ Trading during news releases without explicit volatility playbook
- ❌ Believing any single indicator is The Edge — edge is in the
  combination of level + tape + R:R

## 8. Hooks for the lab

Queue items added to the Edge Lab specifically inspired by this
research:

- [ ] **5-min and 1-min bar variants** of the EdgeDefs (currently 1H only)
- [ ] **Session-aware proxies** — separate edges that fire only in RTH,
      separate ones for ETH
- [ ] **OR-breakout EdgeDef** — define OR over first 30 min, test
      breakout sustainability
- [ ] **Opening-drive-failure EdgeDef** — first-15-min direction vs
      second-15-min direction reversal
- [ ] **VWAP-reclaim EdgeDef** — close crosses VWAP after persistent
      one-sided session
- [ ] **GEX-wall proximity EdgeDef** — distance-from-largest-OI-strike
      as a feature (requires SPY/SPX options chain pull)
- [ ] **A/B/C/D trade grading** scaffolding for the paper trader's
      journal output
