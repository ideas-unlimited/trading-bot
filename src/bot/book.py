'''The book: one wallet shared by several coins, one position at a time.

Why a book and not five separate bots: a $1,000 account split five ways can
only risk 0.4% per trade (each sleeve is $200, and the 95% position cap bites
first).  Pointing the whole wallet at the strongest signal turns the same
edge into roughly five times the pounds, and it is how the rule was measured.

Rule, evaluated on completed 4h bars and filled at the next open:

* entry - fast SMA crosses above slow SMA; stop = close - atr_mult * ATR(14)
* exit  - fast SMA crosses back below slow SMA, or the stop is hit
* slots - at most max_positions positions; when two coins fire together the
          one with the strongest score_window return wins the slot
* size  - risk_per_trade of equity to the stop, capped by max_position_pct
'''

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from . import analysis
from .data.feed import Bar, load_csv
from .strategy.base import Action
from .strategy.sma_cross import SmaCross


@dataclass(frozen=True)
class BookConfig:
    fast: int = 30
    slow: int = 50
    atr_mult: float = 2.0
    atr_window: int = 14
    score_window: int = 30
    max_positions: int = 1
    risk_per_trade: float = 0.03
    max_position_pct: float = 0.95
    initial_cash: float = 1_000.0
    fee_bps: float = 10.0
    slippage_bps: float = 5.0
    damiani_filter: bool = False
    stc_min: float | None = None

    def __post_init__(self) -> None:
        if self.fast >= self.slow:
            raise ValueError('fast must be smaller than slow')
        if self.max_positions < 1:
            raise ValueError('max_positions must be >= 1')
        if not 0 < self.risk_per_trade <= 0.5:
            raise ValueError('risk_per_trade must be in (0, 0.5]')
        if not 0 < self.max_position_pct <= 1.0:
            raise ValueError('max_position_pct must be in (0, 1]')
        if self.initial_cash <= 0:
            raise ValueError('initial_cash must be positive')
        if self.stc_min is not None and not 0 <= self.stc_min <= 100:
            raise ValueError('stc_min must be in [0, 100]')


@dataclass
class BookResult:
    equity: pd.Series
    trades: pd.DataFrame
    config: BookConfig
    symbols: list[str]
    exposure: float
    bars_per_year: float
    buy_hold: float
    fees: float = 0.0
    positions: dict[str, dict[str, object]] = field(default_factory=dict)

    @property
    def final_equity(self) -> float:
        return float(self.equity.iloc[-1]) if len(self.equity) else self.config.initial_cash


def bars_to_frame(bars: Sequence[Bar]) -> pd.DataFrame:
    frame = pd.DataFrame({
        'open': [b.open for b in bars],
        'high': [b.high for b in bars],
        'low': [b.low for b in bars],
        'close': [b.close for b in bars],
        'volume': [b.volume for b in bars],
    }, index=pd.DatetimeIndex([b.ts for b in bars]))
    return frame[~frame.index.duplicated(keep='last')].sort_index()


def load_bars_from_csv(directory: str | Path, symbols: Sequence[str],
                       timeframe: str = '4h') -> dict[str, list[Bar]]:
    directory = Path(directory)
    out: dict[str, list[Bar]] = {}
    for sym in symbols:
        path = directory / f'{sym}_{timeframe}.csv'
        if not path.exists():
            path = directory / f'{sym}.csv'
        if not path.exists():
            raise FileNotFoundError(f'{path} not found')
        out[sym] = load_csv(path)
    return out


def _bars_per_year(index: pd.DatetimeIndex) -> float:
    if len(index) < 2:
        return 365.0
    gaps = pd.Series(index).diff().dropna().dt.total_seconds()
    median = float(gaps.median()) if len(gaps) else 0.0
    return max(1.0, round(365.25 * 24 * 3600 / median)) if median > 0 else 365.0


