"""
Shared ExitProfile contract.

Both pullback and breakout strategies emit signals that the portfolio
backtester executes against a common exit specification. Consolidating
this contract here keeps the strategies free of execution mechanics and
the backtester free of strategy-specific knowledge.

All percentages are fractions of the entry price (e.g., 0.025 = 2.5%).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExitProfile:
    # ----- Hard stop -----
    stop_loss_pct: float

    # ----- Profit ladder -----
    partial_tp_pct: float                 # first target (e.g., +4%)
    partial_tp_size: float                # fraction of qty_initial closed at TP1
    final_tp_pct: float                   # final fixed target (e.g., +10% / +15%)
    final_tp_size: float                  # fraction of qty_initial closed at TP2

    # ----- Stop management -----
    move_stop_to_be_after_partial: bool   # pullback: True, breakout: False

    # ----- Trailing stop -----
    trailing_stop_enabled: bool           # if False, runner closes at final_tp_pct
    trailing_logic_type: str              # "ema_50" | "atr" | "none"
    trailing_starts_at: str               # "immediately" | "after_partial" | "after_final"
    atr_multiplier: float = 2.0           # used only when trailing_logic_type == "atr"

    # ----- Time stop -----
    max_hold_bars: int = 390

    def runner_qty_fraction(self) -> float:
        """Whatever fraction of the initial qty remains after both fixed TPs."""
        return max(0.0, 1.0 - self.partial_tp_size - self.final_tp_size)
