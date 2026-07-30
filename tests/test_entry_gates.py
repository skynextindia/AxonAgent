"""Tests for the two default-OFF entry-quality gates in daemon.py:

  * _is_falling_knife_buy  — the validated 'BUY into a red M15 candle' filter (#1)
  * _maybe_noprogress_abort — the post-entry 'MFE ~ 0 -> scratch' abort (#2)

Both are exercised in isolation with lightweight stubs (no live MT5, no orders).
Run: .venv\\Scripts\\python.exe -m unittest tests.test_entry_gates
"""
import time
import types
import unittest

from axonai.realtime import daemon as daemon_mod
from axonai.realtime.daemon import AxonDaemon

mt5 = daemon_mod.mt5  # real constants (POSITION_TYPE_BUY/SELL); no calls made here


def _candle(o, c, tf="M15"):
    return {"timeframe": tf, "open": o, "high": max(o, c), "low": min(o, c), "close": c}


class _Pos:
    """Minimal stand-in for an MT5 position object."""
    def __init__(self, ticket, ptype, price_open, volume=1.0, symbol="EURUSD.i", magic=123457):
        self.ticket = ticket
        self.type = ptype
        self.price_open = price_open
        self.volume = volume
        self.symbol = symbol
        self.magic = magic
        self.profit = 0.0
        self.sl = 0.0
        self.tp = 0.0


class _AbortStub:
    """Just enough of AxonDaemon for _maybe_noprogress_abort to run."""
    def __init__(self, cfg):
        self.config = cfg
        self.mt5_symbol = "EURUSD.i"
        self._active_trade_entry_time = {}
        self._active_trade_peak_price = {}
        self.closed = []             # records (ticket, reason)

    def _close_position(self, pos, reason):
        self.closed.append((pos.ticket, reason))
        return True


class TestFallingKnife(unittest.TestCase):
    def test_buy_into_red_m15_is_knife(self):
        # today's #5: USDJPY BUY, trigger candle open 163.399 -> close 163.308 (red)
        self.assertTrue(AxonDaemon._is_falling_knife_buy("Buy", _candle(163.399, 163.308)))

    def test_buy_into_green_m15_allowed(self):
        self.assertFalse(AxonDaemon._is_falling_knife_buy("Buy", _candle(1.1000, 1.1020)))

    def test_sell_never_a_knife(self):
        # the filter is BUY-only; SELL into a red candle is a different (fine) quadrant
        self.assertFalse(AxonDaemon._is_falling_knife_buy("Sell", _candle(163.399, 163.308)))

    def test_doji_not_a_knife(self):
        self.assertFalse(AxonDaemon._is_falling_knife_buy("Buy", _candle(1.1000, 1.1000)))

    def test_non_m15_trigger_allowed(self):
        # edge only validated on M15; an H1 red candle must not trip the filter
        self.assertFalse(AxonDaemon._is_falling_knife_buy("Buy", _candle(1.1020, 1.1000, tf="H1")))

    def test_missing_trigger_candle_allowed(self):
        self.assertFalse(AxonDaemon._is_falling_knife_buy("Buy", None))
        self.assertFalse(AxonDaemon._is_falling_knife_buy("Buy", {}))


