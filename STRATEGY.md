# Quant IA Trading System — Reference Doc

A pullback-with-imbalance strategy on **SPY 1h** that translates an
exponential/logarithmic model of market behaviour into discrete trading rules.

---

## 1. Core mathematical idea

Markets cycle through phases that each have a clean mathematical signature.
The system uses simple technicals as proxies for those signatures:

| Mathematical concept | Real-world phase | Trading proxy |
|---|---|---|
| Exponential growth | Bullish expansion | EMA above SMA, EMA slope > 0 |
| Reduced exponential growth | Slowdown / late-trend | EMA still above SMA, slope decaying |
| Logarithmic decay | Crash / capitulation | EMA below SMA, slope strongly negative |
| First derivative | Momentum | EMA slope, n-bar rate of change |
| Residual deviation | Inefficiency from trend | (EMA − SMA) / SMA, (Close − EMA) / EMA |
| Volatility expansion | Regime transition | rolling-σ(returns) / mean(rolling-σ) |

**Translation rule**: an EMA is the discretised solution to dP/dt = α(P̄ − P);
its slope is therefore a numerical first derivative of the smoothed price
process. The SMA approximates a longer-horizon equilibrium, so the gap
`EMA − SMA` is a residual term — interpretable as "how far the local
exponential is from fair value." Trades exploit reversion of that residual
along the dominant structural direction.

### Key formulas

```
EMA_t       = α·P_t + (1−α)·EMA_{t−1},     α = 2 / (N+1)
SMA_t       = (1/N) · Σ P_{t−i}             for i = 0..N−1
slope_t     = (X_t − X_{t−w}) / X_{t−w} / w   (per-bar % change averaged over w)
momentum_t  = (P_t / P_{t−n}) − 1
deviation_t = (EMA_t − SMA_t) / SMA_t
price_dev_t = (P_t − EMA_t) / EMA_t
σ_t         = stdev(returns, window)
vol_ratio_t = σ_t / mean(σ, window)
```

---

## 2. Regime model (subperiod classification)

Five labels assigned per bar from the indicator stack:

| Regime | Trigger (loose form) | Meaning |
|---|---|---|
| `growth` | bullish struct + strong slope + strong momentum | Clean exponential expansion |
| `slowdown` | bullish struct + weak slope | Trend losing acceleration |
| `distribution` | bullish struct + negative momentum + vol spike | Topping divergence |
| `crash` | bearish struct + strong negative slope | Logarithmic decay phase |
| `stabilization` | |slope| ≈ 0 and |divergence| ≈ 0 | Range / no edge |

The classifier is preserved unchanged from the original spec — it is
*decoupled* from execution and only used for diagnostics and pyramiding gates.

---

## 3. Strategy logic

### Market structure

* **Bullish** = EMA > SMA AND slope > 0 AND a recent rolling-window higher
  high occurred within the last 20 bars.
* **Bearish** = EMA < SMA AND slope < 0 AND a recent lower low.
* **Neutral** otherwise (no trades).

### Entry — asymmetric regime coupling

| Side | Filters required to fire |
|---|---|
| **Long** | bullish structure · pullback · imbalance · momentum re-accelerating |
| **Short** | bearish structure · **bearish regime** · pullback · imbalance · momentum decelerating |

* **Pullback** = `|Close − EMA| / EMA ≤ pullback_band` (currently 0.7%)
* **Imbalance** = `|EMA − SMA| / SMA ≥ imbalance_min` (currently 0.03%)
* **Momentum continuation** = the per-bar momentum is rising (long) or
  falling (short) — i.e. `Δ momentum > 0`

The asymmetry is deliberate: SPY's secular drift is up, so longs are kept
permissive while shorts are gated by regime confirmation.

### Exit — two-target system

Each entry has three exit routes:

1. **Stop loss** at `−2.5%` (long) / `+2.5%` (short) — closes the full position.
2. **TP1 (tight)** at `+4%` — closes 50% of qty, trails the stop to breakeven
   for the runner.
