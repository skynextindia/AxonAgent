"""Read-only wrapper: re-runs backtest_patterns with the same argv/seed but
patches summarize() to also emit the per-trade net-pip list, so median/best/
worst can be reported. Does not modify the harness."""
import importlib.util
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(HERE, "backtest_patterns.py"))
bt = importlib.util.module_from_spec(spec)
sys.modules["bt"] = bt
spec.loader.exec_module(bt)

_orig = bt.summarize


def summarize(results):
    out = _orig(results)
    valid = [r for r in results if r.outcome != "TRUNCATED"]
    net = sorted(r.net_pips for r in valid)
    if net:
        out["_net_sorted"] = [round(x, 3) for x in net]
        out["_net_median"] = round(statistics.median(net), 3)
        out["_net_best"] = round(net[-1], 3)
        out["_net_worst"] = round(net[0], 3)
    return out


bt.summarize = summarize
sys.exit(bt.main(sys.argv[1:]))