class TestNoProgressAbort(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "entry_noprogress_abort": True,
            "noprogress_abort_minutes": 12.0,
            "noprogress_abort_min_favorable_pips": 2.0,
            "noprogress_abort_notice_only": False,   # armed for the action tests
        }
        self._orig_alert = daemon_mod.send_alert
        daemon_mod.send_alert = lambda *a, **k: None  # silence real alerts

    def tearDown(self):
        daemon_mod.send_alert = self._orig_alert

    def _run(self, stub, pos, bid, ask, pip=0.0001):
        return AxonDaemon._maybe_noprogress_abort(stub, pos, bid, ask, pip)

    def test_young_position_not_aborted(self):
        stub = _AbortStub(self.cfg)
        pos = _Pos(1, mt5.POSITION_TYPE_BUY, 1.1000)
        stub._active_trade_entry_time[1] = time.time() - 60      # 1 min old
        stub._active_trade_peak_price[1] = 1.1000                 # 0 pips favorable
        self.assertFalse(self._run(stub, pos, 1.0999, 1.0999))
        self.assertEqual(stub.closed, [])

    def test_old_and_flat_is_aborted(self):
        stub = _AbortStub(self.cfg)
        pos = _Pos(2, mt5.POSITION_TYPE_BUY, 1.1000)
        stub._active_trade_entry_time[2] = time.time() - 20 * 60  # 20 min old
        stub._active_trade_peak_price[2] = 1.10005                # peak only +0.5 pip
        self.assertTrue(self._run(stub, pos, 1.0998, 1.0998))
        self.assertEqual(stub.closed, [(2, "No-progress abort")])

    def test_old_but_progressed_not_aborted(self):
        stub = _AbortStub(self.cfg)
        pos = _Pos(3, mt5.POSITION_TYPE_BUY, 1.1000)
        stub._active_trade_entry_time[3] = time.time() - 20 * 60
        stub._active_trade_peak_price[3] = 1.10030                # peaked +3 pips (>= 2)
        self.assertFalse(self._run(stub, pos, 1.1001, 1.1001))
        self.assertEqual(stub.closed, [])

    def test_current_price_counts_as_progress(self):
        # peak dict lagging, but the live tick shows >= 2 pips favorable -> keep
        stub = _AbortStub(self.cfg)
        pos = _Pos(4, mt5.POSITION_TYPE_BUY, 1.1000)
        stub._active_trade_entry_time[4] = time.time() - 20 * 60
        stub._active_trade_peak_price[4] = 1.1000                 # stale peak
        self.assertFalse(self._run(stub, pos, 1.10025, 1.10026))  # +2.5 pips now
        self.assertEqual(stub.closed, [])

    def test_sell_side_abort(self):
        stub = _AbortStub(self.cfg)
        pos = _Pos(5, mt5.POSITION_TYPE_SELL, 1.1000)
        stub._active_trade_entry_time[5] = time.time() - 20 * 60
        stub._active_trade_peak_price[5] = 1.09995                # peak only +0.5 pip (price fell 0.5)
        self.assertTrue(self._run(stub, pos, 1.1002, 1.1002))
        self.assertEqual(stub.closed, [(5, "No-progress abort")])

    def test_missing_entry_time_not_aborted(self):
        stub = _AbortStub(self.cfg)
        pos = _Pos(6, mt5.POSITION_TYPE_BUY, 1.1000)
        # no entry-time recorded -> cannot age it -> never abort
        self.assertFalse(self._run(stub, pos, 1.0990, 1.0990))
        self.assertEqual(stub.closed, [])

    def test_notice_only_logs_but_does_not_close(self):
        cfg = dict(self.cfg, noprogress_abort_notice_only=True)   # default soak mode
        stub = _AbortStub(cfg)
        pos = _Pos(7, mt5.POSITION_TYPE_BUY, 1.1000)
        stub._active_trade_entry_time[7] = time.time() - 20 * 60   # old + flat
        stub._active_trade_peak_price[7] = 1.10005
        self.assertFalse(self._run(stub, pos, 1.0998, 1.0998))     # does not skip-trail
        self.assertEqual(stub.closed, [])                          # and nothing closed


class TestTrailOverride(unittest.TestCase):
    def test_default_is_035(self):
        self.assertEqual(AxonDaemon._effective_trail_mult({}), 0.35)
        self.assertEqual(AxonDaemon._effective_trail_mult({"trail_dist_atr_mult": 0.35}), 0.35)

    def test_override_wins(self):
        self.assertEqual(
            AxonDaemon._effective_trail_mult({"trail_dist_atr_mult": 0.35, "trail_dist_atr_mult_override": 1.0}), 1.0)

    def test_explicit_zero_override_honored(self):
        # 0.0 is a real value, not "unset" — must not fall through to the default
        self.assertEqual(
            AxonDaemon._effective_trail_mult({"trail_dist_atr_mult": 0.35, "trail_dist_atr_mult_override": 0.0}), 0.0)

    def test_resolve_symbol_config_preserves_override(self):
        from axonai.default_config import DEFAULT_CONFIG, resolve_symbol_config
        base = DEFAULT_CONFIG.copy()
        base["trail_dist_atr_mult_override"] = 1.0
        for sym in ("EURUSD", "USDJPY"):
            cfg = resolve_symbol_config(base, sym)
            self.assertEqual(cfg.get("trail_dist_atr_mult"), 0.35)          # spec unchanged
            self.assertEqual(AxonDaemon._effective_trail_mult(cfg), 1.0)     # override applied

    def test_no_override_resolves_to_035(self):
        from axonai.default_config import DEFAULT_CONFIG, resolve_symbol_config
        cfg = resolve_symbol_config(DEFAULT_CONFIG.copy(), "EURUSD")
        self.assertEqual(AxonDaemon._effective_trail_mult(cfg), 0.35)


