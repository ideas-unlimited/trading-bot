# research/ - the lab

Ideas live here; nothing here runs in the live loop. A study earns its way
onto the shelf by being tuned here and then shipped as a static sleeve
module, `src/bot/strategy/<strategy>_<symbol>.py`, which carries its tuned
params and provenance (policy in `docs/TARGETS.md`). There is no runtime
parameter file to drift. Scratch is expected to be messy inside a study; the
folder is not allowed to be messy between studies.

## Layout

    research/
      README.md            this map
      <symbol>/            one folder per per-symbol sleeve (uni/, sol/)
      <study>.py           a pooled / multi-coin study
      <STUDY>.md           its numbers and verdict
      archive/             closed work, kept for provenance

## Rules

- **One study, one script, one `.md`.** The script prints the tables; the
  `.md` carries the verdict, window, timeframe, costs and the headline
  number. A result without its window is not a result (`AGENTS.md`).
- **Per-symbol work goes in `research/<symbol>/`**, and the deployed sleeve
  is named `<strategy>_<symbol>` - never a pooled name (`AGENTS.md`).
- **Do not move or edit what another agent is using.** Check `git status`
  and the file mtime first; whoever is editing it owns the move.
- **Outputs sit next to the script.** Today's scripts write root-relative
  (`research/wvfx_stage1.csv`), so run them from the repo root; a new study
  should write next to itself via `Path(__file__).resolve().parent`.
- **Close a study by archiving it:** `git mv` the script, its outputs and
  its notes into `archive/`, add one line to `archive/README.md`.
- Windows: 2 years max, sub-4h only 12 months, long only. The hard rules
  are in `AGENTS.md`; do not restate them in a study and do not break them.

## Index

### Per-symbol sleeves

| folder | coin | what it is |
| --- | --- | --- |
| `uni/` | UNI | capitulation-dip walk-forward, report in `uni_report.html` |
| `sol/` | SOL | 4h/6h/1d sweep with IS/OOS split (`sol_lab.py` -> `sol_sweep.csv`, `sol_report.html`) |

### Per-symbol parameter work

| file | question it answers |
| --- | --- |
| `per_symbol.py` | does a three-knob per-coin fit survive the year-2 holdout? (no, 5/5) |
| `per_symbol_knob.py` | does even one per-coin knob survive? (2/5, a coin flip) |
| `ensemble_book.py` | per-symbol wallets with fixed sleeves, no per-coin search |
| `PER_SYMBOL.md` | the write-up, with each symbol's year1/year2 table |

### WVF + Stochastic (active study)

| file | question it answers |
| --- | --- |
| `wvfx_reanalyze.py` | independent event study: does WVF + Stoch carry an edge? |
| `wvfx_portfolio.py` | portfolio simulation of the event setup |
| `wvfx_literal.py` | the literal every-green-bar reading vs the deployed rule |
| `wvfx_trail.py` | does a trailing exit, or the bear filter, rescue it? |
| `wvfx_wide.py` | the same rule on a wider coin pool |
| `wvfx_tune.py` | staged tuning lab (`--stage 1..4`) |
| `wvfx_pooled.py` | pooled parameter map across the majors |
| `wvfx_xcheck.py` | cross-symbol check of one parameter set |
| `wvfx_report.py` | builds `wvfx_report.html` from the cache + tuning CSVs |
| `WVFX_STOCH.md`, `WVFX_TUNE.md`, `STOCH_RSI_WVF.md` | findings |

Artifacts: `wvfx_stage1..4.csv`, `wvfx_pooled_4h.csv`, `wvfx_xcheck.csv`,
`wvfx_report.html`, `wvfx_report_template.html`.

### Structure gates

| file | question it answers |
| --- | --- |
| `structure_gates.py` | ported channel / breakout / measured-move gates |
| `structure_tune.py` | fit the `room` gate on year 1, validate on year 2 (book D) |

### Other

| file | what it is |
| --- | --- |
| `tf_trail.py` | timeframe x trailing-stop sweep |
| `fetch_binance.py`, `fetch_tf.py`, `fetch_1h.py` | scratch CSV fetchers (prefer the Postgres bars cache) |
| `HANDOFF.md` | 2026-11-26 handover, kept as provenance (its files are in `archive/`) |

### Archive

`archive/README.md` - the 2026-11-26/27 conviction-model arc, the last-year
rule search (`year_*`), the trailing study and the volume-climax test.
If a file you remember is gone from this level, it is in there.