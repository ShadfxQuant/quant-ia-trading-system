# Quant IA — Live Signals

A deterministic pullback engine on SPY and DIA (1-hour bars) that emits
**LONG / SHORT / flat** signals with a sized entry plan, a two-target exit
ladder, pyramid permission flags, and a macro-context sanity check.

> **Educational only. Not investment advice. Past performance is not
> indicative of future results.**

---

## What it does

- **Pullback alpha engine** — fires when EMA(50) > SMA(130) with a positive
  slope, a recent higher high, an ATR-normalized pullback within the trend,
  and momentum re-accelerating.
- **Trend-carry sleeve** — wider exits, longer holds, structurally similar
  entries.
- **Pyramiding** — VWAP-confirmed adds to an existing position when the
  regime stays favourable; capped per strategy.
- **Macro-sanity filter** — fetches free RSS headlines (BBC, Yahoo Finance,
  CNBC, MarketWatch) and optionally NewsAPI, classifies them into risk-on /
  risk-off themes, and tags any signal whose direction disagrees with the
  current macro mood.
- **Discord notifications** — every fresh signal triggers a webhook embed
  with size %, stop, TP1, TP2, R:R, macro context.
- **Web dashboard** — Streamlit page with the macro verdict, latest signal
  cards, pyramid gates, TradingView Advanced Chart (VWAP + Volume + EMA +
  SMA pre-loaded), Python-computed Volume Profile (POC + Value Area), and
  the recent trade journal.

All percentages are scale-invariant — the exit ladder applies to any size
book and to any instrument that mirrors SPY's percentage returns
(ETF, futures, perp, etc.).

---

## Backtest reference

In-sample SPY+DIA Sharpe-weighted book, ~147 weeks, $100K start, 2.5×
leverage, weak-gating production config:

| Metric | Value |
|---|---|
| Final equity | $221,244 |
| CAGR | 32.4% |
| Max drawdown | 12.9% |
| Sharpe (daily) | 1.45 |
| MAR | 2.51 |

Standard caveats apply: in-sample, single regime window, equity-grade
frictions. Live performance will be lower — expect roughly half of the
backtest CAGR with wider drawdowns. The edge has been stress-tested against
the HMM meta-layer and an alternate-strategy diversification stream, both
of which were falsified and removed (full log in `SESSION_LOG.md`).

---

## Architecture

```
┌──────────────────────────────┐  every ~5 min   ┌──────────────────────┐
│ GitHub Actions cron          │ ──────────────▶│  data/state.json     │
│ → worker.py                  │  atomic write   │  (~130 KB snapshot)  │
│   yfinance + engine + news   │  + Discord ping └──────────┬───────────┘
└──────────────────────────────┘                            │ read (<50 ms)
                                                 ┌──────────▼───────────┐
                                                 │  dashboard.py        │
                                                 │  (Streamlit web)     │
                                                 └──────────────────────┘
```

Worker dedupes notifications by `(symbol, strategy, side, bar_time)` so
the same bar never double-pings.

---

## Files

| Path | Role |
|---|---|
| `strategies/pullback.py` | Pullback alpha engine (deterministic) |
| `strategies/trend_carry.py` | Trend-carry sleeve |
| `core/news_macro.py` | Macro verdict from free RSS + NewsAPI |
| `core/notifier.py` | Discord webhook sender |
| `execution/portfolio.py` | Per-symbol backtester (pyramiding, exits) |
| `main_portfolio.py` | Data + indicator + signal pipeline |
| `live_signal.py` | CLI signal terminal |
| `worker.py` | Background loop → writes `data/state.json` + pings Discord |
| `dashboard.py` | Streamlit web dashboard |
| `.github/workflows/worker.yml` | Cloud cron (every 5 min) |
| `config/settings.py` | All tunables |
| `SESSION_LOG.md` | Internal research log (every variant tested) |
| `DEPLOY.md` | Step-by-step deployment guide |

---

## Quick start (local)

```bash
git clone https://github.com/<your-username>/quant-ia-trading-system.git
cd quant-ia-trading-system
pip install -r requirements.txt

# Terminal 1 — worker (writes data/state.json every 10 min)
python -m worker --interval 600

# Terminal 2 — dashboard
streamlit run dashboard.py
```

Open <http://localhost:8501>.

Optional environment variables:
- `DISCORD_WEBHOOK_URL` — fire signal notifications to Discord
- `NEWSAPI_KEY` — supplement the RSS feeds with ~30 extra business headlines
  per refresh
- `DASH_TOKEN` — lock the dashboard behind `?token=...` URL param

---

## Deploy

Full deployment guide in [`DEPLOY.md`](./DEPLOY.md) — covers Streamlit
Community Cloud + GitHub Actions cron (the recommended free stack) and
alternate paths on a $5/mo VPS.

---

## Stack

Pure Python. No external services beyond yfinance (data), Discord webhooks
(notifications), Streamlit Cloud (web hosting), and GitHub Actions (cron).
No paid data feeds, no API keys required for the engine to run.

---

## Licence

MIT, with a clear statement that this software is published for educational
and research purposes only. The authors and contributors make no
representations regarding suitability of these signals for live trading
and accept no liability for any losses incurred through use of this code.
