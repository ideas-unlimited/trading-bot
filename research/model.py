
"""Logistic conviction model + purged walk-forward evaluation."""
from __future__ import annotations

import numpy as np
import pandas as pd

from lab import FEATURES, SYMBOLS

BARS_PER_YEAR = {"1d": 365.0, "4h": 365.0 * 6}
BARS_PER_DAY = {"1d": 1.0, "4h": 6.0}


def _standardise(X: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mu = np.nanmean(X, axis=0)
    sd = np.nanstd(X, axis=0)
    sd[sd == 0] = 1.0
    return mu, sd, (X - mu) / sd


def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 1.0,
                 iters: int = 40) -> np.ndarray:
    """L2-regularised logistic regression by Newton/IRLS (no sklearn needed)."""
    mu, sd, Z = _standardise(X)
    Z = np.nan_to_num(Z, nan=0.0, posinf=0.0, neginf=0.0)
    Z = np.column_stack([np.ones(len(Z)), Z])
    w = np.zeros(Z.shape[1])
    ridge = np.eye(Z.shape[1]) * l2
    ridge[0, 0] = 0.0
    for _ in range(iters):
        z = Z @ w
        p = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
        grad = Z.T @ (p - y) + ridge @ w
        hess = (Z * (p * (1 - p))[:, None]).T @ Z + ridge + 1e-6 * np.eye(len(w))
        try:
            step = np.linalg.solve(hess, grad)
        except np.linalg.LinAlgError:
            break
        w -= step
        if np.max(np.abs(step)) < 1e-6:
            break
    return mu, sd, w


def predict(bundle: tuple, X: np.ndarray) -> np.ndarray:
    mu, sd, w = bundle
    Z = np.nan_to_num((X - mu) / sd, nan=0.0, posinf=0.0, neginf=0.0)
    Z = np.column_stack([np.ones(len(Z)), Z])
    return 1.0 / (1.0 + np.exp(-np.clip(Z @ w, -30, 30)))


def panel(data: dict[str, pd.DataFrame], tf: str) -> pd.DataFrame:
    frames = []
    for s in SYMBOLS:
        d = data[s].copy()
        d["sym"] = s
        frames.append(d[["ts", "sym"] + FEATURES + ["y", "r_mult", "bars_held",
                                                   "entry", "stop", "target",
                                                   "open", "high", "low", "close",
                                                   "atr" if "atr" in d else "atr_pct"]])
    p = pd.concat(frames, ignore_index=True)
    return p.sort_values(["ts", "sym"]).reset_index(drop=True)


def walk_forward(p: pd.DataFrame, tf: str, *, horizon: int = 40,
                 min_train_days: int = 730, block_days: int = 90,
                 l2: float = 1.0, features: list[str] | None = None) -> pd.DataFrame:
    """Expanding-window OOS predictions. Purged by the label horizon."""
    feats = features or FEATURES
    p = p.dropna(subset=["y"]).copy()
    p["y"] = p["y"].astype(int)
    xall = p[feats].to_numpy(float)
    yall = p["y"].to_numpy(float)
    ts = p["ts"].to_numpy()
    order = np.argsort(ts)
    starts = pd.date_range(p["ts"].min() + pd.Timedelta(days=min_train_days),
                           p["ts"].max(), freq=f"{block_days}D")
    prob = np.full(len(p), np.nan)
    purge = pd.Timedelta(days=(horizon + 2) / BARS_PER_DAY[tf])

    for k, start in enumerate(starts):
        end = start + pd.Timedelta(days=block_days)
        train = (ts < np.datetime64(start - purge))
        test = (ts >= np.datetime64(start)) & (ts < np.datetime64(end))
        if train.sum() < 500 or test.sum() == 0:
            continue
        bundle = fit_logistic(xall[train], yall[train], l2=l2)
        prob[test] = predict(bundle, xall[test])
        p.loc[test, "block"] = k
    p["p"] = prob
    return p.dropna(subset=["p"]).sort_values(["ts", "sym"]).reset_index(drop=True)


def walk_forward_bucket(p: pd.DataFrame, tf: str, *, horizon: int = 25,
                        min_train_days: int = 540, lookback_days: int = 730,
                        keys=("regime", "setup", "vol")) -> pd.DataFrame:
    """Adaptive empirical win rate: trailing bucket mean, beta-smoothed.

    No parametric model, so it cannot overfit 27 coefficients to a regime
    that never repeats.  It only assumes the recent past is the best guide.
    """
    p = p.dropna(subset=["y"]).copy()
    p["y"] = p["y"].astype(int)
    ts = p["ts"].to_numpy()
    p["bucket"] = p[list(keys)].astype(str).agg("|".join, axis=1)
    starts = pd.date_range(p["ts"].min() + pd.Timedelta(days=min_train_days),
                           p["ts"].max(), freq="30D")
    prob = np.full(len(p), np.nan)
    for start in starts:
        end = start + pd.Timedelta(days=30)
        lo = start - pd.Timedelta(days=lookback_days)
        tr = (ts >= np.datetime64(lo)) & (ts < np.datetime64(start - pd.Timedelta(days=(horizon + 2) / 4.0)))
        te = (ts >= np.datetime64(start)) & (ts < np.datetime64(end))
        if tr.sum() < 200 or te.sum() == 0:
            continue
        g = p[tr].groupby("bucket")["y"].agg(["sum", "size"])
        # Beta(prior_mean*prior_n, (1-mean)*prior_n) smoothing.
        prior_n, prior_m = 25.0, 0.5
        rate = (g["sum"] + prior_m * prior_n) / (g["size"] + prior_n)
        prob[te] = p.loc[te, "bucket"].map(rate).fillna(prior_m).to_numpy()
    p["p"] = prob
    return p.dropna(subset=["p"]).sort_values(["ts", "sym"]).reset_index(drop=True)


def auc(y: np.ndarray, p: np.ndarray) -> float:
    y = np.asarray(y, float); p = np.asarray(p, float)
    pos, neg = p[y == 1], p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    return float((pos[:, None] > neg[None, :]).mean() + 0.5 * (pos[:, None] == neg[None, :]).mean())


def reliability(p: pd.DataFrame, bins: int = 8) -> pd.DataFrame:
    q = pd.qcut(p["p"], bins, labels=False, duplicates="drop")
    g = p.groupby(q).agg(pred=("p", "mean"), actual=("y", "mean"), n=("y", "size"))
    return g.reset_index(drop=True)
