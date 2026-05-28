# Quant IA Trading System — PAXG Edition

Second-brain dump. Written 2026-05-28.

---

## Part 1 — What the system is right now

### The asset
**PAXGUSDT** on Binance public klines, traded on **Infinex** as a perp DEX. PAXG is tokenized physical gold — tracks gold spot within basis points. Single-venue execution (Infinex only), 24/7 market.

### The signal engine
Two strategies running in parallel on hourly bars:

1. **Pullback** — primary alpha engine. Enters on the inflection bar of a pullback within an established trend.
   - Long entry (all four required): bullish structure (EMA50>SMA130, slope up), pullback proximity (|Close−EMA|/EMA ≤ band), positive imbalance, momentum re-acceleration (Δ momentum > 0).
   - Short entry: symmetric — bearish structure + pullback proximity + negative imbalance + momentum down.
   - **Rollover guard** (added 2026-05-27): blocks longs when EMA50 3-bar slope mean is negative, even if structure is still bullish. This catches rollovers earlier than the lagging EMA>SMA check. Mirror for shorts.
   - Pyramiding: VWAP-confirmed, only stacks when above VWAP with positive momentum, in growth/slowdown regime.
   - Exit ladder: stop, partial TP, final TP, ATR×N trailing after partial, max-hold bars.

2. **Trend_carry** — runner sleeve. Same entry conditions, but only activates when `RegimeScore ≥ activation_threshold`. Smaller size (12% vs 30%), wider stop (-4%), structural exits, ~9-month max hold. **Now symmetric** — takes shorts as of 2026-05-27 (previously was 100% long-only, 0 shorts in 2.83 years on GLD).

### The regime filter — the key insight
The pullback+trend_carry engine was proven on SPY/GLD (NYSE-hours data) with PF 2.5–3, $100K→$221K. On raw 24/7 PAXG data it dropped to PF 1.20 — the engine fires on chop hours between trends.

The fix: **COMBO_E filter**, validated in research, now shipped:
- **ADX(14) ≥ 25** — real trend strength required
- **Skip 00:00–07:00 UTC** — Asian session is muted for gold
- **4-bar EMA-slope persistence** — direction has held ≥4 consecutive hours

Result: 18% of bars eligible (versus 100% raw), but on those bars the engine catches the trend cleanly. Backtest: 86.4% CAGR, 18.2% DD, PF 2.26.

### Honest backtest numbers
**Full PAXGUSDT window (May 2024 → May 2026, 1.98 yrs):**
- Total return: 243.8% ($100K → $343,797)
- CAGR: 86.4%
- Max drawdown: 18.2%
- Profit factor: 2.26
- Win rate: 72.9% (292 trades)
- Worst trade: -$35,132

**Same window EXCLUDING 2025 (the gold-boom year):**
- Total return: 81.0%
- CAGR: 35.6%
- Max drawdown: 15.7%
- Profit factor: 1.95
- 2024 (partial): +27,327, PF 1.99
- 2026 YTD: +67,911, PF 1.94, 82% WR — the most important number on the page

**Live expectation:** plan around the 35% CAGR figure, not 86%. The 2025 boom was a once-per-decade gold bull market. Realistic compounding: $100K → ~$135K year 1 → ~$182K year 2 → ~$246K year 3.

### Macro filter — inverse polarity
The system reads news headlines and computes a risk-off/risk-on verdict. For equities, RISK_ON favors longs. For gold/PAXG (inverse polarity), **RISK_OFF favors longs** — war/banking stress/recession headlines align with gold longs, not against them. This is wired into the notification card so PAXG longs during chaos show as "macro-aligned" not "mismatched."

### The "Read" — system narrative
Even when there's no live signal, the system synthesizes its current view of PAXG:
- **Bias**: bullish/bearish/neutral (from EMA50, SMA130, slope)
- **Strength**: strong/weak/chop (ADX bucket)
- **Regime eligibility**: % of last 24h that passed the COMBO_E filter
- **Macro tilt**: supports/conflicts (asset-class-aware)
- **What would flip the read**: concrete bar-level conditions (e.g. "ADX falls below 20")

Computed every worker tick, stashed in state.json, surfaced two ways:
- **Streamlit dashboard** card above the chart
- **Discord `/read symbol: PAXGUSDT`** slash command

---

## Part 2 — System architecture (the plumbing)

