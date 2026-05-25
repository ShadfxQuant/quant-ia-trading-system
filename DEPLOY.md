# Deploy the Quant IA dashboard as a website

Stack: **Streamlit web** + **worker.py background loop** + **TradingView widget**.
Two processes, one repo. No database needed.

```
┌─────────────────────────────┐  every 10 min   ┌──────────────────────┐
│  worker.py                  │ ──────────────▶│  data/state.json     │
│  (yfinance + engine + news) │   atomic write  │  (~130 KB snapshot)  │
└─────────────────────────────┘                 └──────────┬───────────┘
                                                           │ read (<50 ms)
                                                ┌──────────▼───────────┐
                                                │  dashboard.py        │
                                                │  (Streamlit web app) │
                                                └──────────────────────┘
```

---

## 1. Local test (5 min)

```bash
cd ~/Desktop/quant_ia_trading_system
pip install -r requirements.txt

# Terminal 1 — start the background worker (writes data/state.json every 10 min)
python3 -m worker --interval 600

# Terminal 2 — start the web dashboard
streamlit run dashboard.py
```

Open <http://localhost:8501>. You'll see `Data source: WORKER (snapshot @ ...)` in the
header. Stop the worker — refresh — you'll see `Data source: LIVE (web process compute)`
(slow, but works). That's the auto-fallback in action.

---

## 2. Lock down for friends (URL token)

```bash
export DASH_TOKEN="pick-anything-here"
streamlit run dashboard.py
```

Anyone hitting `http://localhost:8501` without `?token=pick-anything-here` sees a
🔒 lock screen. The token can be any string — it's just a shared secret.

---

## 3. Free public hosting — Streamlit Community Cloud

Web (dashboard) and worker need to live in different places — Streamlit Cloud only
hosts the web. Two patterns, pick one:

### Pattern A — Cheapest: worker on your laptop, web on the cloud

1. Push the repo to GitHub (private repo is fine — Cloud reads private repos).
2. Go to <https://share.streamlit.io> → **New app** → pick the repo → main file = `dashboard.py`.
3. App settings → **Secrets** → paste:
   ```toml
   NEWSAPI_KEY = "your_key_or_empty"
   DASH_TOKEN  = "pick-anything-here"
   ```
4. Deploy. You get a public URL like `https://quant-ia-trading.streamlit.app`.
5. On your laptop, keep `python3 -m worker --interval 600` running, **and have it
   commit `data/state.json` to GitHub every 10 min** so the cloud picks it up:
   ```bash
   # cron-style helper (Mac/Linux): poll-and-push every 10 min
   while true; do
     python3 -m worker --once
     git add data/state.json && git commit -m "snapshot" --quiet && git push --quiet
     sleep 600
   done
   ```
   Trade-off: data is only as fresh as the last push.

### Pattern B — Cleanest: worker on a $5/mo VPS

Same as A, but the worker runs on a small Linux VPS (Hetzner CX11 €4/mo, DigitalOcean
$4/mo droplet, Oracle Free Tier $0/mo) and pushes `data/state.json` to GitHub.
Your laptop can sleep. The web stays on Streamlit Cloud.

```bash
# On the VPS, in /opt/quant_ia:
git clone <your-repo>
cd quant_ia_trading_system
pip install -r requirements.txt
# Run forever under systemd or tmux:
nohup python3 -m worker --interval 600 > worker.log 2>&1 &
```

Add the same git-push loop from Pattern A so the snapshot reaches Streamlit Cloud.

### Pattern C — All on one box (DigitalOcean / Render / Fly.io)

Run **both** worker and Streamlit on a single $5/mo droplet. No git-push needed
(both processes share the same `data/state.json` on disk).

```bash
# install
git clone <repo> && cd quant_ia_trading_system && pip install -r requirements.txt

# tmux session 1: worker
tmux new -s worker  -d "python3 -m worker --interval 600"

# tmux session 2: web, listening on 0.0.0.0:8501
tmux new -s web     -d "streamlit run dashboard.py --server.port 8501 --server.address 0.0.0.0"
```

Point a domain (or just use the droplet IP) at `:8501`. Use Caddy/nginx for TLS
if you want HTTPS — Caddy auto-issues Let's Encrypt:
```caddy
quant.yourdomain.com {
  reverse_proxy localhost:8501
}
```

---

## 4. TradingView integration — what's included

The dashboard embeds the **TradingView Advanced Chart widget** (free, no key).
Pre-loaded studies on the embedded chart:

- **VWAP** (anchored, intraday)
- **Volume** (per-bar volume bars)
- **EMA** (defaults to 9, but user can change to 50 to match the engine)
- **SMA** (user can set to 130)

Symbol maps automatically: `SPY → AMEX:SPY`, `DIA → AMEX:DIA`, `QQQ → NASDAQ:QQQ`,
`IWM → AMEX:IWM`, etc. Edit `TV_SYMBOL_MAP` in `dashboard.py` to add more.

### Volume Profile

TradingView's true **Volume Profile (Visible Range)** indicator is **Pro-only**
($14.95/mo). Two free alternatives, both shipped:

1. **The TV widget** — viewers can manually add `Volume` and use the `Sessions`
   indicator from the chart's `+ Indicators` button. Full Volume Profile still
   requires Pro.
2. **The Python-computed volume profile section** — bins the last 400 bars of
   1h data into N price buckets, distributes each bar's volume across the bins
   its [low, high] range covers, and computes:
   - **POC** (Point of Control) — the price bin with the most volume
   - **Value Area High/Low** — the smallest set of bins containing 70% of total volume

This is the same data TradingView's Pro chart shows, just computed from yfinance
1h data instead of every tick. Good enough for context.

---

## 5. Disclaimer (don't skip this)

A persistent banner on every page load:

> **Educational only — not investment advice. Past performance is not indicative
> of future results.**

If you ever charge users or solicit money to manage their funds, that disclaimer
**does not** cover you — you need a real legal opinion (US: RIA registration; EU:
MiFID II; UK: FCA). For "me + a few friends, free to view," the disclaimer is
sufficient for personal/educational sharing.

---

## 6. Cost summary

| Setup | Monthly cost |
|---|---|
| Local-only (laptop) | $0 |
| Pattern A (laptop worker + Streamlit Cloud web) | $0 |
| Pattern B ($5 VPS worker + Streamlit Cloud web) | $4–5 |
| Pattern C (single $5 VPS, both processes) | $4–5 |
| Domain name (optional) | ~$1/mo |
| NewsAPI free tier | $0 (100 reqs/day cap — enough at 10-min cadence) |
| TradingView widget | $0 (free public widget) |

---

## 7. What people see when they load the page

1. 🟢/🔴/⚪ macro verdict + theme-hit headlines
2. Latest signal card (pullback + trend-carry) with macro-mismatch chip
3. Pyramid gate status
4. **TradingView Advanced Chart** — full interactive chart, VWAP + Volume +
   EMA/SMA, allow_symbol_change so they can flip to other tickers
5. **Python-computed Volume Profile** — POC + Value Area, bin slider
6. Last 20 journal trades + closed-leg win rate + realised PnL

Total page load with worker mode: <100 ms.
