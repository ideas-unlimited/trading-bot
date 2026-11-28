"""Broad universe sweep: every Binance spot pair whose coin clears a $1B
market cap, one year, the same 26 cells the all-coins study used.

Question: does a *market* edge exist once the universe is the whole liquid
market rather than five majors?  The earlier 30-coin sweep
(`allcoins_sweep.py`, `ALLCOINS.md`) found only one broad thing - the 1d
`lab breakout` family, 69-83% of coins positive - on a universe picked by
hand.  This repeats the grid on every >= $1B coin that trades on Binance
spot, so breadth is measured on a universe nobody chose after seeing the
results.

Universe (committed, reproducible): `broad_universe.csv` - CoinGecko
`/coins/markets` top 500 by market cap, filtered to market_cap >= 1e9, then
to Binance spot `SYMBOL/USDT` pairs that are TRADING.  Stablecoins and
wrapped/restaked duplicates are dropped as such (they are the same dollar or
the same ETH twice, not a strategy candidate); `--refresh` rewrites the CSV.

Protocol: identical to `allcoins_sweep.py` - signal on the close, fill next
open, 10 bps + 5 bps a side, 3% risk capped at 95% of equity,
`max_drawdown_stop=0.99`, long only, one year measured after 90 days of
warmup.  Cells with fewer than 8 trades or under 330 measured days are kept
in the CSV but excluded from the tables.

    python research/broad_sweep.py --refresh   # rebuild universe, run sweep
    python research/broad_sweep.py             # run sweep from the CSV
    python research/broad_sweep.py --report    # tables from broad_stage1.csv
"""

from __future__ import annotations

import json
import sys
import traceback
import urllib.request
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

from bot.backtest import bars_to_frame  # noqa: E402
from bot.scanlib import load_scan_bars  # noqa: E402
from allcoins_sweep import LOAD_DAYS, WINDOW_DAYS, grids, run_one  # noqa: E402

UNIVERSE_CSV = HERE / "broad_universe.csv"
STAGE1_CSV = HERE / "broad_stage1.csv"
MIN_MCAP = 1e9
MIN_TRADES = 8
MIN_DAYS = 330

# Same dollar or the same underlying twice - not a strategy candidate.
STABLES = {
    "usdt", "usdc", "dai", "fdusd", "tusd", "usde", "usds", "pyusd", "busd",
    "frax", "lusd", "usdd", "gusd", "rlusd", "usd1", "usd0", "susds", "usdtb",
    "usdf", "xusd", "usdx", "usd+", "usdy", "bfusd", "usdg", "ousd",
}
WRAPPED = {
    "wbtc", "weth", "wsteth", "weeth", "cbbtc", "reth", "cbeth", "meth",
    "steth", "sfrxeth", "rseth", "ezeth", "susde", "msol", "bsol", "jitosol",
    "jitusol", "clbtc", "tbtc", "wbeth", "sweth",
}


def _get_json(url: str) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def build_universe() -> pd.DataFrame:
    """CoinGecko market caps x Binance spot pairs, with a skip reason each."""
    markets: list[dict] = []
    for page in (1, 2):
        markets += _get_json(
            "https://api.coingecko.com/api/v3/coins/markets"
            f"?vs_currency=usd&order=market_cap_desc&per_page=250&page={page}"
        )
    info = _get_json("https://api.binance.com/api/v3/exchangeInfo")
    spot = {
        s["symbol"] for s in info["symbols"]
        if s["quoteAsset"] == "USDT" and s["status"] == "TRADING"
        and s.get("isSpotTradingAllowed")
    }
    rows = []
    for rank, m in enumerate(markets, start=1):
        mcap = m.get("market_cap") or 0.0
        sym = str(m.get("symbol") or "").upper()
        if not sym or mcap < MIN_MCAP:
            continue
        pair = f"{sym}/USDT"
        if sym.lower() in STABLES:
            reason = "stablecoin"
        elif sym.lower() in WRAPPED:
            reason = "wrapped-duplicate"
        elif f"{sym}USDT" not in spot:
            reason = "no-binance-spot"
        else:
            reason = ""
        rows.append({"rank": rank, "symbol": sym, "pair": pair,
                     "mcap_usd": mcap, "skip": reason})
    out = pd.DataFrame(rows)
    out.to_csv(UNIVERSE_CSV, index=False)
    return out


