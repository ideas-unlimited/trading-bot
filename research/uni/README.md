# UNI research workspace

This folder is research only. The deployable sleeve lives in production code:

- module: `src/bot/strategy/capitulation_dip_uni.py`
- class: `CapitulationDipUni`
- registry name: `capitulation_dip_uni`
- tests: `tests/test_capitulation_dip_uni.py`

The scripts here import that class; there is no second strategy copy in
`research/`. The symbol suffix is part of the name so a UNI-fitted parameter
set can never be mistaken for a pooled rule.

- `UNI_CAPITULATION.md` - findings, limits, demo configuration
- `uni_final.py` - full/train/test/fold/risk/cost/trade/bootstrap tables
- `uni_walkforward.py` - 180-config walk-forward grid
- `uni_walkforward.csv` - grid output
- `uni_trail.py` - fixed target vs trailing exit grid (62 configurations)
- `uni_trail.csv` - trailing grid output
- `make_uni_report.py` - builds `uni_report.html` from the grid and headline run