3. **TP2 (runner)** at `+10%` — closes the remainder.
4. **Time exit** at `390` bars (~3 months on 1h) closes whatever is left.

### Pyramiding

* Single position whenever flat.
* Same-direction signals add a position only when the regime confirms:
  longs in `growth` or `slowdown`; shorts in `crash` or `distribution`.
* Hard cap at `max_pyramid_positions = 10`.
* Counter-direction signals are ignored while positions are open.

### Sizing

* Notional = `position_size_pct × current_equity` per entry. With 27% sizing
  and up to 10 stacked positions, peak notional exposure is **~2.7× equity**
  (i.e., uses leverage during confirmed expansions).

---

## 4. Calibration (current SPY 1h)

| Group | Param | Value |
|---|---|---|
| Data | symbol | `SPY` |
| | interval | `1h` |
| | history | yfinance max (~730 days) |
| Indicators | `ema_period` | 50 |
| | `sma_period` | 130 |
| | `momentum_period` | 30 |
| | `slope_window` | 12 |
| | `volatility_window` | 50 |
| | `deviation_window` | 100 |
| Regime | `slope_strong` | 0.0003 |
| | `slope_weak` | 0.00005 |
| | `divergence_strong` | 0.005 |
| | `divergence_weak` | 0.001 |
| | `volatility_spike` | 1.4 |
| | `momentum_strong` | 0.003 |
| Strategy | `pullback_band` | 0.007 |
| | `imbalance_min` | 0.0003 |
| | `stop_loss_pct` | 0.025 |
| | `take_profit_partial_pct` | 0.04 |
| | `take_profit_partial_size` | 0.5 |
| | `take_profit_runner_pct` | 0.10 |
| | `max_holding_bars` | 390 |
| Backtest | `position_size_pct` | 0.27 |
| | `max_pyramid_positions` | 10 |
| | `fee_pct` | 0.0005 |
| | `slippage_pct` | 0.0002 |

---

## 5. Performance (SPY 1h, ~34 months)

| Metric | Value |
|---|---|
| Total legs | 191 |
| Unique entries | 127 (3.74/mo · longs 3.27/mo · shorts 0.47/mo) |
| Win rate | 70.7% |
| Profit factor | 2.55 |
| Expectancy / leg | +2.42% |
| Max drawdown | 14.77% |
| CAGR | 17.17% |
| Sharpe | 0.37 |
| Final equity | $156,622 from $100k |

**Leg breakdown:**
| Leg | n | win % | total $ |
|---|---|---|---|
| TP1 partial (+4%) | 64 | 100% | +$39,366 |
| TP2 runner (+10%) | 16 | 100% | +$21,297 |
| Time exit | 34 | 97% | +$23,308 |
| Stop (−2.5%) | 68 | 19% | −$36,211 |

---

## 6. Project layout

```
quant_ia_trading_system/
├── data/{raw,processed}/
├── core/
│   ├── data_loader.py    yfinance loader, period-based for intraday
│   ├── indicators.py     EMA, SMA, slope, momentum, deviation, vol-ratio
│   └── regime_model.py   5-state subperiod classifier
├── strategy/
│   ├── structure.py      bullish / bearish / neutral labels
│   └── entry_logic.py    pullback + imbalance + momentum, asymmetric
├── backtest/
│   ├── backtester.py     event-driven, pyramiding, two-target exits
│   └── metrics.py        win rate, drawdown, profit factor, Sharpe, CAGR
├── config/settings.py    all tunables
├── main.py               orchestrator
└── requirements.txt
```

Run it:
```bash
pip install -r requirements.txt
python -m main           # uses defaults (SPY 1h)
python -m main QQQ       # override symbol
```

---

## 7. Code

### `config/settings.py`

