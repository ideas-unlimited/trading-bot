"""Strategy activation manifests: the only route to a runnable strategy.

promotions/paper/<name>.json and promotions/prod/<name>.json are the gate.
The bot imports nothing a manifest does not name, so a strategy module can
sit on a box and be inert there. Gate checks are defaults: --force records
the override and the reason instead of refusing the owner.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[2]
PROMOTIONS = ROOT / "promotions"
ENVS = ("paper", "prod")
DEFAULT_CAPITAL = 1000.0


class PromotionError(RuntimeError):
    """Refused, malformed or missing promotion."""


def params_hash(params: dict[str, Any] | None) -> str:
    """Stable hash of a strategy parameter set - the identity of an edge."""
    blob = json.dumps(params or {}, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def promotions_dir(env: str = "paper", base: Path | None = None) -> Path:
    return Path(base or PROMOTIONS) / env


def manifest_path(strategy: str, env: str = "paper", base: Path | None = None) -> Path:
    return promotions_dir(env, base) / f"{strategy}.json"


@dataclass(frozen=True)
class Manifest:
    strategy: str
    env: str
    params_hash: str
    symbols: tuple[str, ...]
    timeframe: str
    capital: float
    promoted_at: str
    promoted_by: str = ""
    override: bool = False
    override_reason: str = ""
    checks: tuple[dict, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)
    active: bool = True
    demoted_at: str = ""
    demote_reason: str = ""
    path: Path | None = None

    def to_json(self) -> dict[str, Any]:
        payload = {
            "strategy": self.strategy,
            "env": self.env,
            "params_hash": self.params_hash,
            "symbols": list(self.symbols),
            "timeframe": self.timeframe,
            "capital": self.capital,
            "promoted_at": self.promoted_at,
            "promoted_by": self.promoted_by,
            "override": self.override,
            "override_reason": self.override_reason,
            "checks": list(self.checks),
            "evidence": self.evidence,
            "active": self.active,
            "demoted_at": self.demoted_at,
            "demote_reason": self.demote_reason,
        }
        return payload


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_manifest(payload: Any, *, env: str | None = None, path: Path | None = None) -> Manifest:
    """Validate one manifest object; raise PromotionError with the reason."""
    if not isinstance(payload, dict):
        raise PromotionError(f"{path or 'manifest'}: not a JSON object")
    strategy = str(payload.get("strategy") or "").strip()
    if not strategy:
        raise PromotionError(f"{path or 'manifest'}: missing strategy")
    target = str(payload.get("env") or env or "paper").strip().lower()
    if target not in ENVS:
        raise PromotionError(f"{strategy}: env must be one of {ENVS}, got {target!r}")
    symbols = payload.get("symbols")
    if not isinstance(symbols, list) or not symbols:
        raise PromotionError(f"{strategy}: symbols must be a non-empty list")
    timeframe = str(payload.get("timeframe") or "").strip()
    if not timeframe:
        raise PromotionError(f"{strategy}: missing timeframe")
    try:
        capital = float(payload.get("capital", DEFAULT_CAPITAL))
    except (TypeError, ValueError):
        raise PromotionError(f"{strategy}: capital is not numeric") from None
    checks = payload.get("checks") or []
    evidence = payload.get("evidence") or {}
    if not isinstance(evidence, dict):
        raise PromotionError(f"{strategy}: evidence must be an object")
    return Manifest(
        strategy=strategy,
        env=target,
        params_hash=str(payload.get("params_hash") or ""),
        symbols=tuple(str(s) for s in symbols),
        timeframe=timeframe,
        capital=capital,
        promoted_at=str(payload.get("promoted_at") or ""),
        promoted_by=str(payload.get("promoted_by") or ""),
        override=bool(payload.get("override", False)),
        override_reason=str(payload.get("override_reason") or ""),
        checks=tuple(c for c in checks if isinstance(c, dict)),
        evidence=evidence,
        active=bool(payload.get("active", True)),
        demoted_at=str(payload.get("demoted_at") or ""),
        demote_reason=str(payload.get("demote_reason") or ""),
        path=path,
    )


def load_manifests(env: str = "paper", base: Path | None = None) -> list[Manifest]:
    """Every manifest in one environment, sorted by name. Malformed = loud."""
    folder = promotions_dir(env, base)
    if not folder.exists():
        return []
    out = []
    for path in sorted(folder.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PromotionError(f"{path.name}: invalid JSON ({exc})") from None
        manifest = parse_manifest(payload, env=env, path=path)
        if manifest.env != env:
            raise PromotionError(f"{path.name}: says env={manifest.env}, filed under {env}")
        out.append(manifest)
    return out


def active_manifests(env: str = "paper", base: Path | None = None) -> dict[str, Manifest]:
    return {m.strategy: m for m in load_manifests(env, base) if m.active}


def require(strategy: str, env: str = "paper", base: Path | None = None) -> Manifest:
    """Fail loud when a strategy is not activated in this environment."""
    manifests = active_manifests(env, base)
    if strategy not in manifests:
        known = ", ".join(sorted(manifests)) or "none"
        raise PromotionError(
            f"{strategy} is not activated for {env} (active: {known}); promote it first"
        )
    return manifests[strategy]


def write_manifest(manifest: Manifest, base: Path | None = None) -> Path:
    """Atomic write: temp file in the same directory, then replace."""
    path = manifest_path(manifest.strategy, manifest.env, base)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest.to_json(), indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


# -- the gate ---------------------------------------------------------------
@dataclass(frozen=True)
class GatePolicy:
    min_weeks_live: float = 2.0
    min_fills: int = 1
    max_fill_slippage_bps: float = 30.0
    max_fee_drift_pct: float = 10.0
    max_paper_over_backtest_pct: float = 50.0
    stats_min_roundtrips: int = 30
    max_heartbeat_gap_minutes: float = 60.0


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _num(evidence: dict[str, Any], key: str) -> float | None:
    value = evidence.get(key)
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def paper_entry_checks(evidence: dict[str, Any], *, params_hash_expected: str = "") -> list[dict]:
    """lab -> paper: is there a real backtest behind this, on a legal window?"""
    checks = []
    bars = _num(evidence, "bars")
    window = _num(evidence, "window_days")
    trades = _num(evidence, "trades")
    checks.append(_check("backtest_bars", bool(bars and bars > 0), f"bars={bars or 0:g}"))
    checks.append(_check("backtest_window", bool(window and 0 < window <= 730), f"window_days={window or 0:g}"))
    checks.append(_check("backtest_trades", bool(trades and trades > 0), f"trades={trades or 0:g}"))
    fee = _num(evidence, "fee_bps")
    slip = _num(evidence, "slippage_bps")
    checks.append(_check("costs_modelled", bool(fee is not None and slip is not None and fee >= 0 and slip >= 0),
                          f"fee_bps={fee}, slippage_bps={slip}"))
    if params_hash_expected:
        stated = str(evidence.get("params_hash") or "")
        checks.append(_check("params_hash", stated == params_hash_expected,
                              f"evidence={stated or 'missing'} expected={params_hash_expected}"))
    return checks


def prod_entry_checks(evidence: dict[str, Any], policy: GatePolicy | None = None) -> list[dict]:
    """paper -> prod: did the live paper run behave like the backtest?"""
    policy = policy or GatePolicy()
    checks = []
    weeks = _num(evidence, "weeks_live")
    checks.append(_check("weeks_live", weeks is not None and weeks >= policy.min_weeks_live,
                          f"{weeks if weeks is not None else 'missing'} (need {policy.min_weeks_live:g})"))
    fills = _num(evidence, "fills")
    checks.append(_check("fills", fills is not None and fills >= policy.min_fills,
                          f"{fills if fills is not None else 'missing'} (need {policy.min_fills:g})"))
    dupes = _num(evidence, "duplicate_orders")
    checks.append(_check("duplicate_orders", dupes == 0, f"{dupes if dupes is not None else 'missing'}"))
    rejects = _num(evidence, "unexplained_rejects")
    checks.append(_check("unexplained_rejects", rejects == 0, f"{rejects if rejects is not None else 'missing'}"))
    slip = _num(evidence, "max_fill_slippage_bps")
    checks.append(_check("fill_slippage", slip is not None and slip <= policy.max_fill_slippage_bps,
                          f"{slip if slip is not None else 'missing'} bps (max {policy.max_fill_slippage_bps:g})"))
    drift = _num(evidence, "fee_drift_pct")
    checks.append(_check("fee_drift", drift is not None and abs(drift) <= policy.max_fee_drift_pct,
                          f"{drift if drift is not None else 'missing'}% (max {policy.max_fee_drift_pct:g})"))
    excess = _num(evidence, "paper_over_backtest_pct")
    checks.append(_check("not_beating_backtest", excess is not None and excess <= policy.max_paper_over_backtest_pct,
                          f"{excess if excess is not None else 'missing'}% over backtest (max {policy.max_paper_over_backtest_pct:g})"))
    gap = _num(evidence, "max_heartbeat_gap_minutes")
    checks.append(_check("heartbeat", gap is not None and gap <= policy.max_heartbeat_gap_minutes,
                          f"gap={gap if gap is not None else 'missing'} min (max {policy.max_heartbeat_gap_minutes:g})"))
    return checks


def summarize(checks: Sequence[dict]) -> str:
    return ", ".join(c["name"] for c in checks if not c["ok"]) or "all passed"


def promote(
    strategy: str,
    *,
    env: str = "paper",
    params: dict[str, Any] | None = None,
    symbols: Sequence[str] = (),
    timeframe: str = "4h",
    capital: float = DEFAULT_CAPITAL,
    evidence: dict[str, Any] | None = None,
    actor: str = "",
    force: bool = False,
    reason: str = "",
    base: Path | None = None,
    conn: Any = None,
) -> tuple[Manifest, list[dict], int | None]:
    """Run the gate, write the manifest, audit it. Raises PromotionError."""
    if env not in ENVS:
        raise PromotionError(f"env must be one of {ENVS}, got {env!r}")
    if not symbols:
        raise PromotionError("promote needs at least one symbol")
    evidence = dict(evidence or {})
    digest = params_hash(params)
    checks = (prod_entry_checks(evidence) if env == "prod"
              else paper_entry_checks(evidence, params_hash_expected=digest))
    failed = [c for c in checks if not c["ok"]]
    if failed and not force:
        raise PromotionError("gate failed: " + summarize(checks) + "; use --force --reason ... to override")
    if failed and force and not reason.strip():
        raise PromotionError("--force needs --reason: say why the failure is acceptable")
    manifest = Manifest(
        strategy=strategy,
        env=env,
        params_hash=digest,
        symbols=tuple(str(s) for s in symbols),
        timeframe=timeframe,
        capital=float(capital),
        promoted_at=_now(),
        promoted_by=actor,
        override=bool(failed),
        override_reason=reason,
        checks=tuple(checks),
        evidence=evidence,
        path=manifest_path(strategy, env, base),
    )
    write_manifest(manifest, base)
    db_id = None
    if conn is not None:
        from . import db

        db_id = db.create_promotion(
            conn, strategy=strategy, env=env, action="promote", params_hash=digest,
            capital=float(capital), checks=checks, override=bool(failed),
            reason=reason, actor=actor,
        )
    return manifest, checks, db_id


def demote(
    strategy: str,
    *,
    env: str = "prod",
    reason: str = "",
    actor: str = "",
    base: Path | None = None,
    conn: Any = None,
) -> Manifest:
    """Deactivate a strategy: no new entries; existing stops still managed."""
    path = manifest_path(strategy, env, base)
    if not path.exists():
        raise PromotionError(f"{strategy} has no manifest for {env} ({path})")
    payload = json.loads(path.read_text(encoding="utf-8"))
    manifest = parse_manifest(payload, env=env, path=path)
    demoted = Manifest(**{**manifest.__dict__, "active": False, "demoted_at": _now(),
                          "demote_reason": reason})
    write_manifest(demoted, base)
    if conn is not None:
        from . import db

        db.create_promotion(
            conn, strategy=strategy, env=env, action="demote",
            params_hash=manifest.params_hash, capital=manifest.capital,
            checks=list(manifest.checks), override=False, reason=reason, actor=actor,
        )
    return demoted
