# Shadow "level broke → cut" — integration design

Goal: log a **would-cut** row per live trade whenever its faded S/R level breaks,
so the forensic's `+net` (see [EXIT_CUT_FORENSIC.md](out/EXIT_CUT_FORENSIC.md)) can
be confirmed on live, out-of-sample data **before** any real exit change. Changes
nothing about how the daemon trades.

The mechanism is already built + tested: [`shadow_cut.py`](shadow_cut.py)
(`ShadowCutTracker`, 8/8 tests). It takes only primitives, returns a dict or
`None`, writes only to `research/exit_cut_forensics/shadow_out/`. It is incapable
of touching a position, order, or the daemon. This doc is the wiring plan for it —
**NOT YET APPLIED** to `axonai/realtime/daemon.py`.

## Why it mirrors an existing, proven-safe pattern
The daemon already runs per-position live shadows that "watch, never touch":
`_update_exit_capture_shadow(pos, bid, ask, pip)` (daemon.py:3203, called per open
position at daemon.py:3697) and `_update_retest_fwd(...)` (daemon.py:3384). The cut
shadow slots in beside them with the identical shape.

## Three-part patch (all additive, flag-gated `shadow_cut_enabled=False`)

**1) Construct the tracker (in `__init__`, next to the other shadow dicts ~daemon.py:108)**
```python
from research.exit_cut_forensics.shadow_cut import ShadowCutTracker
self._active_trade_sr_level: dict[int, float] = {}   # ticket -> faded level @ entry
self._shadow_cut = ShadowCutTracker(
    buffer_pips=float(self.config.get("shadow_cut_buffer_pips", 3.0)),
    enabled=bool(self.config.get("shadow_cut_enabled", False)),
)
```

**2) Stash the faded level at entry** — everywhere ATR is already stashed per
ticket (daemon.py:1359, 2807, 3569), add one line reading the level the entry
already recorded (`event.details["sr_level_price"]`, set at daemon.py:888):
```python
self._active_trade_sr_level[ticket] = event.details.get("sr_level_price")
```
(For the mirror/adopt paths where no `event` exists, leave it unset → the tracker
treats a missing level as N/A and simply never fires for that ticket.)

**3) Observe per tick** — beside the exit-capture call at daemon.py:3697, inside
the same `for pos in positions:` management loop (lead only — the node has no
entry context):
```python
if not self._exec_node and self.config.get("shadow_cut_enabled", False):
    self._shadow_cut.observe(
        ticket=pos.ticket,
        direction=("SELL" if pos.type == mt5.POSITION_TYPE_SELL else "BUY"),
        entry=pos.price_open,
        sr_level=self._active_trade_sr_level.get(pos.ticket),
        symbol=self.mt5_symbol, bid=bid, ask=ask,
        sl_pips=self._active_trade_sl_pips.get(pos.ticket),  # or |entry-SL|/pip
        epoch=int(time.time()))
```

**4) Forget on close** — where the ticket is discarded (daemon.py:4329,
`self._tracked_positions.discard(ticket)`):
```python
self._shadow_cut.forget(ticket)
self._active_trade_sr_level.pop(ticket, None)
```

> `sl_pips`: if there's no per-ticket SL cache, compute it inline from the live
> position: `abs(pos.price_open - pos.sl) / pip` (guard `pos.sl` against the known
> torn-read of 0 — see the shadow-SL note in memory). Passing `None` is safe; it
> only disables the "stop tighter than break" guard.

## Output → how to judge it later
Each fire appends one row to `research/exit_cut_forensics/shadow_out/would_cut_shadow.jsonl`:
```json
{"type":"would_cut","account":"lead","ticket":245695751,"symbol":"EURUSD",
 "direction":"SELL","entry":1.15820,"faded_level":1.15854,"level_dist_pips":3.4,
 "buffer_pips":3.0,"would_cut_pips":-6.4,"adverse_at_fire_pips":6.4,"sl_pips":20.0,
 "epoch":...}
```
At a checkpoint, join `ticket` → the `trade_closed` row's actual `pips`:
`delta = would_cut_pips − actual_pips`. Sum over live fires = the **out-of-sample**
version of the forensic's net. Positive across a fresh (non-Aug-10–19) window is
the evidence that upgrades this from "measured once, one regime" to "validated."

## Safety properties (unchanged from the risk-observer precedent)
- **Zero behavior change** when `shadow_cut_enabled` is false (default). Running
  daemons hold pre-restart code → wiring this is inert until a flat restart with
  the flag on.
- The tracker cannot mutate live state: it receives numbers, returns a dict, writes
  one research-dir file, and swallows every exception.
- No direction decision, no order, no SL/TP/lot/flatten. Pure observation.

## Recommended settings for the first shadow run
- `shadow_cut_enabled: true`, `shadow_cut_buffer_pips: 3.0` (the robust 3–5p band;
  3p keeps casualties low while capturing the tail).
- Lead only. Let it accrue ≥2–3 weeks spanning a non-adverse stretch, then judge.
