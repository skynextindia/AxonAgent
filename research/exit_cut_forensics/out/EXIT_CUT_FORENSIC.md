# EXIT-CUT FORENSIC — would a "level broke → cut" rule flip the lead?

_Read-only measurement. No production file, config, or order touched. Consumes the reconstructed lead trades only._

## (a) The tail — full history, from exit_reason

- Total lead loss: **-1155.0 p**
- Full-stop losers (all): -1031.8 p across 72 trades
- **Full-stop SELL losers: -607.9 p = 52% of ALL lead loss** (37 trades)

The breakout-fade SELL that rides to the full stop is the single largest loss source. That is the tail the cut rule targets.

## (b) The cut simulation — MFE/MAE subset

Subset with MAE + level data: **n=66**, span 2026-08-10 … 2026-08-19 (single adverse regime — see caveats).

Rule: a fade expects its S/R level to hold; if adverse excursion breaks that level by `buffer`, exit at `-(level_dist+buffer)` instead of the full stop. Two bounds because MFE/MAE give magnitude, not order:

- **naive** = cut fires on the first break regardless of final outcome (realistic for a rule that can't see the future) — *lower bound*.
- **optimistic** = cut only the trades that actually ended as losses (a perfect break-detector) — *upper bound*.

| buffer | fired | savers | casualties | pips saved | pips cost | **net (naive)** | net (optimistic) |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1p ⭐ | 24 | 16 | 8 | +168.5 | -28.6 | **+139.9** | +168.5 |
| 2p | 21 | 11 | 9 | +153.8 | -33.4 | **+120.4** | +153.8 |
| 3p | 14 | 10 | 4 | +143.6 | -24.1 | **+119.5** | +143.6 |
| 4p | 13 | 9 | 4 | +134.2 | -28.1 | **+106.1** | +134.2 |
| 5p | 10 | 8 | 2 | +125.2 | -5.0 | **+120.3** | +125.2 |
| 6p | 9 | 8 | 1 | +117.2 | -5.9 | **+111.3** | +117.2 |
| 8p | 8 | 7 | 1 | +102.6 | -7.9 | **+94.7** | +102.6 |

**Best buffer = 1p** → naive net **+139.9 p** (rescues +168.5p on 16 losers, costs -28.6p on 8 break-then-revert winners); optimistic ceiling +168.5p.

### Per-trade at buffer=1p (fired only, worst delta first)

| dir | sym | actual | cut→ | delta | kind | lvlDist | MAE |
|---|---|---:|---:|---:|---|---:|---:|
| SELL | EURUSD | +5.4 | -2.7 | -8.1 | casualty | 1.7 | 5.8 |
| SELL | EURUSD | +0.9 | -3.4 | -4.3 | casualty | 2.4 | 6.9 |
| SELL | EURUSD | +1.0 | -3.1 | -4.1 | casualty | 2.1 | 4.1 |
| SELL | USDJPY | +0.9 | -2.7 | -3.6 | casualty | 1.7 | 4.1 |
| SELL | EURUSD | +0.9 | -1.9 | -2.8 | casualty | 0.9 | 5.1 |
| SELL | EURUSD | +0.8 | -1.6 | -2.4 | casualty | 0.6 | 2.3 |
| SELL | EURUSD | +1.0 | -1.4 | -2.4 | casualty | 0.4 | 2.4 |
| SELL | EURUSD | -2.8 | -3.7 | -0.9 | casualty | 2.7 | 16.2 |
| BUY | EURUSD | -2.0 | -1.8 | +0.2 | saver | 0.8 | 2.0 |
| BUY | EURUSD | -3.2 | -2.6 | +0.6 | saver | 1.6 | 3.1 |
| SELL | EURUSD | -1.9 | -1.0 | +0.9 | saver | 0.0 | 2.1 |
| BUY | EURUSD | -2.3 | -1.4 | +0.9 | saver | 0.3 | 3.2 |
| SELL | EURUSD | -2.0 | -1.0 | +1.0 | saver | 0.0 | 2.1 |
| SELL | EURUSD | -2.2 | -1.0 | +1.2 | saver | 0.0 | 2.1 |
| SELL | USDJPY | -5.3 | -3.0 | +2.4 | saver | 1.9 | 5.3 |
| SELL | USDJPY | -5.0 | -1.1 | +4.0 | saver | 0.1 | 5.2 |
| BUY | EURUSD | -6.8 | -1.2 | +5.6 | saver | 0.2 | 6.8 |
| SELL | USDJPY | -11.4 | -3.6 | +7.8 | saver | 2.6 | 11.5 |
| SELL | EURUSD | -20.0 | -4.3 | +15.7 | saver | 3.3 | 18.7 |
| SELL | EURUSD | -20.2 | -4.4 | +15.8 | saver | 3.4 | 19.8 |
| SELL | USDJPY | -30.6 | -4.1 | +26.5 | saver | 3.1 | 29.9 |
| SELL | USDJPY | -30.1 | -2.3 | +27.8 | saver | 1.3 | 29.5 |
| SELL | USDJPY | -30.8 | -1.9 | +28.9 | saver | 0.9 | 29.7 |
| SELL | USDJPY | -30.3 | -1.1 | +29.1 | saver | 0.1 | 29.5 |

## Caveats (why this is measurement, not a green light)

1. **Order unknown.** MFE/MAE are magnitudes; we can't prove a break preceded a profit. The naive net is the honest realistic figure; the true value sits between naive and optimistic.
2. **One regime.** The MFE/MAE subset is a single adverse ~10-day window. A buffer tuned here is in-sample — it is a size estimate, not a validated parameter.
3. **Slippage/fill** at the break is modeled as exactly `level_dist+buffer`; a real cut fills slightly worse.
4. Applies only where the faded level is on the ADVERSE side (true breakout-fade geometry); sell-into-support trades are excluded by construction.

_Next gate before any arming: log MFE/MAE + a coarse adverse-path timestamp on EVERY trade so order is known and a second regime accrues._
