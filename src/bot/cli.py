"""Command line interface.

    python -m bot gen-data    --bars 5000 --out data/sample.csv
    python -m bot strategies
    python -m bot backtest    --source synthetic --strategy sma_cross --plot
    python -m bot backtest    --source csv --csv data/sample.csv
    python -m bot paper       --source synthetic --delay 0.01

    # Real TradingView data (unofficial tvDatafeed; TV_USERNAME/TV_PASSWORD env optional)
    python -m bot tv-bars     --symbol BTCUSDT --exchange BINANCE --interval 1h --n-bars 5000 --out data/tv.csv
    python -m bot backtest    --source tv --symbol BTCUSDT --exchange BINANCE --interval 1h --strategy sma_cross
    python -m bot paper       --source tv --symbol SPY --exchange NASDAQ --interval 1d --strategy rsi_meanrev

    # Mirror TradingView paper-trading alerts into this engine's risk rules
    python -m bot serve-alerts --port 8788 --token MYSECRET
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .alpaca_client import AlpacaClient, resolve_timeframe
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
from .config import BotConfig, CostConfig, RiskConfig
from .data.feed import generate_synthetic, load_csv, write_bars
from .data.tvfeed import fetch_latest_tv_bars, fetch_tv_bars
from .metrics import format_metrics
from .paper import PaperTrader
from .portfolio import (
    RotationConfig,
    closes_from_bars,
    format_summary as format_rotation,
    load_closes_from_csv,
    save_rotation,
    simulate_rotation,
)
from .report import save_equity_chart, save_trades_csv, summary_text
from .strategy import REGISTRY, build


def _add_source_args(p: argparse.ArgumentParser, *, live: bool = False) -> None:
    p.add_argument("--source", choices=["synthetic", "csv", "tv"], default="synthetic",
                   help="data source: synthetic bars, OHLCV csv, or real TradingView bars")
    p.add_argument("--csv", type=Path, help="OHLCV csv (required for --source csv)")
    p.add_argument("--symbol", default="BTCUSDT", help="TradingView symbol (for --source tv)")
    p.add_argument("--exchange", default="BINANCE", help="TradingView exchange (for --source tv)")
    p.add_argument("--interval", default="1h", help="bar interval, e.g. 1h, 4h, 1d (for --source tv)")
    p.add_argument("--n-bars", type=int, default=5000, help="bars to fetch from TradingView/Alpaca")
    p.add_argument("--start", default=None, help="fetch bars from this date, e.g. 2020-01-01 (Alpaca only)")
    p.add_argument("--bars", type=int, default=5000, help="synthetic bar count")
    p.add_argument("--seed", type=int, default=7, help="synthetic generator seed")
    p.add_argument("--start-price", type=float, default=100.0)
    p.add_argument("--bars-per-year", type=float, default=None,
                   help="annualisation factor; inferred from bar spacing by default")


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
    p.add_argument("--max-drawdown-stop", type=float, default=0.25)


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


def _load_bars(args: argparse.Namespace) -> list:
    if args.source == "csv":
        if not args.csv:
            raise SystemExit("--source csv requires --csv PATH")
        return load_csv(args.csv)
    if args.source == "alpaca":
        start = None
        if args.start:
            from datetime import datetime, timezone

            start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
        client = AlpacaClient()
        print(f"fetching up to {args.n_bars} bars of {args.symbol} @ {args.interval} "
              f"from Alpaca (paper keys) ...", file=sys.stderr)
        return client.fetch_bars(args.symbol, args.interval, start=start, limit=args.n_bars)
    if args.source == "tv":
        print(f"fetching {args.n_bars} bars of {args.exchange}:{args.symbol} "
              f"@ {args.interval} from TradingView ...", file=sys.stderr)
        return fetch_tv_bars(args.symbol, args.exchange, args.interval, args.n_bars)
    return generate_synthetic(args.bars, seed=args.seed, start_price=args.start_price)


def _config(args: argparse.Namespace, bars: list) -> BotConfig:
    from .metrics import infer_bars_per_year

    bars_per_year = args.bars_per_year or infer_bars_per_year([b.ts for b in bars])
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


def cmd_paper(args: argparse.Namespace) -> int:
    bars = _load_bars(args)
    strategy = build(args.strategy, **_parse_params(args.param))
    PaperTrader(
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
    """Serve the TradingView webhook that mirrors paper-trading fills."""
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
    """Show the Alpaca PAPER account summary and open positions."""
    client = AlpacaClient()
    acct = client.get_account()
    print(f"account       {acct.get('account_number')} ({acct.get('status')})")
    print(f"mode          PAPER (no real orders possible from this code)")
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


def cmd_live(args: argparse.Namespace) -> int:
    """Trade a strategy on live bars into the Alpaca PAPER account."""
    from .live import LiveTrader

    strategy = build(args.strategy, **_parse_params(args.param))
    client = AlpacaClient()
    bars = client.fetch_bars(args.symbol, args.interval, limit=max(strategy.warmup + 2, 300))
    config = _config(args, bars)
    LiveTrader(
        client,
        strategy,
        config,
        args.symbol,
        args.interval,
        state_path=args.state,
        poll=args.poll,
        lookback=max(strategy.warmup + 2, args.lookback),
    ).run(once=args.once)
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    """Backtest strategy/timeframe combinations and rank the results.

    Bars come from Alpaca (stocks or crypto).  ``6h`` is aggregated from 1h
    bars.  Results are printed as a table and saved to runs/scan.csv.
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

    client = AlpacaClient()
    rows: list[dict[str, object]] = []
    for sym in symbols:
        for tf in timeframes:
            print(f"fetching {sym} @ {tf} ...")
            bars = client.fetch_bars(sym, tf, start=start, limit=args.n_bars)
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
    from .web import WebApp

    app = WebApp(args.host, args.port)
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
        client = AlpacaClient()
        print(f"fetching {len(symbols)} symbols @ {args.timeframe} from Alpaca ...", file=sys.stderr)
        bars = {}
        for sym in symbols:
            bars[sym] = client.fetch_bars(sym, args.timeframe, limit=args.n_bars)
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


