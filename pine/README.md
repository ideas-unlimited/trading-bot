# pine/ - TradingView ports

Pine Script v5 ports of two strategies, for TradingView's Strategy Tester
and paper trading:

| file | mirrors |
| --- | --- |
| `sma_cross.pine` | `src/bot/strategy/sma_cross.py` |
| `rsi_meanrev.pine` | `src/bot/strategy/rsi_meanrev.py` |

Defaults match the Python strategies, orders fill at the next bar's open and
the commission/slippage inputs are set to 10 bps / 5 bps, so Strategy Tester
results should track `python -m bot backtest` closely rather than exactly.
The ports are a companion, not the reference implementation: when the two
disagree, the Python engine wins.

## Getting bars and routing alerts

    python -m bot tv-bars --symbol BTCUSDT --exchange BINANCE --interval 1h --n-bars 5000 --out data/tv.csv
    python -m bot serve-alerts --port 8788 --token MYSECRET

`tv-bars` needs the unofficial `tradingview-datafeed` package; `serve-alerts`
mirrors TradingView paper-fill webhook alerts into this engine's risk rules
on simulation money. Webhook alerts require a paid TradingView plan.

One warning carried over from the research: a TradingView port that drifts
from `src/bot/strategy/` silently produces a second, unmeasured strategy.
Change the Python class first, then the port.