class _FakeTick:
    def __init__(self, bid, ask):
        self.bid, self.ask = bid, ask


class _FakeMT5:
    """Minimal MT5 stand-in for the close paths. order_send consults `retcodes`,
    a per-call queue of retcode values (default: always DONE)."""
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    TRADE_RETCODE_DONE = 10009

    def __init__(self, positions, retcodes=None, bid=1.1000, ask=1.1001):
        self._positions = positions
        self._retcodes = list(retcodes) if retcodes is not None else None
        self._tick = _FakeTick(bid, ask)
        self.sent = []                       # list of (position_ticket, type_filling)

    def terminal_info(self):
        return object()

    def positions_get(self, symbol=None):
        return self._positions

    def symbol_info_tick(self, symbol):
        return self._tick

    def order_send(self, req):
        self.sent.append((req.get("position"), req.get("type_filling")))
        rc = self.TRADE_RETCODE_DONE if self._retcodes is None else self._retcodes.pop(0)
        return types.SimpleNamespace(retcode=rc, comment="ok" if rc == self.TRADE_RETCODE_DONE else "fail")


def _pos(ticket, ptype=0, price_open=1.1000, magic=123457, volume=1.0):
    return types.SimpleNamespace(ticket=ticket, type=ptype, price_open=price_open,
                                 magic=magic, volume=volume, symbol="EURUSD.i", profit=0.0)


class _FlattenStub:
    _close_position = AxonDaemon._close_position
    _close_all_positions = AxonDaemon._close_all_positions
    _close_all_profitable_positions = AxonDaemon._close_all_profitable_positions

    def __init__(self, magic=123457):
        self.mt5_symbol = "EURUSD.i"
        self.trade_executor_opt = types.SimpleNamespace(magic=magic)


class TestCloseFallback(unittest.TestCase):
    def setUp(self):
        self._orig = daemon_mod.mt5

    def tearDown(self):
        daemon_mod.mt5 = self._orig

    def test_fok_reject_falls_back_to_ioc(self):
        # FOK rejected (retcode 10030), then IOC accepted — the exact failure the
        # old hardcoded-IOC path could not recover from.
        fake = _FakeMT5([_pos(1)], retcodes=[10030, _FakeMT5.TRADE_RETCODE_DONE])
        daemon_mod.mt5 = fake
        stub = _FlattenStub()
        self.assertEqual(stub._close_all_positions("pre-news flatten"), 1)
        # Proves it tried FOK first, then IOC.
        self.assertEqual(fake.sent, [(1, _FakeMT5.ORDER_FILLING_FOK), (1, _FakeMT5.ORDER_FILLING_IOC)])

    def test_both_fillings_fail_returns_zero(self):
        fake = _FakeMT5([_pos(1)], retcodes=[10030, 10030])
        daemon_mod.mt5 = fake
        self.assertEqual(_FlattenStub()._close_all_positions("x"), 0)

    def test_magic_filter_preserved(self):
        # one of ours (123457), one foreign (999) — only ours is closed
        fake = _FakeMT5([_pos(1, magic=123457), _pos(2, magic=999)])
        daemon_mod.mt5 = fake
        self.assertEqual(_FlattenStub()._close_all_positions("x"), 1)
        self.assertEqual([t for t, _ in fake.sent], [1])

    def test_profitable_gate_only_closes_winners(self):
        # BUY @1.1000 with bid 1.1010 -> +10 pips (close); SELL @1.1000 with ask
        # 1.1010 -> -10 pips (leave). Profit test uses the passed bid/ask.
        fake = _FakeMT5([_pos(1, ptype=0), _pos(2, ptype=1)], bid=1.1010, ask=1.1010)
        daemon_mod.mt5 = fake
        n = _FlattenStub()._close_all_profitable_positions(1.1010, 1.1010, "End of Day (Session Close)")
        self.assertEqual(n, 1)
        self.assertEqual([t for t, _ in fake.sent], [1])


if __name__ == "__main__":
    unittest.main()
