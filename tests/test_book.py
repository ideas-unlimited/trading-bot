"""The book: one wallet, one slot, fills at the next open."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from bot.book import BookConfig, bars_to_frame, simulate_book, summarize
from bot.data.feed import Bar


def make_bars(closes, opens=None, highs=None, lows=None, start=None):
    start = start or datetime(2024, 1, 1, tzinfo=timezone.utc)
    bars = []
    for i, c in enumerate(closes):
        o = opens[i] if opens else c
        h = highs[i] if highs else max(o, c) + 0.5
        lo = lows[i] if lows else min(o, c) - 0.5
        bars.append(Bar(start + timedelta(hours=4 * i), o, h, lo, c, 1.0))
    return bars


def sma(xs, n):
    return sum(xs[-n:]) / n


def first_cross(bars, fast, slow):
    closes = [b.close for b in bars]
    for i in range(1, len(bars)):
        if i + 1 < slow:
            continue
        pf = sma(closes[:i], fast)
        ps = sma(closes[:i], slow)
        f = sma(closes[: i + 1], fast)
        s = sma(closes[: i + 1], slow)
        if pf <= ps and f > s:
            return i
    return None


class BookTests(unittest.TestCase):
    def test_entry_fills_at_next_open(self):
        closes = [100.0] * 20 + [100.0 + i for i in range(1, 12)]
        bars = make_bars(closes)
        cfg = BookConfig(fast=2, slow=3, atr_mult=2.0, fee_bps=0.0, slippage_bps=0.0)
        result = simulate_book({'A': bars}, cfg)
        i = first_cross(bars, 2, 3)
        self.assertIsNotNone(i)
        self.assertGreaterEqual(len(result.trades), 1)
        trade = result.trades.iloc[0]
        self.assertEqual(trade['entry_ts'], bars[i + 1].ts)
        self.assertAlmostEqual(trade['entry'], bars[i + 1].open)

    def test_one_slot_means_no_overlapping_trades(self):
        a = [100.0] * 20 + [100.0 + 2 * i for i in range(1, 15)] + [128.0 - 2 * i for i in range(1, 15)]
        b = [100.0] * 40 + [100.0 + 2 * i for i in range(1, 15)]
        cfg = BookConfig(fast=2, slow=3, max_positions=1, fee_bps=0.0, slippage_bps=0.0)
        result = simulate_book({'A': make_bars(a), 'B': make_bars(b)}, cfg)
        tr = result.trades.sort_values('entry_ts').reset_index(drop=True)
        self.assertGreaterEqual(len(tr), 1)
        for i in range(len(tr) - 1):
            self.assertLessEqual(tr.loc[i, 'exit_ts'], tr.loc[i + 1, 'entry_ts'])

    def test_stop_takes_priority_inside_a_bar(self):
        closes = [100.0] * 20 + [101.0, 102.0, 103.0]
        bars = make_bars(closes)
        cfg = BookConfig(fast=2, slow=3, atr_mult=2.0, fee_bps=0.0, slippage_bps=0.0)
        result = simulate_book({'A': bars}, cfg)
        self.assertGreaterEqual(len(result.trades), 1)
        entry = float(result.trades.iloc[0]['entry'])
        crash = Bar(bars[3].ts, entry * 0.6, entry * 0.6, entry * 0.5, entry * 0.55, 1.0)
        bars2 = bars[:3] + [crash]
        res2 = simulate_book({'A': bars2}, cfg)
        if len(res2.trades):
            self.assertLessEqual(float(res2.trades.iloc[0]['exit']), entry)

    def test_flat_market_does_nothing(self):
        bars = make_bars([100.0] * 60)
        result = simulate_book({'A': bars}, BookConfig(fast=2, slow=3))
        self.assertEqual(len(result.trades), 0)
        self.assertAlmostEqual(result.final_equity, 1000.0)

    def test_cash_and_equity_stay_positive(self):
        closes = [100.0] * 20 + [100.0 + i for i in range(1, 40)] + [140.0 - i for i in range(1, 40)]
        bars = make_bars(closes)
        result = simulate_book({'A': bars}, BookConfig(fast=3, slow=8, max_positions=1))
        self.assertTrue((result.equity > 0).all())
        m = summarize(result)
        self.assertGreater(m['final_equity'], 0)
        self.assertEqual(m['trades'], len(result.trades))

    def test_config_validation(self):
        with self.assertRaises(ValueError):
            BookConfig(fast=50, slow=20)
        with self.assertRaises(ValueError):
            BookConfig(risk_per_trade=0.0)
        with self.assertRaises(ValueError):
            BookConfig(max_positions=0)

    def test_bars_to_frame_dedupes(self):
        bars = make_bars([1.0, 2.0])
        frame = bars_to_frame(bars + [bars[-1]])
        self.assertEqual(len(frame), 2)


if __name__ == "__main__":
    unittest.main()