def _entry_gates(
    frames: Mapping[str, pd.DataFrame], config: BookConfig
) -> dict[str, np.ndarray] | None:
    '''Per-bar 'may open a new position' mask from the regime filters.

    'damiani_filter' requires volatility to be awake (the Damiani
    Volatmeter's 'green' state); 'stc_min' requires the Schaff Trend Cycle
    at or above a level.  Both read the signal bar only, and only ever
    *block* an entry -- exits are untouched, so a filter can never strand a
    position.
    '''
    if not (config.damiani_filter or config.stc_min is not None):
        return None
    gates: dict[str, np.ndarray] = {}
    for sym, frame in frames.items():
        ok = pd.Series(True, index=frame.index)
        if config.damiani_filter:
            ok &= analysis.damiani_volatmeter(frame)['expanding'].fillna(False)
        if config.stc_min is not None:
            ok &= analysis.schaff_trend_cycle(frame['close']) >= config.stc_min
        gates[sym] = ok.to_numpy(bool)
    return gates


def simulate_book(bars_by_symbol: Mapping[str, Sequence[Bar]],
                  config: BookConfig | None = None, *,
                  close_at_end: bool = True) -> BookResult:
    '''Replay the rule over the bars.

    'close_at_end' force-closes whatever is still open at the last bar - the
    backtest's convention.  A live paper book passes False so the open
    position survives between polls.
    '''
    config = config or BookConfig()
    if not bars_by_symbol:
        raise ValueError('no bars')
    strategy = SmaCross(config.fast, config.slow, config.atr_mult)
    frames = {sym: strategy.prepare(bars_to_frame(bars)) for sym, bars in bars_by_symbol.items()}
    gates = _entry_gates(frames, config)
    timeline = sorted(set().union(*[set(f.index) for f in frames.values()]))
    ts_index = pd.DatetimeIndex(timeline)
    bars_per_year = _bars_per_year(ts_index)

    fee = config.fee_bps / 10_000.0
    slip = config.slippage_bps / 10_000.0

    cash = config.initial_cash
    fees_paid = 0.0
    pos: dict[str, dict[str, object]] = {}
    pending_sell: list[str] = []
    pending_buy: list[tuple[float, str, float]] = []   # score, symbol, stop
    last_close: dict[str, float] = {}
    entry_bar: dict[str, int] = {}
    equity = np.empty(len(timeline))
    trades: list[dict[str, object]] = []
    bars_in = 0

    def raw_px(sym: str, ts: datetime, field: str) -> float:
        frame = frames[sym]
        if ts in frame.index:
            return float(frame.at[ts, field])
        return float(last_close.get(sym, 0.0) or 0.0)

    def mark() -> float:
        value = cash
        for sym, p in pos.items():
            value += float(p['qty']) * float(last_close.get(sym, p['entry']))
        return value

    def close_pos(sym: str, ts: datetime, px: float, reason: str) -> None:
        nonlocal cash, fees_paid
        p = pos.pop(sym)
        qty = float(p['qty'])
        proceeds = qty * px * (1.0 - fee)
        cash += proceeds
        exit_fee = qty * px * fee
        fees_paid += exit_fee
        r = (px - float(p['entry'])) / float(p['risk0']) if float(p['risk0']) > 0 else 0.0
        trades.append({
            'symbol': sym, 'entry_ts': p['entry_ts'], 'exit_ts': ts,
            'entry': float(p['entry']), 'exit': px, 'qty': qty,
            'bars': entry_bar.pop(sym, 0),
            'return_pct': px / float(p['entry']) - 1.0 if float(p['entry']) else float('nan'),
            'r_multiple': r, 'pnl': proceeds - float(p['cost']), 'reason': reason,
        })

    for i, ts in enumerate(timeline):
        # 1. fills at this bar's open: exits first so their cash is free
        for sym in pending_sell:
            if sym in pos:
                px = raw_px(sym, ts, 'open') * (1.0 - slip)
                close_pos(sym, ts, px, 'cross')
        pending_sell = []

        for _score, sym, stop in pending_buy:
            if len(pos) >= config.max_positions or sym in pos:
                continue
            if ts not in frames[sym].index:
                continue
            px = float(frames[sym].at[ts, 'open']) * (1.0 + slip)
            if stop <= 0 or stop >= px:
                continue
            value = mark()
            qty = min(config.risk_per_trade * value / (px - stop),
                      config.max_position_pct * value / px,
                      cash / (px * (1.0 + fee)))
            if qty * px < 1.0:
                continue
            cost = qty * px * (1.0 + fee)
            fees_paid += qty * px * fee
            cash -= cost
            pos[sym] = {'qty': qty, 'entry': px, 'stop': stop, 'risk0': px - stop,
                        'entry_ts': ts, 'cost': cost}
            last_close[sym] = px
            entry_bar[sym] = i
        pending_buy = []

        # 2. mark to this bar, then check the stop intrabar
        for sym in list(pos):
            if ts in frames[sym].index:
                last_close[sym] = float(frames[sym].at[ts, 'close'])
        for sym in list(pos):
            if ts not in frames[sym].index:
                continue
            o = float(frames[sym].at[ts, 'open'])
            lo = float(frames[sym].at[ts, 'low'])
            stop = float(pos[sym]['stop'])
            if lo <= stop:
                close_pos(sym, ts, min(o, stop) * (1.0 - slip), 'stop')

        equity[i] = mark()
        if pos:
            bars_in += 1

        # 3. signals at this close, executed at the next open
        candidates: list[tuple[float, str, float]] = []
        for sym, frame in frames.items():
            if ts not in frame.index:
                continue
            j = int(frame.index.get_loc(ts))
            sig = strategy.signal(j, frame, 1.0 if sym in pos else 0.0)
            if sig is None:
                continue
            if sig.action is Action.CLOSE and sym in pos:
                pending_sell.append(sym)
            elif sig.action is Action.BUY and sym not in pos and sig.stop_loss:
                if gates is not None and not gates[sym][j]:
                    continue
                closes = frame['close'].to_numpy()
                k = j - config.score_window
                score = closes[j] / closes[k] - 1.0 if k >= 0 and closes[k] > 0 else -9.0
                candidates.append((float(score), sym, float(sig.stop_loss)))

        free = config.max_positions - len(pos) + len(pending_sell)
        if free > 0 and candidates:
            candidates.sort(reverse=True)
            for score, sym, stop in candidates[:free]:
                pending_buy.append((score, sym, stop))

    open_positions: dict[str, dict[str, object]] = {}
    if pos:
        if close_at_end:
            for sym in list(pos):
                close_pos(sym, timeline[-1], last_close[sym] * (1.0 - slip), 'end')
            equity[-1] = cash
        else:
            for sym, p in pos.items():
                px = float(last_close.get(sym, p['entry']))
                open_positions[sym] = {
                    'qty': float(p['qty']), 'entry': float(p['entry']),
                    'stop': float(p['stop']), 'last': px,
                    'bars': entry_bar.get(sym, 0),
                    'upnl': float(p['qty']) * (px - float(p['entry'])),
                }

    eq = pd.Series(equity, index=ts_index, name='equity')
    trade_df = pd.DataFrame(trades)
    if len(trade_df):
        trade_df = trade_df.sort_values('entry_ts').reset_index(drop=True)

    closes = pd.DataFrame({sym: f['close'] for sym, f in frames.items()}).sort_index()
    aligned = closes.reindex(ts_index).ffill().dropna(how='all')
    buy_hold = float((aligned.iloc[-1] / aligned.iloc[0]).mean() - 1.0) if len(aligned) else float('nan')

    return BookResult(eq, trade_df, config, list(frames),
                      bars_in / max(len(timeline), 1), bars_per_year, buy_hold, fees_paid,
                      open_positions)


