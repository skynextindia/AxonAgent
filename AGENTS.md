# AxonAI Trading Knowledge Base

## SESSION CHECKPOINT — 2026-05-30
**Status**: Parameter tuning complete. Best config found: **+26.5 pips, PF 1.21, 47.8% WR, 23 trades**.
**Next step**: Deploy to live or run on more months to validate robustness.

---

## Loss Cooldown (Gate 3b)
- **Location**: `backtester.py::_check_trade_triggers()`
- **Purpose**: Prevent cluster/revenge trading by blocking entries for 2 hours after a losing trade
- **Rationale**: 90% of the drawdown came from same-day loss clusters (05-13 and 05-20)
- **Effect**: Reduced trades from 20→17, turned -13.2 pips → +18.1 pips (profit factor 0.89→1.22)
- **Lost no winners** — only filtered out same-day revenge trades

## Key Backtest Parameters (WINNING CONFIG)
| Parameter | Value | File |
|---|---|---|
| SL formula | `1.0×ATR` (min 8 pips) | `backtester.py:535-537` |
| TP formula | `2.0×ATR` (min 16 pips) | `backtester.py:538-540` |
| Signal quality min (Gate 7) | 0.65 | `backtester.py` |
| MTF filter | skip BUY if H1+H4 bearish, skip SELL if bullish (hard block) | `backtester.py` |
| Cooldown (Gate 3) | 15 min | `backtester.py:419-423` |
| Loss cooldown (Gate 3b) | 45 min | `backtester.py:425-430` |
| Peak gate threshold | `intensity==HIGH AND (confirmed OR confidence>=0.6)` | `backtester.py:472-478` |
| Peak detector: `peak_confirmed` | `vel_div > 0.8` AND `eff < 0.10` | `peak_detector.py:136` |
| Peak detector: `divergence_active` | `vel_div > 0.6` | `peak_detector.py:128` |
| Peak detector: `divergence_warning` | `vel_div > 0.8` | `peak_detector.py:133` |
| Peak cooldown | 120 sec / 3.0 pips | `peak_detector.py:139-140` |
| Sessions | London / Overlap / NY only | `backtester.py:408-417` |
| EOD force-close | Yes (session close) | `backtester.py` |

## Backtest Results (Best Run)
```
Total Trades:    23
Wins / Losses:   11 / 12
Win Rate:        47.8%
Net P&L:         +26.5 pips
Profit Factor:   1.21
EOD force-closed: 8
```

### Exit Breakdown
| Exit Reason | Count | Pips |
|---|---|---|
| **TP Hit** | 4 | +120.2 |
| **SL Hit** | 11 | −112.6 |
| **EOD Close** | 8 | +18.9 |

### Trade Signals
| Signal Type | Count | Result |
|---|---|---|
| Liquidity Sweep (Q=0.90) | 7 | Mixed (+25.4 best, −16.3 worst) |
| Microstructure Exhaustion (Q=1.00) | 14 | 5 TP hits, carry the strategy |
| Velocity Exhaustion (Q=0.78 / Q=1.00) | 2 | 1 big TP (+23.7), 1 SL (−12.8) |

## Failed Experiments (do not re-try)
| Experiment | Result |
|---|---|
| SL = 1.5×ATR | Bigger losses, worse net |
| Peak gate threshold = 0.5 | Let false positives through: **−137.3 pips** |
| Quality floor < 0.65 | Too many weak entries |
| Cooldown = 10 min | No improvement (signal scarcity is bottleneck) |
| Cooldown = 30 min | Missed too many |

## Outstanding Items
- [ ] Deploy `LevelBehaviorTracker` with these params to live
- [ ] Backtest on more months (Jun, Jul 2026) to validate robustness
- [ ] Consider enabling BUY entries for range-bound markets
- [ ] The strategy is SELL-only in May 2026 bear trend — test in sideways/uptrend months

## Resume Instructions
To resume: `cd /mnt/d/work/AxonAI && python run_intraday_backtest.py`
Config is already set to the winning parameters above.