```python
from dataclasses import dataclass, field
from typing import List


@dataclass
class DataConfig:
    symbols: List[str] = field(default_factory=lambda: ["SPY"])
    start: str = "2024-05-06"
    end: str = "2026-05-06"
    interval: str = "1h"
    raw_dir: str = "data/raw"
    processed_dir: str = "data/processed"


@dataclass
class IndicatorConfig:
    ema_period: int = 50
    sma_period: int = 130
    momentum_period: int = 30
    slope_window: int = 12
    volatility_window: int = 50
    deviation_window: int = 100


@dataclass
class RegimeConfig:
    slope_strong: float = 0.0003
    slope_weak: float = 0.00005
    divergence_strong: float = 0.005
    divergence_weak: float = 0.001
    volatility_spike: float = 1.4
    momentum_strong: float = 0.003


@dataclass
class StrategyConfig:
    pullback_band: float = 0.007
    imbalance_min: float = 0.0003
    stop_loss_pct: float = 0.025
    take_profit_partial_pct: float = 0.04
    take_profit_partial_size: float = 0.5
    take_profit_runner_pct: float = 0.10
    max_holding_bars: int = 390


@dataclass
class BacktestConfig:
    initial_capital: float = 100_000.0
    position_size_pct: float = 0.27
    max_pyramid_positions: int = 10
    fee_pct: float = 0.0005
    slippage_pct: float = 0.0002
    risk_per_trade: float = 0.01


DATA = DataConfig()
INDICATORS = IndicatorConfig()
REGIME = RegimeConfig()
STRATEGY = StrategyConfig()
BACKTEST = BacktestConfig()
```

### `core/data_loader.py`

```python
import os
from typing import Optional
import pandas as pd

try:
    import yfinance as yf
except ImportError:
    yf = None

from config.settings import DATA

_INTRADAY_PERIODS = {
    "1m": "7d", "2m": "60d", "5m": "60d", "15m": "60d", "30m": "60d",
    "60m": "730d", "1h": "730d", "90m": "60d",
}


def _cache_path(symbol, interval):
    os.makedirs(DATA.raw_dir, exist_ok=True)
    return os.path.join(DATA.raw_dir, f"{symbol}_{interval}.csv")


def load_symbol(symbol, start=None, end=None, interval=None, force_refresh=False):
    start = start or DATA.start
    end = end or DATA.end
    interval = interval or DATA.interval
    path = _cache_path(symbol, interval)

    if not force_refresh and os.path.exists(path):
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if not df.empty:
            return _normalize(df)

    if interval in _INTRADAY_PERIODS:
        raw = yf.download(symbol, period=_INTRADAY_PERIODS[interval],
                          interval=interval, auto_adjust=True, progress=False)
    else:
        raw = yf.download(symbol, start=start, end=end, interval=interval,
                          auto_adjust=True, progress=False)

    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw = _normalize(raw)
    raw.to_csv(path)
    return raw


def _normalize(df):
    df = df.rename(columns=str.title)
    keep = [c for c in ("Open","High","Low","Close","Volume") if c in df.columns]
    df = df[keep].copy().dropna(how="any")
    df.index = pd.to_datetime(df.index)
    df.sort_index(inplace=True)
    return df


def load_universe(symbols=None, **kwargs):
    symbols = symbols or DATA.symbols
    return {s: load_symbol(s, **kwargs) for s in symbols}
```

### `core/indicators.py`

