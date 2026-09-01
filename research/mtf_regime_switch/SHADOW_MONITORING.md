# Wide-TP MTF shadow — monitoring setup

Records how the first-row landing of the read-only wide-TP MTF shadow is being
watched. The watchers themselves live OUTSIDE this repo (a machine-local scheduled
task + an ephemeral in-session monitor); this file is the version-controlled record
of what they do, so the setup is discoverable without the originating session.

## What is being watched

`reports/wide_tp_mtf_shadow.jsonl` — the shadow (flag `wide_tp_mtf_shadow_enabled`)
arms a virtual SL20/TP100 bracket pair (fade + with-trend) on every gated EURUSD
peak and writes ONE line per setup once BOTH legs resolve (or hit the 120h max-hold).
Went live on the 2026-09-01 flat restart (pid 11644). USDJPY does not arm
(entries-off). Reader: `python -m research.mtf_regime_switch.wtms_shadow_report`.

Expectation: the first row lands ~5 days out — the 100-pip leg usually rides to the
120h max-hold before force-resolving, so early setups resolve slowly.

## Watcher 1 — scheduled task (durable, cross-session)

- **taskId:** `notify-first-wtms-shadow-row`
  (`~/.claude/scheduled-tasks/notify-first-wtms-shadow-row/SKILL.md`)
- **Cadence:** every 6 hours (`0 */6 * * *`, local time).
- **Behavior:** quiet on every empty check; when the file first has >=1 line it pushes
  a desktop/phone notification, summarizes the row (fade_pips vs withtrend_pips + the
  HTF regime from `mtf_tfs`), then self-deletes so it stops firing.
- Runs while the app is open; if closed when due, runs on next launch.

## Watcher 2 — in-session monitor (this-session only)

A persistent background monitor that polls every 5 min for (a) the first/new rows in
the shadow file and (b) daemon liveness via `reports/daemon.log` staleness (>600s idle
= possible stop). Ephemeral — dies with the session; the scheduled task is the durable
path. Restart it in a new session if live watching is wanted again.

## Not a validation

One row is a DATA-ARRIVAL signal, not an edge validation. The real read is the
`arm-goodspot-selector-checkpoint` scheduled task (~2026-09-21), which runs
`wtms_shadow_report` against n>=60 across >=2 regimes. See
`research/mtf_regime_switch/wtms_shadow_report.py` and the memory notes
(mtf-regime-goodspots).
