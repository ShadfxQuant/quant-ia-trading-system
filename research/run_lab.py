"""
Lab runner — entry point. Run with:
    python3 -m research.run_lab
"""
from __future__ import annotations
import warnings, logging
warnings.filterwarnings("ignore")
logging.getLogger("statsmodels").setLevel(logging.ERROR)

from research.edge_lab import run_lab
from research.edge_library import EDGES

# Symbols to mine across. SPY/QQQ/GLD = clean ETF microstructure;
# GC=F/ES=F = futures 23/5 (different regime); SLV / IWM = extra diversity.
SYMBOLS = ["SPY", "QQQ", "GLD", "GC=F", "ES=F", "SLV", "IWM"]
HORIZONS = (5, 20, 100, 390)


def main():
    print("\n" + "="*100)
    print("  EDGE LAB — comprehensive market-edge mining")
    print(f"  symbols: {SYMBOLS}")
    print(f"  edges:   {len(EDGES)} across "
          f"{len(set(e.category for e in EDGES))} categories")
    print(f"  horizons: {HORIZONS} bars (≈ {[h/6.5 for h in HORIZONS]} trading days)")
    print("="*100)

    df = run_lab(SYMBOLS, EDGES, HORIZONS)

    if len(df) == 0:
        print("  no edges discovered")
        return

    # ─── headline: top 30 edges by |t-stat| ───
    print(f"\n{'='*100}")
    print(f"  TOP 30 EDGES BY |t-stat| (n_signals ≥ 50, p < 0.01)")
    print("="*100)
    sig = df[(df["n_signals"] >= 50) & (df["p_value"] < 0.01)].head(30)
    fmt = "  {sym:<8}{edge:<30}{cat:<12}{h:>5}{dir:>6}{n:>6}{hit:>7}{mean:>10}{sharpe:>8}{t:>7}{p:>9}"
    print(fmt.format(sym="symbol", edge="edge", cat="cat", h="h",
                     dir="dir", n="n", hit="hit%", mean="mean_bp",
                     sharpe="sharpe", t="t", p="p"))
    print("  " + "-"*100)
    for _, r in sig.iterrows():
        print(fmt.format(
            sym=r["symbol"], edge=r["edge_name"][:28], cat=r["category"][:10],
            h=int(r["horizon_bars"]), dir=r["direction"][:5],
            n=int(r["n_signals"]),
            hit=f"{r['hit_rate']*100:.1f}",
            mean=f"{r['mean_return_bps']:+.1f}",
            sharpe=f"{r['sharpe']:+.2f}",
            t=f"{r['t_stat']:+.2f}",
            p=f"{r['p_value']:.4f}",
        ))

    # ─── category summary ───
    print(f"\n{'='*100}")
    print(f"  CATEGORY SUMMARY — edges with |t| > 2 (95% confidence)")
    print("="*100)
    strong = df[df["edge_score"] > 2.0]
    cat_summary = strong.groupby("category").agg(
        n_strong=("edge_score", "count"),
        mean_t=("edge_score", "mean"),
        max_t=("edge_score", "max"),
        best_edge=("edge_name", lambda x: x.iloc[strong.loc[x.index, "edge_score"].idxmax()
                                                  - x.index[0]] if len(x) else "—"),
    ).sort_values("max_t", ascending=False)
    print(cat_summary.to_string())

    # ─── per-symbol top edges ───
    print(f"\n{'='*100}")
    print(f"  PER-SYMBOL BEST EDGE (highest |t-stat|, n≥50, p<0.01)")
    print("="*100)
    for sym in SYMBOLS:
        sub = df[(df["symbol"] == sym) & (df["n_signals"] >= 50) &
                 (df["p_value"] < 0.01)]
        if len(sub) == 0:
            print(f"  {sym:<8}  no significant edges"); continue
        best = sub.iloc[0]
        print(f"  {sym:<8}  {best['edge_name']:<32} (h={int(best['horizon_bars'])} "
              f"{best['direction']:<5} t={best['t_stat']:+.2f} "
              f"mean={best['mean_return_bps']:+.1f}bp hit={best['hit_rate']*100:.1f}% "
              f"n={int(best['n_signals'])})")


if __name__ == "__main__":
    main()
