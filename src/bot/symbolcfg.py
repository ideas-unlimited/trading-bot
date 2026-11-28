'''Per-symbol parameter sets: one record per coin, tuned on that coin.

`config/target_symbols.json` is the authoritative target list (18 Binance
spot USDT pairs) and every entry already has a `strategy`, `timeframe` and
`params` slot.  This module is what reads those slots, so the book replay,
`bot auto` and any future per-symbol bot merge a coin's overrides through
one validated path instead of trusting a JSON blob.

Semantics:

* a coin's `params` override the book's `BookConfig` field by field;
  anything unset inherits, so a coin with `{}` is exactly the pooled default;
* `max_positions` and `initial_cash` are wallet properties, not coin
  properties - they stay on the book and putting them on a symbol raises;
* unknown keys, unknown symbols and wrong types raise, because a typo that
  silently becomes a default is how a per-symbol fit turns into a pooled one.
'''

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .book import PORTFOLIO_FIELDS, BookConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
TARGETS_PATH = REPO_ROOT / 'config' / 'target_symbols.json'

_BOOL_FIELDS = frozenset({'damiani_filter'})
_INT_FIELDS = frozenset({
    'fast', 'slow', 'atr_window', 'score_window', 'max_positions',
    'channel_left', 'channel_right', 'breakout_within',
})
_FLOAT_FIELDS = frozenset({
    'atr_mult', 'risk_per_trade', 'max_position_pct', 'initial_cash',
    'fee_bps', 'slippage_bps', 'breakout_atr', 'squeeze_quantile', 'min_rr',
    'trail_after_r',
})
_OPTIONAL_FLOAT_FIELDS = frozenset({'stc_min', 'trail_atr'})
_OPTIONAL_STR_FIELDS = frozenset({'structure_gate'})
_TUPLE_FIELDS = frozenset({'channel_spans'})

BOOK_FIELDS: tuple[str, ...] = tuple(f.name for f in fields(BookConfig))
_COVERED = (_BOOL_FIELDS | _INT_FIELDS | _FLOAT_FIELDS | _OPTIONAL_FLOAT_FIELDS
            | _OPTIONAL_STR_FIELDS | _TUPLE_FIELDS)
_uncovered = set(BOOK_FIELDS) - _COVERED
if _uncovered:  # a new BookConfig field must get a converter here
    raise RuntimeError(f'symbolcfg has no converter for: {sorted(_uncovered)}')

_TRUE = {'true', '1', 'yes', 'on'}
_FALSE = {'false', '0', 'no', 'off'}

def normalize_symbol(symbol: str) -> str:
    ''''BTC/USDT', 'btcusdt', 'BTC_USDT' -> 'BTCUSDT'.'''
    text = str(symbol).strip().upper().replace('/', '').replace('-', '').replace('_', '')
    if not text.isalnum():
        raise ValueError(f'bad symbol {symbol!r}')
    if not text.endswith('USDT') or len(text) <= 4:
        raise ValueError(f'{symbol!r} is not a USDT spot pair (only X/USDT is in scope)')
    return text


def base_asset(symbol: str) -> str:
    ''''ETHUSDT' -> 'ETH', for display keys.'''
    return normalize_symbol(symbol)[:-4]


def _as_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
    elif isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    raise ValueError(f'{name}: expected a boolean, got {value!r}')


def _as_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f'{name}: expected a whole number, got {value!r}')
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f'{name}: expected a whole number, got {value!r}')
    try:
        return int(str(value).strip() if isinstance(value, str) else value)
    except (TypeError, ValueError):
        raise ValueError(f'{name}: expected a whole number, got {value!r}') from None


def _as_float(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f'{name}: expected a number, got {value!r}')
    try:
        return float(str(value).strip() if isinstance(value, str) else value)
    except (TypeError, ValueError):
        raise ValueError(f'{name}: expected a number, got {value!r}') from None


def _as_optional_float(value: Any, name: str) -> float | None:
    if value is None or (isinstance(value, str) and value.strip().lower() in ('', 'none', 'null')):
        return None
    return _as_float(value, name)


