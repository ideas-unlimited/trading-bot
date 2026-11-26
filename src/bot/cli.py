"""Command line interface.

    python -m bot gen-data  --bars 5000 --out data/sample.csv
    python -m bot strategies
    python -m bot backtest  --source synthetic --strategy sma_cross --plot
    python -m bot backtest  --source csv --csv data/sample.csv
    python -m bot paper     --source synthetic --delay 0.01
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .backtest import run_backtest
from .config import BotConfig, CostConfig, RiskConfig
from .data.feed import generate_synthetic, load_csv, write_bars
from .metrics import format_metrics
from .paper import PaperTrader
from .report import save_equity_chart, save_trades_csv, summary_text
from .strategy import REGISTRY, build


def _add_source_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--source", choices=["synthetic", "csv"], default="synthetic")
    p.add_argument("--csv", type=Path, help="OHLCV csv (required for --source csv)")
    p.add_argument("--bars", type=int, default=5000, help="synthetic bar count")
    p.add_argument("--seed", type=int, default=7, help="synthetic generator seed")
    p.add_argument("--start-price", type=float, default=100.0)
    p.add_argument("--bars-per-year", type=float, default=None,
                   help="annualisation factor; inferred from bar spacing by default")


def _add_strategy_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--strategy", default="sma_cross", choices=sorted(REGISTRY))
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

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