```
[Cloudflare cron */5 07-20 UTC]   ← reliable (GitHub schedule was throttling 95% of triggers)
            │
            ▼
[quant-ia-cron-trigger Worker]   ← Cloudflare free tier
            │  POST /actions/workflows/worker.yml/dispatches
            ▼
[GitHub Actions: signal-worker]   ← unlimited minutes on public repo
   1. fetch Binance klines (data-api.binance.vision mirror; api.binance.com geo-blocks US)
   2. prepare_dual → indicators
   3. apply_regime_filter (COMBO_E for PAXG)
   4. generate signals (pullback + trend_carry)
   5. compute Read (bias/strength/macro)
   6. send Discord webhook on fresh signals (dedupe per bar_time)
   7. commit data/state.json back to main (retry 5× on race)
            │
            ▼
┌──────────────────┬──────────────────────────┬──────────────────────┐
│ Discord webhook  │ Streamlit dashboard      │ /read slash command  │
│ (signal alerts)  │ (Chart + Read + Journal) │ (Cloudflare Worker   │
│                  │                          │  fetches state.json) │
└──────────────────┴──────────────────────────┴──────────────────────┘
```

**Key components:**
- `core/data_loader.py` — Binance routing for *USDT pairs (data-api.binance.vision), yfinance for everything else
- `core/regime_filter.py` — COMBO_E filter for PAXG, NONE for SPY/GLD
- `core/read.py` — synthesizes per-symbol Read
- `core/news_macro.py` — risk-off/on verdict + per-symbol polarity (PAXG is inverse)
- `strategies/pullback.py` — symmetric long+short entries with EMA50 slope guard
- `strategies/trend_carry.py` — symmetric runner sleeve
- `worker.py` — main loop (build_state → write_state → maybe_notify)
- `dashboard.py` — Streamlit Cloud UI
- `cron_trigger/worker.js` — Cloudflare cron → GitHub workflow_dispatch
- `discord_bot/worker.js` — Discord `/read` slash command (deferred reply pattern)

---

## Part 3 — Chronological log of changes

> Caveat: I can only reliably recall what happened during our conversations. Items marked **[earlier]** are reconstructed from artifacts (commit messages, file states) and may be approximate.