def _as_optional_str(value: Any, name: str) -> str | None:
    if value is None or (isinstance(value, str) and value.strip().lower() in ('', 'none', 'null')):
        return None
    return str(value).strip()


def _as_tuple(value: Any, name: str) -> tuple[int, ...]:
    if value is None or (isinstance(value, str) and value.strip().lower() in ('', 'none')):
        return ()
    if isinstance(value, str):
        text = value.strip()
        if text.startswith('['):
            value = json.loads(text)
        else:
            value = [part for part in text.replace('+', ',').split(',') if part.strip()]
    if not isinstance(value, (list, tuple)):
        raise ValueError(f'{name}: expected a list of ints, got {value!r}')
    return tuple(_as_int(v, name) for v in value)


def _convert(name: str, value: Any) -> Any:
    if name in _BOOL_FIELDS:
        return _as_bool(value, name)
    if name in _INT_FIELDS:
        return _as_int(value, name)
    if name in _FLOAT_FIELDS:
        return _as_float(value, name)
    if name in _OPTIONAL_FLOAT_FIELDS:
        return _as_optional_float(value, name)
    if name in _OPTIONAL_STR_FIELDS:
        return _as_optional_str(value, name)
    if name in _TUPLE_FIELDS:
        return _as_tuple(value, name)
    raise ValueError(f'no converter for {name!r}')


def coerce_params(params: Mapping[str, Any] | None, *, symbol: str | None = None,
                  allow_portfolio: bool = False) -> dict[str, Any]:
    '''Validate one coin's overrides and cast them to BookConfig types.'''
    where = f' ({symbol})' if symbol else ''
    if params is None:
        return {}
    if not isinstance(params, Mapping):
        raise ValueError(f'params must be an object{where}')
    out: dict[str, Any] = {}
    for key, value in params.items():
        key = str(key)
        if key not in BOOK_FIELDS:
            raise ValueError(f'unknown parameter {key!r}{where}; known: {", ".join(BOOK_FIELDS)}')
        if key in PORTFOLIO_FIELDS and not allow_portfolio:
            raise ValueError(
                f'{key!r} is a wallet-level setting{where}; set it on the book, not on one symbol'
            )
        out[key] = _convert(key, value)
    return out


def merge_book_config(base: BookConfig, overrides: Mapping[str, Any] | None, *,
                      symbol: str | None = None) -> BookConfig:
    '''The book's config with one coin's overrides applied and validated.'''
    coerced = coerce_params(overrides, symbol=symbol)
    if not coerced:
        return base
    try:
        return replace(base, **coerced)
    except ValueError as exc:  # BookConfig.__post_init__
        raise ValueError(f'{symbol or "book"}: {exc}') from None


def _parse_scalar(text: str) -> Any:
    raw = text.strip()
    if raw.startswith('[') or raw.startswith('{'):
        return json.loads(raw)
    low = raw.lower()
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


def parse_overrides(items: Iterable[str] | None) -> dict[str, dict[str, Any]]:
    '''Parse CLI overrides: 'SUIUSDT:trail_atr=3.0;BTCUSDT:trail_atr=1.0'.'''
    out: dict[str, dict[str, Any]] = {}
    for item in items or ():
        for chunk in str(item).split(';'):
            chunk = chunk.strip()
            if not chunk:
                continue
            if ':' not in chunk:
                raise ValueError(f'expected SYMBOL:key=value, got {chunk!r}')
            sym_raw, pairs = chunk.split(':', 1)
            symbol = normalize_symbol(sym_raw)
            bucket = out.setdefault(symbol, {})
            for pair in pairs.split(','):
                if not pair.strip():
                    continue
                if '=' not in pair:
                    raise ValueError(f'expected key=value in {pair!r}')
                key, value = pair.split('=', 1)
                bucket[key.strip()] = _parse_scalar(value)
    for symbol, params in out.items():
        coerce_params(params, symbol=symbol)
    return out


