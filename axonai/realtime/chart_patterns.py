"""Pure chart-pattern geometry shared by the live breakout detector and the
offline shadow logger.

Moved verbatim from axonai/scripts/shadow_pattern_logger.py (2026-08-12) so the
live path never imports from scripts/. These functions are pure: they operate on
an in-memory bar list ``S`` of ``[open, high, low, close, epoch]`` rows and do no
I/O. The OOS-validated +1R bracket expectancy was measured with EXACTLY this
geometry -- any change here invalidates that measurement. Do not "improve" the
detection without re-running the shadow validation.

Bracket model (validated): entry = neckline break, SL = structural pattern
extreme (defines R), TP = 1R (and measured-move, logged offline only).
"""

LOOK = 40          # bars to find the confirming break
OUTW = 60          # bars after break to resolve the paper trade (~15h on M15)
ER_WIN = 96        # 1 trading day of M15 bars for the efficiency ratio


def _zigzag(S, thr):
    piv = []
    ext = S[0][3]
    exti = 0
    trend = 0
    for i in range(1, len(S)):
        c = S[i][3]
        if trend >= 0:
            if c > ext:
                ext, exti = c, i
            elif ext - c >= thr:
                piv.append((exti, "TOP", S[exti][1]))
                trend, ext, exti = -1, c, i
        else:
            if c < ext:
                ext, exti = c, i
            elif c - ext >= thr:
                piv.append((exti, "BOTTOM", S[exti][2]))
                trend, ext, exti = 1, c, i
    return piv


def _first_break(S, frm, level, down):
    for j in range(frm, min(len(S), frm + LOOK)):
        if (S[j][3] < level) if down else (S[j][3] > level):
            return j
    return None


def _eff_ratio(S, b, win=ER_WIN):
    lo = max(1, b - win)
    denom = sum(abs(S[i][3] - S[i - 1][3]) for i in range(lo + 1, b + 1)) or 1e-12
    return abs(S[b][3] - S[lo][3]) / denom


def _sim(S, b, entry, sl, tp, down, pip, cost_pips=1.0):
    """First-touch SL vs TP over OUTW bars; time-stop => scratch at entry."""
    end = min(len(S), b + OUTW)
    for j in range(b, end):
        hi, lo = S[j][1], S[j][2]
        hit_sl = (hi >= sl) if down else (lo <= sl)
        hit_tp = (lo <= tp) if down else (hi >= tp)
        if hit_sl:                      # SL priority when a bar spans both
            return -abs(entry - sl) / pip - cost_pips, "loss"
        if hit_tp:
            return abs(entry - tp) / pip - cost_pips, "win"
    return -cost_pips, "scratch"


def _chart_hit(S, b, level, target, down):
    seg = S[b:b + OUTW] or [S[b]]
    x = min(seg, key=lambda r: r[2])[2] if down else max(seg, key=lambda r: r[1])[1]
    return (x <= target) if down else (x >= target)


def _candidates(piv, S):
    """Yield (type, dir, down, neck, target, sl_extreme, break_from_bar) tuples."""
    for i in range(len(piv)):
        w3, w4, w5 = piv[i:i + 3], piv[i:i + 4], piv[i:i + 5]
        if len(w3) == 3:
            a, b, c = w3
            if a[1] == "TOP" and c[1] == "TOP":
                h = (a[2] + c[2]) / 2 - b[2]
                if h > 0 and abs(a[2] - c[2]) <= 0.4 * h:
                    yield ("double_top", "SELL", True, b[2], b[2] - h, max(a[2], c[2]), c[0])
            elif a[1] == "BOTTOM" and c[1] == "BOTTOM":
                h = b[2] - (a[2] + c[2]) / 2
                if h > 0 and abs(a[2] - c[2]) <= 0.4 * h:
                    yield ("double_bottom", "BUY", False, b[2], b[2] + h, min(a[2], c[2]), c[0])
        if len(w5) == 5:
            p = [x[2] for x in w5]
            if w5[0][1] == "TOP":
                t1, b1, t2, b2, t3 = p
                neck = (b1 + b2) / 2
                span = max(t1, t2, t3) - neck
                if span > 0:
                    if max(abs(t1 - t2), abs(t2 - t3), abs(t1 - t3)) <= 0.3 * span:
                        yield ("triple_top", "SELL", True, neck, neck - span, max(t1, t2, t3), w5[4][0])
                    elif t2 > t1 and t2 > t3 and abs(t1 - t3) <= 0.35 * (t2 - neck):
                        yield ("head_shoulders", "SELL", True, neck, neck - (t2 - neck), t2, w5[4][0])
            else:
                b1, t1, b2, t2, b3 = p
                neck = (t1 + t2) / 2
                span = neck - min(b1, b2, b3)
                if span > 0:
                    if max(abs(b1 - b2), abs(b2 - b3), abs(b1 - b3)) <= 0.3 * span:
                        yield ("triple_bottom", "BUY", False, neck, neck + span, min(b1, b2, b3), w5[4][0])
                    elif b2 < b1 and b2 < b3 and abs(b1 - b3) <= 0.35 * (neck - b2):
                        yield ("inv_head_shoulders", "BUY", False, neck, neck + (neck - b2), b2, w5[4][0])
        if (len(w4) == 4 and w4[0][1] != w4[1][1]
                and w4[0][1] == w4[2][1] and w4[1][1] == w4[3][1]):
            pr = [x[2] for x in w4]
            span = max(pr) - min(pr)
            last = w4[3][0]
            if span > 0:
                if w4[0][1] == "TOP":
                    hi1, hi2, lo1, lo2 = pr[0], pr[2], pr[1], pr[3]
                else:
                    lo1, lo2, hi1, hi2 = pr[0], pr[2], pr[1], pr[3]
                flat = 0.12 * span
                dh, dl = hi2 - hi1, lo2 - lo1
                if abs(dh) < flat and abs(dl) < flat:
                    bidx = _first_break(S, last, hi2, False) or _first_break(S, last, lo2, True)
                    if bidx is not None:
                        down = S[bidx][3] < lo2
                        lvl = lo2 if down else hi2
                        yield ("rectangle", "SELL" if down else "BUY", down, lvl,
                               lvl - span if down else lvl + span, max(pr) if down else min(pr), last)
                elif abs(dh) < flat and dl > flat:
                    yield ("asc_triangle", "BUY", False, hi2, hi2 + span, min(pr), last)
                elif abs(dl) < flat and dh < -flat:
                    yield ("desc_triangle", "SELL", True, lo2, lo2 - span, max(pr), last)
                elif dh < -flat and dl > flat:
                    bidx = _first_break(S, last, hi2, False) or _first_break(S, last, lo2, True)
                    if bidx is not None:
                        down = S[bidx][3] < lo2
                        lvl = lo2 if down else hi2
                        yield ("sym_triangle", "SELL" if down else "BUY", down, lvl,
                               lvl - span if down else lvl + span, max(pr) if down else min(pr), last)
                elif dh > flat and dl > flat and dl > dh:
                    yield ("rising_wedge", "SELL", True, lo2, lo2 - span, max(pr), last)
                elif dh < -flat and dl < -flat and abs(dh) > abs(dl):
                    yield ("falling_wedge", "BUY", False, hi2, hi2 + span, min(pr), last)
