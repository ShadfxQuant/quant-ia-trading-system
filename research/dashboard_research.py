"""
Research dashboard — visualize edge-lab output.

Run with:
    streamlit run research/dashboard_research.py --server.port 8502

Completely isolated from the main dashboard on 8501.
Reads from research/results/edges_latest.csv only.
"""
from __future__ import annotations
import os
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Edge Lab — Research",
    page_icon="🧪",
    layout="wide",
)

st.title("🧪 Edge Lab — Market Research")
st.caption(
    "Mining 40+ edge hypotheses across 7 symbols and 4 forward horizons. "
    "Both positive and negative edges are surfaced — high negative t-stats "
    "are flagged for inversion. ALL outputs are research-only; not wired to "
    "any live signal."
)

RESULTS = "research/results/edges_latest.csv"

if not os.path.exists(RESULTS):
    st.error("No results yet. Run `python3 -m research.run_lab` first.")
    st.stop()

df = pd.read_csv(RESULTS)
last_modified = os.path.getmtime(RESULTS)
import datetime as _dt
st.caption(f"Source: `{RESULTS}` · last mined "
           f"{_dt.datetime.fromtimestamp(last_modified):%Y-%m-%d %H:%M UTC}")

# ── Sidebar filters ──
with st.sidebar:
    st.header("Filters")
    syms = sorted(df["symbol"].unique())
    sym_sel = st.multiselect("Symbols", syms, default=syms)
    cats = sorted(df["category"].unique())
    cat_sel = st.multiselect("Categories", cats, default=cats)
    horizons = sorted(df["horizon_bars"].unique())
    horizon_sel = st.multiselect("Horizons (bars)", horizons, default=horizons)
    min_n = st.slider("Min n_signals", 10, 1000, 100, step=10)
    max_p = st.select_slider("Max p-value", options=[0.05, 0.01, 0.001, 0.0001],
                              value=0.01)
    direction_sel = st.multiselect("Direction", ["long", "short"],
                                   default=["long", "short"])

# ── Apply filters ──
filt = df[
    df["symbol"].isin(sym_sel) &
    df["category"].isin(cat_sel) &
    df["horizon_bars"].isin(horizon_sel) &
    df["direction"].isin(direction_sel) &
    (df["n_signals"] >= min_n) &
    (df["p_value"] <= max_p)
]

# ── KPI strip ──
c1, c2, c3, c4 = st.columns(4)
c1.metric("Total cells mined", f"{len(df):,}")
c2.metric("Significant (p<0.01)", f"{(df['p_value']<0.01).sum():,}")
c3.metric("Strong (|t|>5)", f"{(df['edge_score']>5).sum():,}")
c4.metric("After filter", f"{len(filt):,}")

# ── Top edges table ──
st.subheader("📊 Top edges (by |t-stat|)")
display = filt.head(50)[["symbol", "edge_name", "category", "horizon_bars",
                         "direction", "n_signals", "hit_rate", "mean_return_bps",
                         "sharpe", "t_stat", "p_value", "edge_score"]]
display = display.rename(columns={
    "edge_name": "edge", "horizon_bars": "h", "n_signals": "n",
    "hit_rate": "hit%", "mean_return_bps": "mean_bp",
    "edge_score": "|t|",
})
display["hit%"] = (display["hit%"] * 100).round(1)
st.dataframe(display, use_container_width=True, hide_index=True, height=420)

# ── Category heatmap ──
st.subheader("🗺️ Edge heatmap — best |t-stat| by category × symbol")
heat = filt.groupby(["category", "symbol"])["edge_score"].max().unstack().fillna(0)
st.dataframe(heat.style.background_gradient(cmap="RdYlGn", vmin=0, vmax=15),
             use_container_width=True)

# ── Horizon profile ──
st.subheader("⏱️ Mean |t-stat| by horizon (which timeframe has the most edge?)")
horizon_profile = (filt.groupby("horizon_bars")
                       .agg(n_edges=("edge_name", "count"),
                            mean_abs_t=("edge_score", "mean"),
                            max_t=("edge_score", "max"))
                       .round(2))
st.dataframe(horizon_profile, use_container_width=True)

# ── Specific edge drill-down ──
st.subheader("🔍 Edge drill-down")
edge_names = sorted(filt["edge_name"].unique())
if edge_names:
    chosen = st.selectbox("Edge to inspect", edge_names)
    sub = df[df["edge_name"] == chosen].sort_values(
        ["symbol", "horizon_bars"]).reset_index(drop=True)
    st.dataframe(
        sub[["symbol", "horizon_bars", "direction", "n_signals", "hit_rate",
             "mean_return_bps", "sharpe", "t_stat", "p_value"]],
        use_container_width=True, hide_index=True
    )

# ── Category breakdown ──
st.subheader("📚 Findings by category")
for cat in sorted(filt["category"].unique()):
    csub = filt[filt["category"] == cat]
    if len(csub) == 0: continue
    with st.expander(f"{cat} — {len(csub)} significant cells, max |t|={csub['edge_score'].max():.1f}",
                     expanded=False):
        st.dataframe(
            csub.head(15)[["symbol", "edge_name", "horizon_bars", "direction",
                           "n_signals", "hit_rate", "mean_return_bps",
                           "sharpe", "t_stat"]],
            use_container_width=True, hide_index=True
        )

# ── Footer ──
st.divider()
st.caption(
    "**Methodology note**: t-stats inflate on long forward horizons (e.g. 390 bars) because forward returns "
    "are autocorrelated across signals. The honest alpha lives in shorter horizons (5-20 bars). "
    "Sharpe annualizes assuming 252×6.5/h bars per year. Auto-direction picks the higher-edge side "
    "(positive forward return → long; negative → short with statistics flipped). "
    "Re-run mining anytime with `python3 -m research.run_lab`. "
    "See SYSTEM_LOG.md Part 8.17 for the research methodology + GEX/orderflow background."
)
