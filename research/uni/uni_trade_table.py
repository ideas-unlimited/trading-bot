"""Rebuild the price-action table in UNI_CAPITULATION.md.

Reads the trade log written by the backtest CLI (runs/trades.csv) and prints
each round trip as entry / stop / target / exit / R, with P&L scaled to a
$1,000 start. The ATR values come from the same cached Binance bars the
backtest used, so the distances match the shipped sleeve exactly.

    $env:PYTHONPATH='src'; .venv\\Scripts\\python.exe research/uni/uni_trade_table.py
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

import pandas as pd

from bot.backtest import bars_to_frame
from bot.scanlib import load_scan_bars
from bot.strategy.capitulation_dip_uni import CapitulationDipUni


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--trades', default='runs/trades.csv')
    ap.add_argument('--symbol', default='UNI/USDT')
    args = ap.parse_args()

    bars = load_scan_bars(
        args.symbol, '4h', source='binance',
        start=datetime(2024, 11, 28, tzinfo=timezone.utc), days=None,
    )
    prep = CapitulationDipUni().prepare(bars_to_frame(bars))
    trades = pd.read_csv(args.trades, parse_dates=['entry_ts', 'exit_ts'])

    print('| entry | entry px | stop | target | exit | kind | R | P&L |')
    print('|---|---:|---:|---:|---|---|---:|---:|')
    for _, t in trades.iterrows():
        i = prep.index[prep['ts'] == t['entry_ts']][0]
        close = t['entry_price'] / 1.0005          # strip entry slippage
        atr = float(prep.loc[i, 'atr'])
        stop = close - 2.5 * atr
        target = close + 3.75 * atr
        risk = close - stop
        r_mult = (t['exit_price'] - t['entry_price']) / risk
        kind = t['reason_close'].split(' ')[0]
        print('| %s | %.3f | %.3f (-%.1f%%) | %.3f (+%.1f%%) | %s | %s | %+.2fR | %+.0f |'
              % (str(t['entry_ts'])[:10], close, stop, risk / close * 100,
                 target, 3.75 * atr / close * 100, str(t['exit_ts'])[:10],
                 kind, r_mult, t['pnl'] / 10))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