def cmd_book(args: argparse.Namespace) -> int:
    """Backtest one shared wallet across several coins, one slot at a time."""
    symbols = [s.strip() for s in args.coins.split(",") if s.strip()]
    if len(symbols) < 2:
        raise SystemExit("--coins needs at least two symbols")

    if args.source == "csv":
        bars = load_bars_from_csv(args.dir, symbols, args.timeframe)
    else:
        client = AlpacaClient()
        print(f"fetching {len(symbols)} symbols @ {args.timeframe} from Alpaca ...", file=sys.stderr)
        bars = {}
        for sym in symbols:
            bars[sym] = client.fetch_bars(sym, args.timeframe, limit=args.n_bars)
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
    )
    result = simulate_book(bars, config)
    print(f"rule       SMA {config.fast}/{config.slow} cross, {config.atr_mult:g} ATR stop")
    print(f"coins      {', '.join(symbols)}  ({args.source}, {args.timeframe})")
    print(f"slots      {config.max_positions}  risk {config.risk_per_trade:.1%} per trade")
    print()
    print(format_book(result))

    paths = save_book(result, args.outdir)
    print()
    for name, path in paths.items():
        print(f"{name:<8} {path}")

    print()
    print("next open (paper):")
    for sym, text in last_signal(result, bars).items():
        print(f"  {sym:<10} {text}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bot", description="Backtest and paper-trade a strategy.")
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

    p_paper = sub.add_parser("paper", help="replay bars live-ish with simulated money")
    _add_source_args(p_paper)
    _add_strategy_args(p_paper)
    _add_cost_args(p_paper)
    p_paper.add_argument("--delay", type=float, default=0.0, help="seconds to sleep per bar")
    p_paper.add_argument("--every", type=int, default=50, help="print a status line every N bars")
    p_paper.set_defaults(func=cmd_paper)

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

    p_acct = sub.add_parser("account", help="show Alpaca PAPER account summary and positions")
    p_acct.set_defaults(func=cmd_account)

    p_live = sub.add_parser("live", help="paper-trade a strategy on live bars via Alpaca PAPER")
    _add_strategy_args(p_live)
    _add_cost_args(p_live)
    p_live.add_argument("--symbol", default="SPY", help="e.g. SPY, AAPL, BTC/USD, ETH/USD")
    p_live.add_argument("--interval", default="1d", help="bar timeframe, e.g. 1h, 4h, 1d")
    p_live.add_argument("--poll", type=float, default=60.0, help="seconds between polls")
    p_live.add_argument("--lookback", type=int, default=400, help="bars fetched each poll")
    p_live.add_argument("--state", default="runs/live_state.json", help="loop state file")
    p_live.add_argument("--once", action="store_true", help="run a single poll cycle and exit")
    p_live.set_defaults(func=cmd_live)

    p_scan = sub.add_parser("scan", help="rank strategies across timeframes (Alpaca bars)")
    _add_strategy_args_multi(p_scan)
    _add_cost_args(p_scan)
    p_scan.add_argument("--symbol", default="BTC/USD", help="comma-separated symbols, e.g. BTC/USD,ETH/USD")
    p_scan.add_argument("--timeframes", default="1h,4h,6h,1d", help="comma-separated: 1h,4h,6h,1d,1w")
    p_scan.add_argument("--start", default=None, help="fetch bars from this date, e.g. 2024-11-01")
    p_scan.add_argument("--days", type=int, default=365, help="lookback days if --start not given")
    p_scan.add_argument("--n-bars", type=int, default=10000)
    p_scan.add_argument("--outdir", default="runs")
    p_scan.set_defaults(func=cmd_scan)

    p_rot = sub.add_parser("rotate", help="backtest the top-N multi-coin trend rotation")
    p_rot.add_argument("--coins", default="BTCUSDT,ETHUSDT,BNBUSDT,XRPUSDT,SOLUSDT",
                       help="comma-separated symbols (csv filenames or Alpaca pairs, e.g. BTC/USD)")
    p_rot.add_argument("--source", choices=["csv", "alpaca"], default="csv")
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
                        help="comma-separated symbols (csv filenames or Alpaca pairs)")
    p_book.add_argument("--source", choices=["csv", "alpaca"], default="csv")
    p_book.add_argument("--dir", type=Path, default=Path("data/crypto"), help="csv directory")
    p_book.add_argument("--timeframe", default="4h")
    p_book.add_argument("--n-bars", type=int, default=5000)
    p_book.add_argument("--start", default=None, help="ignore bars before this date")
    p_book.add_argument("--fast", type=int, default=30)
    p_book.add_argument("--slow", type=int, default=50)
    p_book.add_argument("--atr-mult", type=float, default=2.0)
    p_book.add_argument("--risk-per-trade", type=float, default=0.02)
    p_book.add_argument("--max-position-pct", type=float, default=0.95)
    p_book.add_argument("--max-positions", type=int, default=1)
    p_book.add_argument("--initial-cash", type=float, default=1000.0)
    p_book.add_argument("--fee-bps", type=float, default=10.0)
    p_book.add_argument("--slippage-bps", type=float, default=5.0)
    p_book.add_argument("--outdir", default="runs/book")
    p_book.set_defaults(func=cmd_book)

    p_web = sub.add_parser("web", help="run the local web trader terminal")
    p_web.add_argument("--host", default="127.0.0.1")
    p_web.add_argument("--port", type=int, default=9000)
    p_web.set_defaults(func=cmd_web)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
