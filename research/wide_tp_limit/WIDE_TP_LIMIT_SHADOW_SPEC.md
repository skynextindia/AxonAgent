# Wide-TP / Limit-Entry Shadow — Design Spec

**Status:** spec only, not built. Read-only shadow; never places a real order.
**Owner finding:** [[expectancy-verdict]] — the ONLY backtested net-positive path
(2y H1, 12 quarters): a **wide asymmetric target (SL~20 / TP~100, 1:5) + limit/cheap
execution** → net **+0.98p/trade at limit cost, 8/12 quarters positive**. Everything else
(narrow brackets, momentum entry, regime-switch) is net-negative after cost.

## 1. Purpose
Validate that new profile **forward, on live ticks, without risking a cent**, because it is
the *opposite* of the live system (a 20p symmetric market scalp). Two things the backtest
could only approximate and the shadow must measure for real:
1. **Limit fill mechanics** — a market order always fills; a resting limit at the level does
   not. Real fill rate + fill price are unknown and decide whether the edge survives.
2. **Forward, out-of-sample edge** — does the +2.2p gross hold beyond the backtest window,
   and which regime are we currently in?

## 2. What it measures (the delta vs the live trade)
| | Live entry (fade) | This prototype |
|---|---|---|
| Order type | market @ current price | **limit @ the faded S/R level** |
| Cost | spread + commission (~2p) | commission + exit spread (~1.2p; entry spread saved) |
| Target | 20p (hard-distance) | **~100p (1:5)** |
| Stop | 20p | ~20p |
| Hold | intraday / weekend-flat | to TP/SL or weekend-flat (long) |

## 3. Entry mechanics (the hard part)
- **Trigger:** reuse the EXACT live fade signal — fire the shadow the moment the live peak
  gate passes (daemon ~L924, after S/R + range + structure gates). Same setups the real
  machine takes, so the comparison is apples-to-apples.
- **Limit price:** the S/R level being faded (`event.details["sr_level_price"]`), i.e. a
  slightly *better* price than the current market (you fade *toward* the level). If no level,
  use current price − offset (short) / + offset (long), offset = `limit_offset_pips` (default 0
  = enter at signal price, pure cost-saving mode — matches the backtest's assumption).
- **Fill window:** the limit is live for `fill_window_min` (default 60 min). It FILLS the first
  time a subsequent bar's range touches the limit price; else it EXPIRES un-filled.
- **No-fill is data, not a discard:** log expired limits (`filled=false`). Fill rate is a
  headline output — a low fill rate means far fewer trades and changes the edge.

## 4. Exit mechanics
- Bracket from fill: `sl_pips` (default 20), `tp_pips` (default 100).
- Resolve on candle close (M5 or M15) using bar high/low. **Conservative ordering:** if one
  bar's range touches BOTH SL and TP, assume **SL first** (matches the backtest).
- Always also log **MFE/MAE** so any other (SL,TP) can be re-scored offline later.
- **Max hold:** `max_hold_hours` (default 120h ≈ weekend cap). Force mark-to-close at the
  weekend flatten or the cap, whichever first — mirrors the live weekend-only flatten.

## 5. Cost model (don't hardcode one number)
Log `gross_pips`, then net at a vector of costs `[0.7 raw, 1.2 limit, 2.0 market]` so the
verdict isn't hostage to one assumption. Headline = the 1.2p (limit) column.

## 6. Log schema — `reports/wide_tp_limit_shadow.jsonl` (one row per signal)
```
type, mt5_symbol, sig_ts_utc, signal(Buy/Sell), sig_price,
level_type, limit_price, fill_window_min,
filled(bool), fill_ts_utc, fill_price, fill_delay_sec,
sl_pips, tp_pips, sl_price, tp_price,
outcome(TP|SL|TIMEOUT|WEEKEND_FLAT|NO_FILL), exit_ts_utc, exit_price,
gross_pips, mfe_pips, mae_pips, hold_seconds,
net_pips_at{0.7,1.2,2.0},
er_at_entry, mtf_intraday_pos, mtf_summary   # regime context for bucketing
```

## 7. Integration (all additive, flag-gated, LEAD-only)
- `wide_tp_limit_shadow_enabled` (default True) — master.
- One hook at the fade decision (arm a shadow limit) + a state machine
  `self._wtl_setups: list` resolved on candle close (pattern already used by
  `_update_breakout_retest_shadow` / `_brk_setups`, daemon ~L2221). **Never touches
  `trade_executor`.** Fully wrapped in try/except so a shadow error can't disturb trading.
- Config block: `wide_tp_limit_sl_pips:20`, `_tp_pips:100`, `_fill_window_min:60`,
  `_limit_offset_pips:0`, `_max_hold_hours:120`, `_cost_pips:[0.7,1.2,2.0]`.

## 8. Validation & decision criteria (judged at the Sept checkpoint)
Run ≥3–4 weeks / target **n ≥ 60 filled** across ≥2 regimes, then a reader script reports:
- **fill rate** (filled / signals) — if very low, the profile trades too rarely to matter.
- **net expectancy at 1.2p cost**, overall and per-regime (ER-bucketed + MTF zone).
- **comparison to the backtest** (+0.98p limit / +2.2p gross) — does forward match?
- **DECISION:** build the live wide-TP/limit version ONLY if forward net > 0 at limit cost
  across ≥2 regimes AND the fill rate is high enough to be economic. Else keep shadowing /
  drop. Measurement-first: do not modify live execution on the backtest alone.

## 9. Risks / open questions the shadow exists to answer
- **Fill rate vs edge trade-off** — a limit at a better price fills less often; the missed
  fills may be exactly the runners (adverse selection). This is the #1 unknown.
- **1:5 profile reality** — low win rate, rare big wins, ~4/12 losing regimes ([[expectancy-verdict]]).
  Confirm the tail actually shows up live, not just in-sample.
- **Regime we're in now** — if the coming weeks are a "losing regime," a negative forward
  read is NOT a falsification; bucket by regime before concluding.

## 10. Non-goals
Never trades. No executor, no order, no exposure. Does not change the live 20p scalp, the
USDJPY mirror, or any armed gate. Purely a forward measurement of a candidate redesign.