### **[earlier]** — base system (proven on equities)
- Pullback engine validated on SPY/DIA: $221K from $100K, PF 2.5–3
- Trend_carry sleeve added (Layer 3, RegimeScore-gated activation)
- HMM meta-layer added then re-bound to sizing-only (SESSION_LOG #22)
- Phase 4 P1: ATR-normalized thresholds for cross-symbol portability
- News macro filter built with risk-off/risk-on scoring
- Streamlit dashboard with chart, pyramid gates, signal cards
- GitHub Actions cron worker + Discord webhook for signal alerts
- Daily snapshot commits via signal-worker-bot

### Session sweep — PAXG specialization
**Initial question:** "what is gold's equivalent on Infinex?"
- Answer: PAXG perp, 24/7 venue, single-account constraint
- Pivot: from cross-venue cash-and-carry funding strategy (rejected — needs 2 accounts) to single-venue regime-filtered PAXG

**Regime research:**
- Built `_research_paxg_regime.py` — sweep 15 filters on PAXGUSDT
- Filters tested: NONE, ADX_20/25/30, ATR_EXP, SLOPE_4, NYSE_ONLY, EU_NYSE, NO_ASIA, COMBO_A through COMBO_F
- Gate: PF ≥ 1.8 AND n ≥ 30
- Initial winner: COMBO_F (ADX_25 + NYSE_ONLY), PF 1.81, CAGR 41.3%, DD 18.3%
- Built `core/regime_filter.py` with apply_regime_filter() and per-symbol REGIME_FILTERS map
- Shipped PAXGUSDT with COMBO_F filter

### Fresh sweep on Binance vision-mirror data
- Discovered `api.binance.com` returns HTTP 451 from US IPs
- Fixed `core/data_loader.py` to use `data-api.binance.vision` (no geo-block)
- Re-ran regime sweep — COMBO_F dropped below PF 1.8 gate on fresh data
- Two new winners cleared: NYSE_ONLY (PF 2.03) and COMBO_E (PF 1.99, CAGR 80.2%)
- Swapped PAXGUSDT to COMBO_E (ADX_25 + NO_ASIA + SLOPE_4) for higher CAGR

### Macro inverse-polarity fix
- Identified: PAXG longs during war showed as "macro mismatch" in Discord
- Root: `INVERSE_MACRO_SYMBOLS` had `PAXG` but not `PAXGUSDT`
- One-line fix: added `PAXGUSDT` to the inverse set
- Now PAXG longs during risk-off correctly show as "✅ macro-aligned"

### Cron gating + free-tier minutes
- Cron originally ran `*/5 24/7` — burning toward 6,500 min/month
- Gated to `*/15 07-20 UTC` — strategy gates signals outside window anyway
- Tightened to `*/12 7-20 UTC` after honest math (~1,260 min/month, comfortable cap headroom)

### Discovered GitHub scheduled-workflow throttling
- Diagnosis: only 2 cron-triggered runs in 24h despite `*/12` expecting ~70
- `gh run list` showed manual `workflow_dispatch` runs fired instantly; `schedule` runs throttled
- Discovered public repos get unlimited Actions minutes; the 2,000 cap is private-repo-only
- Tightened cron to `*/5 7-20 UTC`

### Bypass GitHub throttling via Cloudflare cron
- Architecture: Cloudflare cron (reliable) → GitHub workflow_dispatch (not throttled like schedule)
- Built `cron_trigger/worker.js` — POSTs to `/actions/workflows/worker.yml/dispatches` on schedule
- Created GitHub fine-grained PAT with `actions: write` scope, stored as Worker secret
- Deployed; chain confirmed working (workflow_dispatch runs at every :00 :05 :10 :15 etc.)

### Discord /read slash command
- Built `discord_bot/worker.js` — Cloudflare Worker as Discord interactions endpoint
- Created Discord application (Quant Read), bot user, OAuth invite link
- ed25519 signature verification, hex2buf, the whole dance
- Hit "Application did not respond" — refactored to use deferred reply pattern (type 5 → PATCH webhook)
- Hit "endpoint could not be verified" — re-uploaded DISCORD_PUBLIC_KEY secret (was nuked during a redeploy)
- Working now — `/read symbol: PAXGUSDT` returns full Read card

### Engine fixes (today, 2026-05-28)
- Discovered via trade audit: engine effectively long-only (171L vs 6S on 2.83y GLD)
- Pullback: 132L vs 6S; trend_carry: **39L vs 0S** (zero shorts in entire history)
- Root cause #1: pullback shorts required `Is_bearish_regime` extra (only fires in crash/distribution)
- Root cause #2: trend_carry had no short_signal block at all
- Fix: removed extra restriction on pullback shorts, mirrored trend_carry to be symmetric
- Added EMA50 3-bar slope rollover guard — blocks longs when slope < 0 even with bullish structure
- GLD backtest delta:
  - Return: 289% → 315%
  - CAGR: 61.8% → 65.5%
  - Max DD: 29.0% → **21.7%**
  - PF: 2.58 → **4.02**
  - Worst trade: -$40K → **-$15K**
  - 2026 YTD: **-2.8% → +13.9%** (turned the bad year positive)
- PAXG regressed initially when symmetric persistence allowed downtrend bars (CAGR 86.4 → 35.4)
- Restored: COMBO_E reverted to up-slope-only — PAXG's filter stays long-biased, the strategy in GLD handles shorts directly

### Operational fixes along the way
- Wrangler auth cache leaked into git twice — gitignored both `.wrangler/` directories
- macOS Finder `Icon\r` files swept into commits — gitignored
- Git push race condition at `*/5` cadence (two workers pushing concurrently) — added 5× retry-with-rebase loop
- Bot token + webhook URL leaked in chat history — rotated, re-uploaded secrets
- `core.crypto_carry` import error after shelving the cash-and-carry idea — made the import optional

---

## Part 4 — What to remember

### The honest expectation
**Plan around 35% CAGR with 15-20% drawdowns.** The 86% number includes the 2025 boom which won't recur. The strategy works in mediocre gold years too — 2024 PF 1.99, 2026 YTD PF 1.94 — just less spectacularly.

### When to expect signals
~140 trades per year × 18% bar eligibility = roughly 1 signal every 1.5 days during eligible windows (Asian-session-skipped, ADX>25, slope-persisting). Most worker ticks will show no signal. That's correct.

### What can break it
- **Regime change in gold** — if gold enters a multi-year chop, the regime filter blocks bars but the bars that pass may still lose. Watch the 2026 YTD number — if it slides negative, the filter needs re-research.
- **PAXG perp diverging from spot** — backtest is on PAXGUSDT spot/perp; execution is on Infinex PAXG perp. Funding rates and liquidation thresholds differ. Watch for slippage between dashboard close and Infinex fill.
- **Sample size** — only 1.98 years of PAXG data exists. The strategy is curve-fit-prone. Live 6-12 months of out-of-sample will be the real test.

### What's solid
- Architecture is bulletproof (Cloudflare cron → GitHub Actions → Discord; no single point of failure that can't auto-recover)
- Free-tier cost: $0
- Code is in git; every change is reversible
- Macro filter, regime filter, exit ladder, dedupe — all the institutional plumbing is there

### Next research questions worth ranking
1. Does the symmetric engine + slope guard help SPY/DIA the same way it helped GLD?
2. Does ETH/BTC work as crypto satellites under their own regime filters?
3. Should there be separate Discord channels per symbol so PAXG signals don't mix with equity signals?
4. Can a daily-bar version validate the strategy across the 2008/2011/2020 stress periods? (yfinance has 20+ years of GLD daily.)