```python
import pandas as pd
from config.settings import INDICATORS


def ema(series, period):  return series.ewm(span=period, adjust=False).mean()
def sma(series, period):  return series.rolling(period, min_periods=period).mean()
def slope(series, w):     return series.pct_change(w) / w
def momentum(series, n):  return series.pct_change(n)
def deviation(p, base):   return (p - base) / base
def rolling_volatility(s, w): return s.pct_change().rolling(w, min_periods=w).std()


def compute_indicators(df, cfg=INDICATORS):
    out = df.copy()
    close = out["Close"]
    out["EMA"] = ema(close, cfg.ema_period)
    out["SMA"] = sma(close, cfg.sma_period)
    out["EMA_slope"] = slope(out["EMA"], cfg.slope_window)
    out["Momentum"] = momentum(close, cfg.momentum_period)
    out["Deviation"] = (out["EMA"] - out["SMA"]) / out["SMA"]
    out["Price_dev"] = deviation(close, out["EMA"])
    out["Volatility"] = rolling_volatility(close, cfg.volatility_window)
    out["Vol_mean"] = out["Volatility"].rolling(cfg.volatility_window).mean()
    out["Vol_ratio"] = out["Volatility"] / out["Vol_mean"]

    look = cfg.deviation_window
    out["Recent_high"] = out["High"].rolling(look, min_periods=look).max()
    out["Recent_low"]  = out["Low"].rolling(look, min_periods=look).min()
    out["Higher_high"] = out["High"] >= out["Recent_high"].shift(1)
    out["Lower_low"]   = out["Low"]  <= out["Recent_low"].shift(1)
    return out
```

### `core/regime_model.py`

```python
from enum import Enum
import numpy as np
import pandas as pd
from config.settings import REGIME


class Regime(str, Enum):
    GROWTH = "growth"
    SLOWDOWN = "slowdown"
    DISTRIBUTION = "distribution"
    CRASH = "crash"
    STABILIZATION = "stabilization"
    UNDEFINED = "undefined"


def classify_regime(df, cfg=REGIME):
    out = df.copy()
    n = len(out)
    slope = out["EMA_slope"].to_numpy()
    div = out["Deviation"].to_numpy()
    mom = out["Momentum"].to_numpy()
    vol_ratio = out["Vol_ratio"].to_numpy()

    labels = np.full(n, Regime.UNDEFINED.value, dtype=object)
    for i in range(n):
        s, d, m, v = slope[i], div[i], mom[i], vol_ratio[i]
        if any(map(lambda x: x is None or (isinstance(x, float) and np.isnan(x)), (s,d,m,v))):
            continue
        bullish = d > 0
        bearish = d < 0
        vol_expansion = v > cfg.volatility_spike

        if bullish and s > cfg.slope_strong and m > cfg.momentum_strong:
            labels[i] = Regime.GROWTH.value
        elif bearish and s < -cfg.slope_strong and m < -cfg.momentum_strong:
            labels[i] = Regime.CRASH.value
        elif bullish and 0 < s <= cfg.slope_strong:
            labels[i] = Regime.SLOWDOWN.value
        elif bullish and m < 0 and vol_expansion:
            labels[i] = Regime.DISTRIBUTION.value
        elif abs(s) < cfg.slope_weak and abs(d) < cfg.divergence_weak:
            labels[i] = Regime.STABILIZATION.value
        elif bearish and s < 0:
            labels[i] = Regime.SLOWDOWN.value if s > -cfg.slope_strong else Regime.CRASH.value
        else:
            labels[i] = Regime.STABILIZATION.value

    out["Regime"] = labels
    out["Is_bullish_regime"] = out["Regime"].isin([Regime.GROWTH.value, Regime.SLOWDOWN.value])
    out["Is_bearish_regime"] = out["Regime"].isin([Regime.CRASH.value, Regime.DISTRIBUTION.value])
    out["Is_tradeable"] = out["Is_bullish_regime"] | out["Is_bearish_regime"]
    return out
```

### `strategy/structure.py`

```python
from enum import Enum
import pandas as pd


class Structure(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


def label_structure(df):
    out = df.copy()
    recent_hh = out["Higher_high"].fillna(False).rolling(20, min_periods=1).max().astype(bool)
    recent_ll = out["Lower_low"].fillna(False).rolling(20, min_periods=1).max().astype(bool)

    bullish = (out["EMA"] > out["SMA"]) & (out["EMA_slope"] > 0) & recent_hh
    bearish = (out["EMA"] < out["SMA"]) & (out["EMA_slope"] < 0) & recent_ll

    structure = pd.Series(Structure.NEUTRAL.value, index=out.index, dtype=object)
    structure[bullish] = Structure.BULLISH.value
    structure[bearish] = Structure.BEARISH.value
    out["Structure"] = structure
    out["Is_bullish_structure"] = structure == Structure.BULLISH.value
    out["Is_bearish_structure"] = structure == Structure.BEARISH.value
    return out
```

