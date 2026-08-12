"""Shadow-log report — read what every OFF-by-default shadow WOULD have done and
whether it has earned being armed. Pure read of reports/signals.jsonl; touches
nothing. Run:  .venv\\Scripts\\python.exe shadow_report.py  [days_back]

Shadows covered: breakout discriminator, impulse veto, MFE early-exit, retest,
exit-capture (fixed-TP + tighter-trail). Prints per-pair verdict-vs-outcome and
the arming criterion for each. Created 2026-08-11 as the weekly checkpoint tool.
"""
import json
import sys
import random
from collections import defaultdict
from datetime import datetime, timezone, timedelta

PATH = "reports/signals.jsonl"
REVP = "reports/revp_telemetry.jsonl"
REVCONF = "reports/reversal_confirm_shadow.jsonl"
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 14
CUT = (datetime.now(timezone.utc) - timedelta(days=DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")

# Break-retest reconstruction params (mirror daemon defaults; unit-correct).
BR = dict(lookback=20, win=3, margin_atr=0.25, push_atr=0.5, retest_tol_atr=0.2,
          confirm_atr=0.15, invalidate_atr=0.35, timeout=8, out_bars=16, sl_atr=1.0, tp_atr=2.0)
SHUFFLES = 2000  # null-distribution samples per pair


def load():
    rows = []
    with open(PATH, encoding="utf-8") as f:
        for l in f:
            l = l.strip()
            if not l:
                continue
            try:
                rows.append(json.loads(l))
            except Exception:
                pass
    return rows


def pair(sym):
    return "USDJPY" if "JPY" in str(sym).upper() else "EURUSD"


def _load_m15(sym_is_jpy):
    """M15 OHLC bars for one pair from the revp telemetry log (chronological)."""
    try:
        rows = []
        with open(REVP, encoding="utf-8") as f:
            for l in f:
                l = l.strip()
                if not l:
                    continue
                try:
                    r = json.loads(l)
                except Exception:
                    continue
                if ("JPY" in r.get("mt5_symbol", "").upper()) == sym_is_jpy:
                    rows.append(r)
        rows.sort(key=lambda r: r["epoch"])
        return rows
    except FileNotFoundError:
        return []


def _est_atr_pips(m15, pip, n=14):
    trs = []
    for i in range(0, len(m15) - 4, 4):
        h = max(x["high"] for x in m15[i:i + 4]); l = min(x["low"] for x in m15[i:i + 4])
        trs.append((h - l) / pip)
    trs = trs[-n:]
    return (sum(trs) / len(trs)) if trs else (15.0 if pip == 0.01 else 8.0)


def _outcome_pips(bars, i, direction, pip, sl_p, tp_p, out_bars):
    """Forward SL/TP/timeout outcome for an entry at bar i (adverse-first = pessimistic)."""
    e = bars[i]["close"]; mfe = mae = 0.0
    for j in range(i + 1, min(i + 1 + out_bars, len(bars))):
        hi, lo, cl = bars[j]["high"], bars[j]["low"], bars[j]["close"]
        if direction == "long":
            mae = max(mae, (e - lo) / pip); mfe = max(mfe, (hi - e) / pip)
        else:
            mae = max(mae, (hi - e) / pip); mfe = max(mfe, (e - lo) / pip)
        if mae >= sl_p:
            return -sl_p
        if mfe >= tp_p:
            return tp_p
    j = min(i + out_bars, len(bars) - 1)
    return ((bars[j]["close"] - e) / pip) if direction == "long" else ((e - bars[j]["close"]) / pip)


def _reconstruct_br(bars, pip):
    """Replay the break-retest state machine over M15 bars → list of confirmed outcomes.
    Independent reimplementation of the daemon logic (unit-correct); confirmed setups
    return {'dir','entry_i','pips','sl_p','tp_p'}."""
    setups, done = [], []
    for i, candle in enumerate(bars):
        prior_bars = bars[:i]
        if len(prior_bars) < max(6, BR["win"] + 3):
            continue
        m15 = prior_bars[-(BR["lookback"] - 1):] + [candle]
        prior = m15[:-BR["win"]]
        ph = max(c["high"] for c in prior); pl = min(c["low"] for c in prior)
        atr_p = _est_atr_pips(m15, pip); atr_px = atr_p * pip
        margin = BR["margin_atr"] * atr_px; tol = BR["retest_tol_atr"] * atr_px
        confirm = BR["confirm_atr"] * atr_px; invalid = BR["invalidate_atr"] * atr_px
        push_thr = BR["push_atr"] * atr_p; sl_p = BR["sl_atr"] * atr_p; tp_p = BR["tp_atr"] * atr_p
        hi, lo, cl = candle["high"], candle["low"], candle["close"]
        keep = []
        for s in setups:
            s["bars"] += 1
            L = s["level"]
            if s["dir"] == "long":
                if cl < L - invalid:
                    continue
                if not s["rt"] and lo <= L + tol:
                    s["rt"] = True
                if s["rt"] and cl > L + confirm:
                    s["done"] = True
                    done.append({"dir": "long", "entry_i": i,
                                 "pips": _outcome_pips(bars, i, "long", pip, s["sl_p"], s["tp_p"], BR["out_bars"]),
                                 "sl_p": s["sl_p"], "tp_p": s["tp_p"]})
                    continue
            else:
                if cl > L + invalid:
                    continue
                if not s["rt"] and hi >= L - tol:
                    s["rt"] = True
                if s["rt"] and cl < L - confirm:
                    done.append({"dir": "short", "entry_i": i,
                                 "pips": _outcome_pips(bars, i, "short", pip, s["sl_p"], s["tp_p"], BR["out_bars"]),
                                 "sl_p": s["sl_p"], "tp_p": s["tp_p"]})
                    continue
            if s["bars"] <= BR["timeout"]:
                keep.append(s)
        setups = keep
        push = (cl - m15[0]["close"]) / pip
        up = cl > ph + margin and push >= push_thr
        dn = cl < pl - margin and (-push) >= push_thr
        if up and not any(x["dir"] == "long" for x in setups):
            setups.append(dict(dir="long", level=ph, bars=0, rt=False, sl_p=sl_p, tp_p=tp_p))
        if dn and not any(x["dir"] == "short" for x in setups):
            setups.append(dict(dir="short", level=pl, bars=0, rt=False, sl_p=sl_p, tp_p=tp_p))
    return done


def shuffle_null():
    print("---- BREAK-RETEST CONTINUATION + SHUFFLE-NULL ----")
    print("   Q: is the confirmed continuation net better than random (bar,dir) entries, same SL/TP?")
    rng = random.Random(1234)  # fixed seed → reproducible report
    for pf in ("USDJPY", "EURUSD"):
        is_jpy = pf == "USDJPY"; pip = 0.01 if is_jpy else 0.0001
        bars = _load_m15(is_jpy)
        if len(bars) < 30:
            print(f"   {pf}: only {len(bars)} M15 telemetry bars — need more history.")
            continue
        real = _reconstruct_br(bars, pip)
        n = len(real)
        if n == 0:
            print(f"   {pf}: 0 confirmed setups over {len(bars)} bars yet.")
            continue
        real_net = sum(r["pips"] for r in real)
        wins = sum(1 for r in real if r["pips"] > 0)
        # median SL/TP for the null's risk model
        sl_p = sorted(r["sl_p"] for r in real)[n // 2]; tp_p = sorted(r["tp_p"] for r in real)[n // 2]
        valid = [i for i in range(len(bars) - BR["out_bars"] - 1) if i >= max(6, BR["win"] + 3)]
        null_nets = []
        for _ in range(SHUFFLES):
            tot = 0.0
            for _e in range(n):
                i = rng.choice(valid); d = rng.choice(("long", "short"))
                tot += _outcome_pips(bars, i, d, pip, sl_p, tp_p, BR["out_bars"])
            null_nets.append(tot)
        null_nets.sort()
        mean = sum(null_nets) / len(null_nets)
        p95 = null_nets[int(0.95 * len(null_nets))]; p05 = null_nets[int(0.05 * len(null_nets))]
        pval = sum(1 for x in null_nets if x >= real_net) / len(null_nets)
        verdict = ("REAL EDGE (beats null p<0.05)" if pval < 0.05 else
                   "within null — likely artifact" if pval > 0.20 else "borderline — need more data")
        print(f"   {pf}: real n={n} ({wins}W) net={real_net:+.1f}p | null mean={mean:+.1f}p "
              f"[p05 {p05:+.0f}, p95 {p95:+.0f}] | p={pval:.3f} -> {verdict}")
    print("   (in-sample while the sample is one day; the p-value only means something once")
    print("    the confirmed count grows over multiple sessions.)")
    print()


def structure_report(entries, closes):
    """MTF market-structure Stage-1 label: cross-tab structure verdict vs realised
    outcome per pair. The whole point — do AGAINST-structure fades actually lose?"""
    print("== MTF MARKET-STRUCTURE shadow (Stage-1 label; does against-structure lose?) ==")
    recs = defaultdict(list)  # (pair, verdict) -> [pips]
    any_rows = False
    for c in closes:
        e = entries.get(c.get("ticket"))
        if not e:
            continue
        ss = e.get("structure_shadow")
        if not isinstance(ss, dict):
            continue
        v = ss.get("verdict")
        if v in (None, "insufficient_data", "error"):
            continue
        any_rows = True
        recs[(pair(c.get("symbol")), v)].append(c.get("pips", 0.0) or 0.0)
    if not any_rows:
        print("   (no closed trades carry a structure verdict yet — restart to start stamping)\n")
        return
    for pf in ("EURUSD", "USDJPY"):
        if not any(p == pf for (p, _v) in recs):
            print(f"   {pf}: (no rows yet)")
            continue
        print(f"   {pf}:")
        for v in ("with_structure", "against_structure", "range"):
            pips = recs.get((pf, v))
            if not pips:
                continue
            n = len(pips); wins = sum(1 for x in pips if x > 0); net = sum(pips)
            print(f"      {v:<18} n={n:<3} win={wins}/{n} ({wins/n:.0%})  net={net:+.1f}p  avg={net/n:+.1f}p")
        print("      >> ARM (later, per-pair) only if against_structure is clearly net-negative / "
              "lower win-rate than with_structure — then veto it.")
    print()


def regime_report(entries, closes):
    """Does REGIME separate the wrong-direction losers (A) from the early-cut winners
    (B)? Net-after-commission by regime (trending/ranging) + against-trend. The whole
    dynamic-market thesis: fades should be HELD in a range, SKIPPED against a trend."""
    print("== REGIME shadow (dynamic-market: hold-in-range vs skip-in-trend) ==")

    def dollar(sym, entry):
        return (100000 * 0.01 / entry) if "JPY" in (sym or "").upper() else 10.0
    by_reg = defaultdict(list); by_against = defaultdict(list)
    any_rows = False
    for c in closes:
        e = entries.get(c.get("ticket"))
        if not e:
            continue
        rs = e.get("regime_shadow")
        if not isinstance(rs, dict) or rs.get("regime") in (None, "insufficient_data", "error"):
            continue
        any_rows = True
        net = (float(c.get("pips") or 0.0)) * dollar(c.get("symbol"), c.get("entry_price")) - 7
        by_reg[rs["regime"]].append(net)
        by_against[bool(rs.get("against_trend"))].append(net)
    if not any_rows:
        print("   (no closed trades carry a regime label yet — restart to start logging)\n")
        return

    def line(sub, label):
        if not sub:
            return
        w = sum(1 for n in sub if n > 0)
        print(f"   {label:24} n={len(sub):3d}  total=${sum(sub):+6.0f}  avg=${sum(sub)/len(sub):+6.1f}  win%={w/len(sub)*100:2.0f}")
    for rg in ("ranging", "transitional", "trending"):
        line(by_reg.get(rg), rg)
    print("   fade vs a strong trend:")
    line(by_against.get(True), "  against_trend=True")
    line(by_against.get(False), "  against_trend=False")
    print("   >> THESIS holds if trending/against_trend is clearly net-NEGATIVE (skip it) and "
          "ranging is net-POSITIVE-if-held. Needs trades ACROSS regimes — collect first.")
    print()


def selectivity_report(entries, closes):
    """Entry-selectivity shadow: would skipping 'fading-into-momentum' fades lift
    NET-AFTER-COMMISSION? The whole point is cutting the commission drag by trading less."""
    print("== ENTRY-SELECTIVITY shadow (cut over-trading; net is AFTER -$7 commission) ==")

    def dollar(sym, entry):
        return (100000 * 0.01 / entry) if "JPY" in (sym or "").upper() else 10.0
    groups = defaultdict(list)   # verdict -> [net]
    fade = defaultdict(list)     # fading_into bool -> [net]
    wrong = defaultdict(list)    # wrong_side bool -> [net]
    reasons = defaultdict(list)  # skip reason -> [net]
    any_rows = False
    for c in closes:
        e = entries.get(c.get("ticket"))
        if not e:
            continue
        ss = e.get("selectivity_shadow")
        if not isinstance(ss, dict) or ss.get("verdict") in (None, "error"):
            continue
        any_rows = True
        net = (float(c.get("pips") or 0.0)) * dollar(c.get("symbol"), c.get("entry_price")) - 7
        groups[ss["verdict"]].append(net)
        fade[bool(ss.get("fading_into"))].append(net)
        wrong[bool(ss.get("wrong_side"))].append(net)
        if ss.get("reason"):
            reasons[ss["reason"]].append(net)
    if not any_rows:
        print("   (no closed trades carry a selectivity verdict yet — restart to start logging)\n")
        return

    def line(sub, label):
        if not sub:
            return
        w = sum(1 for n in sub if n > 0)
        print(f"   {label:26} n={len(sub):3d}  total=${sum(sub):+6.0f}  avg=${sum(sub)/len(sub):+6.1f}  win%={w/len(sub)*100:2.0f}")
    line(groups.get("would_skip"), "would_skip (all reasons)")
    line(groups.get("allow"), "allow (kept)")
    skip_net = sum(groups.get("would_skip", []))
    print(f"   >> ARM the skip if would_skip stays clearly net-NEGATIVE (skipping it ADDS ${-skip_net:+.0f}) "
          f"and the kept set nets positive. Watch it doesn't also cut winners.")
    print("   by skip reason:")
    for rz in sorted(reasons):
        line(reasons[rz], "  " + rz)
    print("   component flags:")
    line(fade.get(True), "  fading_into=True")
    line(fade.get(False), "  fading_into=False")
    line(wrong.get(True), "  wrong_side_sr=True")
    line(wrong.get(False), "  wrong_side_sr=False")
    print()


def revconf_report():
    """Reversal-confirmation PRE-ENTRY gate: does WAITING for confirmation beat
    entering early? Reads reversal_confirm_shadow.jsonl. Prior offline finding:
    delay-and-reprice LOST -90p / rescued 0 losers — this re-tests it on live fills."""
    print("== REVERSAL-CONFIRMATION gate (pre-entry 'wait for the turn', SHADOW-ONLY) ==")
    try:
        rows = [json.loads(l) for l in open(REVCONF, encoding="utf-8") if l.strip()]
    except FileNotFoundError:
        print("   (no reversal_confirm_shadow.jsonl yet — nothing armed since build)\n")
        return
    rows = [r for r in rows if r.get("sig_ts_utc", "") >= CUT]
    if not rows:
        print("   (no rows in window)\n")
        return
    for pf in ("EURUSD", "USDJPY"):
        pr = [r for r in rows if pair(r.get("mt5_symbol")) == pf]
        if not pr:
            print(f"   {pf}: (no rows yet)")
            continue
        ent = [r for r in pr if r.get("verdict") == "would_enter"]
        inv = [r for r in pr if r.get("verdict") == "would_skip_invalidated"]
        to = [r for r in pr if r.get("verdict") == "would_skip_timeout"]
        gate_net = sum(r.get("outcome_pips", 0.0) or 0.0 for r in ent)  # only taken trades
        wins = [r for r in ent if r.get("forward_outcome") == "win"]
        losses = [r for r in ent if r.get("forward_outcome") == "loss"]
        reprice = sum(r.get("reprice_pips", 0.0) or 0.0 for r in ent) / max(1, len(ent))
        print(f"   {pf}: {len(pr)} signals -> would_enter={len(ent)} "
              f"invalidated(skip)={len(inv)} timeout(skip)={len(to)}")
        print(f"      taken forward: {len(wins)}W/{len(losses)}L  gate_net={gate_net:+.1f}p  "
              f"avg_reprice={reprice:.1f}p (profit forfeited by waiting)")
        # verdict vs the prior finding: if the gate SKIPS mostly losers it may help;
        # if it skips would-be winners (or the taken set nets < the fired system), don't arm.
        take_rate = len(ent) / len(pr)
        print(f"      >> take-rate {take_rate:.0%}; ARM only if gate_net beats the SAME "
              f"signals' actual net AND skips cut losers not winners (prior offline: LOSES).")
    print()


def retest_quality_report(entries, closes):
    """Is the retest veto cutting winners (PREMATURE, bad on trends) or losers
    (PROTECTIVE)? For each scratched trade, look 90 min forward: did the FADE
    direction run (premature cut) or keep going against (protective)? Tracks the
    wider-cap experiment (EURUSD 3p / USDJPY 5p, armed 2026-08-12)."""
    print("== RETEST-VETO QUALITY (premature vs protective scratches) ==")

    def _ep(s):
        return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
    any_rows = False
    tally = defaultdict(lambda: {"premature": 0, "protective": 0, "inconcl": 0,
                                 "confirm": 0, "veto": 0, "timeout": 0})
    for c in closes:
        v = c.get("retest_verdict")
        if v in ("confirm", "veto", "timeout"):
            tally[pair(c.get("symbol"))][v] += 1
        if v != "veto":
            continue
        try:
            ce = _ep(c.get("timestamp_utc", ""))
        except Exception:
            continue
        sym = c.get("symbol", ""); is_jpy = "JPY" in sym.upper(); pip = 0.01 if is_jpy else 0.0001
        entry = c.get("entry_price"); d = c.get("direction")
        if entry is None or d is None:
            continue
        bars = [b for b in _load_m15(is_jpy) if ce <= b["epoch"] <= ce + 5400]
        if not bars:
            continue
        any_rows = True
        if d == "SELL":
            fav = max((entry - b["low"]) / pip for b in bars); adv = max((b["high"] - entry) / pip for b in bars)
        else:
            fav = max((b["high"] - entry) / pip for b in bars); adv = max((entry - b["low"]) / pip for b in bars)
        cls = "premature" if (fav >= 8 and fav > adv) else ("protective" if (adv >= 8 and adv > fav) else "inconcl")
        tally[pair(sym)][cls] += 1
    if not any_rows:
        print("   (no scratched trades with forward data yet)\n")
        return
    for pf in ("EURUSD", "USDJPY"):
        t = tally.get(pf)
        if not t:
            continue
        print(f"   {pf}: retest confirm={t['confirm']} veto={t['veto']} timeout={t['timeout']}")
        print(f"      scratches -> PREMATURE(cut a winner)={t['premature']}  "
              f"PROTECTIVE(cut a loser)={t['protective']}  inconcl={t['inconcl']}")
        print(f"      >> wider cap is HELPING if premature drops over time; HURTING if protective "
              f"losses grow. Revert to 2p if premature stays high.")
    print()


def main():
    rows = load()
    entries = {}
    for r in rows:
        tr = r.get("trade_result")
        if isinstance(tr, dict) and tr.get("order"):
            entries[tr["order"]] = r
    closes = [r for r in rows if r.get("type") == "trade_closed"
              and str(r.get("timestamp_utc", "")) >= CUT]
    closes.sort(key=lambda x: x.get("timestamp_utc", ""))

    print(f"=== SHADOW REPORT — last {DAYS}d (since {CUT}) ===")
    print(f"closed trades in window: {len(closes)}")
    if not closes:
        print("No closed trades in window — nothing to judge yet.")
        return
    print(f"span: {closes[0].get('timestamp_utc')} -> {closes[-1].get('timestamp_utc')}\n")

    def crosstab(name, getter, arm_rule):
        print(f"---- {name} ----")
        print(f"   ARM WHEN: {arm_rule}")
        for pf in ("EURUSD", "USDJPY"):
            cells = defaultdict(lambda: [0, 0, 0.0])  # verdict -> [win, loss, net]
            have = 0
            for c in closes:
                if pair(c.get("symbol")) != pf:
                    continue
                v = getter(c)
                if v is None:
                    continue
                have += 1
                p = c.get("pips", 0.0) or 0.0
                cell = cells[v]
                cell[0 if p > 0 else 1] += 1
                cell[2] += p
            if not have:
                print(f"   {pf}: (no rows carry this field yet)")
                continue
            print(f"   {pf}: {have} trades")
            for v, (w, ls, net) in sorted(cells.items()):
                tot = w + ls
                wr = f"{100*w/tot:.0f}%" if tot else "-"
                print(f"      {str(v):<12} n={tot:<3} win={w} loss={ls} wr={wr:<4} net={net:+.1f}p")
        print()

    crosstab("BREAKOUT discriminator",
             lambda c: (entries.get(c.get("ticket"), {}).get("breakout_shadow") or {}).get("verdict"),
             "would_skip lands on LOSERS (net of skipped trades is negative), not winners; USDJPY, n>=15 would_skip.")
    crosstab("IMPULSE veto",
             lambda c: (entries.get(c.get("ticket"), {}).get("impulse_shadow") or {}).get("verdict"),
             "it actually FIRES would_skip and those are losers. (So far it flags nothing — likely dead.)")
    crosstab("RETEST",
             lambda c: c.get("retest_verdict"),
             "veto/timeout land on LOSERS, confirm on winners; EURUSD. Prior study: ~flat net, halves drawdown.")

    # MFE early-exit
    print("---- MFE early-exit ----")
    print("   ARM WHEN: fired-trades' saved_pips sums clearly positive (cuts losers, not winners).")
    for pf in ("EURUSD", "USDJPY"):
        have = [c for c in closes if pair(c.get("symbol")) == pf and isinstance(c.get("mfe_exit_shadow"), dict)]
        fired = [c for c in have if (c.get("mfe_exit_shadow") or {}).get("fired")]
        if not have:
            print(f"   {pf}: (no rows yet)")
            continue
        saved = sum((c["mfe_exit_shadow"].get("saved_pips", 0.0) or 0.0) for c in fired)
        print(f"   {pf}: fired {len(fired)}/{len(have)}; total saved if acted = {saved:+.1f}p")
    print()

    # Exit-capture (fixed-TP + alt-trail)
    print("---- EXIT-CAPTURE (fixed-TP + tighter-trail) ----")
    print("   ARM WHEN: a TP or the alt-trail beats actual on UNSEEN trades, per pair (mind runners).")
    primary = {"EURUSD": "tp4"}  # designated candidate under shadow evaluation (arm-first target)
    for pf in ("EURUSD", "USDJPY"):
        have = [c for c in closes if pair(c.get("symbol")) == pf and isinstance(c.get("exit_capture_shadow"), dict)]
        if not have:
            print(f"   {pf}: (no rows yet)")
            continue
        act = sum((c["exit_capture_shadow"].get("actual_pips", 0.0) or 0.0) for c in have)
        tps = defaultdict(float)
        for c in have:
            for k, v in (c["exit_capture_shadow"].get("fixed_tp") or {}).items():
                tps[k] += (v or 0.0)
        alt = sum(((c["exit_capture_shadow"].get("alt_trail") or {}).get("pips", 0.0) or 0.0) for c in have)
        print(f"   {pf}: n={len(have)}  actual={act:+.1f}p")
        for k in sorted(tps):
            star = "  <== PRIMARY CANDIDATE" if primary.get(pf) == k else ""
            print(f"      fixed_{k}: {tps[k]:+.1f}p  (vs actual {tps[k]-act:+.1f}){star}")
        print(f"      alt_trail: {alt:+.1f}p  (vs actual {alt-act:+.1f})")
        pk = primary.get(pf)
        if pk and pk in tps:
            n = len(have)
            print(f"   >> {pf} {pk.upper()} verdict: {tps[pk]-act:+.1f}p over {n} trades "
                  f"({'AHEAD — keep watching toward arm' if tps[pk] > act else 'behind — do NOT arm'}); "
                  f"need a solid post-Aug11 sample before arming the live TP cap.")
    print()
    retest_quality_report(entries, closes)
    regime_report(entries, closes)
    selectivity_report(entries, closes)
    structure_report(entries, closes)
    revconf_report()
    shuffle_null()
    print("NOTE: in-sample = the day a filter was built. Only trust rows AFTER build:")
    print("  impulse ~Aug10 AM | breakout/mfe-exit ~Aug10 PM | exit-capture Aug11.")


if __name__ == "__main__":
    main()
