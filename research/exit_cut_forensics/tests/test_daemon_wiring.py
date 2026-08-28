"""Self-check for the daemon shadow-cut wiring: verifies the patch is present and
correctly guarded, and dry-traces the ON path (pure, no MT5, no order)."""
import os
import tempfile

_DAEMON = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                       "axonai", "realtime", "daemon.py")
_CONFIG = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                       "axonai", "default_config.py")


def _src(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def test_config_defaults_off():
    src = _src(_CONFIG)
    assert '"shadow_cut_enabled": False' in src
    assert '"shadow_cut_buffer_pips": 3.0' in src


def test_daemon_patch_present_and_guarded():
    src = _src(_DAEMON)
    # single construction, lazily imported from research/
    assert "from research.exit_cut_forensics.shadow_cut import ShadowCutTracker" in src
    assert src.count("ShadowCutTracker(") == 1
    # flag-gated AND lead-only at the per-tick hook
    assert 'not self._exec_node and self.config.get("shadow_cut_enabled", False)' in src
    # entry stash + close cleanup wired
    assert 'self._active_trade_sr_level[ticket] = event.details.get("sr_level_price")' in src
    assert "self._shadow_cut.forget(ticket)" in src
    assert "self._active_trade_sr_level.pop(ticket, None)" in src


def test_daemon_parses():
    import ast
    ast.parse(_src(_DAEMON))
    ast.parse(_src(_CONFIG))


def test_on_path_dry_trace_replays_0819_loser():
    """Replay the 08-19 -20.2 SELL through the exact tracker the daemon builds."""
    from research.exit_cut_forensics.shadow_cut import ShadowCutTracker
    tr = ShadowCutTracker(buffer_pips=3.0, enabled=True, out_dir=tempfile.mkdtemp())
    kw = dict(ticket=245695751, direction="SELL", entry=1.15820, sr_level=1.15854,
              symbol="EURUSD", sl_pips=20.0)
    # adverse 3.0p < break thresh (3.4+3=6.4) -> no fire
    assert tr.observe(bid=1.15850, ask=1.15850, **kw) is None
    # adverse 7.5p >= 6.4 -> FIRE; would-cut at -(3.4+3) = -6.4 vs actual -20.2
    row = tr.observe(bid=1.15895, ask=1.15895, **kw)
    assert row is not None and row["would_cut_pips"] == -6.4
    # net save on this trade: -6.4 vs -20.2 = +13.8p


if __name__ == "__main__":
    import sys, traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fails = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except Exception:
            fails += 1; print(f"FAIL {fn.__name__}"); traceback.print_exc()
    print(f"\n{len(fns)-fails}/{len(fns)} passed")
    sys.exit(1 if fails else 0)
