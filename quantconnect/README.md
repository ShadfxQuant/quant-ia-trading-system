# QuantConnect port of the pullback engine

This directory holds a standalone QuantConnect / LEAN port of the production
strategy. Use it for an **independent** backtest — different bar source,
different execution simulator — to corroborate the in-repo backtest.

## What's here

- `pullback_engine.py` — single-asset (SPY) algorithm matching production config
  shipped 2026-05-30.

## How to run on QuantConnect.com

1. Sign up free at https://www.quantconnect.com (no payment needed for backtests).
2. **Create Project** → **Python** → name it `PullbackEngineBaseline`.
3. Replace the default `main.py` contents with the entire contents of
   `pullback_engine.py` from this directory.
4. Click **Build** then **Backtest**.
5. Default parameters cover the same window as our backtest
   (`2023-07-25` → `2026-05-22`), starting cash `$100,000`.

## What you're verifying

Our in-repo backtest (SPY 1H, baseline #0 production config):

| Metric | Our backtest |
|---|---|
| CAGR | +17.3% |
| PF | 3.18 |
| Sharpe (daily) | 1.49 |
| Max DD | 10.6% |
| Total legs | 175 |
| Long / Short | 156 / 19 |
| Final equity | $156,926 |

QuantConnect's bars are sourced from QuantBook (their proprietary feed,
typically Polygon-derived) instead of yfinance. Small drift in absolute
numbers is normal and expected (`±5%` on CAGR is the rule of thumb), but
**directional alignment matters** — if QC shows negative CAGR or PF < 1.5
while we show PF 3.18, something is wrong in the port and worth digging
into.

## What's NOT ported (intentional simplifications)

The QC port omits the following pieces of the production system to keep
the script readable:

- **trend_carry sleeve** — single sleeve only (pullback). Add as a second
  StrategySpec on QC if desired.
- **Macro filter** — QC doesn't have native news-headline scoring. The
  macro filter only warns in our production system; it doesn't block.
- **HMM regime detection** — diagnostic only in production anyway.
- **Slippage and fee model** — QC has its own configurable broker model
  (`SetBrokerageModel(...)`); we leave defaults.

## What IS ported (the load-bearing logic)

- Pullback entry: EMA50 + SMA130 trend filter + ATR-normalized pullback
  band + 3-bar EMA slope rollover guard
- Symmetric long + short entries
- Pyramid up to 8 legs in the same direction with first-fire-takes-slot
- Exit ladder: stop / TP1 (50% off, stop → BE) / TP2 / time stop (390 bars)
- RSI(14) size multiplier (1.3× oversold, 0.7× overbought, never zero)
- Position sizing 30% of equity, capital cap 100% (no leverage)

## Multi-asset extension (optional)

To add GLD / PAXGUSDT (or any other ticker QC supports), in `Initialize()`:

```python
self.symbols = [
    self.AddEquity("SPY", Resolution.Hour).Symbol,
    self.AddEquity("GLD", Resolution.Hour).Symbol,
]
```

then track `positions` per symbol (`dict[Symbol, list]`) and refactor
`_open` / `_manage_positions` to take a symbol argument.

## Reading the QC report

After the backtest finishes, look at:

- **Equity curve** in the main chart — should look like our PNG
  (`data/research_baseline0_optimized.png`, panel for config C)
- **Statistics** panel: compare CAGR / Sharpe / Drawdown / PF
- **Orders** tab: every entry/exit is logged. Sanity check the first few
  entries match the conditions (look for ATR pullback proximity)
- **Logs** tab: contains the `Debug()` lines emitted from this algo

If results diverge meaningfully (>10% on CAGR), the most likely sources
are: (a) bar timing differences (QC uses consolidated bars while yfinance
uses session-aligned), (b) fill model differences, (c) dividend handling
on SPY (we use `DataNormalizationMode.Raw`, you may want to compare with
`Adjusted`).
