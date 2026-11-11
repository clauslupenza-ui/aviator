"""Educational moving-average crossover bot. Paper trading only; never submits orders."""

from __future__ import annotations

import argparse
import logging
import time
from typing import Any

import ccxt
import pandas as pd

LOG = logging.getLogger("trading_bot")
COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def fetch_ohlcv(exchange: Any, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
    """Fetch public OHLCV data. No exchange keys or private endpoints are used."""
    rows = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    if not rows:
        raise RuntimeError("The exchange returned no candle data.")
    frame = pd.DataFrame(rows, columns=COLUMNS)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
    return frame


def add_signals(frame: pd.DataFrame, fast: int, slow: int) -> pd.DataFrame:
    """Mark bullish (1) or non-bullish (0) moving-average regimes."""
    if fast < 1 or slow < 2 or fast >= slow:
        raise ValueError("Periods must satisfy 1 <= fast < slow.")
    result = frame.copy()
    result["fast_ma"] = result["close"].rolling(fast).mean()
    result["slow_ma"] = result["close"].rolling(slow).mean()
    result["signal"] = (result["fast_ma"] > result["slow_ma"]).astype(int)
    result.loc[result["slow_ma"].isna(), "signal"] = 0
    return result


def backtest(
    frame: pd.DataFrame,
    initial_balance: float = 1000.0,
    fee_rate: float = 0.001,
    stop_loss_pct: float = 5.0,
) -> dict[str, Any]:
    """Backtest long-only signals on candles; intrabar stops fill at stop price."""
    if initial_balance <= 0 or not 0 <= fee_rate < 1 or stop_loss_pct <= 0:
        raise ValueError("Balance must be positive, fee in [0, 1), and stop loss positive.")

    cash = initial_balance
    amount = 0.0
    entry_price = 0.0
    trades: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []

    for i in range(1, len(frame)):
        row = frame.iloc[i]
        previous_signal = int(frame.iloc[i - 1]["signal"])
        signal = int(row["signal"])
        price = float(row["close"])

        if amount > 0 and float(row["low"]) <= entry_price * (1 - stop_loss_pct / 100):
            fill = entry_price * (1 - stop_loss_pct / 100)
            cash = amount * fill * (1 - fee_rate)
            trades.append({"timestamp": row["timestamp"], "type": "STOP-LOSS SELL", "price": fill, "amount": amount, "balance_after": cash})
            amount, entry_price = 0.0, 0.0
        elif amount > 0 and previous_signal == 1 and signal == 0:
            cash = amount * price * (1 - fee_rate)
            trades.append({"timestamp": row["timestamp"], "type": "SELL", "price": price, "amount": amount, "balance_after": cash})
            amount, entry_price = 0.0, 0.0

        if amount == 0 and cash > 0 and previous_signal == 0 and signal == 1:
            entry_price = price
            amount = cash / (price * (1 + fee_rate))
            cash -= amount * price * (1 + fee_rate)
            trades.append({"timestamp": row["timestamp"], "type": "BUY", "price": price, "amount": amount, "balance_after": cash})

        equity_curve.append({"timestamp": row["timestamp"], "equity": cash + amount * price})

    if amount > 0 and len(frame):
        last = frame.iloc[-1]
        price = float(last["close"])
        cash = amount * price * (1 - fee_rate)
        trades.append({"timestamp": last["timestamp"], "type": "SELL (forced close)", "price": price, "amount": amount, "balance_after": cash})

    return {
        "initial_balance": initial_balance,
        "final_balance": cash,
        "total_return_pct": (cash / initial_balance - 1) * 100,
        "total_trades": len(trades),
        "trades": trades,
        "equity_curve": equity_curve,
    }


class PaperBroker:
    """In-memory simulator; balances reset when the program restarts."""

    def __init__(self, balance: float, fee_rate: float, stop_loss_pct: float):
        self.cash = balance
        self.amount = 0.0
        self.entry_price = 0.0
        self.fee_rate = fee_rate
        self.stop_loss_pct = stop_loss_pct

    def process(self, row: pd.Series, previous_signal: int, signal: int) -> None:
        price = float(row["close"])
        if self.amount > 0 and float(row["low"]) <= self.entry_price * (1 - self.stop_loss_pct / 100):
            fill = self.entry_price * (1 - self.stop_loss_pct / 100)
            self.cash = self.amount * fill * (1 - self.fee_rate)
            LOG.info("PAPER STOP-LOSS SELL price=%.6f balance=%.2f", fill, self.cash)
            self.amount, self.entry_price = 0.0, 0.0
        elif self.amount > 0 and previous_signal == 1 and signal == 0:
            self.cash = self.amount * price * (1 - self.fee_rate)
            LOG.info("PAPER SELL price=%.6f balance=%.2f", price, self.cash)
            self.amount, self.entry_price = 0.0, 0.0

        if self.amount == 0 and self.cash > 0 and previous_signal == 0 and signal == 1:
            self.entry_price = price
            self.amount = self.cash / (price * (1 + self.fee_rate))
            self.cash -= self.amount * price * (1 + self.fee_rate)
            LOG.info("PAPER BUY price=%.6f amount=%.8f", price, self.amount)

        LOG.info("Candle %s close=%.6f equity=%.2f", row["timestamp"], price, self.cash + self.amount * price)


def run_paper(args: argparse.Namespace) -> None:
    exchange_class = getattr(ccxt, args.exchange, None)
    if exchange_class is None:
        raise ValueError(f"Unknown ccxt exchange: {args.exchange}")
    exchange = exchange_class({"enableRateLimit": True})
    broker = PaperBroker(args.balance, args.fee, args.stop_loss)
    last_timestamp = None
    limit = max(args.limit, args.slow + 3)
    interval = max(5, min(30, exchange.parse_timeframe(args.timeframe) / 10))

    LOG.warning("PAPER MODE ONLY — simulated orders; no exchange orders will be sent.")
    while True:
        try:
            frame = fetch_ohlcv(exchange, args.symbol, args.timeframe, limit)
            # Exchanges usually include the currently forming candle as the final row.
            closed = add_signals(frame.iloc[:-1].copy(), args.fast, args.slow)
            if len(closed) >= 2:
                row = closed.iloc[-1]
                if row["timestamp"] != last_timestamp:
                    broker.process(row, int(closed.iloc[-2]["signal"]), int(row["signal"]))
                    last_timestamp = row["timestamp"]
            time.sleep(interval)
        except KeyboardInterrupt:
            LOG.info("Stopped. Paper balance: %.2f", broker.cash + broker.amount * broker.entry_price)
            break
        except (ccxt.BaseError, RuntimeError, ValueError) as error:
            LOG.exception("Polling failed: %s", error)
            time.sleep(max(interval, 15))


def main() -> None:
    parser = argparse.ArgumentParser(description="Moving-average crossover backtest or paper bot (no live orders).")
    parser.add_argument("--mode", choices=("backtest", "paper"), default="backtest")
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--fast", type=int, default=10)
    parser.add_argument("--slow", type=int, default=30)
    parser.add_argument("--balance", type=float, default=1000.0)
    parser.add_argument("--fee", type=float, default=0.001)
    parser.add_argument("--stop-loss", type=float, default=5.0, help="Stop-loss percentage, e.g. 5 for 5%%")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.balance <= 0 or not 0 <= args.fee < 1 or args.stop_loss <= 0:
        parser.error("--balance must be positive, --fee in [0, 1), and --stop-loss positive")
    if args.fast < 1 or args.slow <= args.fast:
        parser.error("Periods must satisfy 1 <= --fast < --slow")

    exchange_class = getattr(ccxt, args.exchange, None)
    if exchange_class is None:
        parser.error(f"Unknown ccxt exchange: {args.exchange}")
    exchange = exchange_class({"enableRateLimit": True})

    if args.mode == "paper":
        run_paper(args)
        return

    frame = add_signals(fetch_ohlcv(exchange, args.symbol, args.timeframe, max(args.limit, args.slow + 3)), args.fast, args.slow)
    # Exclude the unfinished latest candle from the backtest.
    result = backtest(frame.iloc[:-1].copy(), args.balance, args.fee, args.stop_loss)
    print(f"Mode: BACKTEST (historical simulation only)\nSymbol: {args.symbol} | timeframe: {args.timeframe}")
    print(f"Initial balance: {result['initial_balance']:.2f} USDT")
    print(f"Final balance:   {result['final_balance']:.2f} USDT")
    print(f"Return:          {result['total_return_pct']:.2f}%")
    print(f"Trades:          {result['total_trades']}")
    for trade in result["trades"][-5:]:
        print(f"{trade['timestamp']} | {trade['type']} | price={trade['price']:.6f}")


if __name__ == "__main__":
    main()