def summarize(result: BookResult) -> dict[str, float]:
    eq = result.equity
    if len(eq) < 2:
        return {}
    rets = eq.pct_change().dropna()
    total = eq.iloc[-1] / result.config.initial_cash - 1.0
    years = len(rets) / result.bars_per_year
    growth = eq.iloc[-1] / result.config.initial_cash
    cagr = growth ** (1.0 / years) - 1.0 if years > 0 and growth > 0 else float('nan')
    vol = float(rets.std() * math.sqrt(result.bars_per_year))
    sharpe = float(rets.mean() * result.bars_per_year / vol) if vol > 0 else float('nan')
    dd = float((eq / eq.cummax() - 1.0).min())
    rs = result.trades['r_multiple'].to_numpy(float) if len(result.trades) else np.array([])
    wins = rs[rs > 0]
    return {
        'total_return': total, 'buy_hold': result.buy_hold, 'cagr': cagr,
        'sharpe': sharpe, 'max_drawdown': dd,
        'calmar': cagr / abs(dd) if dd < 0 and not math.isnan(cagr) else float('nan'),
        'trades': float(len(rs)),
        'win_rate': len(wins) / len(rs) if len(rs) else float('nan'),
        'avg_r': float(rs.mean()) if len(rs) else float('nan'),
        'exposure': result.exposure,
        'final_equity': float(eq.iloc[-1]),
        'fees': result.fees,
    }