@dataclass(frozen=True)
class SymbolEntry:
    '''One coin in the pool: what it runs, when, and with which parameters.'''

    symbol: str
    base: str = ''
    strategy: str | None = None
    timeframe: str | None = None
    params: Mapping[str, Any] = field(default_factory=dict)
    available: bool = True
    status: str = 'untuned'

    def __post_init__(self) -> None:
        object.__setattr__(self, 'symbol', normalize_symbol(self.symbol))
        if not self.base:
            object.__setattr__(self, 'base', base_asset(self.symbol))
        object.__setattr__(self, 'params', coerce_params(self.params, symbol=self.symbol))

    def config(self, base: BookConfig | None = None) -> BookConfig:
        '''This coin's parameters over a book config (defaults if omitted).'''
        return merge_book_config(base or BookConfig(), self.params, symbol=self.symbol)


def load_pool(path: str | Path | None = None) -> list[SymbolEntry]:
    '''Read the target list; every coin's params are validated on the way in.'''
    path = Path(path) if path is not None else TARGETS_PATH
    data = json.loads(path.read_text(encoding='utf-8'))
    rows = data.get('symbols') if isinstance(data, Mapping) else None
    if not isinstance(rows, list):
        raise ValueError(f'{path}: no "symbols" list')
    out: list[SymbolEntry] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping) or not row.get('symbol'):
            raise ValueError(f'{path}: every entry needs a symbol')
        entry = SymbolEntry(
            symbol=str(row['symbol']),
            base=str(row.get('base') or ''),
            strategy=row.get('strategy'),
            timeframe=row.get('timeframe'),
            params=dict(row.get('params') or {}),
            available=bool(row.get('available', True)),
            status=str(row.get('status') or 'untuned'),
        )
        if entry.symbol in seen:
            raise ValueError(f'{path}: {entry.symbol} listed twice')
        seen.add(entry.symbol)
        out.append(entry)
    return out


def save_pool(entries: Sequence[SymbolEntry], path: str | Path | None = None) -> Path:
    '''Write the per-symbol params back, preserving every other field.'''
    path = Path(path) if path is not None else TARGETS_PATH
    data = json.loads(path.read_text(encoding='utf-8'))
    rows = data.get('symbols') or []
    by_symbol = {normalize_symbol(str(r['symbol'])): r for r in rows}
    for entry in entries:
        row = by_symbol.get(entry.symbol)
        if row is None:
            raise ValueError(f'{entry.symbol} is not in {path}')
        row['params'] = dict(entry.params)
        if entry.strategy is not None:
            row['strategy'] = entry.strategy
        if entry.timeframe is not None:
            row['timeframe'] = entry.timeframe
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    return path


def update_params(updates: Mapping[str, Mapping[str, Any]], *,
                  path: str | Path | None = None) -> list[SymbolEntry]:
    '''Merge overrides into the pool file (the write path a tuner uses).'''
    entries = {e.symbol: e for e in load_pool(path)}
    for symbol, params in updates.items():
        key = normalize_symbol(symbol)
        if key not in entries:
            raise ValueError(f'{key} is not in the target list')
        merged = {**dict(entries[key].params), **coerce_params(params, symbol=key)}
        entries[key] = replace(entries[key], params=merged)
    save_pool(list(entries.values()), path)
    return list(entries.values())


def load_overrides(path: str | Path) -> dict[str, dict[str, Any]]:
    '''Read a params file: either the pool format or {SYMBOL: {params}}.'''
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if isinstance(data, Mapping) and isinstance(data.get('symbols'), list):
        return {normalize_symbol(str(row['symbol'])): dict(row.get('params') or {})
                for row in data['symbols']}
    if isinstance(data, Mapping):
        return {normalize_symbol(k): dict(v or {})
                for k, v in data.items() if not str(k).startswith('_')}
    raise ValueError(f'{path}: expected an object or a pool file')


def describe(entries: Sequence[SymbolEntry]) -> list[dict[str, Any]]:
    '''Flat rows for the CLI / dashboard: one line per coin.'''
    return [{
        'symbol': e.symbol,
        'strategy': e.strategy or '(book default)',
        'timeframe': e.timeframe or '(book default)',
        'status': e.status,
        'available': e.available,
        'params': dict(e.params),
    } for e in entries]