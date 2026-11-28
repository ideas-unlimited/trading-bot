'''Live paper books: the shipped rules, forward, on real bars.

Three independent $1,000 paper wallets, one per book from the research:

* **A** trend gated - 4h SMA 30/50 cross, Damiani + STC gate, 3% risk
* **B** trend max   - the same signals at 5% risk (the most dollars)
* **C** plain cross - the same cross with no filters, 3% risk

Each cycle loads the newest bars from the Postgres cache (delta-sync,
2-year cap), drops the still-forming bar, replays the book over the whole
window and reports the wallet, the open position and what the next open will
do.  Nothing is ordered: the wallets are simulated, so the three books can
hold the same coin with different sizes without fighting over a broker
account.

The replay is deterministic, so equity can never drift from the backtest -
if the bot was restarted, the numbers are the same.  'Since deploy' figures
are measured from the equity the book had on the first run, which is the
honest forward number.
'''

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import scanlib, symbolcfg
from .book import BookConfig, BookResult, last_signal, save_book, simulate_book, summarize
from .data.feed import Bar

TIMEFRAME_SPAN = {"1h": timedelta(hours=1), "4h": timedelta(hours=4), "1d": timedelta(days=1)}
MAX_HISTORY_DAYS = scanlib.MAX_HISTORY_DAYS


@dataclass(frozen=True)
class BookSpec:
    '''One wallet: a config, the coins it may hold, and per-coin overrides.

    'symbols' empty means every coin in the run (the original shared-wallet
    book).  One coin in 'symbols' with its own 'symbol_params' is a
    single-coin wallet - the shape a tuned per-symbol bot takes.  Both can
    coexist in one run, so a promoted per-symbol book and the pooled books
    are replayed side by side.
    '''

    key: str
    label: str
    config: BookConfig
    cash: float = 1_000.0
    symbols: tuple[str, ...] = ()
    symbol_params: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, 'symbols',
                           tuple(symbolcfg.normalize_symbol(s) for s in self.symbols))
        clean: dict[str, Mapping[str, Any]] = {}
        for sym, params in self.symbol_params.items():
            key = symbolcfg.normalize_symbol(sym)
            if self.symbols and key not in self.symbols:
                raise ValueError(
                    f'book {self.key}: {key} has parameters but is not one of its symbols'
                )
            clean[key] = dict(params)
        object.__setattr__(self, 'symbol_params', clean)
        for sym in clean:  # fail at construction, not in hour three of a replay
            self.config_for(sym)

    def config_for(self, symbol: str) -> BookConfig:
        '''This coin's parameters layered over the book's config.'''
        params = self.symbol_params.get(symbol)
        return (symbolcfg.merge_book_config(self.config, params, symbol=symbol)
                if params else self.config)

    def configs(self, symbols: Sequence[str] | None = None) -> dict[str, BookConfig]:
        return {sym: self.config_for(sym) for sym in (symbols or self.symbols)}


def default_books() -> tuple[BookSpec, ...]:
    '''The books that survived the research, in the order to run them.'''
    return (
        BookSpec("A", "trend gated 3%",
                 BookConfig(risk_per_trade=0.03, damiani_filter=True, stc_min=50.0)),
        BookSpec("B", "trend max 5%",
                 BookConfig(risk_per_trade=0.05, damiani_filter=True, stc_min=50.0)),
        BookSpec("C", "plain cross 3%", BookConfig(risk_per_trade=0.03)),
        # forward test: the measured-move 'room' gate on a median channel
        # across pivot spans, so the span is not a tuned parameter
        BookSpec("D", "room gate 3%",
                 BookConfig(risk_per_trade=0.03, structure_gate="room",
                            min_rr=1.2, channel_spans=(10, 15, 20))),
        # the trailing-stop challenger: same entries as A, but a 1.5 ATR
        # Chandelier trail ends the trade instead of the fixed 2 ATR stop
        BookSpec("E", "trail 1.5 ATR 3%",
                 BookConfig(risk_per_trade=0.03, damiani_filter=True, stc_min=50.0,
                            trail_atr=1.5, max_positions=2)),
    )


def books_from_pool(entries: Sequence[symbolcfg.SymbolEntry] | None = None, *,
                    base: BookConfig | None = None, cash: float = 1_000.0,
                    only_available: bool = True) -> tuple[BookSpec, ...]:
    '''One $1,000 wallet per coin from config/target_symbols.json.

    Every coin gets its own wallet with its own parameter set, which is what
    per-symbol tuning needs: a coin can be re-tuned, retired or compared on
    its own P&L without touching another coin's book.
    '''
    entries = list(entries if entries is not None else symbolcfg.load_pool())
    base = base or BookConfig()
    books: list[BookSpec] = []
    for entry in entries:
        if only_available and not entry.available:
            continue
        strategy = (entry.strategy or 'sma_cross').strip()
        if strategy not in ('sma_cross', 'book'):
            raise NotImplementedError(
                f'{entry.symbol}: the book engine runs the SMA cross; '
                f'{strategy!r} needs a per-symbol bot, not a book'
            )
        entry.config(base)  # validates this coin's parameters up front
        books.append(BookSpec(
            key=entry.base,
            label=f'{entry.base} {strategy}',
            config=base,
            cash=float(cash),
            symbols=(entry.symbol,),
            symbol_params={entry.symbol: entry.params} if entry.params else {},
        ))
    return tuple(books)


