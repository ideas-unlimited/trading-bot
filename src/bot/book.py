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
    # chart-structure gates (see bot.analysis.trendline_channel)
    structure_gate: str | None = None   # 'breakout' | 'squeeze' | 'room'
    channel_left: int = 10
    channel_right: int = 10
    breakout_atr: float = 0.5
    breakout_within: int = 5
    squeeze_quantile: float = 0.4
    min_rr: float = 2.0
    channel_spans: tuple[int, ...] = ()   # non-empty => median channel (robust)
    # trailing exit: a Chandelier stop k ATRs under the highest high since entry,
    # ratcheted up only, armed once the trade shows 'trail_after_r' of profit
    trail_atr: float | None = None
    trail_after_r: float = 0.0

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
        if self.structure_gate not in (None, 'breakout', 'squeeze', 'room'):
            raise ValueError("structure_gate must be one of: breakout, squeeze, room")
        if self.breakout_within < 1:
            raise ValueError('breakout_within must be >= 1')
        if not 0 < self.squeeze_quantile < 1:
            raise ValueError('squeeze_quantile must be in (0, 1)')
        if self.trail_atr is not None and self.trail_atr <= 0:
            raise ValueError('trail_atr must be positive or None')
        if self.trail_after_r < 0:
            raise ValueError('trail_after_r must be >= 0')


# Settings that describe the wallet, not a coin.  A per-symbol override of one
# of these would silently change every coin's sizing, so the engine rejects it.
PORTFOLIO_FIELDS: tuple[str, ...] = ('max_positions', 'initial_cash')


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
    symbol_configs: dict[str, BookConfig] = field(default_factory=dict)

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


def _structure_ok(frame: pd.DataFrame, config: BookConfig, channel: pd.DataFrame) -> pd.Series:
    '''One chart-structure gate over a frame; True means 'entry allowed'.'''
    mode = config.structure_gate
    if mode == 'breakout':
        brk = analysis.channel_breakout(
            frame, channel, atr_mult=config.breakout_atr, confirm=1
        )['break_up']
        # a breakout counts for the next `breakout_within` bars
        return brk.rolling(config.breakout_within, min_periods=1).max().fillna(False).astype(bool)
    if mode == 'squeeze':
        height = channel['height'].abs()
        ratio = (height / analysis.atr(frame['high'], frame['low'], frame['close'],
                                      config.atr_window)).replace([np.inf, -np.inf], np.nan)
        # compressed = channel thinner than its own recent distribution
        thresh = ratio.rolling(200, min_periods=50).quantile(config.squeeze_quantile)
        return (ratio <= thresh).fillna(False)
    if mode == 'room':
        target = analysis.measured_move(frame, channel)['target_up']
        risk = config.atr_mult * analysis.atr(
            frame['high'], frame['low'], frame['close'], config.atr_window
        )
        return ((target - frame['close']) >= config.min_rr * risk).fillna(False)
    raise ValueError(f'unknown structure_gate {mode!r}')


def _entry_gates(
    frames: Mapping[str, pd.DataFrame], config: BookConfig
) -> dict[str, np.ndarray] | None:
    '''Per-bar 'may open a new position' mask from the regime filters.

    'damiani_filter' requires volatility to be awake (the Damiani
    Volatmeter's 'green' state); 'stc_min' requires the Schaff Trend Cycle
    at or above a level.  'structure_gate' adds one chart-structure test
    ported from the pasted pattern indicator: 'breakout' wants an upper-rail
    break, 'squeeze' wants a coiling channel, 'room' wants the measured move
    to pay at least 'min_rr' times the ATR stop.  All of them read the
    signal bar only, and only ever *block* an entry -- exits are untouched,
    so a filter can never strand a position.
    '''
    if not (
        config.damiani_filter
        or config.stc_min is not None
        or config.structure_gate is not None
    ):
        return None
    gates: dict[str, np.ndarray] = {}
    for sym, frame in frames.items():
        ok = pd.Series(True, index=frame.index)
        if config.damiani_filter:
            ok &= analysis.damiani_volatmeter(frame)['expanding'].fillna(False)
        if config.stc_min is not None:
            ok &= analysis.schaff_trend_cycle(frame['close']) >= config.stc_min
        if config.structure_gate is not None:
            if config.channel_spans:
                channel = analysis.trendline_channel_ensemble(
                    frame, spans=config.channel_spans
                )
            else:
                channel = analysis.trendline_channel(
                    frame, left=config.channel_left, right=config.channel_right
                )
            ok &= _structure_ok(frame, config, channel)
        gates[sym] = ok.to_numpy(bool)
    return gates