### `strategy/entry_logic.py`

```python
import pandas as pd
from config.settings import STRATEGY


def generate_signals(df, cfg=STRATEGY):
    out = df.copy()

    pullback_long  = out["Price_dev"].abs() <= cfg.pullback_band
    imbalance_long = out["Deviation"] >= cfg.imbalance_min
    momentum_up    = out["Momentum"].diff() > 0

    pullback_short  = out["Price_dev"].abs() <= cfg.pullback_band
    imbalance_short = out["Deviation"] <= -cfg.imbalance_min
    momentum_down   = out["Momentum"].diff() < 0

    long_signal = (
        out["Is_bullish_structure"]
        & pullback_long & imbalance_long & momentum_up
    )
    short_signal = (
        out["Is_bearish_structure"]
        & out["Is_bearish_regime"]   # asymmetric: regime gates shorts only
        & pullback_short & imbalance_short & momentum_down
    )

    out["Signal"] = 0
    out.loc[long_signal, "Signal"] = 1
    out.loc[short_signal, "Signal"] = -1
    out["Pullback"] = pullback_long
    out["Imbalance_long"] = imbalance_long
    out["Imbalance_short"] = imbalance_short
    return out
```

### `backtest/backtester.py` (core loop)

```python
from dataclasses import dataclass, asdict
import pandas as pd
from config.settings import BACKTEST, STRATEGY


@dataclass
class _Position:
    side: int; entry_time: pd.Timestamp; entry_price: float
    qty_initial: float; qty_open: float
    stop_price: float; partial_target: float; runner_target: float
    partial_taken: bool = False; bars_held: int = 0


@dataclass
class TradeRecord:
    symbol: str; side: int
    entry_time: pd.Timestamp; entry_price: float
    exit_time: pd.Timestamp;  exit_price: float
    qty: float; pnl: float; return_pct: float
    bars_held: int; exit_reason: str; leg: str


def _apply_costs(price, side, cfg=BACKTEST):
    return price * (1 + cfg.slippage_pct * side) * (1 + cfg.fee_pct)


def _open_position(side, ts, raw_price, equity, bcfg, scfg):
    entry_fill = _apply_costs(raw_price, side, bcfg)
    qty = (equity * bcfg.position_size_pct) / entry_fill
    if side == 1:
        stop = entry_fill * (1 - scfg.stop_loss_pct)
        tp1  = entry_fill * (1 + scfg.take_profit_partial_pct)
        tp2  = entry_fill * (1 + scfg.take_profit_runner_pct)
    else:
        stop = entry_fill * (1 + scfg.stop_loss_pct)
        tp1  = entry_fill * (1 - scfg.take_profit_partial_pct)
        tp2  = entry_fill * (1 - scfg.take_profit_runner_pct)
    return _Position(side=side, entry_time=ts, entry_price=entry_fill,
                     qty_initial=qty, qty_open=qty,
                     stop_price=stop, partial_target=tp1, runner_target=tp2)


def _close_leg(pos, qty_close, raw_exit, ts, leg, symbol, bcfg):
    fill = _apply_costs(raw_exit, -pos.side, bcfg)
    pnl = (fill - pos.entry_price) * qty_close * pos.side
    ret = pnl / (pos.entry_price * qty_close) if qty_close > 0 else 0.0
    rec = TradeRecord(symbol=symbol, side=pos.side,
        entry_time=pos.entry_time, entry_price=pos.entry_price,
        exit_time=ts, exit_price=fill, qty=qty_close,
        pnl=pnl, return_pct=ret, bars_held=pos.bars_held,
        exit_reason=leg if leg in ("stop","time") else f"target_{leg}", leg=leg)
    pos.qty_open -= qty_close
    return rec, pnl


def run_backtest(df, symbol="ASSET", bcfg=BACKTEST, scfg=STRATEGY):
    cash = bcfg.initial_capital
    positions, closed, equity_history = [], [], []

    for ts, row in df.iterrows():
        price = float(row["Close"])
        high  = float(row.get("High", price))
        low   = float(row.get("Low",  price))
        signal = int(row["Signal"]) if not pd.isna(row["Signal"]) else 0
        regime = row["Regime"]

        # Manage open positions (intrabar)
        for pos in list(positions):
            pos.bars_held += 1

            stop_hit = (pos.side == 1 and low <= pos.stop_price) or \
                       (pos.side == -1 and high >= pos.stop_price)
            if stop_hit:
                rec, _ = _close_leg(pos, pos.qty_open, pos.stop_price, ts, "stop", symbol, bcfg)
                cash += rec.pnl; closed.append(rec); positions.remove(pos); continue

            if not pos.partial_taken:
                tp1_hit = (pos.side == 1 and high >= pos.partial_target) or \
                          (pos.side == -1 and low  <= pos.partial_target)
                if tp1_hit:
                    qty_close = min(pos.qty_initial * scfg.take_profit_partial_size, pos.qty_open)
                    rec, _ = _close_leg(pos, qty_close, pos.partial_target, ts, "partial", symbol, bcfg)
                    cash += rec.pnl; closed.append(rec)
                    pos.partial_taken = True
                    pos.stop_price = pos.entry_price   # trail to breakeven
                    if pos.qty_open <= 0:
                        positions.remove(pos); continue

            tp2_hit = (pos.side == 1 and high >= pos.runner_target) or \
                      (pos.side == -1 and low  <= pos.runner_target)
            if tp2_hit and pos.qty_open > 0:
                rec, _ = _close_leg(pos, pos.qty_open, pos.runner_target, ts, "runner", symbol, bcfg)
                cash += rec.pnl; closed.append(rec); positions.remove(pos); continue

            if pos.bars_held >= scfg.max_holding_bars and pos.qty_open > 0:
                rec, _ = _close_leg(pos, pos.qty_open, price, ts, "time", symbol, bcfg)
                cash += rec.pnl; closed.append(rec); positions.remove(pos); continue

        mtm = sum((price - p.entry_price) * p.qty_open * p.side for p in positions)
        equity = cash + mtm
        equity_history.append((ts, equity))

        # Entries
        if signal == 0: continue
        if positions and any(p.side != signal for p in positions): continue

        if not positions:
            new_pos = _open_position(signal, ts, price, equity, bcfg, scfg)
            if new_pos: positions.append(new_pos)
        else:
            allow_pyramid = (
                (signal == 1 and regime in ("growth", "slowdown")) or
                (signal == -1 and regime in ("crash", "distribution"))
            )
            if allow_pyramid and len(positions) < bcfg.max_pyramid_positions:
                new_pos = _open_position(signal, ts, price, equity, bcfg, scfg)
                if new_pos: positions.append(new_pos)

    # End-of-data force-close
    if positions:
        last_ts = df.index[-1]; last_price = float(df["Close"].iloc[-1])
        for pos in list(positions):
            rec, _ = _close_leg(pos, pos.qty_open, last_price, last_ts, "end_of_data", symbol, bcfg)
            cash += rec.pnl; closed.append(rec); positions.remove(pos)
        equity_history[-1] = (last_ts, cash)

    trades_df = pd.DataFrame([asdict(t) for t in closed])
    equity_curve = pd.Series([v for _, v in equity_history],
                             index=pd.DatetimeIndex([t for t, _ in equity_history]),
                             name="equity")
    return {"trades": trades_df, "equity_curve": equity_curve}
```

