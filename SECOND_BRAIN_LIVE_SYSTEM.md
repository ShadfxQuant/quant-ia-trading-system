# Quant IA — Second Brain

> Short nodes, each a <1-min read. Full detail: `SYSTEM_LOG.md` (65 Parts) ·
> architecture: `research/SYSTEM_OVERVIEW.md`. Updated 2026-07-22.

---

## ⬡ Diagnostic & fixes — 2026-07-22
- **Ran full system audit; fixed & deployed everything mechanical + one validated gate.** Commits `a4bbc094` (GC=F drop + HMM log) → `d4f6a74b` (RSI gate).
- **Icon corruption was breaking git** (298 `Icon` files in `.git`/tree → `fetch`/`push` dead). Cleaned; git healthy. Recurring — if git breaks again: `find .git -name 'Icon*' -type f -delete`.
- **Deploy gap found**: GC=F removal + dashboard fixes were uncommitted → live worker was STILL running 4 symbols. Now committed + pushed; live runs 3 symbols.
- **HMM "not converging" spam** (~100 lines/run) silenced via `logging.getLogger("hmmlearn").setLevel(ERROR)` — came through logging, not warnings. Informational-only, no behavior change.
- **RSI entry gate wired live + VALIDATED** (see gate node below). Also brakes trend_carry.
- **Live truth**: cloud cron healthy (commits every ~1–2h, errors=none). Book was **−4.2% ($95,800)**, deeper than a stale local snapshot showed — the whipsaw + un-braked trend_carry short (6wks) drove it.
- **Still open (not done)**: correlation cap (unvalidated), Node20 runner deprecation (cosmetic), always-on button listener, Infinex bridge. Local clone can drift stale — cloud is source of truth.

## ⬡ What it is
- Systematic **1-hour trend-continuation** engine on liquid index/gold.
- Signals computed on clean data (SPY/^NDX/GLD), executed on MT5/Infinex CFDs.
- Autonomous alerts: Cloudflare cron → GitHub Actions → worker → Telegram. You tap to act.

