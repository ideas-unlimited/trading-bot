"""Forward runner: replay one sleeve over closed bars with simulated money.

This is the ``bot auto`` pattern for a single strategy/symbol pair: every
cycle it loads the capped window, drops the still-forming bar, replays the
sleeve through the same :class:`bot.backtest.Engine` used by backtests
(next-open fills, intrabar stop-first, take-profit, strategy time exits) and
pins the deploy equity on the first cycle.  No exchange keys, no orders and
no order state: because the replay is deterministic, a restart cannot
corrupt the record, and ``live since deploy`` is measured against the pin
rather than against the start of the historical window.

The window is hard-capped by :data:`bot.scanlib.MAX_HISTORY_DAYS`; if the
window start moves by more than two days the pin is re-taken and the old
deploy date is preserved as ``repinned_at`` (same rule as the paper books).
"""

from __future__ import annotations

import csv
import json
import os
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import envcfg, scanlib
from .autobook import closed_bars
from .backtest import Engine, bars_to_frame
from .config import BotConfig, CostConfig, RiskConfig
from .data.feed import Bar
from .strategy import build
from .strategy.base import Strategy


def default_outdir() -> Path:
    """Paper and prod must never share a status file."""
    return envcfg.state_dir() / "forward"


def runner_key(strategy: str, symbol: str) -> str:
    """Stable key for one sleeve/symbol pair."""
    return f"{strategy}:{symbol}"


def _window_moved(prev_start: str, start: str, tolerance_days: float = 2.0) -> bool:
    """True when the replay window now starts far enough back to be a new book."""
    try:
        a = datetime.fromisoformat(prev_start.replace("Z", "+00:00"))
        b = datetime.fromisoformat(start.replace("Z", "+00:00"))
    except ValueError:
        return True
    return abs((a - b).total_seconds()) > tolerance_days * 86400.0


def _dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _load_state(path: Path) -> dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _max_drawdown(curve: Sequence[tuple[datetime, float]]) -> float:
    peak = 0.0
    worst = 0.0
    for _, equity in curve:
        peak = max(peak, equity)
        if peak:
            worst = min(worst, equity / peak - 1.0)
    return worst


def _stats(trades: Sequence[Any], curve: Sequence[tuple[datetime, float]],
           capital: float) -> dict[str, Any]:
    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl < 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    return {
        "equity": curve[-1][1] if curve else capital,
        "return_pct": (curve[-1][1] / capital - 1.0) if curve else 0.0,
        "max_drawdown": _max_drawdown(curve),
        "trades": len(trades),
        "wins": len(wins),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss else None,
    }