def universe_pairs() -> list[str]:
    if not UNIVERSE_CSV.exists():
        build_universe()
    df = pd.read_csv(UNIVERSE_CSV)
    ok = df[df.skip.fillna("").eq("")]
    return list(ok.pair)


def main() -> None:
    args = sys.argv[1:]
    window = WINDOW_DAYS
    load_days = LOAD_DAYS
    tag = ""
    if "--days" in args:
        window = int(args[args.index("--days") + 1])
        load_days = window + 90
    if "--tag" in args:
        tag = "_" + args[args.index("--tag") + 1]
    stage1 = STAGE1_CSV if not tag else STAGE1_CSV.with_name(f"broad_stage1{tag}.csv")
    if "--refresh" in args:
        u = build_universe()
        keep = int(u.skip.fillna("").eq("").sum())
        print(f"universe rebuilt: {len(u)} coins >= $1B, {keep} with a Binance spot pair")
    if "--report" in args:
        report(pd.read_csv(STAGE1_CSV))
        return
    pairs = universe_pairs()
    print(f"{len(pairs)} pairs, charts 4h+1d, window {window}d", flush=True)
    rows: list[dict] = []
    skipped = []
    for tf in ("4h", "1d"):
        bpy = 365.0 * 24.0 / (4.0 if tf == "4h" else 24.0)
        for pair in pairs:
            try:
                bars = load_scan_bars(pair, tf, source="binance", days=load_days)
            except Exception as exc:
                skipped.append((pair, tf, type(exc).__name__, str(exc)[:100]))
                continue
            if len(bars) < 200:
                skipped.append((pair, tf, "short", f"{len(bars)} bars"))
                continue
            df = bars_to_frame(bars)
            last = df["ts"].iloc[-1]
            start_ts = last - pd.Timedelta(days=window)
            win = df[df["ts"] >= start_ts]
            window_days = float((win["ts"].iloc[-1] - win["ts"].iloc[0]).days)
            bh = float(win["close"].iloc[-1] / win["open"].iloc[0] - 1.0)
            built = grids(tf)
            for label, strat in built:
                try:
                    if hasattr(strat, "reset"):
                        strat.reset()
                    m = run_one(strat, df, bpy, start_ts)
                except Exception:
                    print(f"  !! {tf} {pair} {label}", flush=True)
                    traceback.print_exc()
                    continue
                dd = m.get("dd")
                ok_dd = isinstance(dd, float) and dd < 0 and m["ret"] == m["ret"]
                m["calmar"] = (m["ret"] / abs(dd)) if ok_dd else float("nan")
                m.update(pair=pair, tf=tf, config=label, bh=bh,
                         window_days=window_days)
                rows.append(m)
            pd.DataFrame(rows).to_csv(stage1, index=False)
            print(f"  {tf} {pair:12} {len(built):2} cells  window {window_days:.0f}d",
                  flush=True)
    d = pd.DataFrame(rows)
    print(f"rows={len(d)} written to {stage1}")
    if skipped:
        print("skipped:", skipped)
    report(d)


def report(d: pd.DataFrame) -> None:
    ok = d[(d.trades >= MIN_TRADES) & (d.window_days >= MIN_DAYS) & d.ret.notna()]
    print(f"{len(ok)} cells ranked of {len(d)}")
    cols = ["pair", "tf", "config", "ret", "dd", "calmar", "trades", "wins",
            "is_ret", "oos_ret", "bh"]
    fmt = ["ret", "dd", "calmar", "is_ret", "oos_ret", "bh"]
    for name, key in (("return", "ret"), ("Calmar", "calmar")):
        show = ok.sort_values(key, ascending=False).head(20)[cols].copy()
        for c in fmt:
            show[c] = show[c].map(lambda v: "" if pd.isna(v) else f"{v:+.2f}")
        print(f"=== top 20 by {name} ===")
        print(show.to_string(index=False))
    print("=== breadth per config: share of coins positive (>= 10 coins) ===")
    g = ok.groupby(["tf", "config"]).agg(
        coins=("ret", "size"), positive=("ret", lambda s: (s > 0).mean()),
        median=("ret", "median"), med_dd=("dd", "median")).reset_index()
    g = g[g.coins >= 10].sort_values(["positive", "median"], ascending=False)
    for c in ("positive", "median", "med_dd"):
        g[c] = g[c].map(lambda v: f"{v:.0%}" if c == "positive" else f"{v:+.1%}")
    print(g.head(18).to_string(index=False))


if __name__ == "__main__":
    main()