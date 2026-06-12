"""
Global configuration for the quant_ia_trading_system.

All tunable parameters live here so each module stays free of magic numbers.
The mathematical IA model maps onto these proxies:
    EMA  -> exponential growth baseline
    SMA  -> equilibrium / fair value
    dEMA -> first derivative (momentum)
    EMA - SMA -> residual deviation from expected trend
    rolling std of returns -> volatility / regime transition signal
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class DataConfig:
    # Trading universe: SPY only. 1h bars for higher trade frequency.
    # Note: yfinance limits 1h history to ~730 days, so the loader uses
    # period-based download for intraday intervals (start/end ignored).
    # SPY + DIA: production stocks book (SESSION_LOG #21).
    # GLD: yfinance gold ETF (NYSE hours, full engine).
    # PAXGUSDT: Binance perp gold (24/7) — gated via REGIME_FILTERS to
    # NYSE hours + ADX≥25 (COMBO_F: PF 1.81 / DD 18.3% in validation).
    # All three gold instruments use inverse macro polarity.
    symbols: List[str] = field(default_factory=lambda: [
        # Phase A1.4 — universe trim 2026-06-05:
        # User dropped IWM + QQQ from live universe after paper-book postmortem
        # (Part 8.21 — both fired losing trades in the live paper run).
        #
        #   - IWM: had been flagged degraded since Part 8.7 (PF 1.31, "small
        #     caps don't fit engine"). KILLED.
        #   - QQQ replaced with ^NDX (Nasdaq-100 cash index). Backtest reveals
        #     the cash index is DRAMATICALLY better than the ETF as a signal
        #     source for the SAME MT5 US100 execution venue:
        #         QQQ   PF 1.85  CAGR +12.7%  DD -9.9%   WR 73.1%
        #         ^NDX  PF 3.13  CAGR +20.9%  DD -6.6%   WR 78.3%
        #     The ETF's tracking error + dividend gaps + arbitrage flow
        #     create noise the engine fires on. The cash index is the
        #     cleanest possible signal. SAME MT5 EXECUTION TARGET (US100).
        #
        # CURRENT LIVE UNIVERSE (all PF >= 3):
        #   - SPY (S&P 500 ETF):     PF 3.18, CAGR +17.3%, DD -6.9%
        #   - ^NDX (Nasdaq-100 cash): PF 3.13, CAGR +20.9%, DD -6.6%
        #   - GLD (gold ETF):        PF 3.40, CAGR +36.4%, DD -5.3%
        #   - GC=F (gold futures):   regime-flip exit (Part 8.11) — 23/5 gold
        # EXECUTION MAPPING via TRADING_LABEL_MAP:
        #   SPY  → MT5 US500
        #   ^NDX → MT5 US100
        #   GLD  → MT5 XAUUSD
        #   GC=F → MT5 XAUUSD (cross-confirm)
        # Watchlist (signal stream only, no live trading):
        #   SLV (HMM-aligned short, Grade A in postmortem — capped at
        #        watchlist until n>=10 trades validate)
        # DROPPED 2026-06-08 (Part 8.23):
        #   EURUSD=X — FX engine broken (PF 1.01 per Part 8.7).
        #     Open positions manually closed +$94 net. User decision:
        #     "forget about its integration as we agreed it is minimally
        #     beneficial." Reach back for FX only after building a proper
        #     session-aware FX framework.
        "SPY", "^NDX", "GLD", "GC=F",
        "SLV",
    ])
    start: str = "2024-05-06"
    end: str = "2026-05-06"
    interval: str = "1h"
    raw_dir: str = "data/raw"
    processed_dir: str = "data/processed"


@dataclass
class IndicatorConfig:
    # Intraday tuning: ~50h trend, ~130h fair value, momentum over ~1 week.
    ema_period: int = 50
    sma_period: int = 130
    momentum_period: int = 30
    slope_window: int = 12
    volatility_window: int = 50
    deviation_window: int = 100


@dataclass
class RegimeConfig:
    # Per-bar moves are much smaller on 1h vs weekly → tighter thresholds.
    slope_strong: float = 0.0003
    slope_weak: float = 0.00005
    divergence_strong: float = 0.005
    divergence_weak: float = 0.001
    volatility_spike: float = 1.4
    momentum_strong: float = 0.003


@dataclass
class StrategyConfig:
    # Loose pullback / imbalance bands so longs fire ~5/month.
    pullback_band: float = 0.007
    imbalance_min: float = 0.0003
    # Two-target exits, scaled to intraday volatility.
    stop_loss_pct: float = 0.025
    take_profit_partial_pct: float = 0.04
    take_profit_partial_size: float = 0.5
    take_profit_runner_pct: float = 0.10
    max_holding_bars: int = 390              # ~3 months of 6.5h sessions on 1h bars
    # ----- Institutional confirmation filters (off by default; see STRATEGY.md
    # for the empirical analysis that motivated leaving these disabled) -----
    use_rvol_filter: bool = False
    rvol_window: int = 20
    rvol_long_threshold: float = 1.1
    rvol_short_threshold: float = 1.2
    use_vwap_filter: bool = False            # session-anchored VWAP, daily reset
    use_hmm_filter: bool = False
    hmm_long_threshold: float = 0.6
    hmm_short_threshold: float = 0.6
    hmm_pyramid_boost_threshold: float = 0.8


@dataclass
class BacktestConfig:
    initial_capital: float = 100_000.0
    # 27% of equity per entry. With up to 10 pyramided positions, peak
    # notional exposure is 2.7x equity — actual leverage, used only during
    # confirmed `growth` regimes.
    position_size_pct: float = 0.27
    max_pyramid_positions: int = 10
    fee_pct: float = 0.0005
    slippage_pct: float = 0.0002
    risk_per_trade: float = 0.01             # legacy, unused by the new engine


# ---------------------------------------------------------------------------
# Dual-strategy portfolio configuration
# ---------------------------------------------------------------------------
@dataclass
class PullbackStratConfig:
    """
    Pullback engine — production deterministic architecture.

    Core entries are driven 100% by deterministic structure (EMA/SMA, slope,
    higher-high, deviation, momentum re-acceleration). HMM is informational
    only — it stays in the dataframe for diagnostics but does NOT scale size
    or block trades.

    VWAP gates pyramiding (NOT initial entries). RVOL is purely diagnostic
    and surfaced in dashboards / trade metadata.

    Capital: conservative base (15%) with confirmed-trend pyramiding up to
    a hard ~50% account-allocation cap.
    """
    name: str = "pullback"
    # ----- Entry filters (deterministic only) -----
    pullback_band: float = 0.007
    imbalance_min: float = 0.0003
    # ----- Phase 4 P1-P3: ATR-normalized thresholds (cross-symbol portability) -----
    # When `use_atr_normalized = True`, the strategy expresses thresholds and
    # stop as multiples of current ATR / Close (i.e., ATR-as-fraction-of-price)
    # instead of fixed percentages. This automatically scales the strategy to
    # whatever volatility regime the asset trades in.
    #
    # Multipliers below are calibrated so the SPY behaviour is roughly preserved
    # at SPY's typical ATR/price ratio (~0.003). On more volatile assets
    # (QQQ ~0.005, IWM ~0.007) the thresholds widen proportionally.
    # Validated by cross-symbol fanout (SPY/QQQ/IWM): ATR mode lifts IWM from
    # CAGR -2.56% (losing) to +2.68% (profitable), QQQ from 4.64% to 6.20%,
    # while preserving SPY's edge (PF 3.04 vs 3.24, CAGR up 10.80% → 12.56%).
    # Enabled by default in production.
    use_atr_normalized: bool = True
    pullback_atr_mult: float = 2.5
    imbalance_atr_mult: float = 0.10
    stop_atr_mult: float = 8.0
    # ----- Phase 4 P5/Lever-3: Volatility targeting (institutional overlay) -----
    # When enabled, multiplies the position-sizing multiplier by VolTargetMult
    # (computed in core/vol_targeting.py). Equivalent to risk-parity sizing —
    # exposure scales inversely with realised volatility.
    use_vol_targeting: bool = False
    vol_target_annual: float = 0.20
    # ----- Lever 4: VIX-conditional dynamic leverage (institutional standard) -----
    # Multiplies size_mult by VixLeverageMult when enabled and column present.
    # Forward-looking implied-vol signal; complements ATR + VolTarget layers.
    use_vix_leverage: bool = False
    # ----- Early-entry mode: fire ONLY on momentum cross-up bars -----
    # Caveman thesis: enter at the exact bar where momentum-decel flips to
    # momentum-accel (i.e., the bottom of the pullback), giving the runner
    # more room before TP. Fewer signals but better asymmetric payoff per.
    use_momentum_crossup: bool = False
    # ----- Regime-bypass entry: mom_up OR RegimeScore >= threshold -----
    # Broadens entries during expansion regimes (opposite of cross-up's
    # narrowing). Thesis: high RegimeScore == institutional flow tailwind,
    # so don't gate on micro-momentum direction during those periods.
    use_regime_bypass: bool = False
    regime_bypass_threshold: float = 0.60
    # ----- Sizing (fixed; HMM no longer scales) -----
    # Production v3 (2026-05-30, SYSTEM_LOG Part 8): rebuilt from Baseline #0
    # after multi-task research found VWAP pyramid gate harmful even as
    # "non-blocking" (PF 3.16 → 2.60). Sizing now matches the verified
    # baseline; users who want higher leverage can multiply both fields.
    base_size_pct: float = 0.30               # 1.0× leverage (was 0.75 = 2.5×)
    capital_cap_pct: float = 1.00             # 1.0× leverage (was 2.50 = 2.5×)
    max_pyramid_positions: int = 8            # baseline #0 spec (was 10)
    # Fixed sizing multiplier — present so the dashboard always reads cleanly.
    fixed_size_mult: float = 1.0
    # ----- Layer 2: Entry Sensitivity Engine (adaptive thresholds) -----
    # When enabled, RegimeScore in the dataframe modulates `pullback_band` and
    # `imbalance_min` per-bar. Higher score (expansion) → easier entry; lower
    # score (chop) → stricter entry. The CORE entry logic is unchanged — only
    # the threshold magnitudes scale.
    use_adaptive_entry: bool = False
    adaptive_pullback_swing: float = 0.40       # ± 40% modulation around base
    adaptive_imbalance_swing: float = 0.40
    # ----- Pyramiding gates (institutional VWAP confirmation) -----
    # All four must hold to add a stack on top of an already-open position:
    #   1) bullish structure
    #   2) regime ∈ pyramid_regimes
    #   3) Close > VWAP
    #   4) Momentum > 0
    pyramid_regimes: tuple = ("growth", "slowdown")
    # Production v2 → Structure 2: drop the VWAP gate, keep positive-momentum
    # gate. The VWAP gate was clipping the right-tail pyramid stacks that
    # historically drove PF; dropping it should lift PF toward baseline (2.55)
    # while accepting DD back toward 12–14%. RVOL/HMM remain informational.
    # PURE EDGE (2026-05-30 research): both VWAP and momentum pyramid gates
    # confirmed harmful on baseline #0 SPY. VWAP gate dropped PF 3.16→2.60
    # and removed 8 legs (-4.6%) — meets the "indicator-as-gate failure"
    # pattern. Indicators allowed only as size multipliers or diagnostics.
    pyramid_require_above_vwap: bool = False
    pyramid_require_positive_momentum: bool = False
    # ----- Layer 5: RSI size multiplier (validated 2026-05-30) -----
    # RSI(14) modulates entry size WITHOUT blocking entries:
    #   RSI < 40 (oversold pullback)   → 1.3× size (more conviction)
    #   RSI in 40-60 (neutral)         → 1.0× size
    #   RSI > 60 (overbought entry)    → 0.7× size (less conviction)
    # Backtested: CAGR 17.1 → 17.3, PF 3.16 → 3.18, leg count unchanged.
    # Clean additive — set to False to disable.
    use_rsi_size_mult: bool = True
    rsi_oversold: float = 40.0
    rsi_overbought: float = 60.0
    rsi_mult_oversold: float = 1.3
    rsi_mult_overbought: float = 0.7
    # ----- Layer 6: High-conviction agreement size multiplier (2026-05-30) -----
    # Tested at 1.15/1.20/1.30/1.50× on SPY+GLD. SPY shows microscopic positive
    # (+$200-$400), GLD loses substantially (-$2,600 to -$7,900). Combined
    # portfolio is net-negative at every tested level — the simple
    # SPY-trained rule (Task 3: 97% WR on agreement bars) does NOT generalise
    # cross-asset. GLD's deterministic regime classifier overcalls "growth"
    # (75% of GLD stops fired in growth regime per the 2026-05-30 audit),
    # so amplifying agreement-bar size on GLD amplifies losses, not wins.
    # Default OFF. Kept in code for the next-session ML build which will
    # weight features per-asset rather than applying a single threshold.
    use_conviction_size_mult: bool = False
    conviction_size_mult: float = 1.3   # if enabled, this is the test default
    conviction_size_cap: float = 2.0    # cap on combined SizeMult after stacks
    # ----- Exit profile (original deterministic exits) -----
    # Production v3 (Structure 1, validated): no BE-trail clip + extended runner.
    # The trailing-after-partial was the binding constraint on PF — disabling
    # it and pushing TP2 to +15% lets winners express their full distribution.
    stop_loss_pct: float = 0.025
    partial_tp_pct: float = 0.04
    partial_tp_size: float = 0.50
    final_tp_pct: float = 0.15
    final_tp_size: float = 0.50
    move_stop_to_be_after_partial: bool = True
    trailing_stop_enabled: bool = False
    trailing_logic_type: str = "ema_50"
    trailing_starts_at: str = "after_partial"
    max_hold_bars: int = 390
    # ----- Regime-flip exit primitive (Part 8.9 V2 + 8.10 Kalman, 2026-06-02) -----
    # Closes a position when the smoothed HMM state flips against direction,
    # but ONLY if the trade is currently underwater. V1 prototype showed raw
    # flip kills winners; V2 with the drawdown precondition gains +1.9pp
    # realized CAGR and +2.2pp MC p5 on PAXG-class assets.
    # Per-symbol gating via REGIME_FLIP_EXIT_SYMBOLS in this module — defaults
    # to NOT firing on SPY/GLD/DIA/QQQ (NYSE-hours assets don't have the leak).
    use_regime_flip_exit: bool = True
    regime_flip_min_hold_bars: int = 3
    regime_flip_dd_threshold: float = -0.02   # only fire if unrealized return ≤ this
    # ----- HMM meta-layer (SESSION_LOG #22 — re-bound from #6/#7) -----
    # Repurposed from a destructive entry gate into a sizing + pyramid
    # controller. NEVER blocks entries (pure deterministic structure decides
    # trade permission). Parameter values come from #7 — the last point they
    # were measured — and must not be retuned for the #22 comparison.
    #
    #   P_bull > size_threshold_high   → size_mult_high   (2.0×)
    #   size_threshold_low ≤ P_bull ≤ size_threshold_high → size_mult_normal (1.0×)
    #   P_bull < size_threshold_low    → size_mult_low    (0.5×)
    #
    # Regime disagreement (bullish det. regime AND P_bull < disagreement
    # threshold, or symmetric for bearish) → pyramid cap forced to 0.
    use_hmm_meta: bool = False               # OFF = #21 production default (config A)
    hmm_warmup_pass_through: bool = True      # NaN P_bull during 6mo warmup → 1.0×
    size_threshold_high: float = 0.70        # == pyramid_aggressive_p_bull
    size_threshold_low: float = 0.30
    size_mult_high: float = 2.0
    size_mult_normal: float = 1.0
    size_mult_low: float = 0.5
    disagreement_p_bull_threshold: float = 0.30
    disagreement_p_bear_threshold: float = 0.30
    pyramid_aggressive_p_bull: float = 0.70


@dataclass
class BreakoutStratConfig:
    name: str = "breakout"
    # Entry filters
    lookback_bars: int = 20
    rvol_long_min: float = 1.2
    rvol_short_min: float = 1.3
    vol_ratio_min: float = 1.2
    hmm_long_min: float = 0.60
    hmm_short_min: float = 0.60
    # Pyramiding gates
    pyramid_hmm_min: float = 0.75
    pyramid_requires_rvol_rising: bool = True
    max_pyramid_positions: int = 5
    # Sizing
    base_size_pct: float = 0.20
    capital_cap_pct: float = 0.30
    # Exit profile (asymmetric runner)
    stop_loss_pct: float = 0.03
    partial_tp_pct: float = 0.06
    partial_tp_size: float = 0.30
    final_tp_pct: float = 0.15
    final_tp_size: float = 0.40          # 30% remains as runner
    move_stop_to_be_after_partial: bool = False
    trailing_stop_enabled: bool = True
    trailing_logic_type: str = "ema_50"
    trailing_starts_at: str = "immediately"
    max_hold_bars: int = 200             # shorter than pullback


@dataclass
class MeanRevExtremesStratConfig:
    """
    Path 2 — Mean-Reversion-on-Extremes.

    Fires on deep dips inside a bullish deterministic regime, when statistical
    reversion is high-probability. Designed to be uncorrelated with the
    pullback engine (which fires near the EMA, not far below it).
    """
    name: str = "meanrev"
    # ----- Entry filters -----
    deviation_threshold: float = -0.012      # Close must be ≥1.2% below EMA
    require_close_below_sma: bool = True     # additional dip confirmation
    require_intrabar_buying: bool = True     # close in upper part of the bar
    intrabar_threshold: float = 0.4          # Close ≥ Low + 0.4 × (High − Low)
    # Regime gate (deterministic only; HMM is meta layer)
    long_regimes: tuple = ("growth", "slowdown")
    # ----- HMM meta layer (same convention as pullback) -----
    # use_hmm_meta lets config C ("no HMM layer") neutralise the meta layer
    # for an apples-to-apples comparison. #8 values otherwise unchanged.
    use_hmm_meta: bool = True
    hmm_warmup_pass_through: bool = True
    size_threshold_high: float = 0.70
    size_threshold_low: float = 0.30
    size_mult_high: float = 1.5
    size_mult_normal: float = 1.0
    size_mult_low: float = 0.5
    disagreement_p_bull_threshold: float = 0.30
    # ----- Pyramiding -----
    pyramid_regimes: tuple = ("growth", "slowdown")
    max_pyramid_positions: int = 3           # mean-rev needs less stacking
    # ----- Sizing / capital -----
    base_size_pct: float = 0.20
    capital_cap_pct: float = 0.30
    # ----- Exit profile (shorter horizon than pullback) -----
    stop_loss_pct: float = 0.020
    partial_tp_pct: float = 0.025
    partial_tp_size: float = 0.50
    final_tp_pct: float = 0.05
    final_tp_size: float = 0.50
    move_stop_to_be_after_partial: bool = True
    trailing_stop_enabled: bool = False      # mean-rev exits at fixed targets
    trailing_logic_type: str = "ema_50"
    trailing_starts_at: str = "after_final"
    max_hold_bars: int = 60                  # ~1.5 weeks on 1h


@dataclass
class TrendCarryConfig:
    """
    Layer 3 — Trend Capture Module (split-position carry strategy).

    Same alpha-engine entry logic as pullback (so the entries are quality)
    but routed through structural / ATR-trailing exits with much longer
    max-hold so a portion of every signal can ride macro directional legs.

    Activated only when `RegimeScore >= activation_score_threshold` so it
    contributes nothing during chop regimes. The pullback engine continues
    to run regardless — this strategy is purely additive.
    """
    name: str = "trend_carry"
    # ----- Activation gate (Layer 4 regime multiplier) -----
    activation_score_threshold: float = 0.50    # only trigger when RegimeScore ≥ this
    # ----- Entry filters (slightly looser than core pullback) -----
    pullback_band: float = 0.010
    imbalance_min: float = 0.00020
    # ----- Sizing / capital -----
    base_size_pct: float = 0.30                 # 0.12 × 2.5× leverage
    capital_cap_pct: float = 1.25               # 0.50 × 2.5× leverage
    max_pyramid_positions: int = 2
    fixed_size_mult: float = 1.0
    # ----- Pyramid gates (looser than pullback) -----
    pyramid_regimes: tuple = ("growth", "slowdown")
    pyramid_require_above_vwap: bool = True
    pyramid_require_positive_momentum: bool = True
    # ----- Vol targeting (mirrors pullback's flag) -----
    use_vol_targeting: bool = False
    # ----- VIX-conditional leverage (mirrors pullback's flag) -----
    use_vix_leverage: bool = False
    # ----- Cross-up early entry (mirrors pullback's flag) -----
    use_momentum_crossup: bool = False
    # ----- Regime-bypass entry (mirrors pullback's flag) -----
    use_regime_bypass: bool = False
    regime_bypass_threshold: float = 0.60
    # ----- Exit profile: STRUCTURAL ONLY (no premature TP harvesting) -----
    stop_loss_pct: float = 0.04                 # wider stop — survives normal vol
    partial_tp_pct: float = 0.08                # tiny partial only to lock in some
    partial_tp_size: float = 0.30               # close 30% at TP1
    final_tp_pct: float = 0.25                  # macro target +25%
    final_tp_size: float = 0.70                 # close remaining 70% at TP2
    move_stop_to_be_after_partial: bool = True
    trailing_stop_enabled: bool = True
    trailing_logic_type: str = "atr"
    trailing_starts_at: str = "after_partial"
    atr_multiplier: float = 3.0                 # generous breathing room
    max_hold_bars: int = 1500                   # ~9 months on 1h


PULLBACK = PullbackStratConfig()
BREAKOUT = BreakoutStratConfig()
MEANREV = MeanRevExtremesStratConfig()
TRENDCARRY = TrendCarryConfig()


# ---------------------------------------------------------------------------
# Orderflow-exhaustion engine (Part 8.27)
# Fades aggressive selling/buying when it exhausts vs trailing 200-bar
# distribution. Captures the +35bp/+33bp mean-reversion edge documented in
# Part 8.18 (lab) on 23+ symbols including MT5-aligned tickers.
# ---------------------------------------------------------------------------
@dataclass
class OrderflowStratConfig:
    name: str = "orderflow_exhaustion"
    # ----- entry: orderflow proxy windows -----
    cvd_slope_window: int = 20
    tick_window: int = 20
    percentile_window: int = 200
    exhaustion_pct_low: float = 0.20    # bottom 20% = sell exhausted
    exhaustion_pct_high: float = 0.80   # top 20%    = buy exhausted
    # ----- entry: context filters -----
    min_liquidity_frac: float = 0.5     # Vol > 0.5 × MA(20)
    freefall_pad_pct: float = 0.005     # 0.5% pad away from 50-bar extreme
    rsi_floor: float = 25.0             # below this = deep-crisis tape, skip
    rsi_ceiling: float = 75.0           # above this = euphoria, skip shorts on a/symbol
    # ----- sizing -----
    base_size_pct: float = 0.15         # half of pullback (smaller edge per trade)
    capital_cap_pct: float = 0.60       # less stacking than pullback's 1.00
    max_pyramid_positions: int = 1      # single-shot only
    # ----- exit ladder (tighter than pullback because edge is smaller) -----
    stop_loss_pct: float = 0.015        # -1.5%
    partial_tp_pct: float = 0.010       # +1.0% close 50%, stop -> BE
    partial_tp_size: float = 0.50
    final_tp_pct: float = 0.025         # +2.5% close remainder
    final_tp_size: float = 0.50
    move_stop_to_be_after_partial: bool = True
    trailing_stop_enabled: bool = False  # pure structural ladder
    trailing_logic_type: str = "ema_50"
    trailing_starts_at: str = "after_partial"
    max_hold_bars: int = 20              # MUCH shorter than pullback's 390


ORDERFLOW = OrderflowStratConfig()


@dataclass
class NewsFilterConfig:
    """
    Macro-sanity news filter — warn-only context check, not a trade gate.

    Consumed by core/news_macro.py. The live signal terminal calls
    `print_news_warning(side)` before printing a trigger; the engine
    is never blocked. NewsAPI is opt-in: set the NEWSAPI_KEY env var
    to enable it; without the key only RSS feeds are used.
    """
    # RSS sources (free, no key). Stdlib XML parser handles all of these.
    rss_feeds: dict = field(default_factory=lambda: {
        "bbc_world":     "http://feeds.bbci.co.uk/news/world/rss.xml",
        "yahoo_finance": "https://finance.yahoo.com/news/rssindex",
        "cnbc_top":      "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "marketwatch":   "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    })
    per_feed_limit: int = 25
    # NewsAPI free tier (~100 reqs/day). Set NEWSAPI_KEY env var.
    use_newsapi: bool = True
    newsapi_limit: int = 30
    # Verdict logic: RISK_OFF if off_score >= on_score + margin (and v.v.).
    # Higher margin = stickier NEUTRAL; lower = more sensitive.
    verdict_margin: int = 2
    # Cache TTL — avoid hammering RSS every signal tick.
    cache_seconds: int = 1800   # 30 minutes


NEWS_FILTER = NewsFilterConfig()


@dataclass
class CryptoCarryConfig:
    """
    Delta-neutral crypto funding-rate carry.

    Validated 2026-05-25 on BTCUSDT, ETHUSDT, SOLUSDT (Sharpe 5–12, DD <2%,
    CAGR 5.5–7.5%) using Binance perp funding-rate history since Dec 2023.
    BNB excluded — its funding is structurally negative (shorts pay longs).

    Strategy: hold long spot + short perp in equal notional. Earn the
    funding rate every 8h when positive; pay it when negative. Net of
    fees on majors ≈ 5–7%/yr annualized with near-zero directional risk.

    The dashboard surfaces current annualized yield per symbol; Discord
    fires a notification when 8h funding rate exceeds `alert_8h_threshold`
    (default 0.03% per 8h ≈ 33% annualized — extreme crowding).
    """
    enabled: bool = True
    symbols: tuple = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    # Window for computing the trailing "recent" yield shown on the dashboard.
    recent_lookback_days: int = 7
    # 8h funding rate above which to fire a Discord alert (decimal).
    # 0.0003 = 0.03%/8h ≈ 33% annualized.
    alert_8h_threshold: float = 0.0003
    # Per-symbol position size cap as fraction of carry budget.
    # Cap mostly serves as documentation — real execution is up to the user.
    base_size_pct: float = 0.33


CRYPTO_CARRY = CryptoCarryConfig()


# ---------------------------------------------------------------------------
# Per-symbol regime filters (core/regime_filter.py).
#
# For 24/7 perp symbols where the NYSE-tuned engine fires on chop bars,
# restrict signals to known-tradable regimes. Validated via
# `_research_paxg_regime` — see SESSION_LOG entry for the parameter sweep.
#
# Filter kinds:
#   NONE             pass through (default for unlisted symbols)
#   NYSE_ONLY        13:00–20:00 UTC only
#   NO_ASIA          skip 00:00–07:00 UTC
#   ADX_25           ADX(14) ≥ 25 (trend strength)
#   ADX_25_NYSE      ADX≥25 AND NYSE hours  (COMBO_F — PAXG default)
#   ADX_25_NO_ASIA   ADX≥25 AND skip Asia
#
# Symbols not in the dict pass through unfiltered. Stocks (SPY/DIA/GLD)
# already have NYSE-hours data so they don't need a filter.
# ---------------------------------------------------------------------------
REGIME_FILTERS: dict = {
    # GC=F: gold spot FX (24/5). Inherits PAXG's regime filter heuristic
    # since gold spot off-hours liquidity has the same chop characteristics
    # the COMBO_E filter was tuned for.
    "GC=F": "ADX_25_NO_ASIA_SLOPE",
}


# ---------------------------------------------------------------------------
# Per-symbol regime-flip exit gating (Part 8.9 V2 + 8.10 Kalman).
# Only listed symbols will have the regime-flip exit primitive applied.
# SPY/GLD/DIA/QQQ do NOT have the time-stop leak that motivated this rule —
# applying it to them regressed CAGR by 3-25pp in MC. Restrict to gold
# perp / gold spot class only.
# ---------------------------------------------------------------------------
REGIME_FLIP_EXIT_SYMBOLS: set = {"GC=F"}


# ---------------------------------------------------------------------------
# Trading-venue label map (Part 8.16).
# The signal engine runs on yfinance proxy tickers (clean PF, NYSE-hours
# microstructure). The user executes on MT5 CFDs. SPY ↔ US500, QQQ ↔ US100,
# GLD ↔ XAUUSD all track the same underlying within basis points, so a
# signal on the proxy is directly executable on the MT5 CFD at the same
# instant. The dashboard / Discord card surfaces the MT5 label so the user
# reads "US500 BUY" instead of "SPY BUY".
# ---------------------------------------------------------------------------
TRADING_LABEL_MAP: dict = {
    "SPY":   "US500",
    "^NDX":  "US100",        # Nasdaq-100 cash index (Part 8.22 swap from QQQ)
    "GLD":   "XAUUSD",
    "GC=F":  "XAUUSD",       # cross-confirm gold via 23/5 futures
}


def trade_label(symbol: str) -> str:
    """Return the MT5-execution label for a signal-generation symbol.
    Falls back to the symbol itself if no mapping exists."""
    return TRADING_LABEL_MAP.get(symbol, symbol)



# Singletons used across modules.
DATA = DataConfig()
INDICATORS = IndicatorConfig()
REGIME = RegimeConfig()
STRATEGY = StrategyConfig()
BACKTEST = BacktestConfig()
# PULLBACK and BREAKOUT are defined above (dual-strategy block).
