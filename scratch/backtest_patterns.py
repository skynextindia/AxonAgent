#!/usr/bin/env python
"""
backtest_patterns.py -- standalone, look-ahead-free backtest harness for chart
patterns driven off the per-tick engine snapshots.

READ-ONLY. Touches nothing under axonai/. Never runs the engine, never trades.

    python scratch/backtest_patterns.py --symbol GBPUSD --timeframe M5 --seed 42 --json out.json
    python scratch/backtest_patterns.py --pool --timeframe M5 --seed 42 --json pooled.json

ITERATION 2 CHANGES (see the numbered list below for the iteration-1 baseline)
-----------------------------------------------------------------------------
 A. --timeframe {M5,M15}. Bars bucket as (epoch//N)*N; the min-ticks floor and
    the max-gap segmentation are applied at the chosen timeframe. The zigzag
    threshold is scaled by 1/sqrt(3) at M5 -- see ZIGZAG_TF_SCALE.
 B. --pool. All symbols, pooled with PATTERN TYPE as the unit of analysis and
    every outcome expressed in R-multiples so instruments are commensurable.
 C. Per-cell content-keyed RNG. Iteration 1 threaded ONE random.Random through
    every symbol, so p-values depended on evaluation order and were not
    reproducible at a fixed seed. See make_rng().
 D. Truncated trades are marked to market at the last available close and
    COUNTED in n. Iteration 1 dropped them, conditioning inclusion on
    post-entry price action. See simulate().
 E. Test surface cut from 4 exit rules to 2, with the pre-declared cell count
    and the Bonferroni-corrected alpha both written into the JSON meta.

WHAT THIS FIXES RELATIVE TO THE LIVE /api/patterns DETECTOR
-----------------------------------------------------------
Every numbered item maps to a leakage risk reported in the reconnaissance.

 1. PIVOT CONFIRMATION INDEX. The live zigzag appends a pivot at bar `exti` but
    only discovers it at a later bar once price retraced >= thr. It then starts
    the break search at `exti`, so a pattern could be "confirmed" by a break
    that happened before the pattern was knowable. Here every pivot carries
    both `bar` (where the extreme sits) and `confirm` (the bar at whose CLOSE
    the retracement completed). A pattern's formation index is the confirm
    index of its LAST pivot, and no forward scan ever starts before that.

 2. CAUSAL DIRECTION. rectangle and sym_triangle have no a-priori direction.
    The live code calls _first_break() which looks 40 bars into the future and
    hands back whichever side broke -- the prediction is read from the outcome.
    Here the forward walk assigns direction at the bar the breach is OBSERVED,
    which is knowable at that bar's close. Same answer, causally obtained.

 3. NO SURVIVORSHIP. Live code drops patterns that never broke. Here every
    formed pattern is logged; no break within LOOK bars -> outcome NO_BREAK,
    and it counts in the denominator.

 4. REAL EXIT SIMULATION, NOT MFE. Live "hit" == target touched at any point in
    the next 60 bars, with no stop and no ordering test. Here each trade is
    walked bar by bar against a stop AND a target; if both are touched inside
    one bar the bar is resolved pessimistically as a STOP. Exit reasons are
    TARGET / STOP / TIME / TRUNCATED.

 5. FILL PRICE, NOT GEOMETRIC LEVEL. Live P&L is measured from `level`. The
    break is only knowable at the break bar's CLOSE, so the earliest honest
    fill is the NEXT bar's OPEN. That is the entry price used throughout. If no
    next bar exists in the segment the signal is NO_ENTRY.

 6. SINGLE FORWARD PASS FROM A FIXED ORIGIN. Live code anchors its window to
    the END of the data (`sorted(bars)[-days*96:]`) and re-seeds the zigzag at
    that moving left edge, so historical labels get rewritten as new data
    arrives. Here the pass runs forward from the start of each contiguous
    segment and is never re-seeded. Also fixes the trend=0 seed that forced the
    first pivot of every run to be a TOP.

 7. TIME-BOUNDED WINDOWS. LOOK/OUTW count bars, and bars only exist where ticks
    landed, so "40 bars" could span a weekend. Here every forward scan is
    confined to one contiguous SEGMENT; segments break at any gap > --max-gap-s.

 8. CHRONOLOGICAL CLOSES. Live code merges rotated files in mtime order and
    assigns bar close from the last tick PROCESSED, not the last in time. Here
    ticks are deduplicated on the full sub-second timestamp and sorted by time
    before bucketing, so open/close are genuinely first/last chronologically.

 9. WIDTH-AWARE PARSING. The *_pre_location files mix 25-col and 33-col rows
    under a 25-col header. Columns are resolved per row by len(row); unknown
    widths are counted and skipped.

10. SYNTHETIC DATA EXCLUDED. The 2026-07-18 block in every live file is a
    random walk (fixed +/-0.1-pip steps, tick_rate pinned to 1/s). Cut by hard
    epoch bound plus a defensive price-step signature check.

11. MINIMUM PATTERN SIZE. Live code has no minimum pivot separation and no
    minimum height, so a "head and shoulders" can be 5 consecutive bars with a
    target smaller than the spread. Enforced here via --min-pivot-sep and a
    target that must clear --min-target-spreads x spread.

12. COSTS. Live code has none anywhere. A per-symbol round-trip spread is
    charged once per trade; gross and net expectancy are both reported.

13. BASELINE. Pattern edge is meaningless without a null. For each cell the
    same exit rule, the same direction mix and the same stop/target DISTANCES
    are replayed at random bars, many times, seeded from --seed. An empirical
    p-value is reported.

14. IS/OOS. Each symbol's bar series is split chronologically 70/30 and both
    halves are reported. An edge present only in-sample is noise.

15. SAMPLE SIZE. Every cell reports n and is flagged `underpowered` when n<20.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import glob
import hashlib
import json
import math
import os
import random
import statistics
import sys
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------
# CONSTANTS -- all pre-declared, nothing tuned after seeing results.
# --------------------------------------------------------------------------

REPO = "D:/AXON.AI/AxonAgent-Agy"
REPORTS = os.path.join(REPO, "reports")

ALL_SYMBOLS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]

# 2026-07-18 00:00:00 UTC. Everything at/after this is the confirmed synthetic
# random-walk block that sits inside the LIVE files.
SYNTHETIC_EPOCH_CUTOFF = 1784332800

# Known snapshot schemas. Keyed by row width. timestamp=0 and price=1 in all
# four, but we validate the width so genuinely malformed rows are dropped.
KNOWN_WIDTHS = (25, 33, 39, 41)

# Bucketing is (epoch // N) * N, matching the live convention at N=900.
TIMEFRAMES = {"M5": 300, "M15": 900}

# Zigzag swing threshold in pips. These are the LIVE hardcoded values, kept as
# defaults so results are comparable to the live detector. They are in-sample
# with respect to this data; --zigzag-pips overrides, and the OOS split is what
# actually validates them.
ZIGZAG_PIPS = {"XAUUSD": 120.0, "USDJPY": 12.0}
ZIGZAG_PIPS_DEFAULT = 8.0

# --------------------------------------------------------------------------
# M5 ZIGZAG SCALING -- pre-declared, derived, NOT tuned on outcomes.
#
# A zigzag threshold is a swing-amplitude filter, so it must scale with the
# typical amplitude of price change over one bar, not with the bar count. Under
# a diffusion, amplitude scales as sqrt(dt), so moving M15 -> M5 (dt / 3) gives
#     thr_M5 = thr_M15 / sqrt(3) = 0.5774 * thr_M15.
# Leaving the 8-pip M15 threshold in place at M5 would demand a swing ~1.7x
# larger than the timeframe naturally produces and would starve the pivot
# stream; that is the "over-fire"/under-fire failure the brief warns about, in
# the opposite direction to naive intuition (an unscaled threshold under-fires
# pivots, which then over-fires nothing at all).
#
# The sqrt(3) figure is CONFIRMED by this dataset's own fixed-clock variance
# scaling (data validity gate, check "fixed-clock variance scaling"), which
# reports var/dt at h=300s and h=900s. Converting to a standard-deviation ratio
# sd(300)/sd(900) = sqrt(300*vr300 / 900*vr900):
#     EURUSD sqrt(300*4.22 / 900*4.07) = 0.588
#     GBPUSD sqrt(300*4.98 / 900*4.53) = 0.605
#     AUDUSD sqrt(300*2.62 / 900*2.54) = 0.586
#     USDJPY sqrt(300*5.38 / 900*4.98) = 0.600
# against the theoretical 0.577. The empirical ratios are 1.6-4.9% above the
# diffusion value (mild sublinearity from short-horizon mean reversion), so
# 1/sqrt(3) is very slightly conservative -- it yields marginally MORE pivots
# than the observed volatility ratio would. We keep the closed-form 1/sqrt(3)
# rather than a per-symbol empirical ratio precisely because it cannot be
# accused of having been fitted to this sample.
ZIGZAG_TF_SCALE = {"M15": 1.0, "M5": 1.0 / math.sqrt(3.0)}

# Round-trip spread charged once per trade, in pips. No per-symbol spread table
# exists anywhere in the repo; these follow the repo's own working assumption
# of ~1 pip FX / ~3 pips gold (backtester.py uses a 0.5-pip half-spread).
SPREAD_PIPS = {
    "EURUSD": 1.0,
    "GBPUSD": 1.2,
    "AUDUSD": 1.2,
    "USDJPY": 1.0,
    "XAUUSD": 3.0,
}
SPREAD_PIPS_DEFAULT = 1.5

# Geometry tolerances, mirroring the live detector so the shapes are the same
# shapes. Declared here, not tuned.
TOL_DOUBLE = 0.40   # |peak1-peak2| <= TOL_DOUBLE * height
TOL_TRIPLE = 0.30   # pairwise peak spread <= TOL_TRIPLE * span
TOL_SHOULDER = 0.35  # |left-right shoulder| <= TOL_SHOULDER * head height
TOL_FLAT = 0.12     # trendline flatness <= TOL_FLAT * span

LOOK_BARS = 40      # bars after formation in which a break may occur

# --------------------------------------------------------------------------
# THE EXIT GRID -- fixed and pre-declared. Identical for every pattern, every
# symbol, every split, and for the random baseline. Nothing here was chosen
# after looking at results.
#
#   stop  : always the pattern's opposing structural boundary.
#   target: either the classic measured move, or a fixed multiple of the
#           stop distance R. Both are declared up front; reporting both stops
#           the measured move from being the only lens on the data.
#   cap   : hard time cap in BARS, so no trade is held indefinitely. Bars, not
#           wall-clock: at M15 24 bars is 6h, at M5 it is 2h. Everything else
#           measured in bars (LOOK_BARS, min_pivot_sep) is likewise
#           timeframe-relative, which keeps the test self-consistent at both
#           resolutions.
#
# ITERATION 2: the grid was cut from 4 exit rules to 2. Iteration 1 ran 260
# cells, which no honest multiplicity correction can survive on this much data.
# The two survivors are the two genuinely different lenses: the classic
# measured move (target set by pattern geometry) and a fixed 2R target (target
# set by the risk taken). R1 was dropped because at a 1:1 payoff the result is
# a pure win-rate restatement of R2, and the 60-bar cap was dropped because it
# tests holding period, not pattern edge.
# --------------------------------------------------------------------------
GRID: Tuple[Tuple[str, str, int], ...] = (
    # (cell_name, target_mode, time_cap_bars)
    ("MM_STRUCT_24", "measured_move", 24),
    ("R2_STRUCT_24", "r2", 24),
)

# Every pattern type the classifier can emit. Fixed and known before the data
# is read, so the pre-declared cell count does not depend on what the sample
# happens to contain.
PATTERN_UNIVERSE: Tuple[str, ...] = (
    "double_top", "double_bottom",
    "rectangle", "asc_triangle", "desc_triangle", "sym_triangle",
    "rising_wedge", "falling_wedge",
    "triple_top", "triple_bottom", "head_shoulders", "inv_head_shoulders",
)

SPLITS: Tuple[str, ...] = ("ALL", "IS", "OOS")

FAMILY_ALPHA = 0.05

UNDERPOWERED_N = 20


def make_rng(seed: int, *parts) -> random.Random:
    """Deterministic, order-independent RNG.

    ITERATION 1 BUG: a single random.Random was threaded through every symbol
    and every cell in sequence, so a cell's draws depended on how many draws
    every preceding cell had made. Adding, removing or reordering a symbol
    silently changed every downstream p-value, and 49 of 60 EURUSD cells moved
    between two invocations at the same seed.

    Here the stream is keyed by content, not by position: the seed and the
    identifying parts (symbol, cell, split, pattern) are hashed with SHA-256 to
    a 64-bit seed. Python's builtin hash() is NOT usable -- string hashing is
    salted per process, so it is not reproducible across invocations.
    """
    key = "|".join([str(seed)] + [str(p) for p in parts])
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def pip_size(symbol: str) -> float:
    s = symbol.upper()
    return 0.01 if ("JPY" in s or "XAU" in s) else 0.0001


# --------------------------------------------------------------------------
# DATA LOADING
# --------------------------------------------------------------------------

@dataclass
class LoadStats:
    files: List[str] = field(default_factory=list)
    rows_read: int = 0
    rows_bad_width: int = 0
    rows_bad_timestamp: int = 0
    rows_bad_price: int = 0
    rows_synthetic_epoch: int = 0
    rows_synthetic_signature: int = 0
    rows_duplicate: int = 0
    ticks_kept: int = 0


def _parse_epoch(ts: str) -> Optional[float]:
    """Timestamps carry sub-second precision ('%Y-%m-%d %H:%M:%S.%f').

    The brief documents '%Y-%m-%d %H:%M:%S', which fails on 100% of rows.
    Split the fractional part off, then use calendar.timegm (UTC).
    """
    ts = ts.strip()
    if len(ts) < 19:
        return None
    base = ts[:19]
    frac = 0.0
    if len(ts) > 20 and ts[19] == ".":
        try:
            frac = float("0." + ts[20:])
        except ValueError:
            frac = 0.0
    try:
        return calendar.timegm(time.strptime(base, "%Y-%m-%d %H:%M:%S")) + frac
    except ValueError:
        return None


def load_ticks(symbol: str, stats: LoadStats,
               drop_synthetic: bool = True) -> List[Tuple[float, float]]:
    """Return chronologically sorted, de-duplicated (epoch, price) ticks.

    Deduplication key is the FULL sub-second epoch, not int(epoch). Deduping on
    the integer second is lossy -- on XAUUSD it would discard ~72% of genuine
    distinct ticks that merely share a second with a neighbour.
    """
    pattern = os.path.join(REPORTS, "engine_snapshots_%s*.csv" % symbol.upper())
    files = sorted(glob.glob(pattern))
    if not files:
        raise SystemExit("no snapshot files matched: %s" % pattern)
    stats.files = [p.replace("\\", "/") for p in files]

    seen: Dict[float, float] = {}
    for path in files:
        with open(path, "r", newline="", encoding="utf-8", errors="replace") as fh:
            rdr = csv.reader(fh)
            try:
                next(rdr)  # header -- deliberately ignored, widths vary per row
            except StopIteration:
                continue
            for row in rdr:
                stats.rows_read += 1
                # Resolve schema by ROW WIDTH, never by the file header. The
                # *_pre_location files carry 33-col rows under a 25-col header.
                if len(row) not in KNOWN_WIDTHS:
                    stats.rows_bad_width += 1
                    continue
                ep = _parse_epoch(row[0])
                if ep is None:
                    stats.rows_bad_timestamp += 1
                    continue
                if drop_synthetic and ep >= SYNTHETIC_EPOCH_CUTOFF:
                    stats.rows_synthetic_epoch += 1
                    continue
                try:
                    px = float(row[1])
                except (ValueError, IndexError):
                    stats.rows_bad_price += 1
                    continue
                if px <= 0.0 or not math.isfinite(px):
                    stats.rows_bad_price += 1
                    continue
                if ep in seen:
                    stats.rows_duplicate += 1
                    continue
                seen[ep] = px

    ticks = sorted(seen.items())

    if drop_synthetic:
        ticks, n_sig = _strip_synthetic_signature(ticks, symbol)
        stats.rows_synthetic_signature = n_sig

    stats.ticks_kept = len(ticks)
    return ticks


def _strip_synthetic_signature(ticks: List[Tuple[float, float]],
                               symbol: str) -> Tuple[List[Tuple[float, float]], int]:
    """Defensive second filter for random-walk stretches.

    The generator emits price steps of exactly 0 or +/-1 point regardless of
    instrument, and ~1 tick/sec. Scan in 10-minute windows; drop a window when
    nearly every step is <= 0.1 pip AND the mean inter-tick gap is ~1s. This is
    scale-aware, so it catches a synthetic block on any weekday too -- the hard
    epoch cutoff alone would not.
    """
    if not ticks:
        return ticks, 0
    pip = pip_size(symbol)
    win = 600.0
    keep: List[Tuple[float, float]] = []
    dropped = 0
    i = 0
    n = len(ticks)
    while i < n:
        j = i
        t0 = ticks[i][0]
        while j < n and ticks[j][0] - t0 < win:
            j += 1
        block = ticks[i:j]
        if len(block) >= 60:
            small = 0
            for k in range(1, len(block)):
                if abs(block[k][1] - block[k - 1][1]) <= 0.1 * pip + 1e-12:
                    small += 1
            frac_small = small / float(len(block) - 1)
            span = block[-1][0] - block[0][0]
            mean_dt = span / float(len(block) - 1) if len(block) > 1 else 0.0
            if frac_small >= 0.95 and 0.5 <= mean_dt <= 1.5:
                dropped += len(block)
                i = j
                continue
        keep.extend(block)
        i = j
    return keep, dropped


# --------------------------------------------------------------------------
# BAR CONSTRUCTION
# --------------------------------------------------------------------------

@dataclass
class Bar:
    bucket: int
    o: float
    h: float
    l: float
    c: float
    ticks: int
    seg: int = -1


def build_bars(ticks: Sequence[Tuple[float, float]], min_ticks: int,
               max_gap_s: int, drop_edges: int, min_segment_bars: int,
               bar_seconds: int) -> Tuple[List[Bar], Dict[str, int]]:
    """OHLC from chronologically ordered ticks, then segmented on gaps.

    `bar_seconds` is 900 (M15) or 300 (M5); bucketing is (epoch // N) * N in
    both cases. The min-ticks floor and the max-gap segmentation are applied
    AT THE CHOSEN TIMEFRAME, i.e. after re-bucketing, not inherited from M15.

    Ticks arrive sorted by time, so `o` is genuinely the first tick of the
    bucket and `c` genuinely the last -- unlike the live code, where merging
    rotated files in mtime order makes `c` the last tick PROCESSED.
    """
    agg: Dict[int, List[float]] = {}
    for ep, px in ticks:
        bk = (int(ep) // bar_seconds) * bar_seconds
        b = agg.get(bk)
        if b is None:
            agg[bk] = [px, px, px, px, 1.0]
        else:
            if px > b[1]:
                b[1] = px
            if px < b[2]:
                b[2] = px
            b[3] = px
            b[4] += 1.0

    bars = [Bar(bk, v[0], v[1], v[2], v[3], int(v[4]))
            for bk, v in sorted(agg.items())]
    diag = {"bars_raw": len(bars)}

    # Thin buckets are not tradeable and their OHLC is not meaningful.
    bars = [b for b in bars if b.ticks >= min_ticks]
    diag["bars_after_min_ticks"] = len(bars)

    # Segment on time discontinuity. State must NEVER be carried across a
    # weekend or a 5-hour engine outage.
    segs: List[List[Bar]] = []
    cur: List[Bar] = []
    prev = None
    for b in bars:
        if prev is not None and (b.bucket - prev) > max_gap_s:
            segs.append(cur)
            cur = []
        cur.append(b)
        prev = b.bucket
    if cur:
        segs.append(cur)

    # Every segment's first and last buckets are partial by construction.
    out: List[Bar] = []
    sid = 0
    for s in segs:
        if drop_edges > 0:
            s = s[drop_edges:len(s) - drop_edges] if len(s) > 2 * drop_edges else []
        if len(s) < min_segment_bars:
            continue
        for b in s:
            b.seg = sid
            out.append(b)
        sid += 1

    diag["segments"] = sid
    diag["bars_final"] = len(out)
    return out, diag


# --------------------------------------------------------------------------
# CAUSAL ZIGZAG
# --------------------------------------------------------------------------

@dataclass
class Pivot:
    bar: int      # index of the extreme bar
    confirm: int  # index of the bar at whose CLOSE this pivot became knowable
    kind: str     # "TOP" | "BOTTOM"
    px: float     # bar high for TOP, bar low for BOTTOM


def zigzag(bars: Sequence[Bar], lo_i: int, hi_i: int, thr: float) -> List[Pivot]:
    """Forward-only zigzag over [lo_i, hi_i), run per segment, never re-seeded.

    Two departures from the live implementation:

      * `confirm` is recorded. The live code discards it, which is the single
        largest source of look-ahead downstream.
      * `trend` starts genuinely neutral. The live code seeds trend=0 and tests
        `trend >= 0`, which forces the first pivot of every run to be a TOP.
        Here both directions are tracked until one retraces by thr.

    The stored price is the extreme bar's HIGH/LOW. That is not look-ahead:
    the bar's high is a completed fact at that bar's close, and nothing may
    reference the pivot before `confirm` anyway.
    """
    piv: List[Pivot] = []
    if hi_i - lo_i < 3:
        return piv

    trend = 0
    hi = lo = bars[lo_i].c
    hii = loi = lo_i
    ext = bars[lo_i].c
    exti = lo_i

    for i in range(lo_i + 1, hi_i):
        c = bars[i].c
        if trend == 0:
            if c > hi:
                hi, hii = c, i
            if c < lo:
                lo, loi = c, i
            if hi - c >= thr:
                piv.append(Pivot(hii, i, "TOP", bars[hii].h))
                trend, ext, exti = -1, c, i
            elif c - lo >= thr:
                piv.append(Pivot(loi, i, "BOTTOM", bars[loi].l))
                trend, ext, exti = 1, c, i
        elif trend > 0:
            if c > ext:
                ext, exti = c, i
            elif ext - c >= thr:
                piv.append(Pivot(exti, i, "TOP", bars[exti].h))
                trend, ext, exti = -1, c, i
        else:
            if c < ext:
                ext, exti = c, i
            elif c - ext >= thr:
                piv.append(Pivot(exti, i, "BOTTOM", bars[exti].l))
                trend, ext, exti = 1, c, i

    # The still-forming final extreme is deliberately NOT emitted.
    return piv


# --------------------------------------------------------------------------
# PATTERN GEOMETRY
# --------------------------------------------------------------------------

@dataclass
class Candidate:
    ptype: str
    formed: int              # bar index at which the pattern became knowable
    direction: Optional[str]  # "BUY" | "SELL" | None (resolved at break)
    level: Optional[float]    # break trigger level, None if direction is None
    target: Optional[float]
    stop: Optional[float]
    up_level: Optional[float] = None   # for direction-at-break patterns
    dn_level: Optional[float] = None
    span: float = 0.0
    first_bar: int = 0


def _sep_ok(pv: Sequence[Pivot], min_sep: int) -> bool:
    for a, b in zip(pv, pv[1:]):
        if b.bar - a.bar < min_sep:
            return False
    return True


def classify(pv: Sequence[Pivot], min_sep: int) -> List[Candidate]:
    """Emit every pattern whose LAST pivot is pv[-1].

    Called once per newly confirmed pivot, so formation time is well defined.
    Mirrors the live shape definitions and their if/elif precedence, so the
    geometry is directly comparable; only the causality is different.
    """
    out: List[Candidate] = []
    n = len(pv)

    # ---- 3-pivot family -------------------------------------------------
    if n >= 3:
        w = pv[-3:]
        if _sep_ok(w, min_sep):
            a, b, c = w
            formed = c.confirm
            if a.kind == "TOP" and c.kind == "TOP":
                h = (a.px + c.px) / 2.0 - b.px
                if h > 0 and abs(a.px - c.px) <= TOL_DOUBLE * h:
                    out.append(Candidate("double_top", formed, "SELL",
                                         b.px, b.px - h, max(a.px, c.px),
                                         span=h, first_bar=a.bar))
            elif a.kind == "BOTTOM" and c.kind == "BOTTOM":
                h = b.px - (a.px + c.px) / 2.0
                if h > 0 and abs(a.px - c.px) <= TOL_DOUBLE * h:
                    out.append(Candidate("double_bottom", formed, "BUY",
                                         b.px, b.px + h, min(a.px, c.px),
                                         span=h, first_bar=a.bar))

    # ---- 4-pivot family -------------------------------------------------
    if n >= 4:
        w = pv[-4:]
        if _sep_ok(w, min_sep):
            formed = w[3].confirm
            pr = [p.px for p in w]
            span = max(pr) - min(pr)
            if span > 0:
                if w[0].kind == "TOP":
                    hi1, hi2, lo1, lo2 = pr[0], pr[2], pr[1], pr[3]
                else:
                    lo1, lo2, hi1, hi2 = pr[0], pr[2], pr[1], pr[3]
                flat = TOL_FLAT * span
                dh = hi2 - hi1
                dl = lo2 - lo1
                fb = w[0].bar
                if abs(dh) < flat and abs(dl) < flat:
                    # rectangle: direction unknown at formation, resolved at
                    # the bar the first breach is OBSERVED.
                    out.append(Candidate("rectangle", formed, None, None, None,
                                         None, up_level=hi2, dn_level=lo2,
                                         span=span, first_bar=fb))
                elif abs(dh) < flat and dl > flat:
                    out.append(Candidate("asc_triangle", formed, "BUY", hi2,
                                         hi2 + span, lo2, span=span, first_bar=fb))
                elif abs(dl) < flat and dh < -flat:
                    out.append(Candidate("desc_triangle", formed, "SELL", lo2,
                                         lo2 - span, hi2, span=span, first_bar=fb))
                elif dh < -flat and dl > flat:
                    out.append(Candidate("sym_triangle", formed, None, None, None,
                                         None, up_level=hi2, dn_level=lo2,
                                         span=span, first_bar=fb))
                elif dh > flat and dl > flat and dl > dh:
                    out.append(Candidate("rising_wedge", formed, "SELL", lo2,
                                         lo2 - span, hi2, span=span, first_bar=fb))
                elif dh < -flat and dl < -flat and abs(dh) > abs(dl):
                    out.append(Candidate("falling_wedge", formed, "BUY", hi2,
                                         hi2 + span, lo2, span=span, first_bar=fb))

    # ---- 5-pivot family -------------------------------------------------
    if n >= 5:
        w = pv[-5:]
        if _sep_ok(w, min_sep):
            formed = w[4].confirm
            p = [x.px for x in w]
            fb = w[0].bar
            if w[0].kind == "TOP":
                t1, b1, t2, b2, t3 = p
                neck = (b1 + b2) / 2.0
                span = max(t1, t2, t3) - neck
                if span > 0:
                    spread = max(abs(t1 - t2), abs(t2 - t3), abs(t1 - t3))
                    if spread <= TOL_TRIPLE * span:
                        out.append(Candidate("triple_top", formed, "SELL", neck,
                                             neck - span, max(t1, t2, t3),
                                             span=span, first_bar=fb))
                    elif t2 > t1 and t2 > t3 and abs(t1 - t3) <= TOL_SHOULDER * (t2 - neck):
                        # stop = right shoulder, the nearest opposing structure
                        out.append(Candidate("head_shoulders", formed, "SELL", neck,
                                             neck - (t2 - neck), t3,
                                             span=t2 - neck, first_bar=fb))
            else:
                b1, t1, b2, t2, b3 = p
                neck = (t1 + t2) / 2.0
                span = neck - min(b1, b2, b3)
                if span > 0:
                    spread = max(abs(b1 - b2), abs(b2 - b3), abs(b1 - b3))
                    if spread <= TOL_TRIPLE * span:
                        out.append(Candidate("triple_bottom", formed, "BUY", neck,
                                             neck + span, min(b1, b2, b3),
                                             span=span, first_bar=fb))
                    elif b2 < b1 and b2 < b3 and abs(b1 - b3) <= TOL_SHOULDER * (neck - b2):
                        out.append(Candidate("inv_head_shoulders", formed, "BUY", neck,
                                             neck + (neck - b2), b3,
                                             span=neck - b2, first_bar=fb))
    return out


# --------------------------------------------------------------------------
# SIGNAL GENERATION -- strictly left to right
# --------------------------------------------------------------------------

@dataclass
class Signal:
    symbol: str
    ptype: str
    direction: str
    formed_bar: int
    break_bar: int
    entry_bar: int
    entry_ts: int
    entry_px: float
    level: float
    target: float
    stop: float
    span_pips: float
    status: str = "OK"   # OK | NO_BREAK | NO_ENTRY | BAD_GEOMETRY


def generate_signals(symbol: str, bars: Sequence[Bar], thr: float,
                     min_sep: int, look: int) -> Tuple[List[Signal], Dict[str, int]]:
    """Walk each segment once, left to right. Nothing here reads bars > the
    bar currently being decided on."""
    pip = pip_size(symbol)
    sigs: List[Signal] = []
    tally = {"formed": 0, "no_break": 0, "no_entry": 0, "bad_geometry": 0, "ok": 0}

    # segment boundaries
    seg_ranges: List[Tuple[int, int]] = []
    if bars:
        s0 = 0
        for i in range(1, len(bars)):
            if bars[i].seg != bars[i - 1].seg:
                seg_ranges.append((s0, i))
                s0 = i
        seg_ranges.append((s0, len(bars)))

    for lo_i, hi_i in seg_ranges:
        piv = zigzag(bars, lo_i, hi_i, thr)
        if len(piv) < 3:
            continue
        # Replay pivots in confirmation order. After each new confirmation the
        # pattern set knowable at that instant is exactly classify(piv[:k]).
        for k in range(3, len(piv) + 1):
            for cand in classify(piv[:k], min_sep):
                tally["formed"] += 1
                sig = _resolve_break(symbol, bars, cand, lo_i, hi_i, look, pip)
                if sig.status == "OK":
                    tally["ok"] += 1
                    sigs.append(sig)
                elif sig.status == "NO_BREAK":
                    tally["no_break"] += 1
                elif sig.status == "NO_ENTRY":
                    tally["no_entry"] += 1
                else:
                    tally["bad_geometry"] += 1

    # Two windows can produce the same trade. Collapse exact duplicates so the
    # same bar is not counted as several independent trials.
    uniq: Dict[Tuple[str, str, int], Signal] = {}
    for s in sigs:
        uniq.setdefault((s.ptype, s.direction, s.entry_bar), s)
    deduped = sorted(uniq.values(), key=lambda s: s.entry_bar)
    tally["deduped"] = len(deduped)
    return deduped, tally


def _resolve_break(symbol: str, bars: Sequence[Bar], c: Candidate,
                   lo_i: int, hi_i: int, look: int, pip: float) -> Signal:
    """Find the first confirming break at or after formation, inside the segment.

    Scan starts at c.formed (NOT at the last pivot's bar, which is the live
    bug). The break test uses the bar CLOSE, and c.formed is itself defined by
    a bar close, so both facts land at the same instant -- consistent.
    """
    blank = Signal(symbol, c.ptype, "?", c.formed, -1, -1, 0, 0.0, 0.0, 0.0, 0.0, 0.0)

    end = min(hi_i, c.formed + look + 1)
    bidx = -1
    direction = c.direction
    level = c.level
    target = c.target
    stop = c.stop

    for j in range(c.formed, end):
        cl = bars[j].c
        if direction is None:
            # rectangle / sym_triangle: whichever side is breached first WINS,
            # and that is known at bar j's close. No future is consulted.
            if cl > c.up_level:
                direction, level = "BUY", c.up_level
                target, stop = c.up_level + c.span, c.dn_level
                bidx = j
                break
            if cl < c.dn_level:
                direction, level = "SELL", c.dn_level
                target, stop = c.dn_level - c.span, c.up_level
                bidx = j
                break
        else:
            if direction == "SELL" and cl < level:
                bidx = j
                break
            if direction == "BUY" and cl > level:
                bidx = j
                break

    if bidx < 0:
        blank.status = "NO_BREAK"
        return blank

    # Earliest honest fill: the break is only knowable at bar bidx's CLOSE, so
    # entry is the OPEN of bidx+1. Never the geometric level.
    e = bidx + 1
    if e >= hi_i:
        blank.status = "NO_ENTRY"
        blank.break_bar = bidx
        return blank

    entry = bars[e].o
    # Reject geometry that the fill has already invalidated: target already
    # reached, or price already through the stop.
    if direction == "BUY":
        if not (target > entry > stop):
            blank.status = "BAD_GEOMETRY"
            return blank
    else:
        if not (target < entry < stop):
            blank.status = "BAD_GEOMETRY"
            return blank

    return Signal(symbol, c.ptype, direction, c.formed, bidx, e,
                  bars[e].bucket, entry, level, target, stop,
                  abs(c.span) / pip, "OK")


# --------------------------------------------------------------------------
# TRADE SIMULATION
# --------------------------------------------------------------------------

@dataclass
class TradeResult:
    outcome: str      # TARGET | STOP | TIME | TRUNCATED
    gross_pips: float
    net_pips: float
    bars_held: int
    risk_pips: float = 0.0   # entry-to-stop distance, the R unit for this trade
    gross_r: float = 0.0
    net_r: float = 0.0


def _finish(outcome: str, direction: str, entry: float, exit_px: float,
            pip: float, spread: float, risk_pips: float,
            bars_held: int) -> TradeResult:
    """Score one closed trade in BOTH pips and R-multiples.

    R = the entry-to-stop distance in pips, i.e. the risk actually taken on
    that specific trade. Dividing by it is what makes a 3-pip EURUSD outcome
    and a 40-cent XAUUSD outcome commensurable, which is the precondition for
    pooling symbols at all. The spread is charged inside the R conversion too,
    so a wide-stop trade is correctly charged a SMALLER fraction of an R than a
    tight-stop trade for the same absolute spread.
    """
    g = _pips(direction, entry, exit_px, pip)
    n = g - spread
    if risk_pips > 0:
        gr, nr = g / risk_pips, n / risk_pips
    else:
        gr = nr = 0.0
    return TradeResult(outcome, g, n, bars_held, risk_pips, gr, nr)


def simulate(bars: Sequence[Bar], seg_end: int, entry_bar: int, direction: str,
             entry: float, target: float, stop: float, cap: int,
             pip: float, spread: float) -> TradeResult:
    """Bar-by-bar resolution against BOTH stop and target.

    Starts at entry_bar itself (we bought its open, so the rest of that bar is
    live). If a single bar touches both levels the intrabar path is unknown, so
    it resolves as a STOP -- the pessimistic convention. This is the direct
    replacement for the live code's max-favourable-excursion scoring, which has
    no stop and no ordering test at all.

    ITERATION 2 POLICY CHANGE -- TRUNCATED TRADES.
    Iteration 1 returned 0.0 for a trade whose segment ended before the trade
    resolved, and summarize() then DROPPED it from n. That is a real
    selection bias: whether a signal counts depended on post-entry price
    action (specifically, on the trade not having resolved yet), so the
    surviving sample was conditioned on the outcome window. 20-33% of signals
    were removed this way. Here a truncated trade is marked-to-market at the
    LAST AVAILABLE CLOSE, charged the same spread, and counted in n exactly
    like a TIME exit. The only difference retained is the label, so the
    truncation rate stays auditable in the output.
    """
    risk_pips = abs(entry - stop) / pip
    last = min(seg_end, entry_bar + cap + 1)
    for j in range(entry_bar, last):
        b = bars[j]
        if direction == "BUY":
            hit_stop = b.l <= stop
            hit_tgt = b.h >= target
        else:
            hit_stop = b.h >= stop
            hit_tgt = b.l <= target
        if hit_stop:  # pessimistic: stop wins ties within a bar
            return _finish("STOP", direction, entry, stop, pip, spread,
                           risk_pips, j - entry_bar + 1)
        if hit_tgt:
            return _finish("TARGET", direction, entry, target, pip, spread,
                           risk_pips, j - entry_bar + 1)

    held = last - entry_bar
    px = bars[last - 1].c
    if held >= cap + 1:
        # Time cap reached with data still available -> honest flat exit at the
        # close of the last bar in the window.
        return _finish("TIME", direction, entry, px, pip, spread,
                       risk_pips, held)

    # Ran out of segment before the cap -> mark to market at the last close.
    # Counted in n; see the policy note above.
    return _finish("TRUNCATED", direction, entry, px, pip, spread,
                   risk_pips, held)


def _pips(direction: str, entry: float, exit_px: float, pip: float) -> float:
    d = (exit_px - entry) if direction == "BUY" else (entry - exit_px)
    return d / pip


def resolve_targets(mode: str, direction: str, entry: float, target: float,
                    stop: float) -> Optional[float]:
    if mode == "measured_move":
        return target
    r = abs(entry - stop)
    if r <= 0:
        return None
    mult = 1.0 if mode == "r1" else 2.0
    return entry + mult * r if direction == "BUY" else entry - mult * r


# --------------------------------------------------------------------------
# STATS
# --------------------------------------------------------------------------

def summarize(results: Sequence[TradeResult]) -> Dict:
    """Every trade counts, including TRUNCATED ones (see simulate()).

    Both unit systems are always reported. The per-symbol path reads the _pips
    fields; the pooled path reads the _r fields, because pips are not
    comparable across instruments and R-multiples are.
    """
    valid = list(results)
    n = len(valid)
    n_trunc = sum(1 for r in valid if r.outcome == "TRUNCATED")
    out = {
        "n": n,
        "n_truncated": n_trunc,
        "truncated_pct": (round(100.0 * n_trunc / n, 1) if n else None),
        "underpowered": n < UNDERPOWERED_N,
    }
    if n == 0:
        out.update({"win_rate_pct": None, "gross_expectancy_pips": None,
                    "net_expectancy_pips": None, "net_total_pips": None,
                    "gross_expectancy_r": None, "net_expectancy_r": None,
                    "net_total_r": None, "profit_factor": None,
                    "target_rate_pct": None, "stop_rate_pct": None,
                    "time_rate_pct": None, "avg_bars_held": None,
                    "avg_risk_pips": None})
        return out
    net = [r.net_pips for r in valid]
    gross = [r.gross_pips for r in valid]
    net_r = [r.net_r for r in valid]
    gross_r = [r.gross_r for r in valid]
    wins = [x for x in net if x > 0]
    losses = [-x for x in net if x < 0]
    gp, gl = sum(wins), sum(losses)
    out.update({
        "win_rate_pct": round(100.0 * len(wins) / n, 1),
        "gross_expectancy_pips": round(statistics.fmean(gross), 3),
        "net_expectancy_pips": round(statistics.fmean(net), 3),
        "net_total_pips": round(sum(net), 1),
        "gross_expectancy_r": round(statistics.fmean(gross_r), 4),
        "net_expectancy_r": round(statistics.fmean(net_r), 4),
        "net_total_r": round(sum(net_r), 2),
        "profit_factor": (round(gp / gl, 3) if gl > 0 else None),
        "target_rate_pct": round(100.0 * sum(1 for r in valid if r.outcome == "TARGET") / n, 1),
        "stop_rate_pct": round(100.0 * sum(1 for r in valid if r.outcome == "STOP") / n, 1),
        "time_rate_pct": round(100.0 * sum(1 for r in valid if r.outcome == "TIME") / n, 1),
        "avg_bars_held": round(statistics.fmean([r.bars_held for r in valid]), 1),
        "avg_risk_pips": round(statistics.fmean([r.risk_pips for r in valid]), 2),
    })
    return out


# --------------------------------------------------------------------------
# BASELINE
# --------------------------------------------------------------------------

@dataclass
class Template:
    """One observed signal reduced to everything the null needs to replay it."""
    sym: str
    direction: str
    stop_d: float   # entry-to-stop distance in pips
    tgt_d: float    # entry-to-target distance in pips


def baseline(rng: random.Random, venues: Dict[str, Dict],
             templates: Sequence[Template], split: str, cap: int,
             resamples: int) -> Optional[Dict]:
    """Null hypothesis: same exit rule, same direction mix, same stop/target
    DISTANCES -- only the timing and location are random.

    Templates are drawn (with replacement) from the actual signals of the same
    cell, so the null differs from the pattern in exactly one respect: whether
    the entry bar was chosen by the pattern or at random. Any excess return
    over this baseline is attributable to the pattern and nothing else.

    Each template carries its own symbol, and the random entry bar is drawn
    from THAT symbol's eligible bars. So in the pooled case the null inherits
    the pattern's symbol mix as well as its direction mix -- a pooled cell that
    happens to be 80% GBPUSD is compared against a null that is also 80%
    GBPUSD, and cannot be flattered by a quieter instrument.
    """
    usable = [t for t in templates if venues[t.sym]["eligible"][split]]
    if not usable:
        return None
    k = len(usable)
    means: List[float] = []
    means_r: List[float] = []
    wins: List[float] = []
    for _ in range(resamples):
        res: List[TradeResult] = []
        for _ in range(k):
            t = usable[rng.randrange(k)]
            v = venues[t.sym]
            elig = v["eligible"][split]
            bars, pip, spread = v["bars"], v["pip"], v["spread"]
            bi = elig[rng.randrange(len(elig))]
            entry = bars[bi].o
            if t.direction == "BUY":
                stop, target = entry - t.stop_d * pip, entry + t.tgt_d * pip
            else:
                stop, target = entry + t.stop_d * pip, entry - t.tgt_d * pip
            res.append(simulate(bars, v["seg_end_of"][bi], bi, t.direction,
                                entry, target, stop, cap, pip, spread))
        means.append(statistics.fmean([r.net_pips for r in res]))
        means_r.append(statistics.fmean([r.net_r for r in res]))
        wins.append(100.0 * sum(1 for r in res if r.net_pips > 0) / len(res))
    if not means:
        return None
    means.sort()
    means_r.sort()

    def _pct(xs, q):
        return round(xs[int(q * (len(xs) - 1))], 4)

    return {
        "resamples": len(means),
        "net_expectancy_pips_mean": round(statistics.fmean(means), 3),
        "net_expectancy_pips_p05": _pct(means, 0.05),
        "net_expectancy_pips_p95": _pct(means, 0.95),
        "net_expectancy_r_mean": round(statistics.fmean(means_r), 4),
        "net_expectancy_r_p05": _pct(means_r, 0.05),
        "net_expectancy_r_p95": _pct(means_r, 0.95),
        "win_rate_pct_mean": round(statistics.fmean(wins), 1),
        "_dist_pips": means,
        "_dist_r": means_r,
    }


def p_value(observed: Optional[float], dist: Sequence[float]) -> Optional[float]:
    """One-sided empirical p: P(random >= observed)."""
    if observed is None or not dist:
        return None
    ge = sum(1 for x in dist if x >= observed)
    return round((ge + 1) / float(len(dist) + 1), 4)


# --------------------------------------------------------------------------
# DRIVER
# --------------------------------------------------------------------------

def build_venue(symbol: str, args) -> Dict:
    """Load, bucket, segment and scan ONE symbol.

    Returns a self-contained "venue": everything a cell evaluation needs for
    that instrument, plus the reporting diagnostics. Deliberately does no
    statistics -- so the per-symbol path and the pooled path consume exactly
    the same objects and cannot silently diverge.
    """
    bar_seconds = TIMEFRAMES[args.timeframe]
    stats = LoadStats()
    ticks = load_ticks(symbol, stats, drop_synthetic=not args.keep_synthetic)
    pip = pip_size(symbol)
    spread = args.spread if args.spread is not None else SPREAD_PIPS.get(
        symbol.upper(), SPREAD_PIPS_DEFAULT)

    bars, bar_diag = build_bars(ticks, args.min_ticks, args.max_gap_s,
                                args.drop_edge_bars, args.min_segment_bars,
                                bar_seconds)
    bar_diag["bar_seconds"] = bar_seconds
    if len(bars) < 40:
        return {"symbol": symbol, "error": "insufficient bars after filtering",
                "report": {"symbol": symbol,
                           "error": "insufficient bars after filtering",
                           "load": asdict(stats), "bars": bar_diag}}

    base_thr = args.zigzag_pips if args.zigzag_pips is not None else \
        ZIGZAG_PIPS.get(symbol.upper(), ZIGZAG_PIPS_DEFAULT)
    scale = ZIGZAG_TF_SCALE[args.timeframe]
    thr_pips = base_thr * scale
    thr = thr_pips * pip

    sigs, tally = generate_signals(symbol, bars, thr, args.min_pivot_sep, LOOK_BARS)

    # Minimum size filter: a target that does not clear a few spreads is noise.
    min_tgt = args.min_target_spreads * spread
    kept = [s for s in sigs if abs(s.target - s.entry_px) / pip >= min_tgt]
    tally["dropped_below_min_target"] = len(sigs) - len(kept)
    sigs = kept

    # Chronological 70/30 split on BAR INDEX. A signal belongs to the split
    # containing its ENTRY bar; its outcome may extend past the boundary, which
    # is unavoidable and noted in the output.
    split_at = int(len(bars) * args.is_frac)

    seg_end_of = [0] * len(bars)
    i = 0
    while i < len(bars):
        j = i
        while j < len(bars) and bars[j].seg == bars[i].seg:
            j += 1
        for k in range(i, j):
            seg_end_of[k] = j
        i = j

    eligible_all = [i for i in range(len(bars)) if seg_end_of[i] - i > 2]
    eligible = {
        "IS": [i for i in eligible_all if i < split_at],
        "OOS": [i for i in eligible_all if i >= split_at],
        "ALL": eligible_all,
    }

    return {
        "symbol": symbol,
        "bars": bars,
        "seg_end_of": seg_end_of,
        "eligible": eligible,
        "split_at": split_at,
        "signals": sigs,
        "pip": pip,
        "spread": spread,
        "report": {
            "symbol": symbol,
            "pip_size": pip,
            "spread_pips_assumed": spread,
            "zigzag_pips": round(thr_pips, 4),
            "zigzag_pips_m15_base": base_thr,
            "zigzag_tf_scale": round(scale, 6),
            "load": asdict(stats),
            "bars": bar_diag,
            "bar_range_utc": [
                time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(bars[0].bucket)),
                time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(bars[-1].bucket)),
            ],
            "split_bar_index": split_at,
            "signal_tally": tally,
            "n_signals": len(sigs),
            "cells": [],
        },
    }


def split_of(sig: Signal, split_at: int, split: str) -> bool:
    if split == "IS":
        return sig.entry_bar < split_at
    if split == "OOS":
        return sig.entry_bar >= split_at
    return True


def evaluate_cells(venues: Dict[str, Dict], args, unit: str,
                   rng_scope: str) -> List[Dict]:
    """Evaluate the fixed GRID x PATTERN x SPLIT surface over one or more
    venues.

    `unit` is "pips" (single symbol) or "r" (pooled). It selects which
    expectancy the p-value and the beats-baseline test are computed on; both
    unit systems are still written to the JSON.
    """
    key = "net_expectancy_r" if unit == "r" else "net_expectancy_pips"
    p95 = "net_expectancy_r_p95" if unit == "r" else "net_expectancy_pips_p95"
    dist_key = "_dist_r" if unit == "r" else "_dist_pips"

    ptypes = sorted({s.ptype for v in venues.values() for s in v["signals"]})
    cells: List[Dict] = []

    for cell_name, tmode, cap in GRID:
        for split in SPLITS:
            for ptype in ["*ALL*"] + ptypes:
                results: List[TradeResult] = []
                templates: List[Template] = []
                per_symbol_n: Dict[str, int] = {}
                entry_keys = set()
                for sym, v in sorted(venues.items()):
                    for s in v["signals"]:
                        if not split_of(s, v["split_at"], split):
                            continue
                        if ptype != "*ALL*" and s.ptype != ptype:
                            continue
                        tgt = resolve_targets(tmode, s.direction, s.entry_px,
                                              s.target, s.stop)
                        if tgt is None:
                            continue
                        results.append(simulate(
                            v["bars"], v["seg_end_of"][s.entry_bar],
                            s.entry_bar, s.direction, s.entry_px, tgt, s.stop,
                            cap, v["pip"], v["spread"]))
                        templates.append(Template(
                            sym, s.direction,
                            abs(s.entry_px - s.stop) / v["pip"],
                            abs(tgt - s.entry_px) / v["pip"]))
                        per_symbol_n[sym] = per_symbol_n.get(sym, 0) + 1
                        entry_keys.add((sym, s.entry_bar))
                if not results:
                    continue
                summ = summarize(results)
                # RNG is keyed by content, so this cell's p-value does not
                # depend on which other cells ran, or in what order.
                rng = make_rng(args.seed, rng_scope, args.timeframe,
                               cell_name, split, ptype)
                bl = baseline(rng, venues, templates, split, cap, args.resamples)
                blp = None
                if bl:
                    dists = {"pips": bl.pop("_dist_pips"),
                             "r": bl.pop("_dist_r")}
                    blp = p_value(summ.get(key), dists[unit])
                cells.append({
                    "cell": cell_name, "target_mode": tmode,
                    "time_cap_bars": cap, "split": split, "pattern": ptype,
                    "unit": unit,
                    "distinct_entry_bars": len(entry_keys),
                    "per_symbol_n": per_symbol_n,
                    "stats": summ,
                    "baseline": bl,
                    "p_value_vs_baseline": blp,
                    "beats_baseline": (
                        None if (bl is None or summ[key] is None)
                        else bool(summ[key] > bl[p95])),
                    "significant_bonferroni": (
                        None if blp is None
                        else bool(blp <= FAMILY_ALPHA / predeclared_cells())),
                })
    return cells


def predeclared_cells() -> int:
    """Size of the multiple-comparison family, fixed BEFORE the data is read.

    len(GRID) x (|PATTERN_UNIVERSE| + 1 aggregate) x |SPLITS|. Using the
    pre-declared universe rather than the pattern types that happen to appear
    keeps the correction from being a function of the sample.
    """
    return len(GRID) * (len(PATTERN_UNIVERSE) + 1) * len(SPLITS)


def _cell_rows(cells: List[Dict], cell_name: str, unit: str) -> None:
    rows = [c for c in cells if c["cell"] == cell_name]
    if not rows:
        return
    ukey = "net_expectancy_r" if unit == "r" else "net_expectancy_pips"
    gkey = "gross_expectancy_r" if unit == "r" else "gross_expectancy_pips"
    bkey = ("net_expectancy_r_mean" if unit == "r"
            else "net_expectancy_pips_mean")
    print()
    print("  [%s]  (unit: %s)" % (cell_name, "R-multiple" if unit == "r" else "pips"))
    print("    %-20s %-4s %5s %5s %7s %9s %9s %9s %8s %s"
          % ("pattern", "spl", "n", "trunc", "win%", "gross/t", "net/t",
             "base/t", "p", "flag"))
    for c in sorted(rows, key=lambda x: (x["pattern"] != "*ALL*",
                                         x["pattern"],
                                         {"ALL": 0, "IS": 1, "OOS": 2}[x["split"]])):
        s = c["stats"]
        b = c["baseline"]
        flags = []
        if s["underpowered"]:
            flags.append("UNDERPOWERED n<%d" % UNDERPOWERED_N)
        if c["beats_baseline"]:
            flags.append("beats-baseline")
        if c["significant_bonferroni"]:
            flags.append("SIGNIFICANT@bonferroni")
        print("    %-20s %-4s %5d %5d %7s %9s %9s %9s %8s %s"
              % (c["pattern"][:20], c["split"], s["n"], s["n_truncated"],
                 _f(s["win_rate_pct"]), _f(s[gkey]), _f(s[ukey]),
                 _f(b[bkey]) if b else "-",
                 _f(c["p_value_vs_baseline"]),
                 ", ".join(flags)))


def print_summary(report: Dict) -> None:
    m = report["meta"]
    print("=" * 96)
    print("PATTERN BACKTEST -- look-ahead-free   seed=%s  timeframe=%s  mode=%s"
          % (m["seed"], m["timeframe"], m["mode"]))
    print("=" * 96)
    print("Entry rule   : OPEN of the bar AFTER the confirming close-break "
          "(earliest honest fill).")
    print("Stop         : opposing structural boundary of the pattern.")
    print("Tie-break    : stop and target inside one bar -> scored as STOP "
          "(pessimistic).")
    print("Exit grid    : " + " | ".join(
        "%s(tgt=%s,cap=%db)" % (n, t, c) for n, t, c in m["grid"]))
    print("Costs        : round-trip spread charged once per trade -- " +
          ", ".join("%s %.1fp" % (k, v) for k, v in sorted(m["spread_pips"].items())))
    print("Baseline     : %d resamples per cell; random entry bars, same "
          "direction mix and same stop/target distances." % m["resamples"])
    print("Split        : chronological %d%%/%d%% IS/OOS by bar index."
          % (int(m["is_frac"] * 100), int(100 - m["is_frac"] * 100)))
    print("Excluded     : synthetic 2026-07-18 block, thin bars (<%d ticks), "
          "segment edges, gaps >%ds." % (m["min_ticks"], m["max_gap_s"]))
    print("Truncated    : scored at last available close and COUNTED in n "
          "(iteration-1 drop policy removed).")
    print("Zigzag       : M15 base x %.4f (= 1/sqrt(3) at M5) -- amplitude "
          "scales as sqrt(dt)." % m["zigzag_tf_scale"])
    print("Multiplicity : %d pre-declared cells (%d grid x %d patterns+ALL x "
          "%d splits)" % (m["cells_predeclared"], len(m["grid"]),
                          len(m["pattern_universe"]), len(m["splits"])))
    print("               family alpha %.2f -> Bonferroni alpha = %.6f"
          % (m["family_alpha"], m["bonferroni_alpha"]))
    print("               %d cells actually evaluated (alpha would be %s "
          "on evaluated only)"
          % (m["cells_evaluated"],
             "-" if m["bonferroni_alpha_evaluated"] is None
             else "%.6f" % m["bonferroni_alpha_evaluated"]))
    print()

    if report.get("pooled") is not None:
        print("-" * 96)
        print("POOLED across %s -- unit is R-multiple (R = entry-to-stop "
              "distance)." % ", ".join(m["pool_symbols"]))
        print("Pattern type is the unit of analysis; symbol identity is "
              "normalized away by the R conversion.")
        tot_sig = sum(r.get("n_signals", 0) for r in report["symbols"])
        tot_bars = sum(r.get("bars", {}).get("bars_final", 0)
                       for r in report["symbols"])
        print("Pooled inputs: %d bars, %d signals across %d symbols."
              % (tot_bars, tot_sig, len(report["symbols"])))
        for cell_name, _tm, _cap in m["grid"]:
            _cell_rows(report["pooled"], cell_name, "r")
        print()

    for r in report["symbols"]:
        print("-" * 96)
        if "error" in r:
            print("%-8s ERROR: %s" % (r["symbol"], r["error"]))
            continue
        ld = r["load"]
        print("%s   bars %d (%d segments)   %s -> %s   spread %.1fp   zigzag %.1fp"
              % (r["symbol"], r["bars"]["bars_final"], r["bars"]["segments"],
                 r["bar_range_utc"][0], r["bar_range_utc"][1],
                 r["spread_pips_assumed"], r["zigzag_pips"]))
        print("  data: %d rows -> %d ticks | dropped: synth %d+%d, dup %d, "
              "badwidth %d, badts %d"
              % (ld["rows_read"], ld["ticks_kept"], ld["rows_synthetic_epoch"],
                 ld["rows_synthetic_signature"], ld["rows_duplicate"],
                 ld["rows_bad_width"], ld["rows_bad_timestamp"]))
        t = r["signal_tally"]
        print("  patterns formed %d -> broke %d, no-break %d, no-entry %d, "
              "bad-geometry %d, too-small %d -> %d signals"
              % (t["formed"], t["ok"], t["no_break"], t["no_entry"],
                 t["bad_geometry"], t.get("dropped_below_min_target", 0),
                 r["n_signals"]))

        for cell_name, _tm, _cap in m["grid"]:
            _cell_rows(r["cells"], cell_name, "pips")

    # ---- verdict -------------------------------------------------------
    surface = list(report.get("pooled") or [])
    for r in report["symbols"]:
        surface.extend(r.get("cells", []))
    tot = len(surface)
    powered = [c for c in surface if not c["stats"]["underpowered"]]
    powered_beat = [c for c in powered if c["beats_baseline"]]
    powered_sig = [c for c in powered if c["significant_bonferroni"]]
    print("=" * 96)
    print("VERDICT")
    print("  cells evaluated                    : %d" % tot)
    print("  cells with n >= %d                 : %d" % (UNDERPOWERED_N, len(powered)))
    print("  ...of those, beating baseline p95  : %d" % len(powered_beat))
    print("  ...of those, significant @ %.6f : %d"
          % (m["bonferroni_alpha"], len(powered_sig)))
    if not powered:
        print("  >> NO CELL IS ADEQUATELY POWERED. Nothing here supports a claim")
        print("     about pattern edge in either direction.")
    elif not powered_sig:
        print("  >> NO POWERED CELL CLEARS THE CORRECTED THRESHOLD. Any positive")
        print("     number above is within what random entry produces on this")
        print("     sample once multiplicity is accounted for.")
    print("=" * 96)
    print("net/t and base/t are mean NET outcome per trade, in the unit named "
          "on each block header.")
    print("p is the one-sided empirical probability that a random-entry replay "
          "matches or beats the")
    print("pattern. Cells marked UNDERPOWERED (n<%d) are not findings. "
          "TRUNCATED trades are marked to" % UNDERPOWERED_N)
    print("market at the last available close and ARE counted in n; the trunc "
          "column shows how many.")


def _f(v) -> str:
    return "-" if v is None else ("%.3f" % v if isinstance(v, float) else str(v))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", default="EURUSD",
                    help="symbol, or ALL for every symbol")
    ap.add_argument("--timeframe", default="M15", choices=sorted(TIMEFRAMES),
                    help="bar resolution; buckets are (epoch//N)*N")
    ap.add_argument("--pool", action="store_true",
                    help="run every symbol and pool cells by PATTERN TYPE, "
                         "with outcomes normalized to R-multiples")
    ap.add_argument("--pool-symbols", default=None,
                    help="comma-separated override for the pooled symbol set")
    ap.add_argument("--seed", type=int, default=42, help="RNG seed (baseline)")
    ap.add_argument("--json", default=None, help="path for machine-readable results")
    ap.add_argument("--resamples", type=int, default=200)
    ap.add_argument("--is-frac", type=float, default=0.70)
    ap.add_argument("--min-ticks", type=int, default=5,
                    help="minimum ticks for a bar to be tradeable")
    ap.add_argument("--max-gap-s", type=int, default=3600,
                    help="gap above which a new segment starts")
    ap.add_argument("--drop-edge-bars", type=int, default=1,
                    help="partial bars to drop from each end of a segment")
    ap.add_argument("--min-segment-bars", type=int, default=20)
    ap.add_argument("--min-pivot-sep", type=int, default=2,
                    help="minimum bars between consecutive pivots")
    ap.add_argument("--min-target-spreads", type=float, default=2.0,
                    help="target must be at least this many spreads away")
    ap.add_argument("--zigzag-pips", type=float, default=None,
                    help="override the per-symbol zigzag threshold")
    ap.add_argument("--spread", type=float, default=None,
                    help="override the per-symbol spread in pips")
    ap.add_argument("--keep-synthetic", action="store_true",
                    help="DO NOT USE -- retains the 2026-07-18 random-walk block")
    args = ap.parse_args(argv)

    if args.pool:
        if args.pool_symbols:
            syms = [s.strip().upper() for s in args.pool_symbols.split(",")
                    if s.strip()]
        else:
            syms = list(ALL_SYMBOLS)
    elif args.symbol.upper() == "ALL":
        syms = list(ALL_SYMBOLS)
    else:
        syms = [args.symbol.upper()]

    report = {
        "meta": {
            "generated_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            "seed": args.seed,
            "mode": "pooled" if args.pool else "per_symbol",
            "timeframe": args.timeframe,
            "bar_seconds": TIMEFRAMES[args.timeframe],
            "pool_symbols": syms if args.pool else None,
            "pooling_unit": ("R-multiple, R = entry-to-stop distance; pattern "
                             "type is the unit of analysis"
                             if args.pool else None),
            "zigzag_tf_scale": ZIGZAG_TF_SCALE[args.timeframe],
            "zigzag_scale_rationale": (
                "swing amplitude scales as sqrt(dt), so thr_M5 = thr_M15 / "
                "sqrt(3) = 0.5774x. Cross-checked against this dataset's own "
                "fixed-clock variance scaling: sd(300s)/sd(900s) = 0.588 "
                "EURUSD, 0.605 GBPUSD, 0.586 AUDUSD, 0.600 USDJPY, vs the "
                "0.577 diffusion value. The closed form is used rather than "
                "the empirical ratio so it cannot be said to be fitted."),
            "pattern_universe": list(PATTERN_UNIVERSE),
            "splits": list(SPLITS),
            "cells_predeclared": predeclared_cells(),
            "family_alpha": FAMILY_ALPHA,
            "bonferroni_alpha": FAMILY_ALPHA / predeclared_cells(),
            "rng_policy": ("one random.Random per (seed, scope, timeframe, "
                           "cell, split, pattern), derived by SHA-256. Fixes "
                           "the iteration-1 shared-RNG bug in which p-values "
                           "depended on evaluation order."),
            "grid": [list(g) for g in GRID],
            "entry_rule": "open of the bar after the confirming close-break",
            "stop_rule": "opposing structural boundary of the pattern",
            "tie_break": "stop and target in the same bar -> STOP",
            "truncated_policy": ("marked to market at the last available close "
                                 "and INCLUDED in n; count reported per cell "
                                 "as n_truncated. Iteration 1 dropped these, "
                                 "which conditioned inclusion on post-entry "
                                 "price action."),
            "spread_pips": SPREAD_PIPS,
            "spread_note": ("round-trip spread charged once per trade; no "
                            "per-symbol spread table exists in the repo, these "
                            "follow the repo's own ~1p FX / ~3p XAU assumption"),
            "resamples": args.resamples,
            "is_frac": args.is_frac,
            "min_ticks": args.min_ticks,
            "max_gap_s": args.max_gap_s,
            "min_pivot_sep": args.min_pivot_sep,
            "min_target_spreads": args.min_target_spreads,
            "underpowered_threshold": UNDERPOWERED_N,
            "look_bars": LOOK_BARS,
            "synthetic_epoch_cutoff": SYNTHETIC_EPOCH_CUTOFF,
            "caveats": [
                "A signal is assigned to the split containing its ENTRY bar; "
                "its outcome window may extend past the boundary.",
                "Overlapping pivot windows can emit several patterns over the "
                "same swings; distinct_entry_bars shows the effective "
                "independence of each cell.",
                "Zigzag threshold, geometry tolerances and LOOK are the live "
                "hardcoded values and are in-sample with respect to this data; "
                "the OOS column is the only out-of-sample evidence here.",
                "Only six weekday sessions exist (2026-07-09 20:57 .. "
                "2026-07-17 20:58 UTC) in 6 disconnected segments, so the OOS "
                "split is thin and covers a single regime.",
                "There is NO bid/ask history anywhere: daemon.py persists the "
                "engine mid only (tick.bid/tick.ask are never written). The "
                "flat per-trade spread charge is therefore an assumption, not "
                "a measurement.",
                "The series is a ~1Hz polled mid (gap median 1.0-1.3s), so "
                "intrabar sequencing within a second is unresolvable and the "
                "same-bar stop-before-target tie rule is doing real work.",
                "LOOK_BARS, min_pivot_sep and the time caps are counted in "
                "BARS, so they are timeframe-relative: a 24-bar cap is 6h at "
                "M15 and 2h at M5.",
                "The data validity gate flagged XAUUSD as failing three "
                "independent realism checks (step granularity, flat "
                "fixed-clock variance ratio, magnitude) and recommended "
                "excluding it. It is retained in the pooled set as specified; "
                "use --pool-symbols to exclude it and see the difference.",
            ],
        },
        "symbols": [],
        "pooled": None,
    }

    venues: Dict[str, Dict] = {}
    for sym in syms:
        v = build_venue(sym, args)
        report["symbols"].append(v["report"])
        if "error" not in v:
            venues[sym] = v

    if args.pool:
        report["pooled"] = evaluate_cells(venues, args, "r", "POOL") if venues else []
    else:
        for sym, v in venues.items():
            v["report"]["cells"] = evaluate_cells({sym: v}, args, "pips", sym)

    n_eval = len(report["pooled"] or []) + sum(
        len(r.get("cells", [])) for r in report["symbols"])
    report["meta"]["cells_evaluated"] = n_eval
    report["meta"]["bonferroni_alpha_evaluated"] = (
        FAMILY_ALPHA / n_eval if n_eval else None)

    print_summary(report)

    if args.json:
        path = args.json
        d = os.path.dirname(os.path.abspath(path))
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        print("\nJSON written: %s" % os.path.abspath(path).replace("\\", "/"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
