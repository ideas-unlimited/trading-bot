"""dryrun runner: replay one sleeve over closed bars with simulated money.

This is the ``bot auto`` pattern for a strategy/symbol pair: every cycle it
loads the capped window, drops the still-forming bar, replays the sleeve
through the same :class:`bot.backtest.Engine` used by backtests (next-open
fills, intrabar stop-first, take-profit, strategy time exits) and pins the
deploy equity on the first cycle.  No exchange keys, no orders and no order
state: because the replay is deterministic, a restart cannot corrupt the
record, and ``since deploy`` is measured against the pin rather than
against the start of the historical window.

State is one folder per sleeve - ``runs/<env>/<strategy>/dryrun/status.json``
with ``<SYMBOL>.trades.csv`` and ``<SYMBOL>.equity.csv`` beside it - so two
sleeves can never overwrite each other's deploy pin.  ``bot dryrun --all``
walks the active manifests in one process and re-reads them every cycle.

The window is hard-capped by :data:`bot.scanlib.MAX_HISTORY_DAYS`; if the
window start moves by more than two days the pin is re-taken and the old
deploy date is preserved as ``repinned_at`` (same rule as the demo books).
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
from .heartbeat import Heartbeat
from .notify import Notifier
from .strategy import build
from .strategy.base import Strategy


def default_outdir(strategy: str) -> Path:
    """One folder per sleeve: runs/<env>/<strategy>/dryrun/status.json.

    Demo and live never share a folder (state_dir carries the env), and two
    sleeves never share a status file, so a promote/demote cannot delete
    another sleeve's deploy pin.
    """
    return envcfg.state_dir() / strategy / "dryrun"


def runner_key(symbol: str) -> str:
    """Status key for one symbol inside a sleeve folder."""
    return symbol


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


def _fill_key(fill: Any) -> str:
    """Stable identity for one execution inside a deterministic replay."""
    return (
        f"{fill.ts.isoformat()}|{fill.side}|"
        f"{float(fill.qty):.12g}|{float(fill.price):.12g}"
    )


def _trade_for_fill(fill: Any, trades: Sequence[Any]) -> Any | None:
    """The round-trip trade closed by this sell fill, when there is one."""
    if fill.side != "sell":
        return None
    for trade in reversed(trades):
        if trade.exit_ts != fill.ts:
            continue
        if abs(trade.exit_price - fill.price) > 1e-9:
            continue
        if abs(trade.qty - fill.qty) > 1e-9:
            continue
        return trade
    return None


def _notify_fills(
    *,
    notifier: Notifier | None,
    previous: Mapping[str, Any],
    fills: Sequence[Any],
    trades: Sequence[Any],
    strategy: str,
    symbol: str,
    timeframe: str,
    now: datetime,
    equity: float,
    deploy_equity: float,
    current_entry_ts: datetime | None,
    current_stop: float | None,
    current_take: float | None,
) -> dict[str, Any]:
    """Alert on fills that appeared since the last cycle.

    The replay rebuilds every fill each cycle, so the cursor is a fill key
    persisted in status.json, not a count.  The first cycle after alerts are
    installed only anchors the cursor: history is not replayed as alerts.
    If every channel fails, the cursor is not advanced and the same fill is
    retried on the next cycle (at-least-once, never silently lost).
    """
    previous_alerts = previous.get("alerts")
    alerts = previous_alerts if isinstance(previous_alerts, dict) else {}
    last_key = str(alerts.get("last_fill") or "")
    keys = [_fill_key(fill) for fill in fills]
    last_sent_at = alerts.get("last_sent_at")

    if not alerts.get("initialized"):
        return {
            "initialized": True,
            "last_fill": keys[-1] if keys else "",
            "last_sent_at": last_sent_at,
        }

    if last_key and last_key in keys:
        new_fills = fills[keys.index(last_key) + 1:]
    elif last_key:
        # The window moved and pruned the old cursor.  Re-anchor instead of
        # replaying a whole capped window to the user as fresh trades.
        return {
            "initialized": True,
            "last_fill": keys[-1] if keys else "",
            "last_sent_at": last_sent_at,
        }
    else:
        new_fills = list(fills)

    cursor = last_key
    sent = 0
    can_send = notifier is not None and notifier.enabled
    env = (os.environ.get("BOT_ENV") or "demo").strip().lower() or "demo"

    for fill in new_fills:
        trade = _trade_for_fill(fill, trades)
        held_hours = None
        if trade is not None:
            held_hours = (trade.exit_ts - trade.entry_ts).total_seconds() / 3600.0
        stop = None
        take_profit = None
        if fill.side == "buy" and current_entry_ts == fill.ts:
            stop = current_stop
            take_profit = current_take
        event = {
            "side": fill.side,
            "strategy": strategy,
            "symbol": symbol,
            "timeframe": timeframe,
            "env": env,
            "ts": fill.ts,
            "entry": trade.entry_price if trade is not None else fill.price,
            "price": fill.price,
            "qty": fill.qty,
            "notional": fill.notional,
            "stop": stop,
            "take_profit": take_profit,
            "reason": fill.reason,
            "pnl": trade.pnl if trade is not None else None,
            "return_pct": trade.return_pct if trade is not None else None,
            "held_hours": held_hours,
            "wallet": equity,
            "deploy_equity": deploy_equity,
        }
        if can_send and not notifier.send_trade(event):
            break
        cursor = _fill_key(fill)
        if can_send:
            sent += 1

    return {
        "initialized": True,
        "last_fill": cursor,
        "last_sent_at": now.isoformat() if sent else last_sent_at,
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
    risk_per_trade: float = 0.03,
    max_position_pct: float = 0.95,
    max_drawdown_stop: float = 0.99,
    days: int = scanlib.MAX_HISTORY_DAYS,
    source: str = "binance",
    outdir: str | Path | None = None,
    now: datetime | None = None,
    bars: Sequence[Bar] | None = None,
    notifier: Notifier | None = None,
    profile_source: str = "flags",
) -> dict[str, Any]:
    """One cycle: load bars, replay the sleeve, pin/read the deploy mark, write state."""
    now = now or datetime.now(timezone.utc)
    # Accept a name (the CLI path, always a fresh instance) or an instance
    # (tests and unregistered research sleeves).  An instance is used as given,
    # so the caller must not reuse a stateful one across cycles.
    if isinstance(strategy_name, Strategy):
        strategy = strategy_name
    else:
        strategy = build(strategy_name, **dict(params or {}))
    name = strategy.name
    outdir = Path(outdir) if outdir is not None else default_outdir(name)
    outdir.mkdir(parents=True, exist_ok=True)

    if bars is None:
        bars = scanlib.load_scan_bars(symbol, timeframe, source=source,
                                      days=days, n_bars=10_000)
    bars = closed_bars(bars, timeframe, now)
    if len(bars) <= strategy.warmup + 5:
        raise RuntimeError(
            f"{symbol} {timeframe}: {len(bars)} closed bars, need more than "
            f"warmup {strategy.warmup} + 5"
        )

    profile = {
        "risk_per_trade": float(risk_per_trade),
        "max_position_pct": float(max_position_pct),
        "max_drawdown_stop": float(max_drawdown_stop),
        "source": profile_source,
    }
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
    owner = str(state.get("strategy") or "")
    if owner and owner != name:
        raise RuntimeError(
            f"{state_path} belongs to {owner}, not {name}; use one folder per sleeve"
        )
    previous = state.get("runners") if isinstance(state.get("runners"), dict) else {}
    key = runner_key(symbol)
    prev = previous.get(key, {}) if isinstance(previous.get(key), dict) else {}
    prev_window = str((prev.get("window") or {}).get("start") or "")
    # A different risk profile sizes every trade differently, so it is a
    # different book: comparing it to the old pin would invent a return.
    # A status file written before the profile was recorded cannot prove the
    # book is unchanged, so it re-pins once.
    prev_risk = prev.get("risk") if isinstance(prev.get("risk"), dict) else {}
    profile_moved = bool(prev) and (
        not prev_risk
        or any(
            abs(float(prev_risk.get(key, profile[key])) - profile[key]) > 1e-12
            for key in ("risk_per_trade", "max_position_pct", "max_drawdown_stop")
        )
    )
    moved = bool(prev_window and _window_moved(prev_window, window_start)) or profile_moved
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

    deploy_return = equity / deploy_equity - 1.0 if deploy_equity else 0.0
    deploy_dt = _dt(deploy_bar)
    # A trade belongs to the dryrun record only if it opened after the bar the
    # pin was taken on; the position that was already open stays in history.
    dryrun_trades = [t for t in engine.broker.trades if t.entry_ts > deploy_dt]

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
    dryrun = _stats(dryrun_trades, [(deploy_dt, deploy_equity), (now, equity)], capital)
    # The public wallet is capital-normalised: deploy_equity lives on the full
    # history curve, so exposing it here made `dryrun.equity` look like the
    # two-year history equity while return_pct was measured since deploy.
    dryrun["equity"] = capital * (1.0 + deploy_return)
    dryrun["return_pct"] = deploy_return
    dryrun["wallet"] = dryrun["equity"]
    dryrun["trades"] = len(dryrun_trades)

    # Alerts are a side effect of the replay, never a reason for it to fail.
    # Only fills after the deploy bar are dryrun events; a position inherited
    # from history can still produce a legitimate exit alert.
    dryrun_fills = [fill for fill in engine.broker.fills if fill.ts > deploy_dt]
    try:
        alerts = _notify_fills(
            notifier=notifier,
            previous=prev,
            fills=dryrun_fills,
            trades=engine.broker.trades,
            strategy=name,
            symbol=symbol,
            timeframe=timeframe,
            now=now,
            equity=equity,
            deploy_equity=deploy_equity,
            current_entry_ts=engine.broker.entry_ts,
            current_stop=engine._stop,
            current_take=engine._take,
        )
    except Exception as exc:  # noqa: BLE001 - alerting must not break the record
        print(f"[dryrun] {name}:{symbol} alerts failed: {type(exc).__name__}: {exc}", flush=True)
        previous_alerts = prev.get("alerts")
        alerts = previous_alerts if isinstance(previous_alerts, dict) else {}

    payload = {
        "updated_at": now.isoformat(),
        "source": source,
        "timeframe": timeframe,
        "strategy": name,
        "symbol": symbol,
        "params": dict(strategy.params),
        "capital": capital,
        "risk": profile,
        "window": {"start": window_start, "end": last_bar, "bars": len(df)},
        "deploy": {
            "deployed_at": deployed_at,
            "deploy_bar": deploy_bar,
            "deploy_equity": deploy_equity,
            "repinned_at": repinned_at,
            "return_pct": deploy_return,
            "wallet": capital * (1.0 + deploy_return),
            "trades": len(dryrun_trades),
        },
        "history": history,
        "dryrun": dryrun,
        "position": position,
        "halted_reason": engine.risk.halt_reason,
        "alerts": alerts,
        "last_bar": last_bar,
        "mark": mark,
    }

    previous[key] = payload
    state = {
        "updated_at": now.isoformat(),
        "strategy": name,
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
    trades_path = outdir / f"{symbol.replace('/', '')}.trades.csv"
    with trades_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "entry_ts", "exit_ts", "qty", "entry_price", "exit_price", "fees",
            "pnl", "return_pct", "reason_open", "reason_close", "dryrun",
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
    equity_path = outdir / f"{symbol.replace('/', '')}.equity.csv"
    existing: list[list[str]] = []
    if equity_path.exists():
        with equity_path.open(newline="", encoding="utf-8") as fh:
            existing = list(csv.reader(fh))
    header = ["ts", "equity", "deploy_return", "position_qty"]
    rows = [r for r in existing[1:] if r and r[0] != last_bar] if existing else []
    rows.append([last_bar, f"{equity:.6f}", f"{deploy_return:.8f}", str(engine.broker.qty)])
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
    risk = payload.get("risk")
    if isinstance(risk, dict) and risk:
        lines.append(
            "  risk      %4.1f%%/trade  cap %3.0f%%  brake %3.0f%%  (%s)"
            % (float(risk.get("risk_per_trade", 0.0)) * 100,
               float(risk.get("max_position_pct", 0.0)) * 100,
               float(risk.get("max_drawdown_stop", 0.0)) * 100,
               risk.get("source") or "flags")
        )
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


def active_specs(env: str | None = None, base: Path | None = None) -> list[dict[str, Any]]:
    """One spec per promoted strategy/symbol pair in this environment."""
    from . import promotions as promo

    env = env or envcfg.env_name()
    specs: list[dict[str, Any]] = []
    manifests = promo.active_manifests(env, base=base).values()
    for manifest in sorted(manifests, key=lambda m: m.strategy):
        for symbol in manifest.symbols:
            spec = {
                "strategy": manifest.strategy,
                "symbol": symbol,
                "timeframe": manifest.timeframe,
                "capital": manifest.capital,
                "params_hash": manifest.params_hash,
            }
            # The manifest states the book it was measured on; the runner must
            # reproduce that book, not lend it the service's flags.
            spec.update(manifest.risk_profile())
            specs.append(spec)
    return specs


def run_specs(
    specs: Sequence[Mapping[str, Any]],
    *,
    notifier: Notifier | None = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """One pass over every sleeve; a broken runner is logged, not fatal.

    The three risk knobs are resolved per sleeve: the manifest's measured
    profile wins, the caller's flags (or run_cycle defaults) fill the gaps.
    """
    from . import promotions as promo
    from .promotions import RISK_PROFILE_KEYS

    defaults = {key: kwargs.pop(key, None) for key in RISK_PROFILE_KEYS if key in kwargs}
    results: list[dict[str, Any]] = []
    for spec in specs:
        name = str(spec.get("strategy") or "")
        symbol = str(spec.get("symbol") or "")
        profile: dict[str, float] = {}
        source: list[str] = []
        for key in RISK_PROFILE_KEYS:
            if spec.get(key) is not None:
                profile[key] = float(spec[key])
                source.append("manifest")
            elif defaults.get(key) is not None:
                profile[key] = float(defaults[key])
                source.append("flags")
        profile_source = "+".join(sorted(set(source))) or "default"
        try:
            strategy = build(name)
            expected = str(spec.get("params_hash") or "")
            digest = promo.params_hash(strategy.params)
            if expected and digest != expected:
                raise RuntimeError(
                    f"params hash {digest} != promoted {expected}; promote the change first"
                )
            payload = run_cycle(
                strategy, symbol, str(spec.get("timeframe") or "4h"),
                capital=float(spec.get("capital") or 1_000.0),
                notifier=notifier,
                profile_source=profile_source,
                **profile,
                **kwargs,
            )
            print(format_status(payload), flush=True)
            results.append(payload)
        except Exception as exc:  # noqa: BLE001 - isolate one sleeve from the rest
            print(f"[dryrun] {name}:{symbol} failed: {type(exc).__name__}: {exc}", flush=True)
            traceback.print_exc()
            results.append({"strategy": name, "symbol": symbol, "error": str(exc)})
    return results


def run_all_forever(
    interval: float,
    *,
    env: str | None = None,
    heartbeat_url: str | None = None,
    heartbeat_every: float = 1800.0,
    notifier: Notifier | None = None,
    **kwargs: Any,
) -> None:
    """Poll loop over the active manifests; re-read them every cycle.

    One outside ping per pass, after the pass (and after a failed pass), proves
    the loop is turning even when there is nothing to trade.  A monitoring
    outage must never stop the record, so the ping never raises.
    """
    heartbeat = Heartbeat(heartbeat_url, every=heartbeat_every)
    notifier = notifier or Notifier.from_env()
    print(
        f"[dryrun] heartbeat every {heartbeat_every:.0f}s"
        if heartbeat_url
        else "[dryrun] heartbeat off (set HEARTBEAT_URL)",
        flush=True,
    )
    print(
        f"[dryrun] trade alerts: {notifier.describe()}"
        if notifier.enabled
        else "[dryrun] trade alerts off (dryrun replay is silent)",
        flush=True,
    )
    while True:
        try:
            specs = active_specs(env)
            if not specs:
                print("[dryrun] no active sleeves in this environment", flush=True)
            run_specs(specs, notifier=notifier, **kwargs)
        except Exception as exc:  # noqa: BLE001 - the loop must outlive one cycle
            print(f"[dryrun] cycle failed: {type(exc).__name__}: {exc}", flush=True)
            traceback.print_exc()
        finally:
            heartbeat.ping()
        time.sleep(max(30.0, float(interval)))


def run_forever(
    interval: float,
    *,
    heartbeat_url: str | None = None,
    heartbeat_every: float = 1800.0,
    notifier: Notifier | None = None,
    **kwargs: Any,
) -> None:
    """Poll loop; each cycle is independent and idempotent."""
    heartbeat = Heartbeat(heartbeat_url, every=heartbeat_every)
    notifier = notifier or Notifier.from_env()
    print(
        f"[dryrun] heartbeat every {heartbeat_every:.0f}s"
        if heartbeat_url
        else "[dryrun] heartbeat off (set HEARTBEAT_URL)",
        flush=True,
    )
    print(
        f"[dryrun] trade alerts: {notifier.describe()}"
        if notifier.enabled
        else "[dryrun] trade alerts off (dryrun replay is silent)",
        flush=True,
    )
    while True:
        try:
            payload = run_cycle(notifier=notifier, **kwargs)
            print(format_status(payload), flush=True)
        except Exception as exc:  # noqa: BLE001 - one bad cycle must not stop the record
            print(f"[dryrun] cycle failed: {type(exc).__name__}: {exc}", flush=True)
            traceback.print_exc()
        finally:
            heartbeat.ping()
        time.sleep(max(30.0, float(interval)))