## ⬡ Universe & execution
- SPY→US500 · ^NDX→US100 · GLD→XAUUSD. **3 symbols.** SLV dropped (no MT5 venue); **GC=F dropped 2026-06-26** (gold via GLD only — yfinance can't serve XAUUSD, and GC=F/GLD both → XAUUSD anyway).
- User trades **US500 + XAUUSD on Infinex** (perp DEX: 0.1% fee, fractional sizing, leverage).

## ⬡ Strategy — 2 sleeves
- **Pullback**: buy dips to EMA50 in confirmed uptrend (EMA50>SMA130, slope+, momentum re-accel, rollover guard). Symmetric short.
- **Trend_carry**: rides strong trends longer (wider stop/TP). ✅ **now RSI-braked** (2026-07-22 RSI entry gate applies to it too) — previously un-braked, which caused the worst paper loss (6-week short into an oversold rip).
- 3-state Gaussian HMM (Kalman-smoothed) reads regime → informs sizing. Never hard-gates baseline entries. (Regime-flip exit was GC=F-only → now OFF since GC=F dropped; not migrated to GLD — regressed it in MC.)

## ⬡ Exit ladder
- Stop −2.5% / TP1 +4% (close 50%, stop→BE) / TP2 +15% / time stop 390 bars (~16d).
- **GLD override (LIVE)**: TP1 +5% / TP2 +20% (gold trends run longer). Regime-flip exit: now OFF (was GC=F-only; GC=F dropped 2026-06-26).

## ⬡ Gates
- **RSI entry gate — LIVE 2026-07-22** (`use_rsi_entry_gate=True`): block shorts RSI<40, longs RSI>60, on BOTH pullback + trend_carry (in `prepare_dual`). Re-validated on SPY/^NDX/GLD: pooled PnL +$1,277, PF up on all 3, DD unchanged, 98.2% trades kept. This is also the **trend_carry brake**.
- **HMM veto** (was GC=F only, +$16.8k): **MOOT now GC=F is dropped.** Blanket veto = −$34k → stays rejected. No HMM veto on the remaining 3 symbols.

## ⬡ Double-trade overlay (validated MC, your idea)
- Strong (HMM conv>0.65) signal on SPY/^NDX + other index **trailing** → copy to laggard, 1.5× wider TP/SL, **15% size**.
- +$54k, PF 2.30. Block-bootstrap MC: +4pp CAGR, P(ruin) 0%, DD unchanged. Ship small; don't stack with high leverage.

## ⬡ Performance (gated, pooled, after friction)
- Per asset: SPY +52% · ^NDX +46% · GLD +138% (DD −6 to −15%). _(GC=F +35% dropped from live book 2026-06-26.)_
- **Pooled book: +248% / +68% CAGR / −9.7% DD** vs buy-hold basket +90% / −15.6%.
- Beats buy-hold on US500 & XAUUSD on **both** return and drawdown.

## ⬡ Leverage & the cliff
- MC: ~80% CAGR net at ~1.4× · 1.5× → +93% CAGR, DD −11% · 0% ruin to 2.5×.
- ⚠ Above ~3× the projections are fantasy; 10× = liquidations, **25× ≈ 93% blowup**. **Start at 1×.**

## ⬡ Notifications (LIVE)
- Cloudflare cron (every 5min, 7–20 UTC) → GitHub Actions → `worker --once` → Telegram (@QuantIAsignalsBot).
- Card: instrument · side · entry · stop · TP1/TP2 + ✅Took it / ❌Skip buttons.
- `telegram_listener.py` catches taps → journals decision. Does **NOT** auto-execute Infinex (needs bridge).
- Expect **~4–5 signals/week** across the 3 symbols (lumpy, clustered); ~2–3/wk if only US500+XAUUSD.

## ⬡ What's LIVE vs validated-only
- **LIVE**: universe = SPY/^NDX/GLD (SLV + GC=F dropped), engine (pullback + trend_carry, both **RSI-gated** 2026-07-22 + HMM-informational), GLD exits, Telegram alerts + cron. Regime-flip exit OFF.
- **Validated, NOT wired**: double-trade overlay, leverage knob. (RSI gate now LIVE; GC=F HMM veto moot.)

## ⬡ REJECTED — don't retry (all tested, all lose money)
- Conviction sizing, blanket HMM veto, regime suppression, volume filter, CVD/orderflow filter.
- Cross-sectional momentum, tangency optimization, Donchian/Turtle, vol-breakout engine.
- Universal exit retune, stocks/crypto expansion, naive ML classifier, shorter timeframes (<1h).
- **Rule**: engine wins by TRADING its signals, not filtering them. Only *adding* trades (double-trade) or *surgical* gates help.

## ⬡ Realistic expectations (honest)
- $100k @1× → ~$475k/3yr median (MC). @1.4× → ~80% CAGR.
- $700 on Infinex @1–1.5× → ~$10–50k/3yr **if edge holds**; crazy leverage = blowup.
- All MC is bull-favorable backtest, **not live-proven**. Paper-validate before sizing up.

## ⬡ Key files
- `strategies/`: pullback.py · trend_carry.py · pullback_vwap.py · `execution/portfolio.py`
- `worker.py` (cron scan) · `core/notifier.py` (Telegram/SMS/Discord) · `telegram_listener.py` · `core/env.py`
- `config/settings.py` (configs + gates) · `main_portfolio.py` (prepare_dual)
- `SYSTEM_LOG.md` = full 65-Part journal · `research/SYSTEM_OVERVIEW.md` = architecture

## ⬡ Open / next
- ✅ DONE 2026-07-22: RSI gate wired live, trend_carry braked, GC=F dropped, HMM log silenced, Icon corruption cleaned.
- **Correlation cap** — still open (unvalidated): book can hold LONG SPY + SHORT ^NDX simultaneously; the two sleeves can also fight on the same symbol.
- Double-trade overlay + leverage knob (validated, not wired). Infinex execution bridge (tap→trade) — security-gated, not built. Always-on button listener (Cloudflare webhook).
- Walk-forward monitor (SPY edge degraded in last 12mo — watch).