def effective_configs(
    config: BookConfig,
    symbol_configs: Mapping[str, BookConfig] | None,
    symbols: Sequence[str],
) -> dict[str, BookConfig]:
    '''One config per coin: its override, else the book's.

    A per-symbol config may differ in anything that describes a *coin* (the
    MA spans, the filters, the trail, costs, sizing).  The wallet-level
    fields in PORTFOLIO_FIELDS must match the book, because one coin silently
    changing max_positions or the starting cash would move every other coin.
    '''
    out: dict[str, BookConfig] = {}
    for sym in symbols:
        cfg = (symbol_configs or {}).get(sym) or config
        for name in PORTFOLIO_FIELDS:
            if getattr(cfg, name) != getattr(config, name):
                raise ValueError(
                    f'{sym}: {name!r} is a wallet-level setting and cannot be '
                    f'set per symbol'
                )
        out[sym] = cfg
    return out


def simulate_book(bars_by_symbol: Mapping[str, Sequence[Bar]],
                  config: BookConfig | None = None, *,
                  close_at_end: bool = True,
                  symbol_configs: Mapping[str, BookConfig] | None = None) -> BookResult:
    '''Replay the rule over the bars.

    'close_at_end' force-closes whatever is still open at the last bar - the
    backtest's convention.  A live paper book passes False so the open
    position survives between polls.

    'symbol_configs' gives individual coins their own parameters (see
    bot.symbolcfg): the engine then evaluates each coin's MAs, filters, trail
    and costs with that coin's config, while the wallet - starting cash and
    the number of slots - stays the book's.
    '''
    config = config or BookConfig()
    if not bars_by_symbol:
        raise ValueError('no bars')
    cfgs = effective_configs(config, symbol_configs, tuple(bars_by_symbol))
    strategies = {sym: SmaCross(cfgs[sym].fast, cfgs[sym].slow, cfgs[sym].atr_mult)
                  for sym in bars_by_symbol}
    frames = {sym: strategies[sym].prepare(bars_to_frame(bars))
              for sym, bars in bars_by_symbol.items()}
    gates: dict[str, np.ndarray] = {}
    for sym, frame in frames.items():
        one = _entry_gates({sym: frame}, cfgs[sym])
        if one is not None:
            gates[sym] = one[sym]
    timeline = sorted(set().union(*[set(f.index) for f in frames.values()]))
    ts_index = pd.DatetimeIndex(timeline)
    bars_per_year = _bars_per_year(ts_index)

    fee = {sym: cfgs[sym].fee_bps / 10_000.0 for sym in frames}
    slip = {sym: cfgs[sym].slippage_bps / 10_000.0 for sym in frames}

    cash = config.initial_cash
    fees_paid = 0.0
    pos: dict[str, dict[str, object]] = {}
    pending_sell: list[str] = []
    pending_buy: list[tuple[float, str, float]] = []   # score, symbol, stop
    last_close: dict[str, float] = {}
    entry_bar: dict[str, int] = {}
    peak: dict[str, float] = {}
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
        proceeds = qty * px * (1.0 - fee[sym])
        cash += proceeds
        exit_fee = qty * px * fee[sym]
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
                px = raw_px(sym, ts, 'open') * (1.0 - slip[sym])
                close_pos(sym, ts, px, 'cross')
        pending_sell = []

        for _score, sym, stop in pending_buy:
            if len(pos) >= config.max_positions or sym in pos:
                continue
            if ts not in frames[sym].index:
                continue
            cfg = cfgs[sym]
            px = float(frames[sym].at[ts, 'open']) * (1.0 + slip[sym])
            if stop <= 0 or stop >= px:
                continue
            value = mark()
            qty = min(cfg.risk_per_trade * value / (px - stop),
                      cfg.max_position_pct * value / px,
                      cash / (px * (1.0 + fee[sym])))
            if qty * px < 1.0:
                continue
            cost = qty * px * (1.0 + fee[sym])
            fees_paid += qty * px * fee[sym]
            cash -= cost
            pos[sym] = {'qty': qty, 'entry': px, 'stop': stop, 'risk0': px - stop,
                        'entry_ts': ts, 'cost': cost}
            peak[sym] = px
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
                close_pos(sym, ts, min(o, stop) * (1.0 - slip[sym]), 'stop')

        # 2b. ratchet the trail from this bar's close for the *next* bar, so no
        #     bar can both update the trail and fill against it (no lookahead)
        if pos:
            for sym in list(pos):
                if ts not in frames[sym].index:
                    continue
                cfg = cfgs[sym]
                if not cfg.trail_atr:
                    continue
                row = frames[sym].loc[ts]
                atr = float(row['atr'])
                if not np.isfinite(atr) or atr <= 0:
                    continue
                peak[sym] = max(peak.get(sym, float(pos[sym]['entry'])), float(row['high']))
                risk0 = float(pos[sym]['risk0'])
                gain_r = (float(last_close[sym]) - float(pos[sym]['entry'])) / risk0 if risk0 > 0 else 0.0
                if gain_r < cfg.trail_after_r:
                    continue
                candidate = peak[sym] - cfg.trail_atr * atr
                if candidate > float(pos[sym]['stop']):
                    pos[sym]['stop'] = candidate

        equity[i] = mark()
        if pos:
            bars_in += 1

        # 3. signals at this close, executed at the next open
        candidates: list[tuple[float, str, float]] = []
        for sym, frame in frames.items():
            if ts not in frame.index:
                continue
            j = int(frame.index.get_loc(ts))
            sig = strategies[sym].signal(j, frame, 1.0 if sym in pos else 0.0)
            if sig is None:
                continue
            if sig.action is Action.CLOSE and sym in pos:
                pending_sell.append(sym)
            elif sig.action is Action.BUY and sym not in pos and sig.stop_loss:
                if sym in gates and not gates[sym][j]:
                    continue
                closes = frame['close'].to_numpy()
                k = j - cfgs[sym].score_window
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
                close_pos(sym, timeline[-1], last_close[sym] * (1.0 - slip[sym]), 'end')
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
                      open_positions, cfgs)


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


def last_signal(result: BookResult, bars_by_symbol: Mapping[str, Sequence[Bar]],
                symbol_configs: Mapping[str, BookConfig] | None = None) -> dict[str, object]:
    '''What the rule says on the most recent completed bars, for the next open.'''
    cfgs = effective_configs(
        result.config,
        symbol_configs if symbol_configs is not None else result.symbol_configs,
        tuple(bars_by_symbol),
    )
    out: dict[str, object] = {}
    for sym, bars in bars_by_symbol.items():
        cfg = cfgs[sym]
        strategy = SmaCross(cfg.fast, cfg.slow, cfg.atr_mult)
        frame = strategy.prepare(bars_to_frame(bars))
        i = len(frame) - 1
        held = 1.0 if sym in result.positions else 0.0
        sig = strategy.signal(i, frame, held)
        if sig is None:
            out[sym] = 'flat / no setup'
        elif sig.action is Action.BUY:
            gates = _entry_gates({sym: frame}, cfg)
            blocked = gates is not None and not bool(gates[sym][i])
            out[sym] = (f'BUY at next open, stop {sig.stop_loss:.4f}'
                        + ('  [filter blocks it]' if blocked else ''))
        else:
            out[sym] = sig.action.value
    return out