def select_books(keys: str | Sequence[str] | None, books: Sequence[BookSpec] | None = None,
                 cash: float | None = None) -> list[BookSpec]:
    '''Resolve 'A,C' style selections and apply a cash override.'''
    pool = list(books or default_books())
    if keys is None:
        chosen = pool
    else:
        parts = keys.split(",") if isinstance(keys, str) else keys
        wanted = [k.strip().upper() for k in parts if str(k).strip()]
        if wanted and wanted[0] == "ALL":
            wanted = [b.key for b in pool]
        by_key = {b.key: b for b in pool}
        missing = [k for k in wanted if k not in by_key]
        if missing:
            raise ValueError(f'unknown book(s): {", ".join(missing)}')
        chosen = [by_key[k] for k in wanted]
    if cash is not None:
        chosen = [replace(b, cash=float(cash)) for b in chosen]
    return chosen


def closed_bars(bars: Sequence[Bar], timeframe: str, now: datetime | None = None) -> list[Bar]:
    '''Drop the bar that is still forming - a signal may only use closed bars.'''
    span = TIMEFRAME_SPAN.get(timeframe)
    if span is None:
        return list(bars)
    now = now or datetime.now(timezone.utc)
    return [b for b in bars if b.ts + span <= now]


def load_bars(symbols: Sequence[str], timeframe: str = "4h", *, source: str = "binance",
              days: int = MAX_HISTORY_DAYS) -> dict[str, list[Bar]]:
    '''Bars per symbol from the shared cache, with a delta sync against the feed.'''
    out: dict[str, list[Bar]] = {}
    for sym in symbols:
        bars = scanlib.load_scan_bars(sym, timeframe, source=source, days=days, n_bars=10_000)
        if bars:
            out[sym] = bars
    return out


def _param_differences(base: BookConfig,
                       configs: Mapping[str, BookConfig]) -> dict[str, dict[str, Any]]:
    '''The settings a coin does not share with its book (empty = pooled).'''
    shared = asdict(base)
    out: dict[str, dict[str, Any]] = {}
    for sym, cfg in configs.items():
        diff = {k: v for k, v in asdict(cfg).items() if v != shared[k]}
        if diff:
            out[sym] = diff
    return out


def run_books(bars_by_symbol: Mapping[str, Sequence[Bar]], books: Sequence[BookSpec],
              *, now: datetime | None = None) -> dict[str, dict[str, Any]]:
    '''One replay per book over the same bars.  No orders, no lookahead.'''
    now = now or datetime.now(timezone.utc)
    reports: dict[str, dict[str, Any]] = {}
    for spec in books:
        if spec.symbols:
            missing = [s for s in spec.symbols if s not in bars_by_symbol]
            if missing:
                raise ValueError(f'book {spec.key}: no bars for {", ".join(missing)}')
            pool = {s: bars_by_symbol[s] for s in spec.symbols}
        else:
            pool = dict(bars_by_symbol)
        if not pool:
            continue
        config = replace(spec.config, initial_cash=float(spec.cash))
        configs = {sym: spec.config_for(sym) for sym in pool}
        result = simulate_book(pool, config, close_at_end=False, symbol_configs=configs)
        stats = summarize(result)
        reports[spec.key] = {
            'label': spec.label,
            'result': result,
            'stats': stats,
            'signals': last_signal(result, pool),
            'symbols': list(pool),
            'symbol_params': _param_differences(config, configs),
            'updated_at': now.isoformat(),
        }
    return reports


def _load_state(path: Path) -> dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def default_outdir() -> Path:
    """Paper and prod must never share a status file."""
    from . import envcfg

    return envcfg.state_dir() / "autobooks"


def _window_moved(prev_start: str, start: str, tolerance_days: float = 2.0) -> bool:
    '''True when the replay window now starts far enough back to be a new book.'''
    try:
        a = datetime.fromisoformat(prev_start.replace('Z', '+00:00'))
        b = datetime.fromisoformat(start.replace('Z', '+00:00'))
    except ValueError:
        return True
    return abs((a - b).total_seconds()) > tolerance_days * 86400.0


