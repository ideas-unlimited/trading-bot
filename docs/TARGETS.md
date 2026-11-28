# Target symbols and per-symbol strategy rule

`config/target_symbols.json` is the authoritative target list. It now contains
**18 active Binance spot USDT pairs**. Other agents may read it to build one
strategy per symbol; do not add, remove or substitute symbols without the
user's approval.

## Hard trading rule

- **Binance spot only.** Every trade and every backtest uses Binance spot
  data. Binance is the default and only data source.
- **USDT quote only.** No USDC, BTC, BUSD or other quote pairs; no futures,
  margin, leverage or shorts.
- Only symbols present and tradeable on Binance spot belong here. Do not
  silently switch venue or quote, and do not substitute a different token.

## Availability check

Checked against Binance `exchangeInfo` on **2026-11-28**. The list was cleaned
of every symbol that is not available on Binance spot, then XAUT was added:

- **CC** — no Binance spot pair (previously shown as MEXC `CC/USDC`).
- **LIT** — `LITUSDT` exists, but its status is `BREAK` (halted), so it is not
  tradeable.
- **AKT** — no Binance spot pair.
- **EDGE** — no Binance spot pair.

`XAUT/USDT` is an active Binance spot pair and is included as the 18th target.

The removed entries and reasons are retained in the JSON's `removed` array for
audit. They are not targets and must not be tuned or traded.

## Target table

| # | Symbol | Venue | Status |
|---:|---|---|---|
| 1 | ETH/USDT | Binance spot | untuned |
| 2 | BNB/USDT | Binance spot | untuned |
| 3 | SOL/USDT | Binance spot | untuned |
| 4 | XRP/USDT | Binance spot | untuned |
| 5 | HYPE/USDT | Binance spot | untuned |
| 6 | LINK/USDT | Binance spot | untuned |
| 7 | UNI/USDT | Binance spot | untuned |
| 8 | AVAX/USDT | Binance spot | untuned |
| 9 | SUI/USDT | Binance spot | untuned |
| 10 | TAO/USDT | Binance spot | untuned |
| 11 | ONDO/USDT | Binance spot | untuned |
| 12 | JUP/USDT | Binance spot | untuned |
| 13 | RENDER/USDT | Binance spot | untuned |
| 14 | INJ/USDT | Binance spot | untuned |
| 15 | CAKE/USDT | Binance spot | untuned |
| 16 | PYTH/USDT | Binance spot | untuned |
| 17 | FET/USDT | Binance spot | untuned |
| 18 | XAUT/USDT | Binance spot | untuned |

## Per-symbol strategy rule

- **One strategy per symbol.** Tune its parameters on that symbol; do not
  assume a parameter set that works on BTC works on SUI or HYPE.
- Prefer one registered implementation per signal family with a per-symbol
  config block over 18 forked strategy files. When a registration's parameters
  are fitted to one symbol, name it with the symbol suffix
  (`capitulation_dip_uni`) and keep its research in `research/<symbol>/`;
  fork the code only when the signal logic genuinely differs.
- Tune on the earlier slice, validate on the later slice. Report the symbol,
  timeframe, venue and both windows with every result; never select
  parameters on the out-of-sample slice.

## Data and windows

- **4h or 1d** bars, with the repo-wide **2-year (730-day)** cap.
- A newer listing uses everything since inception; do not fabricate history.
- Sub-4h research remains limited to the last 12 months.
- Long-only spot, no shorts, no margin.

## Current assignments

All 18 symbols are unassigned. `strategy`, `timeframe` and `params` in the JSON
are placeholders; a completed assignment must name a registered strategy, its
exact parameters, its timeframe and its in-sample / out-of-sample dates.