def run_cycle(
    strategy_name: str | Strategy,
    symbol: str,
    timeframe: str = "4h",
    *,
    params: Mapping[str, object] | None = None,
    capital: float = 1_000.0,
    fee_bps: float = 10.0,
    slippage_bps: float = 5.0,
    risk_per_trade: float = 0.05,
    max_position_pct: float = 0.5,
    max_drawdown_stop: float = 0.5,
    days: int = scanlib.MAX_HISTORY_DAYS,
    source: str = "binance",
    outdir: str | Path | None = None,
    now: datetime | None = None,
    bars: Sequence[Bar] | None = None,
) -> dict[str, Any]:
    """One cycle: load bars, replay the sleeve, pin/read the deploy mark, write state."""
    now = now or datetime.now(timezone.utc)
    outdir = Path(outdir) if outdir is not None else default_outdir()
    outdir.mkdir(parents=True, exist_ok=True)
    # Accept a name (the CLI path, always a fresh instance) or an instance
    # (tests and unregistered research sleeves).  An instance is used as given,
    # so the caller must not reuse a stateful one across cycles.
    if isinstance(strategy_name, Strategy):
        strategy = strategy_name
    else:
        strategy = build(strategy_name, **dict(params or {}))
    name = strategy.name

    if bars is None:
        bars = scanlib.load_scan_bars(symbol, timeframe, source=source,
                                      days=days, n_bars=10_000)
    bars = closed_bars(bars, timeframe, now)
    if len(bars) <= strategy.warmup + 5:
        raise RuntimeError(
            f"{symbol} {timeframe}: {len(bars)} closed bars, need more than "
            f"warmup {strategy.warmup} + 5"
        )

    config = BotConfig(
        initial_cash=capital,
        costs=CostConfig(fee_bps=fee_bps, slippage_bps=slippage_bps),
        risk=RiskConfig(
            risk_per_trade=risk_per_trade,
            max_position_pct=max_position_pct,
            max_drawdown_stop=max_drawdown_stop,
        ),
    )
    engine = Engine(bars_to_frame(bars), strategy, config)
    while engine.step():
        pass

    df = engine.df
    last_row = df.iloc[-1]
    last_bar = str(last_row["ts"])
    mark = float(last_row["close"])
    equity = engine.broker.equity(mark)
    # The engine keeps one equity point per processed bar; it is the same
    # curve the backtest reports, so read it rather than re-marking here.
    curve = list(engine._equity)
    window_start = str(df.iloc[0]["ts"])

    state_path = outdir / "status.json"
    state = _load_state(state_path)
    previous = state.get("runners") if isinstance(state.get("runners"), dict) else {}
    key = runner_key(name, symbol)
    prev = previous.get(key, {}) if isinstance(previous.get(key), dict) else {}
    prev_window = str((prev.get("window") or {}).get("start") or "")
    moved = bool(prev_window and _window_moved(prev_window, window_start))
    prev_deploy = prev.get("deploy") if isinstance(prev.get("deploy"), dict) else {}

    if prev_deploy.get("deploy_equity") and not moved:
        deploy_equity = float(prev_deploy["deploy_equity"])
        deployed_at = prev_deploy.get("deployed_at") or now.isoformat()
        deploy_bar = prev_deploy.get("deploy_bar") or last_bar
        repinned_at = prev_deploy.get("repinned_at")
    else:
        deploy_equity = equity
        deployed_at = now.isoformat()
        deploy_bar = last_bar
        repinned_at = (prev_deploy.get("deployed_at") or prev_deploy.get("repinned_at")) if moved else None

    live_return = equity / deploy_equity - 1.0 if deploy_equity else 0.0
    deploy_dt = _dt(deploy_bar)
    # A trade belongs to the forward record only if it opened after the bar the
    # pin was taken on; the position that was already open stays in history.
    forward_trades = [t for t in engine.broker.trades if t.entry_ts > deploy_dt]

    position = None
    if engine.broker.in_position:
        qty = float(engine.broker.qty)
        position = {
            "qty": qty,
            "entry": float(engine.broker.entry_price),
            "entry_ts": str(engine.broker.entry_ts),
            "mark": mark,
            "unrealized": (mark - float(engine.broker.entry_price)) * qty,
            "stop": engine._stop,
            "take_profit": engine._take,
        }

    history = _stats(engine.broker.trades, curve, capital)
    history["wins"] = len([t for t in engine.broker.trades if t.pnl > 0])
    forward = _stats(forward_trades, [(deploy_dt, deploy_equity), (now, equity)], capital)
    forward["equity"] = equity
    forward["return_pct"] = live_return
    forward["wallet"] = capital * (1.0 + live_return)
    forward["trades"] = len(forward_trades)

    payload = {
        "updated_at": now.isoformat(),
        "source": source,
        "timeframe": timeframe,
        "strategy": name,
        "symbol": symbol,
        "params": dict(strategy.params),
        "capital": capital,
        "window": {"start": window_start, "end": last_bar, "bars": len(df)},
        "deploy": {
            "deployed_at": deployed_at,
            "deploy_bar": deploy_bar,
            "deploy_equity": deploy_equity,
            "repinned_at": repinned_at,
            "return_pct": live_return,
            "wallet": capital * (1.0 + live_return),
            "trades": len(forward_trades),
        },
        "history": history,
        "forward": forward,
        "position": position,
        "halted_reason": engine.risk.halt_reason,
        "last_bar": last_bar,
        "mark": mark,
    }

    previous[key] = payload
    state = {
        "updated_at": now.isoformat(),
        "timeframe": timeframe,
        "source": source,
        "runners": previous,
    }
    # Atomic replace: a reader (dashboard, promote evidence) must never see a
    # half-written status file, even if two loops race on the same cycle.
    tmp_path = state_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    os.replace(tmp_path, state_path)

    # Trades CSV: the whole replay, with a flag for what opened after deploy.
    trades_path = outdir / f"{name}_{symbol.replace('/', '')}.trades.csv"
    with trades_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "entry_ts", "exit_ts", "qty", "entry_price", "exit_price", "fees",
            "pnl", "return_pct", "reason_open", "reason_close", "forward",
        ])
        for t in engine.broker.trades:
            writer.writerow([
                str(t.entry_ts), str(t.exit_ts), t.qty, t.entry_price,
                t.exit_price, t.fees, round(t.pnl, 6), round(t.return_pct, 6),
                t.reason_open, t.reason_close,
                int(t.entry_ts > deploy_dt),
            ])

    # Equity CSV: one row per distinct closed bar, so a manual re-run of the
    # same cycle does not duplicate a point.
    equity_path = outdir / f"{name}_{symbol.replace('/', '')}.equity.csv"
    existing: list[list[str]] = []
    if equity_path.exists():
        with equity_path.open(newline="", encoding="utf-8") as fh:
            existing = list(csv.reader(fh))
    header = ["ts", "equity", "live_return", "position_qty"]
    rows = [r for r in existing[1:] if r and r[0] != last_bar] if existing else []
    rows.append([last_bar, f"{equity:.6f}", f"{live_return:.8f}", str(engine.broker.qty)])
    with equity_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)

    payload["status_path"] = str(state_path)
    payload["files"] = {"status": str(state_path), "trades": str(trades_path),
                         "equity": str(equity_path)}
    return payload