def run_once(symbols: Sequence[str] | None = None, *, books: Sequence[BookSpec] | None = None,
             timeframe: str = "4h", source: str = "binance", days: int = MAX_HISTORY_DAYS,
             outdir: str | Path | None = None, now: datetime | None = None,
             bars_by_symbol: Mapping[str, Sequence[Bar]] | None = None) -> dict[str, Any]:
    '''One cycle: load bars, replay every book, write state and CSVs.'''
    outdir = Path(outdir) if outdir is not None else default_outdir()
    outdir.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(timezone.utc)
    specs = list(books or default_books())
    symbols = list(symbols or ("BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT"))

    if bars_by_symbol is None:
        bars_by_symbol = load_bars(symbols, timeframe, source=source, days=days)
    trimmed = {s: closed_bars(b, timeframe, now) for s, b in bars_by_symbol.items()}
    trimmed = {s: b for s, b in trimmed.items() if len(b) > 60}
    if not trimmed:
        raise RuntimeError('no closed bars available for any symbol')

    reports = run_books(trimmed, specs, now=now)
    state_path = outdir / 'status.json'
    state = _load_state(state_path)
    # keep books this cycle did not run, so '--books A' cannot wipe B and C
    previous = state.get('books', {}) if isinstance(state.get('books'), dict) else {}
    keep = {k: v for k, v in previous.items() if k not in {b.key for b in specs}}
    payload: dict[str, Any] = {'updated_at': now.isoformat(), 'timeframe': timeframe,
                               'source': source, 'symbols': list(trimmed),
                               'books': dict(keep)}
    for key, rep in reports.items():
        result: BookResult = rep['result']
        stats = rep['stats']
        prev = state.get('books', {}).get(key, {})
        window_start = str(trimmed[list(trimmed)[0]][0].ts)
        # A replay over a different window is a different book: comparing this
        # equity to a pin taken on older history would report a phantom loss,
        # so re-pin the deploy mark when the window has moved.  Keep the fact
        # visible instead of silently rewriting the record.
        prev_window = str(prev.get('window_start') or '')
        moved = bool(prev_window and _window_moved(prev_window, window_start))
        if prev.get('deploy_equity') and not moved:
            deploy_equity = float(prev['deploy_equity'])
            deployed_at = prev.get('deployed_at') or now.isoformat()
            repinned_at = prev.get('repinned_at')
        else:
            deploy_equity = float(result.final_equity)
            deployed_at = now.isoformat()
            repinned_at = (prev.get('deployed_at') or prev.get('repinned_at')) if moved else None
        since = result.final_equity / deploy_equity - 1.0 if deploy_equity else 0.0
        paths = save_book(result, outdir / key)
        cash = float(result.config.initial_cash)
        payload['books'][key] = {
            'label': rep['label'],
            'deployed_at': deployed_at,
            'deploy_equity': deploy_equity,
            'cash': cash,
            'wallet': cash * (result.final_equity / deploy_equity) if deploy_equity else cash,
            'window_start': window_start,
            'repinned_at': repinned_at,
            'equity': result.final_equity,
            'total_return': stats.get('total_return', 0.0),
            'since_deploy': since,
            'cagr': stats.get('cagr', 0.0),
            'sharpe': stats.get('sharpe', 0.0),
            'max_drawdown': stats.get('max_drawdown', 0.0),
            'trades': stats.get('trades', 0),
            'win_rate': stats.get('win_rate', 0.0),
            'exposure': stats.get('exposure', 0.0),
            'positions': result.positions,
            'signals': rep['signals'],
            'symbols': rep['symbols'],
            'symbol_params': rep['symbol_params'],
            'updated_at': now.isoformat(),
            'files': {k: str(v) for k, v in paths.items()},
        }
    state_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    payload['status_path'] = str(state_path)
    return payload


def format_status(payload: Mapping[str, Any]) -> str:
    '''Short human block for one cycle.'''
    lines = [f"books  {payload.get('timeframe', '4h')}  {payload.get('source', 'binance')}"
             f"  {', '.join(payload.get('symbols', []))}",
             f"as of  {payload.get('updated_at', '')[:19].replace('T', ' ')} UTC"]
    for key, b in payload.get('books', {}).items():
        started = str(b.get('deployed_at', ''))[:10]
        lines.append("")
        lines.append(f"[{key}] {b['label']}   live since {started}")
        lines.append("  live      $%8.2f   %+6.1f%% since deploy" % (
            b['wallet'], b['since_deploy'] * 100))
        lines.append("  history   $%8.2f   %+7.1f%% over the window  (%d trades, %4.0f%% win, dd %5.1f%%)" % (
            b['equity'], b['total_return'] * 100, b['trades'],
            b['win_rate'] * 100, b['max_drawdown'] * 100))
        pos = b.get('positions') or {}
        if pos:
            for sym, p in pos.items():
                lines.append("  holding   %-9s %10.6f @ %8.4f  stop %8.4f  upnl $%+7.2f"
                             % (sym, p['qty'], p['entry'], p['stop'], p['upnl']))
        else:
            lines.append("  holding   none (flat)")
        for sym, sig in (b.get('signals') or {}).items():
            if 'no setup' not in str(sig):
                lines.append(f"  next      {sym:<9} {sig}")
    return "\n".join(lines)


def run_forever(interval: float, **kwargs: Any) -> None:
    '''Poll loop; each cycle is independent and idempotent.'''
    while True:
        try:
            payload = run_once(**kwargs)
            print(format_status(payload), flush=True)
        except Exception as exc:
            print(f"[auto] cycle failed: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(max(30.0, float(interval)))