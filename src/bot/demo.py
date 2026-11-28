"""Demo trading: replay bars through the *same* engine, slowly, and narrate.

This is the bridge between a backtest and a deploy bot.  It exercises the full
loop (market data -> signal -> sizing -> fills -> state) against simulated
money only.  Nothing here can place a real order, by construction.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from typing import TextIO

from .backtest import Engine, bars_to_frame
from .config import BotConfig
from .data.feed import Bar
from .strategy.base import Strategy


class DemoTrader:
    def __init__(
        self,
        bars: list[Bar],
        strategy: Strategy,
        config: BotConfig | None = None,
        *,
        delay: float = 0.0,
        every: int = 50,
        stream: TextIO | None = None,
        clock: Callable[[float], None] = time.sleep,
    ) -> None:
        self.engine = Engine(bars_to_frame(bars), strategy, config)
        self.delay = max(0.0, delay)
        self.every = max(1, every)
        self.stream = stream if stream is not None else sys.stdout
        self.clock = clock

    def _emit(self, text: str) -> None:
        print(text, file=self.stream, flush=True)

    def run(self) -> None:
        self._emit("[demo] simulated trading started - no real orders are sent")
        fills_seen = 0
        while self.engine.step():
            broker = self.engine.broker
            # Report new fills as they happen.
            while fills_seen < len(broker.fills):
                f = broker.fills[fills_seen]
                self._emit(
                    f"[demo] {f.ts:%Y-%m-%d %H:%M} {f.side.upper():<4} "
                    f"{f.qty:.6f} @ {f.price:.4f} fee={f.fee:.4f} :: {f.reason}"
                )
                fills_seen += 1

            n = self.engine.i
            if n % self.every == 0:
                row = self.engine.df.iloc[n - 1]
                close = float(row["close"])
                equity = broker.equity(close)
                pos = f"{broker.qty:.6f}" if broker.in_position else "flat"
                self._emit(
                    f"[demo] {row['ts']:%Y-%m-%d %H:%M} close={close:.4f} "
                    f"position={pos} equity={equity:,.2f}"
                )
            if self.delay:
                self.clock(self.delay)

        self.engine.finish()
        result = self.engine.result()
        self._emit(
            f"[demo] finished: {len(result.trades)} trades, "
            f"final equity {result.final_equity:,.2f} "
            f"({(result.final_equity / result.initial_equity - 1) * 100:+.2f}%)"
        )
        if result.halted_reason:
            self._emit(f"[demo] halted: {result.halted_reason}")
