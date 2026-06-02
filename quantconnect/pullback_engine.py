"""
QuantConnect / LEAN port of the production pullback engine.

Mirrors the live system shipped 2026-05-30 (commit chain ending at the
baseline-#0 rebuild). Single-asset version — paste into QuantConnect.com's
algorithm editor and run on SPY 1H bars 2023-07-25 → present.

Entry logic (matches production):
  LONG: bullish structure (EMA50 > SMA130, slope up) + pullback to EMA50
        (|Close − EMA50| / EMA50 ≤ pullback_band) + EMA50 3-bar slope > 0
  SHORT: symmetric mirror

Size multiplier (RSI overlay, never blocks entries):
  RSI(14) < 40 → 1.3× size
  RSI(14) 40-60 → 1.0× size
  RSI(14) > 60 → 0.7× size

Exits (managed by stop/TP orders):
  Stop loss: 2.5%
  TP1: +4% (close 50%, move stop to breakeven)
  TP2: +15% (close remainder)
  Time stop: 390 hourly bars (~1 NYSE-week)

Sizing:
  Base 30% of equity per position
  Capital cap 100% — no leverage
  Max 8 pyramid additions allowed when conditions confirm

To run:
  1. Create new Python algo on QuantConnect.com
  2. Paste this file as main.py
  3. Backtest from 2023-07-25 with $100K start cash
  4. Compare results against our backtest output:
       SPY 1H 2023-07-25 → 2026-05-22: CAGR 17.3%, PF 3.18, DD 10.6%, n=175
"""
from AlgorithmImports import *


