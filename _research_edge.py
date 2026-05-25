"""Two backtests: full baseline-edge vs middle path, single-symbol SPY."""
import io
import contextlib
from config.settings import PULLBACK
import main_portfolio


def set_cfg(**kw):
    for k, v in kw.items():
        setattr(PULLBACK, k, v)


def run_quiet(symbol="SPY"):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        main_portfolio.run(symbol)
    return buf.getvalue()


def grab(out):
    lines = out.splitlines()
    keep = {}
    inblock = False
    for ln in lines:
        if "Combined portfolio" in ln:
            inblock = True
            continue
        if inblock:
            s = ln.strip()
            if not s or s.startswith("---"):
                break
            parts = s.split()
            keep[parts[0]] = parts[-1]
    # also legs / entries
    for ln in lines:
        if "total legs" in ln:
            keep["legs"] = ln.split(":")[-1].strip()
        if "unique entries" in ln:
            keep["entries"] = ln.split(":")[-1].strip()
    return keep


SCENARIOS = {
    "A_FULL_EDGE (cap1.0, pyr10, no VWAP gate, no mom gate)": dict(
        capital_cap_pct=1.00,
        max_pyramid_positions=10,
        pyramid_require_above_vwap=False,
        pyramid_require_positive_momentum=False,
    ),
    "B_MIDDLE (cap1.0, pyr8, VWAP gate ON, mom gate ON)": dict(
        capital_cap_pct=1.00,
        max_pyramid_positions=8,
        pyramid_require_above_vwap=True,
        pyramid_require_positive_momentum=True,
    ),
    "C_MIDDLE_3.0x_LEV": dict(
        capital_cap_pct=3.00, max_pyramid_positions=8,
        pyramid_require_above_vwap=True, pyramid_require_positive_momentum=True,
    ),
    "D_MIDDLE_3.5x_LEV": dict(
        capital_cap_pct=3.50, max_pyramid_positions=8,
        pyramid_require_above_vwap=True, pyramid_require_positive_momentum=True,
    ),
    "E_FULLEDGE_3.0x_pyr10": dict(
        capital_cap_pct=3.00, max_pyramid_positions=10,
        pyramid_require_above_vwap=False, pyramid_require_positive_momentum=False,
    ),
    "F_FULLEDGE_3.5x_pyr14": dict(
        capital_cap_pct=3.50, max_pyramid_positions=14,
        pyramid_require_above_vwap=False, pyramid_require_positive_momentum=False,
    ),
    "G_FULLEDGE_4.0x_pyr16": dict(
        capital_cap_pct=4.00, max_pyramid_positions=16,
        pyramid_require_above_vwap=False, pyramid_require_positive_momentum=False,
    ),
}

results = {}
for name, kw in SCENARIOS.items():
    set_cfg(**kw)
    out = run_quiet("SPY")
    results[name] = grab(out)

for name, m in results.items():
    print("\n==", name, "==")
    for k, v in m.items():
        print(f"  {k:<18s} {v}")
