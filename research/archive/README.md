# archive/ - closed research

Frozen 2026-11-28. Nothing here is imported by `src/`, the books or the live
loop; it is kept because each file was an experiment whose verdict is
recorded in `AGENTS.md` or in the notes beside it. Run from the repo root
with `PYTHONPATH=src`. Sibling imports still resolve because a script's own
directory is on `sys.path`, and the `ROOT = Path(__file__).resolve()
.parents[2]` lines were adjusted for the extra directory level.

## What is here

- **Conviction-model arc (2026-11-26).** `lab.py` (features, triple-barrier
  labels, walk-forward calibration), `model.py` (logistic model + purged
  walk-forward), `sim.py` / `fastsim.py` (portfolio and grid simulators),
  `screen.py`, `conviction_model.py`, `conv_model.py`, `highconv.py`,
  `combo.py`, `diag.py`, `run1.py`, `run_bucket.py`, `portfolio_study.py`,
  `sweep_geom.py`, `sweep_labels.py`, `validate_geom.py`, `sensitivity.py`,
  `_mcap.py`, `trend_study.py`, `tf_study.py`, `trailing.py`.
  Verdict: no conviction feature survived walk-forward; superseded by the
  per-symbol work in `../PER_SYMBOL.md`.
- **Last-year rule search (2026-11-27).** `year_search.py`, `year_refine.py`,
  `year_grid.py`, `year_checks.py`, `year_validate.py`, `year_book.py`,
  `year_1h.py`. Verdict: produced the SMA-cross + trail book that became
  books A-E; several searched the then-available 3-year window, which the
  2-year cap now forbids.
- **Report builders.** `make_report.py`, `make_year_report.py`,
  `build_report_data.py`, `levers.py` (3-year lever search).
- **Trailing study.** `trail_book.py`, `trail_refine.py` - produced the
  Chandelier trail now in `BookConfig`.
- **Volume climax (2026-11-27).** `vol_climax.py`, `vol_climax_stats.py`,
  `vol_climax_out.txt`. Verdict: no information left after costs.
- `bf2026-vps-deals.html` - a scraped hosting-pricing page, not research.