def format_status(payload: Mapping[str, Any]) -> str:
    """Short human block for one cycle."""
    deploy = payload.get("deploy", {})
    history = payload.get("history", {})
    lines = [
        f"{payload.get('strategy')}  {payload.get('symbol')}  {payload.get('timeframe')}"
        f"  bars {payload.get('window', {}).get('bars')}"
        f"  as of {str(payload.get('updated_at', ''))[:19].replace('T', ' ')} UTC",
        ("  live      $%8.2f   %+6.1f%% since %s  (%d trades)"
         % (deploy.get("wallet", 0.0), float(deploy.get("return_pct", 0.0)) * 100,
            str(deploy.get("deployed_at", ""))[:10], int(deploy.get("trades", 0)))),
        ("  history   $%8.2f   %+7.1f%% over the window  (%d trades, %4.0f%% win, dd %5.1f%%)"
         % (history.get("equity", 0.0), float(history.get("return_pct", 0.0)) * 100,
            int(history.get("trades", 0)), float(history.get("win_rate", 0.0)) * 100,
            float(history.get("max_drawdown", 0.0)) * 100)),
    ]
    pos = payload.get("position")
    if pos:
        lines.append("  holding   %10.6f @ %8.4f  stop %s  target %s  upnl $%+7.2f"
                     % (pos["qty"], pos["entry"],
                        "-" if pos.get("stop") is None else "%.4f" % pos["stop"],
                        "-" if pos.get("take_profit") is None else "%.4f" % pos["take_profit"],
                        pos["unrealized"]))
    else:
        lines.append("  holding   none (flat)")
    if payload.get("halted_reason"):
        lines.append(f"  halted    {payload['halted_reason']}")
    return "\n".join(lines)


def run_forever(interval: float, **kwargs: Any) -> None:
    """Poll loop; each cycle is independent and idempotent."""
    while True:
        try:
            payload = run_cycle(**kwargs)
            print(format_status(payload), flush=True)
        except Exception as exc:  # noqa: BLE001 - one bad cycle must not stop the record
            print(f"[forward] cycle failed: {type(exc).__name__}: {exc}", flush=True)
            traceback.print_exc()
        time.sleep(max(30.0, float(interval)))
