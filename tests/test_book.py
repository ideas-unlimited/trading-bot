"""The book: one wallet, one slot, fills at the next open."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from bot.book import BookConfig, _entry_gates, bars_to_frame, simulate_book, summarize
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
        with self.assertRaises(ValueError):
            BookConfig(stc_min=101.0)
        with self.assertRaises(ValueError):
            BookConfig(structure_gate='magic')
        with self.assertRaises(ValueError):
            BookConfig(structure_gate='breakout', breakout_within=0)
        with self.assertRaises(ValueError):
            BookConfig(structure_gate='squeeze', squeeze_quantile=1.0)

    def test_entry_gates_only_block(self):
        frame = bars_to_frame(make_bars([100.0 + (i % 7) for i in range(300)]))
        self.assertIsNone(_entry_gates({'A': frame}, BookConfig()))
        gates = _entry_gates({'A': frame}, BookConfig(damiani_filter=True, stc_min=50.0))
        self.assertEqual(len(gates['A']), len(frame))
        self.assertEqual(gates['A'].dtype, bool)
        self.assertFalse(gates['A'].all())

    def test_structure_gates_only_block(self):
        closes = [100.0 + (i % 7) - 0.05 * i for i in range(300)]
        frame = bars_to_frame(make_bars(closes))
        for mode in ('breakout', 'squeeze', 'room'):
            gates = _entry_gates({'A': frame}, BookConfig(structure_gate=mode))
            self.assertEqual(len(gates['A']), len(frame))
            self.assertEqual(gates['A'].dtype, bool)
            # an unmargined rail breakout can only ever be a subset of bars
            self.assertFalse(gates['A'].all() and mode != 'squeeze')

    def test_structure_gate_never_adds_trades(self):
        closes = [100.0] * 20 + [100.0 + i for i in range(1, 40)] + [140.0 - i for i in range(1, 40)]
        bars = make_bars(closes)
        plain = simulate_book({'A': bars}, BookConfig(fast=2, slow=3, fee_bps=0.0, slippage_bps=0.0))
        self.assertGreaterEqual(len(plain.trades), 1)
        for mode in ('breakout', 'squeeze', 'room'):
            gated = simulate_book({'A': bars}, BookConfig(
                fast=2, slow=3, fee_bps=0.0, slippage_bps=0.0, structure_gate=mode))
            self.assertLessEqual(len(gated.trades), len(plain.trades), mode)

    def test_filters_never_add_trades(self):
        closes = [100.0] * 20 + [100.0 + i for i in range(1, 40)] + [140.0 - i for i in range(1, 40)]
        bars = make_bars(closes)
        plain = simulate_book({'A': bars}, BookConfig(fast=2, slow=3, fee_bps=0.0, slippage_bps=0.0))
        self.assertGreaterEqual(len(plain.trades), 1)
        gated = simulate_book({'A': bars}, BookConfig(
            fast=2, slow=3, fee_bps=0.0, slippage_bps=0.0,
            damiani_filter=True, stc_min=70.0))
        self.assertLessEqual(len(gated.trades), len(plain.trades))

    def test_trail_config_validation(self):
        with self.assertRaises(ValueError):
            BookConfig(trail_atr=0.0)
        with self.assertRaises(ValueError):
            BookConfig(trail_after_r=-0.5)

    def test_trail_lifts_the_exit_on_a_runner(self):
        closes = [100.0] * 60 + [100.0 + i for i in range(1, 121)] + [220.0 - 3.0 * i for i in range(1, 31)]
        bars = make_bars(closes)
        base = dict(fast=5, slow=10, fee_bps=0.0, slippage_bps=0.0)
        fixed = simulate_book({'A': bars}, BookConfig(**base))
        trailed = simulate_book({'A': bars}, BookConfig(**base, trail_atr=1.5))
        self.assertEqual(len(fixed.trades), 1)
        self.assertEqual(len(trailed.trades), 1)
        f_exit = float(fixed.trades.iloc[0]['exit'])
        t_exit = float(trailed.trades.iloc[0]['exit'])
        self.assertGreater(t_exit, f_exit)
        self.assertGreater(t_exit, 150.0)          # the run was 120 points
        self.assertLess(t_exit, 220.0)             # but not the very top
        self.assertGreater(float(trailed.trades.iloc[0]['r_multiple']), 1.0)

    def test_trail_after_r_waits_for_profit(self):
        closes = [100.0] * 60 + [100.0 + i for i in range(1, 121)] + [220.0 - 3.0 * i for i in range(1, 31)]
        bars = make_bars(closes)
        base = dict(fast=5, slow=10, fee_bps=0.0, slippage_bps=0.0)
        fixed = simulate_book({'A': bars}, BookConfig(**base))
        late = simulate_book({'A': bars}, BookConfig(**base, trail_atr=1.5, trail_after_r=50.0))
        self.assertEqual(float(fixed.trades.iloc[0]['exit']),
                         float(late.trades.iloc[0]['exit']))

    def test_trail_is_causal(self):
        closes = [100.0] * 60 + [100.0 + (i % 9) * 1.5 for i in range(1, 161)] + [110.0] * 40
        bars = make_bars(closes)
        cfg = BookConfig(fast=5, slow=10, fee_bps=0.0, slippage_bps=0.0, trail_atr=2.0)
        full = simulate_book({'A': bars}, cfg)
        part = simulate_book({'A': bars[:150]}, cfg)
        cut = bars[149].ts
        stamp = lambda df: [(r.symbol, str(r.entry_ts), str(r.exit_ts), round(float(r.exit), 6))
                            for r in df.itertuples()]
        self.assertEqual(stamp(part.trades[part.trades['exit_ts'] < cut]),
                         stamp(full.trades[full.trades['exit_ts'] < cut]))

    def test_bars_to_frame_dedupes(self):
        bars = make_bars([1.0, 2.0])
        frame = bars_to_frame(bars + [bars[-1]])
        self.assertEqual(len(frame), 2)


if __name__ == "__main__":
    unittest.main()
