# AxonAI Dashboard Redesign — "Honest Instrument"

Date: 2026-07-19
Status: DESIGN — awaiting user review (not committed per user instruction)
Scope: `cli/static/index.html` only. Dashboard/reporting layer — FREEZE-SAFE. No engine, gate, or exit change.

## 1. Guiding principle

The dashboard is an **honest instrument**: every pixel reflects what the engine is
actually doing right now. Seriousness comes from truth and precision, not chrome.
This is the acceptance test for every element — "is this a real engine state, shown
truthfully?" If no, it does not ship.

The prior neo-glass mockup was rejected because it lied: emoji icons, decorative glow,
and a toy chart that misrepresented how much the engine analyzes. Those are the exact
anti-patterns this design forbids.

## 2. Non-negotiables

1. **Only real telemetry is drawn.** Each panel maps to a real WebSocket message type
   or telemetry field. No filler, no decorative widgets.
2. **State honesty is explicit and loud:**
   - Synthetic ticks (weekend / dead feed) -> a `SIMULATED` state badge; the chart and
     values are visibly marked not-live. (Backed by `TickEngine.is_synthetic`, already wired.)
   - Warm-up (velocity z=0, <500 samples) -> a `WARMING` marker, not a fake number.
   - Stale feed -> `STALE 8s` with the real age.
   - Unavailable value -> `--` / `n/a`, never a placeholder digit.
3. **Color is semantic only.** Green/red/amber encode real state (pass/block/watch,
   long/short, healthy/degraded/tripped). No glow-for-glow.
4. **Provenance is visible.** Feed terminal (Exness), exec terminal (MetaQuotes),
   last-tick age, per-daemon health — the truth about the data's source and freshness.
5. **Motion carries information.** Transitions fire only on a real change (a tick, a
   state transition, a fill). No ambient animation. Respect `prefers-reduced-motion`.

## 3. Aesthetic (design system)

Quant prop-desk density, zero toy. Concrete tokens:

- **Color:** near-black base `#06080d`; panel `#0d121c` (solid, subtle 1px border, no
  gradient fills, no heavy shadow); neutrals cool-grey (`--ink #e9eef7`, `--sub #9aa9c2`,
  `--dim #5f6d86`); semantic `--good #37d3a6`, `--bad #ff5c72`, `--warn #f6b545`,
  `--info #57b6ff`; ONE restrained accent `#4c8dff` used ONLY for live/active truth and
  the price line — not everywhere.
- **Type:** cool-grey sans for labels (system stack); tabular monospace for ALL numerics
  (`font-variant-numeric: tabular-nums`) so columns align and values don't jitter on tick.
  Micro-labels uppercase, tracked. Tight but legible scale (8.5px labels / 11px data /
  13px key figures).
- **Density:** hairline separators, compact padding, information over whitespace. Radius
  small (2-3px) — sharp, not rounded-toy.
- **No:** emoji icons (use precise glyphs or tiny inline SVG), gradient fills on panels,
  glow spam, decorative shadows.

## 4. Architecture

Single-file `cli/static/index.html`, class `Axon`, unchanged data plumbing:
- **Keep intact:** the WebSocket route()/onX handlers, `lightweight-charts` chart engine,
  the `/api/replay|patterns|exhaustion|range_stats|fade_signals` fetches, journal/calendar.
- **Change only:** the CSS design system, the DOM layout/structure, and the chart-layer
  rendering (fused surface + toggle toolbar). All element IDs preserved where a handler
  writes to them, so the update code is untouched.
- Port is incremental and ID-preserving; verified per step with `node --check` on the
  extracted script and a user visual pass (localhost is not previewable in this harness,
  so the user is the visual verification loop).

## 5. The fused chart surface (the core)

The chart is the app. `lightweight-charts` candles are the base; the engine's real
analytical layers are painted on top as SVG/price-line overlays, each backed by a real
source, each toggleable from a layer toolbar:

