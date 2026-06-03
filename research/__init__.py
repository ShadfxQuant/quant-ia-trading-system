"""
Research / edge-mining package — strictly isolated from the live engine.

This directory NEVER imports from execution/, strategies/, or worker.py.
It only reads from core/data_loader.py and core/indicators.py (read-only).
Nothing in here can affect production behavior; all outputs go to
research/results/ as JSON and CSV.

The framework:
  - edge_lab.py     — harness: takes an EdgeDef, computes hit-rate,
                      expectancy, t-stat, Sharpe over multiple forward
                      horizons. Mines BOTH directions (long + inverted)
                      because high negative-edges are equally valuable —
                      flip them.
  - edge_library.py — 30+ EdgeDef instances across categories: time-of-day,
                      volatility regime, momentum, mean-reversion, volume,
                      cross-asset, gamma proxies, orderflow proxies.
  - proxies.py      — gamma-exposure and orderflow proxy computations
                      (since we don't have options chain or L2 data).
  - dashboard_research.py — separate Streamlit app, port 8502 (vs main 8501).
"""