### `backtest/metrics.py`

```python
import numpy as np
import pandas as pd


def win_rate(trades):       return float((trades["pnl"] > 0).mean()) if len(trades) else 0.0
def profit_factor(trades):
    if trades.empty: return 0.0
    gp = trades.loc[trades["pnl"] > 0, "pnl"].sum()
    gl = -trades.loc[trades["pnl"] < 0, "pnl"].sum()
    return float("inf") if gl == 0 and gp > 0 else float(gp / gl) if gl else 0.0
def max_drawdown(eq):
    if eq.empty: return 0.0
    return float(-((eq - eq.cummax()) / eq.cummax()).min())
def cagr(eq):
    if len(eq) < 2 or eq.iloc[0] <= 0: return 0.0
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    return float((eq.iloc[-1] / eq.iloc[0]) ** (1/yrs) - 1) if yrs > 0 else 0.0
def sharpe_ratio(eq, ppy=252):
    if len(eq) < 2: return 0.0
    r = eq.pct_change().dropna()
    return float(np.sqrt(ppy) * r.mean() / r.std()) if r.std() else 0.0
def expectancy(trades):     return float(trades["return_pct"].mean()) if len(trades) else 0.0


def summarize(trades, eq):
    return {
        "trades": len(trades),
        "win_rate": round(win_rate(trades), 4),
        "profit_factor": round(profit_factor(trades), 4),
        "expectancy": round(expectancy(trades), 6),
        "max_drawdown": round(max_drawdown(eq), 4),
        "cagr": round(cagr(eq), 4),
        "sharpe": round(sharpe_ratio(eq), 4),
        "final_equity": round(float(eq.iloc[-1]) if not eq.empty else 0.0, 2),
    }
```