| Layer | Real source | Rendering |
|---|---|---|
| Candles + price line | live candles / ticks | base series, accent close line |
| Levels | `levels` WS / `dynamic-levels` | price lines, S/R colored, distance labels |
| Sessions | `range_stats` + clock | shaded vertical bands (ASIA/LDN/NY) |
| Decision Replay ribbon | `/api/replay` (entry_state per bucket) | bottom ribbon, state-colored |
| Skip-reason tags | `/api/replay` skip | abbreviated tags on the ribbon |
| Conviction lane | `/api/replay` (confluence + reversal_pressure) | sparkline lane |
| Liquidity strip | `/api/replay` (sweeps/voids/breaks) | 3-row strip |
| Patterns | `/api/patterns` | shape polyline + neckline + expected(dashed)/actual(solid) |
| Exhaustion | `/api/exhaustion` | machine-pattern markers (disp-collapse+revP) |
| Trades + MFE/MAE | positions / analytics | entry/exit markers, excursion band |
| Fade signals | `/api/fade_signals` | shadow-signal arrows |

Toolbar: `[Replay][Skips][Conviction][Liquidity][Patterns][Exhaustion][Levels][Sessions][Trades][Fades]`
— default subset on; the user dials density. Layer state persists in localStorage.
Per-symbol chart precision already fixed (gold 2dp / JPY 3dp / FX 5dp).

## 6. The decision pipeline spine

The 6 stages (Sense · Read-move · Trend · Location · Decide · Act) are the per-tick
pipeline. Each node shows: real current value, verdict (pass/watch/block), and the
engine's ACTUAL gate/skip reason string (from `trigger_metrics` / entry decision).
This is the honest centerpiece — "what the system is doing this instant."

## 7. Tabs (feature-separated; all engine visible)

- **Cockpit** — live per-symbol instrument: pipeline spine (top) + fused chart (center,
  dominant) + compact telemetry rails (trigger, trade state, regime, swing, levels,
  microstructure, news/events, sessions).
- **Engine** — the machine's guts: full 7-layer pipeline internals with LEDs + live
  values (velocity, regime, MTF, liquidity, entry SM, health, exit mgr), shadow-detector
  status (exhaustion/fade signal counts), active-trade lifecycle, reason stream.
- **Portfolio** — real money state: account, risk breaker (armed/tripped + day PnL vs
  limit), open positions, resting orders, per-symbol exposure.
- **System** — infra truth: WS/daemon connections, feed + exec terminal identity, last-
  tick age, candle cache, latency (reversal/broadcast ms), uptime.
- **Journal** — realized history: P&L calendar, trade log, system console.

Symbol rail (persistent, above tabs): 5 daemon chips showing each daemon's REAL state
(price, quality, entry_state), active one accented.

## 8. State-honesty implementation map

| Truth | Signal | UI |
|---|---|---|
| Live vs simulated | `is_synthetic` (tick) | header state pill: `LIVE` / `SIMULATED` |
| Warm-up | z-score n<500 | `WARMING` on velocity node |
| Stale feed | last-tick age > threshold | `STALE {age}s` on core feed |
| Value missing | null/NaN | `--` (never a fake number) |
| Terminal routing | feed vs exec identity | System tab shows both, distinct |

## 9. Out of scope / constraints

- No engine, gate, exit, or threshold change. Freeze-safe.
- No new backend endpoints required (all sources already exist on `clean`).
- Not committed until the user approves both this spec and the built result.

## 10. Risks

- **Blind visual verification.** The harness cannot preview localhost; the user is the
  visual loop. Mitigation: incremental port, structural checks (`node --check`, tag/ID
  balance) each step, small reviewable diffs, user eyeballs after each.
- **Single large file.** `index.html` is ~800 lines. Mitigation: keep the port section-by-
  section, preserve IDs, never touch the WS/update JS.