class PullbackEngine(QCAlgorithm):

    def Initialize(self):
        # ---- Universe ----
        self.SetStartDate(2023, 7, 25)
        self.SetEndDate(2026, 5, 22)
        self.SetCash(100000)
        self.symbol = self.AddEquity("SPY", Resolution.Hour).Symbol
        # Use raw price for consistent indicator values (we'll handle dividends).
        self.Securities[self.symbol].SetDataNormalizationMode(DataNormalizationMode.Raw)

        # ---- Strategy parameters (mirror config/settings.py PULLBACK) ----
        self.ema_period         = 50
        self.sma_period         = 130
        self.atr_period         = 14
        self.rsi_period         = 14
        self.slope_window       = 3        # rollover guard lookback
        self.pullback_atr_mult  = 2.5      # |Close-EMA|/EMA ≤ band
        self.stop_pct           = 0.025
        self.tp1_pct            = 0.04
        self.tp2_pct            = 0.15
        self.tp1_size_frac      = 0.50
        self.max_hold_bars      = 390

        self.base_size_pct      = 0.30
        self.capital_cap_pct    = 1.00
        self.max_pyramid_legs   = 8

        self.rsi_oversold       = 40
        self.rsi_overbought     = 60
        self.rsi_mult_low       = 1.3
        self.rsi_mult_high      = 0.7

        # ---- Indicators (QC built-ins compute incrementally on each bar) ----
        self.ema = self.EMA(self.symbol, self.ema_period, Resolution.Hour)
        self.sma = self.SMA(self.symbol, self.sma_period, Resolution.Hour)
        self.atr = self.ATR(self.symbol, self.atr_period, MovingAverageType.Wilders, Resolution.Hour)
        self.rsi = self.RSI(self.symbol, self.rsi_period, MovingAverageType.Wilders, Resolution.Hour)
        self.ema_history = RollingWindow[float](self.slope_window + 1)

        self.SetWarmUp(max(self.sma_period, self.atr_period) + self.slope_window + 5,
                       Resolution.Hour)

        # ---- Position state ----
        self.positions = []   # list of dicts; supports pyramiding up to max_pyramid_legs
        self.bars_since_entry = {}

    # =====================================================================
    # OnData — fires once per hourly bar
    # =====================================================================
    def OnData(self, data):
        if self.IsWarmingUp or not self.ema.IsReady or not self.sma.IsReady \
                or not self.atr.IsReady:
            return
        if self.symbol not in data.Bars:
            return
        bar = data.Bars[self.symbol]
        price = bar.Close

        # Track EMA history for slope guard
        self.ema_history.Add(self.ema.Current.Value)
        if not self.ema_history.IsReady:
            return

        # ---- Manage existing positions ----
        self._manage_positions(price, bar.High, bar.Low)

        # ---- Compute structural state ----
        ema_now = self.ema.Current.Value
        sma_now = self.sma.Current.Value
        atr_now = self.atr.Current.Value
        rsi_now = self.rsi.Current.Value
        slope_now = ema_now - self.ema_history[self.slope_window]   # 3-bar slope

        is_bullish_structure = (ema_now > sma_now) and (slope_now > 0)
        is_bearish_structure = (ema_now < sma_now) and (slope_now < 0)
        atr_pct = atr_now / price
        pullback_band = self.pullback_atr_mult * atr_pct
        pullback_proximity = abs(price - ema_now) / ema_now <= pullback_band

        long_signal = is_bullish_structure and pullback_proximity and price < ema_now
        short_signal = is_bearish_structure and pullback_proximity and price > ema_now

        # ---- Pyramid allowed? ----
        legs_open = len(self.positions)
        can_pyramid = legs_open > 0 and legs_open < self.max_pyramid_legs
        same_direction_long = can_pyramid and self.positions[0]["side"] == 1 and long_signal
        same_direction_short = can_pyramid and self.positions[0]["side"] == -1 and short_signal

        # ---- Entry decision ----
        if legs_open == 0:
            if long_signal:
                self._open(price, side=1, rsi=rsi_now)
            elif short_signal:
                self._open(price, side=-1, rsi=rsi_now)
        elif same_direction_long or same_direction_short:
            self._open(price, side=self.positions[0]["side"], rsi=rsi_now)

    # =====================================================================
    # Position management
    # =====================================================================
    def _rsi_size_mult(self, rsi):
        if rsi < self.rsi_oversold:
            return self.rsi_mult_low
        if rsi > self.rsi_overbought:
            return self.rsi_mult_high
        return 1.0

    def _open(self, price, side, rsi):
        equity = self.Portfolio.TotalPortfolioValue
        current_exposure = sum(p["size_usd"] for p in self.positions)
        room = equity * self.capital_cap_pct - current_exposure
        if room <= 0:
            return

        size_usd = min(equity * self.base_size_pct * self._rsi_size_mult(rsi), room)
        qty = int((size_usd / price) * side)
        if qty == 0:
            return

        self.MarketOrder(self.symbol, qty)
        pos = {
            "side": side,
            "entry_price": price,
            "qty": qty,
            "size_usd": abs(qty * price),
            "entry_bar": self.Time,
            "stop_price": price * (1 - self.stop_pct * side),
            "tp1_price": price * (1 + self.tp1_pct * side),
            "tp2_price": price * (1 + self.tp2_pct * side),
            "tp1_hit": False,
            "rsi_at_entry": rsi,
        }
        self.positions.append(pos)
        self.Debug(
            f"[OPEN] {('LONG' if side==1 else 'SHORT')} qty={qty} "
            f"price=${price:.2f} stop=${pos['stop_price']:.2f} "
            f"tp1=${pos['tp1_price']:.2f} tp2=${pos['tp2_price']:.2f} "
            f"rsi={rsi:.1f} legs_now={len(self.positions)}"
        )

    def _manage_positions(self, close, bar_high, bar_low):
        still_open = []
        for pos in self.positions:
            side = pos["side"]
            stop_hit = (bar_low <= pos["stop_price"]) if side == 1 else (bar_high >= pos["stop_price"])
            tp1_hit = (bar_high >= pos["tp1_price"]) if side == 1 else (bar_low <= pos["tp1_price"])
            tp2_hit = (bar_high >= pos["tp2_price"]) if side == 1 else (bar_low <= pos["tp2_price"])
            bars_held = (self.Time - pos["entry_bar"]).total_seconds() / 3600

            if stop_hit:
                self.MarketOrder(self.symbol, -pos["qty"])
                self.Debug(f"[STOP] price=${pos['stop_price']:.2f} qty={-pos['qty']}")
                continue

            if not pos["tp1_hit"] and tp1_hit:
                tp1_qty = int(pos["qty"] * self.tp1_size_frac)
                if tp1_qty != 0:
                    self.MarketOrder(self.symbol, -tp1_qty)
                    pos["qty"] -= tp1_qty
                    pos["tp1_hit"] = True
                    pos["stop_price"] = pos["entry_price"]    # move stop to BE
                    self.Debug(f"[TP1] price=${pos['tp1_price']:.2f} "
                               f"closed {tp1_qty}, stop→BE")
            if tp2_hit:
                self.MarketOrder(self.symbol, -pos["qty"])
                self.Debug(f"[TP2] price=${pos['tp2_price']:.2f} qty={-pos['qty']}")
                continue
            if bars_held >= self.max_hold_bars:
                self.MarketOrder(self.symbol, -pos["qty"])
                self.Debug(f"[TIME] {bars_held:.0f} bars elapsed, qty={-pos['qty']}")
                continue
            still_open.append(pos)
        self.positions = still_open

    def OnEndOfAlgorithm(self):
        # Close any leftover positions for clean PnL reporting
        for pos in self.positions:
            self.MarketOrder(self.symbol, -pos["qty"])
        equity = self.Portfolio.TotalPortfolioValue
        ret = (equity / 100000 - 1) * 100
        self.Debug(f"=== Final equity: ${equity:,.0f}  ({ret:+.1f}%) ===")
