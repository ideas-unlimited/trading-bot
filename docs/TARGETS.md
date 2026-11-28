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
| 7 | UNI/USDT | Binance spot | sleeve: `capitulation_dip_uni`, paper pending |
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
- A finished assignment ships as a **static sleeve module**
  `src/bot/strategy/<strategy>_<symbol>.py`: the rule with its tuned defaults
  baked in, a docstring carrying the window, timeframe and numbers, a registry
  entry and its own test. Reuse a family's signal logic by subclassing it or
  by passing params into the shared class; fork the code only when the signal
  logic genuinely differs. The sleeve is the artifact - there is no separate
  runtime parameter file to drift, and git history plus the docstring are the
  audit trail. Research may sweep per-coin configs (`bot.symbolcfg`,
  `bot auto --pool`), but that plumbing is a measurement harness, never the
  deployment path.
- Tune on the earlier slice, validate on the later slice. Report the symbol,
  timeframe, venue and both windows with every result; never select
  parameters on the out-of-sample slice.

## Data and windows

- **4h or 1d** bars, with the repo-wide **2-year (730-day)** cap.
- A newer listing uses everything since inception; do not fabricate history.
- Sub-4h research remains limited to the last 12 months.
- Long-only spot, no shorts, no margin.

## Current assignments

17 symbols are unassigned; `strategy`, `timeframe` and `params` in the JSON
are placeholders for them. A completed assignment names the sleeve module that
ships (`<strategy>_<symbol>`), its timeframe, and its in-sample /
out-of-sample dates. The numbers live in that module, not in this file.

UNI shipped `capitulation_dip_uni` (4h, 2024-11-28 .. 2026-11-28, +67.8% /
-5.3% drawdown / 19 trades at 5% risk and 50% position cap, researched in
`research/uni/`). It is a **candidate, not live**: no
`promotions/paper/capitulation_dip_uni.json` exists yet, and the forward
runner does not yet execute its 1.5R target or persist the 18-bar hold clock
(`research/uni/UNI_CAPITULATION.md`, "Deployment status").