### `main.py`

```python
import os, sys
import pandas as pd
from config.settings import DATA
from core.data_loader import load_universe
from core.indicators import compute_indicators
from core.regime_model import classify_regime, regime_summary
from strategy.structure import label_structure
from strategy.entry_logic import generate_signals
from backtest.backtester import run_backtest
from backtest.metrics import summarize


def prepare(df):
    df = compute_indicators(df)
    df = classify_regime(df)
    df = label_structure(df)
    df = generate_signals(df)
    return df.dropna(subset=["EMA","SMA","EMA_slope","Momentum","Deviation"])


def run(symbols=None):
    universe = load_universe(symbols=list(symbols) if symbols else None)
    results = {}
    for symbol, raw in universe.items():
        prepared = prepare(raw)
        os.makedirs(DATA.processed_dir, exist_ok=True)
        prepared.to_csv(os.path.join(DATA.processed_dir, f"{symbol}.csv"))
        bt = run_backtest(prepared, symbol=symbol)
        results[symbol] = summarize(bt["trades"], bt["equity_curve"])
    print(pd.DataFrame(results).T.to_string())
    return results


if __name__ == "__main__":
    run(sys.argv[1:] or None)
```

---

## 8. Mental model in one paragraph

> Treat each timeframe as a local solution to a smooth differential equation
> with an exponential-growth dominant term plus a residual. The EMA estimates
> the dominant exponential, the SMA estimates the longer-term equilibrium,
> and `EMA − SMA` is the residual. When the residual is non-zero (imbalance
> exists), price has been pulled away from local equilibrium and tends to
> revert *along the dominant structural direction* — go long on bullish-structure
> pullbacks, short only when the regime classifier explicitly confirms the
> exponential has flipped sign. Pyramid into trends, scale out at the first
> profit target, run the rest, and time-stop anything that overstays.

---

## 9. Tuning levers cheat-sheet

| Want | Knob | Direction |
|---|---|---|
| More trades | `pullback_band`, `imbalance_min` | up / down |
| Bigger CAGR (with bigger DD) | `position_size_pct`, `max_pyramid_positions` | up |
| Tighter risk | `stop_loss_pct` | down |
| Faster capital recycling | `max_holding_bars`, `take_profit_partial_pct` | down |
| Fewer false signals | `slope_strong`, `divergence_strong` | up |
| More shorts | drop `Is_bearish_regime` from short_signal | — |
