"""Quick PAXG signal check — run with `python3 _check_paxg.py`."""
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from core.regime_filter import apply_regime_filter

df = apply_regime_filter(prepare_dual(load_symbol("PAXGUSDT")), "PAXGUSDT")
print(df[["Close", "pullback_Signal", "trend_carry_Signal", "regime_eligible"]].tail(20))
print(f"\neligible bars: {df.regime_eligible.mean()*100:.1f}%")
print(f"pullback signals (last 500h): {int((df.pullback_Signal != 0).tail(500).sum())}")
print(f"trend    signals (last 500h): {int((df.trend_carry_Signal != 0).tail(500).sum())}")