_LABELS = (
    ('total_return', 'Total return'), ('buy_hold', 'Buy & hold (equal weight)'),
    ('cagr', 'CAGR'), ('sharpe', 'Sharpe'), ('max_drawdown', 'Max drawdown'),
    ('calmar', 'Calmar'), ('trades', 'Round trips'), ('win_rate', 'Win rate'),
    ('avg_r', 'Average trade'), ('exposure', 'Avg exposure'), ('fees', 'Fees paid'),
    ('final_equity', 'Final equity'),
)
_PCT = {'total_return', 'buy_hold', 'cagr', 'max_drawdown', 'win_rate', 'exposure'}


def format_summary(result: BookResult) -> str:
    m = summarize(result)
    lines = []
    for key, label in _LABELS:
        if key not in m:
            continue
        value = m[key]
        if key in _PCT:
            text = f'{value * 100:.2f}%'
        elif key == 'avg_r':
            text = f'{value:+.2f} R'
        elif key == 'final_equity':
            text = f'{value:,.0f}'
        elif key == 'trades':
            text = f'{value:,.0f}'
        elif key == 'fees':
            text = f'{value:,.2f}'
        else:
            text = f'{value:.2f}'
        lines.append(f'  {label:<26} {text:>12}')
    return '\n'.join(lines)


def save_book(result: BookResult, outdir: str | Path) -> dict[str, Path]:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    eq_path = outdir / 'equity.csv'
    result.equity.rename('equity').to_csv(eq_path)
    tr_path = outdir / 'trades.csv'
    result.trades.to_csv(tr_path, index=False)
    return {'equity': eq_path, 'trades': tr_path}


def last_signal(result: BookResult, bars_by_symbol: Mapping[str, Sequence[Bar]]) -> dict[str, object]:
    '''What the rule says on the most recent completed bars, for the next open.'''
    strategy = SmaCross(result.config.fast, result.config.slow, result.config.atr_mult)
    frames = {sym: strategy.prepare(bars_to_frame(bars)) for sym, bars in bars_by_symbol.items()}
    gates = _entry_gates(frames, result.config)
    out: dict[str, object] = {}
    for sym, frame in frames.items():
        i = len(frame) - 1
        held = 1.0 if sym in result.positions else 0.0
        sig = strategy.signal(i, frame, held)
        if sig is None:
            out[sym] = 'flat / no setup'
        elif sig.action is Action.BUY:
            blocked = gates is not None and not bool(gates[sym][i])
            out[sym] = (f'BUY at next open, stop {sig.stop_loss:.4f}'
                        + ('  [filter blocks it]' if blocked else ''))
        else:
            out[sym] = sig.action.value
    return out
