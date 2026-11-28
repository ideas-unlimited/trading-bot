"""Command line interface.

    python -m bot gen-data    --bars 5000 --out data/sample.csv
    python -m bot strategies
    python -m bot backtest    --source synthetic --strategy sma_cross --plot
    python -m bot backtest    --source csv --csv data/sample.csv
    python -m bot demo       --source synthetic --delay 0.01

    # Real TradingView data (unofficial tvDatafeed; TV_USERNAME/TV_PASSWORD env optional)
    python -m bot tv-bars     --symbol BTCUSDT --exchange BINANCE --interval 1h --n-bars 5000 --out data/tv.csv
    python -m bot backtest    --source tv --symbol BTCUSDT --exchange BINANCE --interval 1h --strategy sma_cross
    python -m bot demo       --source tv --symbol SPY --exchange NASDAQ --interval 1d --strategy rsi_meanrev

    # Mirror TradingView demo-trading alerts into this engine's risk rules
    python -m bot serve-alerts --port 8788 --token MYSECRET
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Deploy lookback floor in days (see cmd_deploy): fetch at least this much of the
# bot's own timeframe every poll, regardless of what a fixed bar count would
# shrink to on a faster chart.
DEPLOY_LOOKBACK_DAYS = 100

from .exchange.order_client import BinanceOrderClient, duration_for, interval_for, split_symbol
from .alerts import AlertServer, MirrorAccount, random_token
from .backtest import run_backtest
from .book import (
    BookConfig,
    format_summary as format_book,
    last_signal,
    load_bars_from_csv,
    save_book,
    simulate_book,
)
from .symbolcfg import merge_book_config
from .config import BotConfig, CostConfig, RiskConfig
from .data.binance import INTERVALS, fetch_binance_bars
from .data.feed import generate_synthetic, load_csv, write_bars
from .data.tvfeed import fetch_latest_tv_bars, fetch_tv_bars
from .metrics import format_metrics
from .demo import DemoTrader
from .portfolio import (
    RotationConfig,
    closes_from_bars,
    format_summary as format_rotation,
    load_closes_from_csv,
    save_rotation,
    simulate_rotation,
)
from .report import save_equity_chart, save_trades_csv, summary_text
from .scanlib import load_scan_bars
from .strategy import REGISTRY, build


def _add_source_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--source", choices=["synthetic", "csv", "binance", "tv"], default="synthetic",
                   help="data source: synthetic, OHLCV csv, Binance klines, or TradingView bars")
    p.add_argument("--csv", type=Path, help="OHLCV csv (required for --source csv)")
    p.add_argument("--symbol", default="BTCUSDT", help="TradingView symbol (for --source tv)")
    p.add_argument("--exchange", default="BINANCE", help="TradingView exchange (for --source tv)")
    p.add_argument("--interval", default="1h", help="bar interval, e.g. 1h, 4h, 1d (for --source tv)")
    p.add_argument("--n-bars", type=int, default=5000, help="bars to fetch from Binance/TradingView")
    p.add_argument("--start", default=None, help="fetch bars from this date, e.g. 2020-01-01 (Binance only)")
    p.add_argument("--bars", type=int, default=5000, help="synthetic bar count")
    p.add_argument("--seed", type=int, default=7, help="synthetic generator seed")
    p.add_argument("--start-price", type=float, default=100.0)
    p.add_argument("--bars-per-year", type=float, default=None,
                   help="annualisation factor; inferred from bar spacing by default")
    p.add_argument("--max-years", type=float, default=2.0,
                   help="keep only the last N years of bars (default 2; 0 disables)")


def _add_strategy_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--strategy", default="sma_cross", choices=sorted(REGISTRY))
    p.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                   help="strategy parameter, repeatable (e.g. --param fast=10)")


def _add_strategy_args_multi(p: argparse.ArgumentParser) -> None:
    """Like _add_strategy_args but accepts a comma-separated strategy list."""
    p.add_argument("--strategies", default="sma_cross",
                   help=f"comma-separated strategies to compare: {','.join(sorted(REGISTRY))}")
    p.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                   help="strategy parameter, repeatable (e.g. --param fast=10)")


def _add_cost_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--initial-cash", type=float, default=10_000.0)
    p.add_argument("--fee-bps", type=float, default=10.0)
    p.add_argument("--slippage-bps", type=float, default=5.0)
    p.add_argument("--risk-per-trade", type=float, default=0.01)
    p.add_argument("--max-position-pct", type=float, default=0.95)
    p.add_argument("--max-drawdown-stop", type=float, default=0.10)


def _parse_params(raw: list[str]) -> dict[str, object]:
    params: dict[str, object] = {}
    for item in raw:
        if "=" not in item:
            raise SystemExit(f"--param expects KEY=VALUE, got {item!r}")
        key, _, value = item.partition("=")
        key = key.strip()
        value = value.strip()
        try:
            parsed: object = int(value)
        except ValueError:
            try:
                parsed = float(value)
            except ValueError:
                parsed = value
        params[key] = parsed
    return params


def _binance_bars(symbol: str, timeframe: str, *, start=None, limit: int = 5000) -> list:
    """Bars from production Binance; public klines, no API keys needed."""
    interval, _ = interval_for(timeframe)
    _, _, pair = split_symbol(symbol)
    limit = max(1, int(limit))
    if start is None and limit > 1000:
        # The venue returns at most 1000 rows per page; start from the far
        # edge of the requested window so the pager fills it.
        start = datetime.now(timezone.utc) - INTERVALS[interval] * limit
    return fetch_binance_bars(pair, interval, start=start, limit=limit)[-limit:]


def _load_bars(args: argparse.Namespace) -> list:
    if args.source == "csv":
        if not args.csv:
            raise SystemExit("--source csv requires --csv PATH")
        bars = load_csv(args.csv)
    elif args.source == "binance":
        start = None
        if args.start:
            from datetime import datetime, timezone

            start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
        print(f"fetching up to {args.n_bars} bars of {args.symbol} @ {args.interval} "
              f"from Binance production klines ...", file=sys.stderr)
        bars = _binance_bars(args.symbol, args.interval, start=start, limit=args.n_bars)
    elif args.source == "tv":
        print(f"fetching {args.n_bars} bars of {args.exchange}:{args.symbol} "
              f"@ {args.interval} from TradingView ...", file=sys.stderr)
        bars = fetch_tv_bars(args.symbol, args.exchange, args.interval, args.n_bars)
    else:
        bars = generate_synthetic(args.bars, seed=args.seed, start_price=args.start_price)

    # Hard user rule: backtests and strategy searches never see more than the
    # last two years.  ``--max-years 0`` is the explicit research escape.
    max_years = getattr(args, "max_years", 2.0)
    if max_years and bars:
        cutoff = bars[-1].ts - timedelta(days=365.25 * max_years)
        bars = [b for b in bars if b.ts >= cutoff]
    return bars


def _config(args: argparse.Namespace, bars: list) -> BotConfig:
    from .metrics import infer_bars_per_year

    # Not every subparser adds --bars-per-year (the deploy loop defines its own
    # --symbol/--interval instead of using _add_source_args), so read it
    # defensively and fall back to inferring the factor from the bars.
    bars_per_year = getattr(args, "bars_per_year", None) or infer_bars_per_year([b.ts for b in bars])
    return BotConfig(
        initial_cash=args.initial_cash,
        costs=CostConfig(fee_bps=args.fee_bps, slippage_bps=args.slippage_bps),
        risk=RiskConfig(
            risk_per_trade=args.risk_per_trade,
            max_position_pct=args.max_position_pct,
            max_drawdown_stop=args.max_drawdown_stop,
        ),
        bars_per_year=bars_per_year,
    )


def cmd_gen_data(args: argparse.Namespace) -> int:
    bars = generate_synthetic(
        args.bars, seed=args.seed, start_price=args.start_price, vol=args.vol
    )
    out = write_bars(args.out, bars)
    print(f"wrote {len(bars)} bars -> {out}")
    print(f"  first {bars[0].ts:%Y-%m-%d %H:%M}  close={bars[0].close:.4f}")
    print(f"  last  {bars[-1].ts:%Y-%m-%d %H:%M}  close={bars[-1].close:.4f}")
    return 0


def cmd_strategies(_: argparse.Namespace) -> int:
    for name, cls in sorted(REGISTRY.items()):
        doc = (cls.__doc__ or "").strip().splitlines()[0]
        print(f"{name:<14} {doc}")
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    bars = _load_bars(args)
    strategy = build(args.strategy, **_parse_params(args.param))
    config = _config(args, bars)

    print(f"strategy   {strategy!r}")
    print(f"bars       {len(bars):,}  ({args.source}), annualising at "
          f"{config.bars_per_year:,.0f} bars/year")
    result = run_backtest(bars, strategy, config)
    print()
    print(summary_text(result))

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    trades_path = save_trades_csv(result, outdir / "trades.csv")
    print(f"\ntrades     {trades_path}")
    if args.plot:
        try:
            chart = save_equity_chart(result, outdir / "equity.png")
            print(f"chart      {chart}")
        except ImportError:
            print("chart      skipped (matplotlib not installed)")
    if args.json:
        import json

        payload = {
            "strategy": result.strategy,
            "params": strategy.params,
            "bars": len(bars),
            "metrics": {k: (None if v != v else v) for k, v in result.metrics.items()},
        }
        print(json.dumps(payload, indent=2))
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    bars = _load_bars(args)
    strategy = build(args.strategy, **_parse_params(args.param))
    DemoTrader(
        bars,
        strategy,
        _config(args, bars),
        delay=args.delay,
        every=args.every,
    ).run()
    return 0


def cmd_tv_bars(args: argparse.Namespace) -> int:
    """Fetch real TradingView bars and save them as an OHLCV csv."""
    from .data.feed import write_bars as write

    out = Path(args.out)
    if not args.watch:
        bars = fetch_tv_bars(args.symbol, args.exchange, args.interval, args.n_bars)
        write(out, bars)
        print(f"wrote {len(bars)} bars of {args.exchange}:{args.symbol} @ {args.interval} -> {out}")
        print(f"  first {bars[0].ts:%Y-%m-%d %H:%M}  close={bars[0].close:.4f}")
        print(f"  last  {bars[-1].ts:%Y-%m-%d %H:%M}  close={bars[-1].close:.4f}")
        return 0

    # Watch mode: poll for newer bars and append them to the csv.
    import time as _time

    if not out.exists():
        bars = fetch_tv_bars(args.symbol, args.exchange, args.interval, args.n_bars)
        write(out, bars)
        print(f"wrote {len(bars)} bars -> {out}; now watching for new ones ...")
        last_ts = bars[-1].ts
    else:
        last_ts = load_csv(out)[-1].ts
        print(f"continuing {out} from {last_ts:%Y-%m-%d %H:%M}; watching for new bars ...")
    try:
        while True:
            _time.sleep(args.poll)
            try:
                fresh = [b for b in fetch_latest_tv_bars(args.symbol, args.exchange, args.interval, 5)
                         if b.ts > last_ts]
            except Exception as exc:  # noqa: BLE001 - keep watching through transient errors
                print(f"[tv-bars] fetch failed ({exc}); retrying", file=sys.stderr)
                continue
            if fresh:
                path = out.open("a", newline="", encoding="utf-8")
                import csv as _csv

                writer = _csv.writer(path)
                for b in fresh:
                    writer.writerow([b.ts.isoformat(), f"{b.open:.8f}", f"{b.high:.8f}",
                                     f"{b.low:.8f}", f"{b.close:.8f}", f"{b.volume:.8f}"])
                path.close()
                last_ts = fresh[-1].ts
                print(f"[tv-bars] +{len(fresh)} bar(s), latest {last_ts:%Y-%m-%d %H:%M} "
                      f"close={fresh[-1].close:.4f}")
    except KeyboardInterrupt:
        print("[tv-bars] stopped")
    return 0


def cmd_serve_alerts(args: argparse.Namespace) -> int:
    """Serve the TradingView webhook that mirrors demo-trading fills."""
    token = args.token or random_token()
    mirror = MirrorAccount(
        initial_cash=args.initial_cash,
        risk_per_trade=args.risk_per_trade,
        max_position_pct=args.max_position_pct,
        max_drawdown_stop=args.max_drawdown_stop,
        fee_bps=args.fee_bps,
    )
    AlertServer(token, mirror, ledger=args.out, host=args.host, port=args.port).serve_forever()
    return 0


def cmd_account(_: argparse.Namespace) -> int:
    """Show the Binance demo account summary and open positions."""
    client = BinanceOrderClient()
    acct = client.get_account()
    print(f"account       {acct.get('account_number')} ({acct.get('status')})")
    print(f"mode          demo (no real orders possible from this code)")
    print(f"equity        {float(acct.get('equity') or 0):,.2f}")
    print(f"cash          {float(acct.get('cash') or 0):,.2f}")
    print(f"buying power  {float(acct.get('buying_power') or 0):,.2f}")
    positions = client.get_positions()
    if positions:
        print("positions")
        for p in positions:
            pnl = float(p.get("unrealized_pl") or 0)
            print(f"  {p['symbol']:<12} {float(p['qty']):>10,.4f} @ {float(p['avg_entry_price']):>10,.2f} "
                  f"  pnl {pnl:+,.2f}")
    else:
        print("positions    none")
    return 0


def cmd_deploy(args: argparse.Namespace) -> int:
    """Deploy a strategy on freshly closed Binance bars; orders go to the demo account.

    Outside lab, only an active promotion manifest may run - the same rule
    the dashboard follows, enforced where the orders actually happen.
    """
    from . import envcfg, promotions as promo
    from .deploy import DeployTrader
    from .notify import Notifier

    if not envcfg.lab_mode():
        try:
            manifest = promo.require(args.strategy, envcfg.env_name())
        except promo.PromotionError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        if args.symbol not in manifest.symbols:
            covered = ", ".join(manifest.symbols)
            print(f"refused: {args.strategy} is promoted on {covered}; {args.symbol} is not covered", file=sys.stderr)
            return 2
        if args.interval != manifest.timeframe:
            print(f"refused: {args.strategy} is promoted on {manifest.timeframe}; this run asks for {args.interval}", file=sys.stderr)
            return 2
        # The sleeve owns its measured risk profile.  Environment files may
        # carry knobs for older releases, but the manifest is authoritative so
        # a stale /etc/tradingbot/*.env cannot silently change the book.
        profile = manifest.risk_profile()
        args.risk_per_trade = profile["risk_per_trade"]
        args.max_position_pct = profile["max_position_pct"]
        args.max_drawdown_stop = profile["max_drawdown_stop"]

    strategy = build(args.strategy, **_parse_params(args.param))
    # Day-based lookback floor: a fixed bar count shrinks with the timeframe
    # (400 bars is ~66 days on 4h but ~4 days on 15m), which starves any
    # strategy carrying higher-timeframe context.  Always fetch at least
    # DEPLOY_LOOKBACK_DAYS of the bot's own timeframe plus its indicator
    # warmup, so a 15m bot and a 4h bot both see a century of context.
    bars_100d = math.ceil(timedelta(days=DEPLOY_LOOKBACK_DAYS) / duration_for(args.interval))
    lookback = max(strategy.warmup + 2, bars_100d, args.lookback)
    notifier = Notifier.from_env()
    client = BinanceOrderClient()
    bars = client.fetch_bars(args.symbol, args.interval, limit=lookback)
    config = _config(args, bars)
    DeployTrader(
        client,
        strategy,
        config,
        args.symbol,
        args.interval,
        state_path=args.state,
        poll=args.poll,
        lookback=lookback,
        capital=args.capital,
        heartbeat_url=args.heartbeat_url,
        heartbeat_every=args.heartbeat_every,
        record=True,
        notifier=notifier,
        # Service bots opt in via STARTUP_REPLAY=1 (run_bot.sh defaults it on).
        # A manual `bot deploy` keeps the conservative anchor-to-newest default.
        startup_replay=(
            args.startup_replay
            or os.environ.get("STARTUP_REPLAY", "").strip().lower()
            not in ("", "0", "false", "no", "off")
        ),
    ).run(once=args.once)
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    """Backtest strategy/timeframe combinations and rank the results.

    Bars come from production Binance klines (Postgres-cached, delta-synced).
    Results are printed as a table and saved to runs/scan.csv.
    """
    import csv as _csv
    from datetime import datetime, timedelta, timezone

    symbols = [s.strip() for s in args.symbol.split(",") if s.strip()]
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    strategy_names = [s.strip() for s in args.strategies.split(",") if s.strip()]

    start = None
    if args.start:
        start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    elif args.days:
        start = datetime.now(timezone.utc) - timedelta(days=args.days)

    rows: list[dict[str, object]] = []
    for sym in symbols:
        for tf in timeframes:
            print(f"fetching {sym} @ {tf} ...")
            bars = load_scan_bars(
                sym, tf, source="binance", start=start,
                days=args.days, n_bars=args.n_bars, cache_key=sym,
            )
            if len(bars) < 60:
                print(f"  !! only {len(bars)} bars; skipping {sym} @ {tf}")
                continue
            config = _config(args, bars)
            for name in strategy_names:
                strategy = build(name, **_parse_params(args.param))
                result = run_backtest(bars, strategy, config)
                m = result.metrics
                row = {
                    "symbol": sym, "timeframe": tf, "strategy": name,
                    "bars": len(bars),
                    "total_return": m.get("total_return", float("nan")),
                    "buy_and_hold": m.get("buy_and_hold", float("nan")),
                    "sharpe": m.get("sharpe", float("nan")),
                    "sortino": m.get("sortino", float("nan")),
                    "max_drawdown": m.get("max_drawdown", float("nan")),
                    "calmar": m.get("calmar", float("nan")),
                    "trades": int(m.get("trades", 0)),
                    "win_rate": m.get("win_rate", float("nan")),
                    "profit_factor": m.get("profit_factor", float("nan")),
                    "halted": bool(result.halted_reason),
                }
                rows.append(row)
                print(
                    f"  {name:<14} return {row['total_return']:+8.2%}  "
                    f"b&h {row['buy_and_hold']:+8.2%}  sharpe {row['sharpe']:6.2f}  "
                    f"maxdd {row['max_drawdown']:+7.2%}  trades {row['trades']:>4}"
                    + ("  (halted)" if row["halted"] else "")
                )

    if not rows:
        print("no results")
        return 1

    def sort_key(r: dict[str, object]) -> float:
        sharpe = r["sharpe"]
        return float(sharpe) if sharpe == sharpe else float("-inf")  # NaN -> -inf

    print()
    print("=== ranked by Sharpe ===")
    header = (
        f"{'symbol':<10} {'tf':<4} {'strategy':<14} {'return':>9} {'b&h':>9} "
        f"{'sharpe':>7} {'maxdd':>8} {'trades':>7} {'win%':>6} {'PF':>5}"
    )
    print(header)
    print("-" * len(header))
    for r in sorted(rows, key=sort_key, reverse=True):
        win = r["win_rate"]
        pf = r["profit_factor"]
        print(
            f"{r['symbol']:<10} {r['timeframe']:<4} {r['strategy']:<14} "
            f"{r['total_return']:+9.2%} {r['buy_and_hold']:+9.2%} "
            f"{r['sharpe']:7.2f} {r['max_drawdown']:+8.2%} {r['trades']:>7} "
            f"{win * 100:6.1f} {pf:5.2f}"
        )

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / "scan.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = _csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nsaved {len(rows)} results -> {out}")
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    """Serve the local web terminal."""
    from . import envcfg
    from .web import WebApp

    port = args.port or envcfg.web_port()
    label = "LIVE - real money (venue not configured)" if envcfg.env_name() == "live" else "DEMO - simulated fills"
    print(f"env        {envcfg.env_name()}  ({label})")
    print(f"port       {port}")
    app = WebApp(args.host, port)
    app.serve_forever()
    return 0


def cmd_rotate(args: argparse.Namespace) -> int:
    """Backtest the multi-coin trend rotation (the researched portfolio rule)."""
    symbols = [s.strip() for s in args.coins.split(",") if s.strip()]
    if len(symbols) < 2:
        raise SystemExit("--coins needs at least two symbols")

    if args.source == "csv":
        closes = load_closes_from_csv(args.dir, symbols, args.timeframe)
    else:
        print(f"fetching {len(symbols)} symbols @ {args.timeframe} from Binance ...", file=sys.stderr)
        bars = {}
        for sym in symbols:
            bars[sym] = _binance_bars(sym, args.timeframe, limit=args.n_bars)
            print(f"  {sym:<10} {len(bars[sym]):>5} bars", file=sys.stderr)
        closes = closes_from_bars(bars)

    if args.start:
        from datetime import datetime, timezone

        cutoff = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
        closes = closes[closes.index >= cutoff]
    if len(closes) < 300:
        raise SystemExit(f"only {len(closes)} bars; need at least 300")

    regime = args.regime_symbol or next(
        (s for s in symbols if "BTC" in s.upper()), symbols[0]
    )
    config = RotationConfig(
        lookback=args.lookback,
        top_n=args.top,
        regime_ma=args.regime_ma,
        vol_target=args.vol_target,
        rebalance_days=args.rebalance_days,
        initial_cash=args.initial_cash,
        fee_bps=args.fee_bps,
        regime_symbol=regime,
    )
    result = simulate_rotation(closes, config)
    print()
    print(format_rotation(result))

    paths = save_rotation(result, args.outdir)
    print()
    for name, path in paths.items():
        print(f"{name:<8} {path}")
    return 0


def _symbol_overrides(args: argparse.Namespace) -> dict[str, dict]:
    """Per-coin parameter overrides from --symbol-params and --params-file."""
    from . import symbolcfg

    out = symbolcfg.parse_overrides(getattr(args, "symbol_params", None))
    path = getattr(args, "params_file", None)
    if path:
        for sym, params in symbolcfg.load_overrides(path).items():
            out.setdefault(sym, {}).update(symbolcfg.coerce_params(params, symbol=sym))
    return out


def cmd_book(args: argparse.Namespace) -> int:
    """Backtest one shared wallet across several coins, one slot at a time."""
    symbols = [s.strip() for s in args.coins.split(",") if s.strip()]
    if len(symbols) < 2:
        raise SystemExit("--coins needs at least two symbols")

    if args.source == "csv":
        bars = load_bars_from_csv(args.dir, symbols, args.timeframe)
    else:
        print(f"fetching {len(symbols)} symbols @ {args.timeframe} from Binance ...", file=sys.stderr)
        bars = {}
        for sym in symbols:
            bars[sym] = _binance_bars(sym, args.timeframe, limit=args.n_bars)
            print(f"  {sym:<10} {len(bars[sym]):>5} bars", file=sys.stderr)

    if args.start:
        from datetime import datetime, timezone

        cutoff = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
        bars = {sym: [b for b in rows if b.ts >= cutoff] for sym, rows in bars.items()}

    config = BookConfig(
        fast=args.fast, slow=args.slow, atr_mult=args.atr_mult,
        risk_per_trade=args.risk_per_trade,
        max_position_pct=args.max_position_pct,
        max_positions=args.max_positions,
        initial_cash=args.initial_cash,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        damiani_filter=args.damiani_filter,
        stc_min=args.stc_min,
        structure_gate=None if args.structure_gate in (None, "none") else args.structure_gate,
        channel_left=args.channel_span,
        channel_right=args.channel_span,
        breakout_atr=args.breakout_atr,
        breakout_within=args.breakout_within,
        squeeze_quantile=args.squeeze_quantile,
        min_rr=args.min_rr,
        channel_spans=tuple(
            int(x) for x in str(getattr(args, "channel_spans", "") or "").split(",") if x.strip()
        ),
        trail_atr=getattr(args, "trail_atr", None),
        trail_after_r=getattr(args, "trail_after_r", 0.0),
    )
    overrides = _symbol_overrides(args)
    unknown = [sym for sym in overrides if sym not in bars]
    if unknown:
        raise SystemExit(f"--symbol-params: {', '.join(unknown)} not in --coins")
    configs = {sym: config for sym in bars}
    for sym, params in overrides.items():
        configs[sym] = merge_book_config(config, params, symbol=sym)

    result = simulate_book(bars, config, symbol_configs=configs)
    print(f"rule       SMA {config.fast}/{config.slow} cross, {config.atr_mult:g} ATR stop")
    gate = []
    if config.damiani_filter:
        gate.append("Damiani volatility awake")
    if config.stc_min is not None:
        gate.append(f"STC >= {config.stc_min:g}")
    if config.structure_gate == "breakout":
        gate.append(f"channel breakout > {config.breakout_atr:g} ATR, {config.breakout_within} bars")
    elif config.structure_gate == "squeeze":
        gate.append(f"channel squeeze (bottom {config.squeeze_quantile:.0%})")
    elif config.structure_gate == "room":
        gate.append(f"measured-move room >= {config.min_rr:g}R")
    if gate:
        print("filter     " + " + ".join(gate))
    print(f"coins      {', '.join(symbols)}  ({args.source}, {args.timeframe})")
    print(f"slots      {config.max_positions}  risk {config.risk_per_trade:.1%} per trade")
    for sym in overrides:
        diff = {k: v for k, v in configs[sym].__dict__.items()
                if getattr(config, k) != v}
        print(f"params     {sym:<10} " + "  ".join(f"{k}={v}" for k, v in diff.items()))
    print()
    print(format_book(result))

    paths = save_book(result, args.outdir)
    print()
    for name, path in paths.items():
        print(f"{name:<8} {path}")

    print()
    print("next open (demo):")
    for sym, text in last_signal(result, bars, configs).items():
        print(f"  {sym:<10} {text}")
    return 0


def cmd_auto(args: argparse.Namespace) -> int:
    '''Dry-run the demo books on closed bars.  No orders are ever sent.'''
    from .autobook import (books_from_pool, default_books, format_status, run_forever,
                           run_once, select_books)
    from . import symbolcfg

    if getattr(args, "pool", None):
        entries = symbolcfg.load_pool(args.pool)
        books = list(books_from_pool(entries, cash=args.cash))
        symbols = [e.symbol for e in entries if e.available]
        label = f"pool {args.pool} ({len(books)} wallets, one per coin)"
    else:
        books = list(select_books(args.books, cash=args.cash))
        symbols = [s.strip() for s in args.coins.split(",") if s.strip()]
        label = ", ".join(b.key + " " + b.label for b in books)
    overrides = _symbol_overrides(args)
    if overrides:
        from dataclasses import replace as _replace

        merged = []
        for book in books:
            allowed = book.symbols or tuple(symbols)
            mine = {sym: params for sym, params in overrides.items() if sym in allowed}
            if not mine:
                merged.append(book)
                continue
            merged.append(_replace(book, symbol_params={**book.symbol_params, **mine}))
        books = merged
    kwargs = dict(symbols=symbols, books=books, timeframe=args.timeframe,
                  source=args.source, days=args.days, outdir=args.outdir)
    print(f"books      {label}")
    print(f"wallets    ${books[0].cash:,.0f} each")
    print(f"data       {args.source} {args.timeframe} {', '.join(symbols)}")
    for book in books:
        for sym, params in book.symbol_params.items():
            diff = "  ".join(f"{k}={v}" for k, v in params.items())
            print(f"params     [{book.key}] {sym:<10} {diff}")
    print()
    if args.once:
        payload = run_once(**kwargs)
        print(format_status(payload))
        print()
        print(f"state      {payload['status_path']}")
        return 0
    run_forever(args.interval, **kwargs)
    return 0


def cmd_dryrun(args: argparse.Namespace) -> int:
    """Dry-run promoted sleeves as simulated wallets.  No orders are sent.

    This is the per-symbol counterpart of `bot auto`: the same deterministic
    replay reports "since deploy" without an exchange key, an order or a
    position the demo account could close.  With --all it walks every active
    manifest in one process, one folder per sleeve; without it, one sleeve is
    checked and replayed by hand.  Outside lab a single-sleeve run requires an
    active manifest whose parameter hash matches the sleeve defaults.
    """
    from . import envcfg, promotions as promo
    from .dryrun import (
        active_specs, default_outdir, format_status, run_all_forever, run_cycle,
        run_forever, run_specs,
    )
    from .notify import Notifier

    env = envcfg.env_name()
    # A dry run is a shadow replay, not a trade: silent unless explicitly asked.
    # A disabled Notifier (not None) stops the loop helper from auto-reading the
    # alert environment.
    notifier = Notifier.from_env() if args.alerts else Notifier()
    # Resolve after env_name(): that call loads .env, so the heartbeat URL may
    # live in the repo dotfile, a systemd EnvironmentFile, or the shell.
    heartbeat_url = args.heartbeat_url or os.environ.get("HEARTBEAT_URL", "")

    if args.all:
        if args.strategy or args.symbol or args.param or args.outdir:
            print("refused: --all takes no --strategy/--symbol/--param/--outdir", file=sys.stderr)
            return 2
        specs = active_specs(env)
        if args.profile == "flags":
            specs = [{k: v for k, v in spec.items() if k not in promo.RISK_PROFILE_KEYS}
                     for spec in specs]
        common = dict(
            fee_bps=args.fee_bps,
            slippage_bps=args.slippage_bps,
            risk_per_trade=args.risk_per_trade,
            max_position_pct=args.max_position_pct,
            max_drawdown_stop=args.max_drawdown_stop,
            days=args.days,
            source=args.source,
        )
        print(f"env        {env}")
        print(f"sleeves    {len(specs)} active runner(s)")
        for spec in specs:
            print(f"  {spec['strategy']:<28} {spec['symbol']:<10} {spec['timeframe']:<3}"
                  f"  ${spec['capital']:,.0f}")
        print(f"risk       {args.risk_per_trade:.1%}/trade   cap {args.max_position_pct:.0%}"
              f"   fees {args.fee_bps:g}/{args.slippage_bps:g} bps"
              f"   profile {args.profile}")
        print(f"alerts     {notifier.describe() or 'off (dry-run replay)'}")
        print()
        if args.once:
            if not specs:
                print("nothing to do: no active manifests")
                return 1
            results = run_specs(specs, notifier=notifier, **common)
            print()
            for result in results:
                if result.get("error"):
                    print(f"failed     {result['strategy']}:{result['symbol']}: {result['error']}")
                else:
                    print(f"state      {result['status_path']}")
            return 1 if any(r.get("error") for r in results) else 0
        run_all_forever(
            args.poll,
            env=env,
            heartbeat_url=heartbeat_url,
            heartbeat_every=args.heartbeat_every,
            notifier=notifier,
            **common,
        )
        return 0

    if not args.strategy or not args.symbol:
        print("refused: pass --strategy and --symbol, or --all", file=sys.stderr)
        return 2

    manifest = None
    if not envcfg.lab_mode():
        try:
            manifest = promo.require(args.strategy, env)
        except promo.PromotionError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        if args.symbol not in manifest.symbols:
            covered = ", ".join(manifest.symbols)
            print(f"refused: {args.strategy} is promoted on {covered}; {args.symbol} is not covered", file=sys.stderr)
            return 2
        if args.interval != manifest.timeframe:
            print(f"refused: {args.strategy} is promoted on {manifest.timeframe}; this run asks for {args.interval}", file=sys.stderr)
            return 2

    params = _parse_params(args.param)
    try:
        probe = build(args.strategy, **params)
    except KeyError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    if manifest is not None:
        digest = promo.params_hash(probe.params)
        if digest != manifest.params_hash:
            print(
                f"refused: {args.strategy} is promoted with {manifest.params_hash}; "
                f"these parameters hash to {digest}. Run the promoted set or promote the change.",
                file=sys.stderr,
            )
            return 2
        params = dict(probe.params)
    if manifest is not None and args.profile == "manifest":
        # The sleeve was measured on one book; replay that book unless the
        # caller asked for the flags on purpose.
        measured = manifest.risk_profile()
        for key, value in measured.items():
            current = getattr(args, key)
            if abs(float(current) - float(value)) > 1e-12:
                print(f"profile    {key} {value:g} (manifest) overrides the flag {current:g}")
            setattr(args, key, float(value))
    kwargs = dict(
        strategy_name=args.strategy,
        symbol=args.symbol,
        timeframe=args.interval,
        params=params,
        capital=args.initial_cash,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        risk_per_trade=args.risk_per_trade,
        max_position_pct=args.max_position_pct,
        max_drawdown_stop=args.max_drawdown_stop,
        days=args.days,
        source=args.source,
        outdir=args.outdir or default_outdir(args.strategy),
        profile_source="manifest" if manifest is not None and args.profile == "manifest" else "flags",
    )
    print(f"sleeve     {args.strategy}  {args.symbol}  {args.interval}")
    print(f"wallet     ${args.initial_cash:,.0f}   risk {args.risk_per_trade:.1%}/trade"
          f"   cap {args.max_position_pct:.0%}")
    print(f"data       {args.source} {args.interval}  window {args.days}d")
    print(f"alerts     {notifier.describe() or 'off (dry-run replay)'}")
    print()
    if args.once:
        payload = run_cycle(notifier=notifier, **kwargs)
        print(format_status(payload))
        print()
        print(f"state      {payload['status_path']}")
        return 0
    run_forever(
        args.poll,
        heartbeat_url=heartbeat_url,
        heartbeat_every=args.heartbeat_every,
        notifier=notifier,
        **kwargs,
    )
    return 0


def cmd_watchdog(args: argparse.Namespace) -> int:
    """One pass: alert the stale bots, ping only when every loop is fresh.

    Run from tradingbot-watchdog.timer.  It reads bots.last_heartbeat_at, so
    it names the dead loop instead of hiding it behind a shared ping URL.
    """
    from . import db, envcfg
    from .heartbeat import Heartbeat
    from .notify import Notifier
    from .watchdog import (
        DEFAULT_REPEAT_HOURS,
        DEFAULT_STALE_MINUTES,
        format_age,
        run_once,
    )

    env = envcfg.env_name()
    stale_minutes = args.stale_minutes
    if stale_minutes is None:
        stale_minutes = float(
            os.environ.get("WATCHDOG_STALE_MINUTES") or DEFAULT_STALE_MINUTES
        )
    repeat_hours = args.repeat_hours
    if repeat_hours is None:
        repeat_hours = float(
            os.environ.get("WATCHDOG_REPEAT_HOURS") or DEFAULT_REPEAT_HOURS
        )
    state_path = Path(args.state) if args.state else envcfg.state_dir() / "watchdog.json"
    heartbeat_url = args.heartbeat_url or os.environ.get("HEARTBEAT_URL", "")
    notifier = Notifier() if args.no_alerts else Notifier.from_env()

    # A dry run reports and writes nothing, so it must not ping either: one
    # stray ping would tell the dead-man switch the book is fine.
    heartbeat = Heartbeat("" if args.dry_run else heartbeat_url)
    conn = db.connect()
    try:
        summary = run_once(
            conn,
            state_path=state_path,
            stale_after=timedelta(minutes=stale_minutes),
            repeat_after=timedelta(hours=repeat_hours),
            heartbeat=heartbeat,
            notifier=notifier,
            persist=not args.dry_run,
        )
    finally:
        conn.close()

    print()
    print(f"env        {env}")
    print(f"running    {summary['running']} bot(s)")
    print(f"stale      {len(summary['stale'])}")
    for bot in summary["stale"]:
        print(f"  {bot['name']}  {bot['symbol']}  silent {format_age(bot['age_s'])}")
    print(f"alerts     {summary['alerted']} sent  ({summary['delivered']} delivered, "
          f"{notifier.describe() or 'no channels'})")
    print(f"dead-man   {'pinged' if summary['pinged'] else 'not pinged'}"
          + ("" if heartbeat_url else "  (no URL configured)"))
    print(f"state      {state_path}" + ("  (dry run: not written)" if args.dry_run else ""))
    return 0


def cmd_notify_test(args: argparse.Namespace) -> int:
    """Send one test message through every configured alert channel."""
    from .envdot import load_env
    from .notify import Notifier

    if getattr(args, "env_file", None):
        load_env(args.env_file)
    notifier = Notifier.from_env()
    print(f"channels   {notifier.describe() or 'none configured'}")
    if not notifier.enabled:
        print("nothing sent: set TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID, NTFY_URL "
              "or SMTP_* in the environment")
        return 1
    ok = notifier.send(
        "Trading bot alert test",
        "If you can read this, trade alerts are working.\n\n"
        "No order was sent; this is a delivery test.",
    )
    print("test sent" if ok else "test failed (see the channel error above)")
    return 0 if ok else 1


def cmd_pool(args: argparse.Namespace) -> int:
    '''Show or edit the per-symbol parameter sets in config/target_symbols.json.'''
    from . import symbolcfg

    if args.set:
        updates = symbolcfg.parse_overrides(args.set)
        symbolcfg.update_params(updates, path=args.file)
        for sym, params in updates.items():
            print(f"set        {sym:<10} " + "  ".join(f"{k}={v}" for k, v in params.items()))
    try:
        entries = symbolcfg.load_pool(args.file)
    except ValueError as exc:
        print(f"invalid    {exc}")
        return 1
    rows = symbolcfg.describe(entries)
    print(f"pool       {len(rows)} symbols  ({args.file or symbolcfg.TARGETS_PATH})")
    for row in rows:
        params = "  ".join(f"{k}={v}" for k, v in row["params"].items()) or "(book defaults)"
        print(f"  {row['symbol']:<10} {str(row['timeframe'] or '-'):<3} "
              f"{row['status']:<9} {params}")
    return 0


def cmd_env(_: argparse.Namespace) -> int:
    """Show which world this shell is pointed at."""
    from . import envcfg, db

    info = envcfg.describe()
    label = "LIVE - real money (venue not configured)" if info["env"] == "live" else "DEMO - simulated fills"
    print(f"env          {info['env']}  ({label})")
    print(f"database     {info['database']}")
    print(f"common db    {info['common_database']}")
    print(f"state dir    {info['state_dir']}")
    print(f"log dir      {info['log_dir']}")
    print(f"web port     {info['web_port']}")
    print(f"postgres     {'configured' if db.is_available() else 'not configured (DATABASE_URL missing)'}")
    return 0


def cmd_depth(args: argparse.Namespace) -> int:
    """Show or record production Binance order-book snapshots."""
    from .data import depth

    symbols = [s.strip() for s in args.coins.split(",") if s.strip()]
    if not symbols:
        raise SystemExit("--coins needs at least one symbol")
    conn = None
    if not args.no_db:
        from . import db

        try:
            conn = db.connect_common()
        except Exception as exc:  # noqa: BLE001 - recorder must still show the book
            print(f"! database unavailable ({exc}); showing without recording")

    def show(snaps) -> None:
        print(f"{chr(10)}{'symbol':<10}{'bid':>14}{'ask':>14}{'spread_bp':>11}{'imb5':>8}"
              f"{'bid_10bp':>12}{'ask_10bp':>12}")
        for s in snaps:
            print(f"{s.symbol:<10}{s.best_bid:>14.6f}{s.best_ask:>14.6f}{s.spread_bps:>11.3f}"
                  f"{s.imbalance(5):>8.3f}{s.notional('bid'):>12.0f}{s.notional('ask'):>12.0f}")

    if args.once or args.every <= 0:
        show(depth.record_once(symbols, limit=args.levels, conn=conn))
        return 0
    where = "recording to postgres" if conn is not None else "not recording"
    print(f"depth recorder: {len(symbols)} books every {args.every:g}s, {where} - Ctrl-C to stop")
    try:
        depth.run_recorder(symbols, every=args.every, limit=args.levels, conn=conn, on_snapshot=show)
    except KeyboardInterrupt:
        print("stopped")
    return 0


def _promotion_conn(env: str):
    from . import db

    if not db.is_available():
        print("! postgres not configured; any audit row will be skipped")
        return None
    try:
        return db.connect(kind=env)
    except Exception as exc:  # noqa: BLE001 - manifest is still the source of truth
        print(f"! postgres unavailable ({exc}); any audit row will be skipped")
        return None


def cmd_promote(args: argparse.Namespace) -> int:
    """Run the gate and activate a strategy in demo or live."""
    import json as _json
    from pathlib import Path as _Path

    from . import promotions as promo

    try:
        params = _json.loads(args.params or "{}")
    except _json.JSONDecodeError as exc:
        raise SystemExit(f"--params is not valid JSON: {exc}") from None
    evidence = {}
    if args.evidence:
        try:
            evidence = _json.loads(_Path(args.evidence).read_text(encoding="utf-8"))
        except OSError as exc:
            raise SystemExit(f"cannot read --evidence: {exc}") from None
        except _json.JSONDecodeError as exc:
            raise SystemExit(f"--evidence is not valid JSON: {exc}") from None
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    capital = args.capital if args.capital is not None else promo.DEFAULT_CAPITAL
    conn = _promotion_conn(args.to)
    try:
        manifest, checks, db_id = promo.promote(
            args.strategy, env=args.to, params=params, symbols=symbols,
            timeframe=args.timeframe, capital=capital, evidence=evidence,
            actor=args.actor, force=args.force, reason=args.reason, conn=conn,
        )
    except promo.PromotionError as exc:
        print(f"promotion refused: {exc}")
        return 2
    for check in checks:
        print(f"{'PASS' if check['ok'] else 'FAIL'}  {check['name']:<24}{check['detail']}")
    print()
    print(f"activated  {manifest.strategy}  env={manifest.env}  capital=${manifest.capital:,.0f}")
    print(f"params     {manifest.params_hash}")
    print(f"manifest   {manifest.path}")
    if db_id is not None:
        print(f"audit      promotions row {db_id}")
    if manifest.override:
        print(f"OVERRIDE   {manifest.override_reason}")
    return 0


def cmd_demote(args: argparse.Namespace) -> int:
    """Deactivate a strategy: drain by default, --flatten to close now."""
    from . import promotions as promo

    conn = _promotion_conn(args.env)
    try:
        manifest = promo.demote(args.strategy, env=args.env, reason=args.reason,
                                actor=args.actor, conn=conn)
    except promo.PromotionError as exc:
        print(f"demote refused: {exc}")
        return 2
    print(f"demoted    {manifest.strategy}  env={manifest.env}")
    print(f"manifest   {manifest.path}")
    if args.flatten:
        print("next       stop the bot, market-close the position, restart it")
    else:
        print("next       no new entries; the bot keeps managing its stop and exits on signal")
    return 0


def cmd_promotions(args: argparse.Namespace) -> int:
    """List active and revoked strategies for one or both environments."""
    from . import envcfg, promotions as promo

    env = args.env or envcfg.env_name()
    manifests = promo.load_manifests(env)
    if not manifests:
        print(f"no promotions for {env}")
        return 0
    print(f"{'strategy':<24}{'active':<8}{'capital':>10}  {'params':<26}{'promoted_at':<21}flag")
    for m in manifests:
        flag = "OVERRIDE" if (m.override and m.active) else ("" if m.active else "revoked")
        print(f"{m.strategy:<24}{'yes' if m.active else 'no':<8}{m.capital:>10,.0f}  "
              f"{m.params_hash:<26}{m.promoted_at:<21}{flag}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bot", description="Backtest and demo-trade a strategy.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser("gen-data", help="write a synthetic OHLCV csv")
    p_gen.add_argument("--bars", type=int, default=5000)
    p_gen.add_argument("--seed", type=int, default=7)
    p_gen.add_argument("--start-price", type=float, default=100.0)
    p_gen.add_argument("--vol", type=float, default=0.004)
    p_gen.add_argument("--out", type=Path, default=Path("data/sample.csv"))
    p_gen.set_defaults(func=cmd_gen_data)

    p_strat = sub.add_parser("strategies", help="list available strategies")
    p_strat.set_defaults(func=cmd_strategies)

    p_bt = sub.add_parser("backtest", help="run a historical simulation")
    _add_source_args(p_bt)
    _add_strategy_args(p_bt)
    _add_cost_args(p_bt)
    p_bt.add_argument("--outdir", default="runs")
    p_bt.add_argument("--plot", action="store_true", help="save an equity chart png")
    p_bt.add_argument("--json", action="store_true", help="also print metrics as json")
    p_bt.set_defaults(func=cmd_backtest)

    p_demo = sub.add_parser("demo", help="replay bars in real time with simulated money")
    _add_source_args(p_demo)
    _add_strategy_args(p_demo)
    _add_cost_args(p_demo)
    p_demo.add_argument("--delay", type=float, default=0.0, help="seconds to sleep per bar")
    p_demo.add_argument("--every", type=int, default=50, help="print a status line every N bars")
    p_demo.set_defaults(func=cmd_demo)

    p_tv = sub.add_parser("tv-bars", help="download real TradingView bars to a csv")
    p_tv.add_argument("--symbol", default="BTCUSDT")
    p_tv.add_argument("--exchange", default="BINANCE")
    p_tv.add_argument("--interval", default="1h", help="e.g. 1m, 5m, 1h, 4h, 1d, 1w")
    p_tv.add_argument("--n-bars", type=int, default=5000)
    p_tv.add_argument("--out", type=Path, default=Path("data/tv.csv"))
    p_tv.add_argument("--watch", action="store_true", help="keep polling and append new bars")
    p_tv.add_argument("--poll", type=float, default=30.0, help="seconds between polls in --watch")
    p_tv.set_defaults(func=cmd_tv_bars)

    p_alerts = sub.add_parser("serve-alerts", help="receive TradingView alert webhooks (simulation money only)")
    p_alerts.add_argument("--token", help="secret URL token; a random one is generated if omitted")
    p_alerts.add_argument("--host", default="127.0.0.1")
    p_alerts.add_argument("--port", type=int, default=8788)
    p_alerts.add_argument("--out", default="runs/alerts.csv", help="alert ledger csv")
    _add_cost_args(p_alerts)
    p_alerts.set_defaults(func=cmd_serve_alerts)

    p_acct = sub.add_parser("account", help="show the Binance demo account and open positions")
    p_acct.set_defaults(func=cmd_account)

    p_deploy = sub.add_parser("deploy", help="run a deploy bot on closed Binance bars (demo orders)")
    _add_strategy_args(p_deploy)
    _add_cost_args(p_deploy)
    p_deploy.add_argument("--symbol", default="BTC/USDT", help="e.g. BTC/USDT, ETH/USDT")
    p_deploy.add_argument("--interval", default="1d", help="bar timeframe, e.g. 1h, 4h, 1d")
    p_deploy.add_argument("--poll", type=float, default=60.0, help="seconds between polls")
    p_deploy.add_argument("--lookback", type=int, default=400, help="bars fetched each poll")
    p_deploy.add_argument("--capital", type=float, default=None,
                       help="max equity this bot may size from (default: whole demo account) so "
                            "several bots can share one account with separate wallets")
    p_deploy.add_argument("--state", default="runs/deploy_state.json", help="loop state file")
    p_deploy.add_argument("--heartbeat-url", default=os.environ.get("HEARTBEAT_URL", ""),
                        help="ping this URL on each cycle; an outside service "
                             "(healthchecks.io, Uptime Kuma) alerts when the pings stop")
    p_deploy.add_argument("--heartbeat-every", type=float, default=1800.0,
                        help="minimum seconds between pings (default 1800)")
    p_deploy.add_argument("--once", action="store_true", help="run a single poll cycle and exit")
    p_deploy.add_argument(
        "--startup-replay", action="store_true",
        help="on a fresh state, replay closed bars and open the model's "
             "current long as a new entry at today's price (service bots "
             "enable this via STARTUP_REPLAY=1; historical P&L is not credited)",
    )
    p_deploy.set_defaults(func=cmd_deploy)

    p_scan = sub.add_parser("scan", help="rank strategies across timeframes (Binance bars)")
    _add_strategy_args_multi(p_scan)
    _add_cost_args(p_scan)
    p_scan.add_argument("--symbol", default="BTC/USDT", help="comma-separated symbols, e.g. BTC/USDT,ETH/USDT")
    p_scan.add_argument("--timeframes", default="1h,4h,6h,1d", help="comma-separated: 1h,4h,6h,1d,1w")
    p_scan.add_argument("--start", default=None, help="fetch bars from this date, e.g. 2024-11-01")
    p_scan.add_argument("--days", type=int, default=365, help="lookback days if --start not given")
    p_scan.add_argument("--n-bars", type=int, default=10000)
    p_scan.add_argument("--outdir", default="runs")
    p_scan.set_defaults(func=cmd_scan)

    p_rot = sub.add_parser("rotate", help="backtest the top-N multi-coin trend rotation")
    p_rot.add_argument("--coins", default="BTCUSDT,ETHUSDT,BNBUSDT,XRPUSDT,SOLUSDT",
                       help="comma-separated symbols (csv filenames or Binance pairs, e.g. BTC/USDT)")
    p_rot.add_argument("--source", choices=["csv", "binance"], default="csv")
    p_rot.add_argument("--dir", type=Path, default=Path("data/crypto"), help="csv directory")
    p_rot.add_argument("--timeframe", default="1d")
    p_rot.add_argument("--n-bars", type=int, default=5000)
    p_rot.add_argument("--start", default=None, help="ignore bars before this date")
    p_rot.add_argument("--top", type=int, default=2, help="concurrent positions")
    p_rot.add_argument("--lookback", type=int, default=60, help="momentum lookback, bars")
    p_rot.add_argument("--regime-ma", type=int, default=200)
    p_rot.add_argument("--regime-symbol", default=None, help="defaults to the BTC symbol")
    p_rot.add_argument("--vol-target", type=float, default=0.40)
    p_rot.add_argument("--rebalance-days", type=int, default=7)
    p_rot.add_argument("--initial-cash", type=float, default=1000.0)
    p_rot.add_argument("--fee-bps", type=float, default=0.0)
    p_rot.add_argument("--outdir", default="runs/rotation")
    p_rot.set_defaults(func=cmd_rotate)

    p_book = sub.add_parser("book", help="backtest one shared wallet across coins (one slot)")
    p_book.add_argument("--coins", default="BTCUSDT,ETHUSDT,BNBUSDT,XRPUSDT,SOLUSDT",
                        help="comma-separated symbols (csv filenames or Binance pairs)")
    p_book.add_argument("--source", choices=["csv", "binance"], default="csv")
    p_book.add_argument("--dir", type=Path, default=Path("data/crypto"), help="csv directory")
    p_book.add_argument("--timeframe", default="4h")
    p_book.add_argument("--n-bars", type=int, default=5000)
    p_book.add_argument("--start", default=None, help="ignore bars before this date")
    p_book.add_argument("--fast", type=int, default=30)
    p_book.add_argument("--slow", type=int, default=50)
    p_book.add_argument("--atr-mult", type=float, default=2.0)
    p_book.add_argument("--risk-per-trade", type=float, default=0.03)
    p_book.add_argument("--max-position-pct", type=float, default=0.95)
    p_book.add_argument("--max-positions", type=int, default=1)
    p_book.add_argument("--initial-cash", type=float, default=1000.0)
    p_book.add_argument("--fee-bps", type=float, default=10.0)
    p_book.add_argument("--slippage-bps", type=float, default=5.0)
    p_book.add_argument("--damiani-filter", action="store_true",
                        help="only enter while volatility is awake (Damiani Volatmeter)")
    p_book.add_argument("--stc-min", type=float, default=None,
                        help="only enter when the Schaff Trend Cycle is at least this (0-100)")
    p_book.add_argument("--structure-gate", choices=["none", "breakout", "squeeze", "room"],
                        default="none",
                        help="chart-structure entry gate ported from the pasted pattern indicator")
    p_book.add_argument("--channel-span", type=int, default=10,
                        help="pivot bars on each side of a trendline channel")
    p_book.add_argument("--breakout-atr", type=float, default=0.5,
                        help="breakout margin above the rail, in ATR")
    p_book.add_argument("--breakout-within", type=int, default=5,
                        help="a rail breakout stays usable for this many bars")
    p_book.add_argument("--squeeze-quantile", type=float, default=0.4,
                        help="channel height/ATR below this rolling quantile counts as a squeeze")
    p_book.add_argument("--min-rr", type=float, default=2.0,
                        help="measured-move room required, in multiples of the ATR stop")
    p_book.add_argument("--channel-spans", default="",
                        help="comma separated pivot spans; two or more make the channel "
                             "their median, which removes the span as a tuned parameter")
    p_book.add_argument("--trail-atr", type=float, default=None,
                        help="exit on a Chandelier trail this many ATRs under the peak "
                             "(omit for the fixed ATR stop)")
    p_book.add_argument("--symbol-params", action="append", default=None,
                        metavar="SYM:k=v;SYM:k=v",
                        help="per-coin override, e.g. SUIUSDT:trail_atr=3.0 (repeatable)")
    p_book.add_argument("--params-file", default=None,
                        help="JSON {SYMBOL: {param: value}} or a target_symbols.json pool")
    p_book.add_argument("--trail-after-r", type=float, default=0.0,
                        help="only arm the trail once the trade shows this many R of profit")
    p_book.add_argument("--outdir", default="runs/book")
    p_book.set_defaults(func=cmd_book)

    p_auto = sub.add_parser("auto", help="dry-run the demo books on closed bars (no orders sent)")
    p_auto.add_argument("--coins", default="BTCUSDT,ETHUSDT,BNBUSDT,XRPUSDT,SOLUSDT")
    p_auto.add_argument("--source", choices=["binance", "tv"], default="binance")
    p_auto.add_argument("--timeframe", default="4h")
    p_auto.add_argument("--days", type=int, default=730, help="history window (2-year cap)")
    p_auto.add_argument("--books", default="all", help="A,B,C,D or all (default)")
    p_auto.add_argument("--cash", type=float, default=1000.0, help="starting wallet per book")
    p_auto.add_argument("--interval", type=int, default=900, help="seconds between cycles")
    p_auto.add_argument("--once", action="store_true", help="one cycle then exit (Task Scheduler)")
    p_auto.add_argument("--outdir", default=None, help="defaults to runs/<env>/autobooks")
    p_auto.add_argument("--pool", default=None,
                        help="one wallet per symbol from this target_symbols.json")
    p_auto.add_argument("--symbol-params", action="append", default=None,
                        metavar="SYM:k=v;SYM:k=v",
                        help="per-coin override applied to every book that may hold it")
    p_auto.add_argument("--params-file", default=None,
                        help="JSON {SYMBOL: {param: value}} or a target_symbols.json pool")
    p_auto.set_defaults(func=cmd_auto)

    p_dryrun = sub.add_parser(
        "dryrun",
        help="dry-run promoted sleeves as simulated wallets (no orders sent)",
    )
    p_dryrun.add_argument("--all", action="store_true",
                       help="run every active manifest, one folder per sleeve")
    p_dryrun.add_argument("--strategy", default=None, help="sleeve name, e.g. capitulation_dip_uni")
    p_dryrun.add_argument("--symbol", default=None, help='pair, e.g. "UNI/USDT"')
    p_dryrun.add_argument("--interval", default="4h", help="bar timeframe")
    p_dryrun.add_argument("--param", action="append", default=[], metavar="k=v",
                       help="override a sleeve parameter (repeatable)")
    p_dryrun.add_argument("--initial-cash", type=float, default=1_000.0, help="wallet size")
    p_dryrun.add_argument("--fee-bps", type=float, default=10.0)
    p_dryrun.add_argument("--slippage-bps", type=float, default=5.0)
    p_dryrun.add_argument("--risk-per-trade", type=float, default=0.03)
    p_dryrun.add_argument("--max-position-pct", type=float, default=0.95)
    p_dryrun.add_argument("--max-drawdown-stop", type=float, default=0.99)
    p_dryrun.add_argument("--days", type=int, default=730, help="history window (2-year cap)")
    p_dryrun.add_argument("--source", choices=["binance", "tv"], default="binance")
    p_dryrun.add_argument("--poll", type=int, default=900, help="seconds between cycles")
    p_dryrun.add_argument("--heartbeat-url", default=None,
                       help="dead-man ping URL (default: HEARTBEAT_URL)")
    p_dryrun.add_argument("--heartbeat-every", type=float, default=1800.0,
                       help="seconds between pings")
    p_dryrun.add_argument("--once", action="store_true", help="one cycle then exit (Task Scheduler)")
    p_dryrun.add_argument("--alerts", action="store_true",
                       help="also send trade alerts from this replay (off by default)")
    p_dryrun.add_argument("--outdir", default=None,
                       help="single-sleeve override; defaults to runs/<env>/<strategy>/dryrun")
    p_dryrun.add_argument("--profile", choices=["manifest", "flags"], default="manifest",
                       help="risk profile: the manifest's measured one (default) or the flags")
    p_dryrun.set_defaults(func=cmd_dryrun)
    p_wd = sub.add_parser(
        "watchdog",
        help="alert on a dead loop by name and ping the book's dead-man check",
    )
    p_wd.add_argument("--stale-minutes", type=float, default=None,
                      help="silence before a bot is called stale "
                           "(default: WATCHDOG_STALE_MINUTES or 10)")
    p_wd.add_argument("--repeat-hours", type=float, default=None,
                      help="repeat an alert about the same bot "
                           "(default: WATCHDOG_REPEAT_HOURS or 6)")
    p_wd.add_argument("--state", default=None,
                      help="episode file (default: <state dir>/watchdog.json)")
    p_wd.add_argument("--heartbeat-url", default=None,
                      help="dead-man ping URL (default: HEARTBEAT_URL)")
    p_wd.add_argument("--no-alerts", action="store_true",
                      help="report only; never send an alert")
    p_wd.add_argument("--dry-run", action="store_true",
                      help="print findings; no alert, no ping, no state write")
    p_wd.set_defaults(func=cmd_watchdog)
    p_notify = sub.add_parser(
        "notify-test", help="send a test message through the configured alert channels"
    )
    p_notify.add_argument("--env-file", default=None,
                         help="read alert settings from this file (e.g. alerts.env)")
    p_notify.set_defaults(func=cmd_notify_test)
    p_pool = sub.add_parser("pool", help="show or edit per-symbol parameters")
    p_pool.add_argument("--file", default=None, help="defaults to config/target_symbols.json")
    p_pool.add_argument("--set", action="append", default=None, metavar="SYM:k=v;SYM:k=v",
                        help="write a coin's parameters into the pool file")
    p_pool.set_defaults(func=cmd_pool)

    p_env = sub.add_parser("env", help="show which environment and databases this shell uses")
    p_env.set_defaults(func=cmd_env)

    p_depth = sub.add_parser("depth", help="show or record live Binance order-book depth")
    p_depth.add_argument("--coins", default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT")
    p_depth.add_argument("--levels", type=int, default=20)
    p_depth.add_argument("--every", type=float, default=60.0)
    p_depth.add_argument("--once", action="store_true")
    p_depth.add_argument("--no-db", action="store_true")
    p_depth.set_defaults(func=cmd_depth)

    p_promo = sub.add_parser("promote", help="promote a strategy into demo or live")
    p_promo.add_argument("strategy")
    p_promo.add_argument("--to", choices=["demo", "live"], default="demo")
    p_promo.add_argument("--symbols", default="BTCUSDT")
    p_promo.add_argument("--timeframe", default="4h")
    p_promo.add_argument("--params", default="{}", help="strategy parameters as JSON")
    p_promo.add_argument("--capital", type=float, default=None)
    p_promo.add_argument("--evidence", default="", help="path to a JSON evidence file")
    p_promo.add_argument("--actor", default="")
    p_promo.add_argument("--force", action="store_true", help="override failed checks")
    p_promo.add_argument("--reason", default="", help="why the override is acceptable")
    p_promo.set_defaults(func=cmd_promote)

    p_demote = sub.add_parser("demote", help="deactivate a promoted strategy")
    p_demote.add_argument("strategy")
    p_demote.add_argument("--env", choices=["demo", "live"], default="live")
    p_demote.add_argument("--flatten", action="store_true", help="market-close now instead of draining")
    p_demote.add_argument("--reason", default="")
    p_demote.add_argument("--actor", default="")
    p_demote.set_defaults(func=cmd_demote)

    p_promos = sub.add_parser("promotions", help="list promotions and their state")
    p_promos.add_argument("--env", choices=["demo", "live"], default=None)
    p_promos.set_defaults(func=cmd_promotions)
    p_web = sub.add_parser("web", help="run the local web trader terminal")
    p_web.add_argument("--host", default="127.0.0.1")
    p_web.add_argument("--port", type=int, default=None, help="defaults to 9000 demo, 9001 live")
    p_web.set_defaults(func=cmd_web)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
