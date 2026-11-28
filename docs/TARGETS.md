# Target symbols and per-symbol strategy rule

`config/target_symbols.json` is the authoritative target list. It contains the
21 symbols the user selected after removing RAY. Other agents may read it to
build one strategy per symbol; do not add, remove or substitute symbols without
the user's approval.

| # | Symbol | Status |
|---:|---|---|
| 1 | ETH/USDT | untuned |
| 2 | BNB/USDT | untuned |
| 3 | SOL/USDT | untuned |
| 4 | XRP/USDT | untuned |
| 5 | HYPE/USDT | untuned |
| 6 | LINK/USDT | untuned |
| 7 | UNI/USDT | untuned |
| 8 | CC/USDC | untuned |
| 9 | AVAX/USDT | untuned |
| 10 | SUI/USDT | untuned |
| 11 | TAO/USDT | untuned |
| 12 | ONDO/USDT | untuned |
| 13 | JUP/USDT | untuned |
| 14 | RENDER/USDT | untuned |
| 15 | LIT/USDT | untuned |
| 16 | INJ/USDT | untuned |
| 17 | CAKE/USDT | untuned |
| 18 | PYTH/USDT | untuned |
| 19 | FET/USDT | untuned |
| 20 | AKT/USDT | untuned |
| 21 | EDGE/USDT | untuned |

## Rule

- **One strategy per symbol.** Tune its parameters on that symbol; do not
  assume a parameter set that works on BTC works on EDGE or HYPE. Prefer one
  registered strategy implementation with a per-symbol config block over 21
  forked strategy files; fork the code only when the signal logic genuinely
  differs.
- Use **4h or 1d** bars, with the same **2-year (730-day)** cap as the rest of
  the repo. A newer listing uses everything since inception. Sub-4h research
  remains limited to the last 12 months.
- Long-only spot, no shorts, no margin. Report the symbol, timeframe and
  train/OOS window with every result.
- Keep the train/test split honest: tune on the earlier slice, validate on the
  later slice. Do not select parameters on the OOS slice.

## Venue caveat

This file fixes the **symbols**, not their data venue. Binance spot is the
house default. HYPE (`HYPE:HYPEUSDT`) and CC (`MEXC:CCUSDC`) are known to use
non-Binance feeds; LIT, AKT and EDGE also need venue verification before a run.
Record the resolved venue in the symbol's strategy note. Do not silently map an
unlisted symbol to a different pair.

## Current strategy assignments

All 21 are `untuned`; `strategy` and `params` in the JSON are placeholders.
A completed assignment should name a registered strategy and its exact
parameters, its timeframe, and its in-sample / out-of-sample dates.
