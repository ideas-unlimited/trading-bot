"""Conviction model v2: walk-forward GBM P(profitable swing trade).

Label: long entered at NEXT bar's open (the earliest possible fill),
stop = stop_mult*ATR below entry, time exit after `horizon` bars.
y = 1 if exit PnL > 0, else 0.

Model: HistGradientBoostingClassifier on lab.FEATURES (NaN-tolerant).
Walk-forward: expanding window, refit every REFIT bars, min train MIN_TRAIN.
Calibration: reliability mapping (bin observed rate vs predicted) fit on the
last CAL_FRAC of each training window, applied to that fold's predictions.

Everything is past-only by construction: features at bar i use bars <= i;
the label starts at bar i+1's open.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

import lab
from lab import FEATURES, SYMBOLS

LABEL_KW = {"4h": {"stop_mult": 2.0, "horizon": 40}, "1d": {"stop_mult": 2.0, "horizon": 20}}
REFIT = {"4h": 240, "1d": 45}
MIN_TRAIN = {"4h": 2000, "1d": 500}
CAL_FRAC = 0.25
HALF_LIFE = {"4h": 1460, "1d": 365}  # ~1 year sample-weight decay
DATA_YEARS = 3  # hard limit: strategy research uses at most the last 3 years


def _gbm():
    return HistGradientBoostingClassifier(
        max_iter=250, learning_rate=0.06, max_leaf_nodes=15, min_samples_leaf=80,
        l2_regularization=2.0, early_stopping=False, random_state=7,
    )


def fit_calibration(raw: np.ndarray, y: np.ndarray, n_bins: int = 10):
    ok = np.isfinite(raw) & np.isfinite(y)
    r, yy = raw[ok], y[ok]
    if len(r) < 300:
        return None
    qs = np.percentile(r, np.linspace(0, 100, n_bins + 1))
    qs[0], qs[-1] = -0.01, 1.01
    ctr, obs = [], []
    for i in range(n_bins):
        m = (r >= qs[i]) & (r < qs[i + 1])
        if m.sum() >= 25:
            ctr.append((qs[i] + qs[i + 1]) / 2)
            obs.append(yy[m].mean())
    if len(obs) < 4:
        return None
    o = np.array(obs)
    for _ in range(5):  # pool-adjacent-violators, light
        for i in range(len(o) - 1):
            if o[i] > o[i + 1]:
                o[i] = o[i + 1] = (o[i] + o[i + 1]) / 2
    return np.array(ctr), o


def apply_calibration(p: np.ndarray, cal) -> np.ndarray:
    if cal is None:
        return p
    ctr, obs = cal
    return np.clip(np.interp(p, ctr, obs), 0.01, 0.99)


def build_dataset(tf: str) -> dict[str, pd.DataFrame]:
    data = lab.build(tf, stop_mult=LABEL_KW[tf]["stop_mult"], rr=2.0,
                     horizon=LABEL_KW[tf]["horizon"])
    cutoff = max(d["ts"].max() for d in data.values()) - pd.DateOffset(years=DATA_YEARS)
    out = {}
    for s in data:
        d = data[s][data[s]["ts"] >= cutoff].reset_index(drop=True)
        d["y_pos"] = (d["r_mult"] > 0).astype(float)
        d.loc[d["r_mult"].isna(), "y_pos"] = np.nan
        out[s] = d
    return out


def walk_forward(tf: str, symbols: tuple[str, ...] = SYMBOLS) -> dict[str, pd.DataFrame]:
    data = build_dataset(tf)
    ts_union = pd.DatetimeIndex(sorted(set().union(*[set(data[s]["ts"]) for s in symbols])))
    n_t = len(ts_union)

    X, y, rmult = [], [], []
    for s in symbols:
        d = data[s].set_index("ts").reindex(ts_union)
        data[s] = d
        X.append(d[FEATURES].to_numpy(dtype=np.float64))
        y.append(d["y_pos"].to_numpy(dtype=np.float64))
        rmult.append(d["r_mult"].to_numpy())
    X = np.vstack(X)
    y = np.concatenate(y)
    rmult = np.concatenate(rmult)

    min_train, refit, hl = MIN_TRAIN[tf], REFIT[tf], HALF_LIFE[tf]
    model, cal = _gbm(), None
    p_raw = np.full(len(y), np.nan)
    p_cal = np.full(len(y), np.nan)
    train_end = min_train
    fitted_until = 0

    while train_end < n_t:
        train_mask = np.zeros(len(y), dtype=bool)
        for k in range(len(symbols)):
            train_mask[k * n_t: k * n_t + train_end] = True
        train_mask &= np.isfinite(y)
        if train_mask.sum() > 500:
            ages = train_end - np.tile(np.arange(n_t), len(symbols))[train_mask]
            w = np.power(0.5, ages / hl)
            model.fit(X[train_mask], y[train_mask], sample_weight=w)
            c0 = int(train_end * (1 - CAL_FRAC))
            cal_mask = np.zeros(len(y), dtype=bool)
            for k in range(len(symbols)):
                cal_mask[k * n_t + c0: k * n_t + train_end] = True
            cal_mask &= np.isfinite(y)
            cal = fit_calibration(model.predict_proba(X[cal_mask])[:, 1], y[cal_mask])
        pred_hi = min(train_end + refit, n_t)
        for k in range(len(symbols)):
            sl = slice(k * n_t + fitted_until, k * n_t + pred_hi)
            pr = model.predict_proba(X[sl])[:, 1]
            p_raw[sl] = pr
            p_cal[sl] = apply_calibration(pr, cal)
        fitted_until = pred_hi
        train_end += refit

    out = {}
    for k, s in enumerate(symbols):
        sl = slice(k * n_t, (k + 1) * n_t)
        pr, pc = p_raw[sl].copy(), p_cal[sl].copy()
        pr[:min_train] = np.nan
        pc[:min_train] = np.nan
        out[s] = pd.DataFrame({
            "ts": ts_union, "p_raw": pr, "p_cal": pc, "y_pos": y[sl], "r_mult": rmult[sl],
        }).iloc[min_train:].reset_index(drop=True)
    return out


def auc(y: np.ndarray, p: np.ndarray) -> float:
    ok = np.isfinite(y) & np.isfinite(p)
    y, p = y[ok], p[ok]
    pos, neg = p[y == 1], p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    return float((pos[:, None] > neg[None, :]).mean() + 0.5 * (pos[:, None] == neg[None, :]).mean())


def reliability(p: np.ndarray, y: np.ndarray, bins: int = 10) -> pd.DataFrame:
    ok = np.isfinite(p) & np.isfinite(y)
    q = pd.qcut(p[ok], bins, labels=False, duplicates="drop")
    return (pd.DataFrame({"b": q, "p": p[ok], "y": y[ok]}).groupby("b")
            .agg(n=("y", "size"), pred=("p", "mean"), actual=("y", "mean")))


if __name__ == "__main__":
    for tf in ("1d", "4h"):
        oos = walk_forward(tf)
        allp = np.concatenate([oos[s]["p_cal"].to_numpy() for s in oos])
        allr = np.concatenate([oos[s]["p_raw"].to_numpy() for s in oos])
        ally = np.concatenate([oos[s]["y_pos"].to_numpy() for s in oos])
        base = np.nanmean(ally)
        print(f"=== {tf}: OOS n={np.isfinite(allp).sum()} base={base:.3f} "
              f"AUC_raw={auc(ally, allr):.3f} AUC_cal={auc(ally, allp):.3f}")
        print(reliability(allp, ally).round(3).to_string())
        pd.concat([oos[s].assign(sym=s) for s in oos]).to_csv(
            f"runs/conviction_oos_{tf}.csv", index=False)
