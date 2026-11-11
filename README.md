# Aviator paper trading starter

This repository includes a moving-average crossover **paper-trading** example. It fetches public candle data through CCXT and simulates trades locally. It does not submit orders or require API keys. Paper balance is in memory and resets when the process restarts. This is educational software, not financial advice; backtests and simulated performance do not predict future results.

## Setup

Requires Python 3.10+.

```bash
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run a historical simulation

```bash
python trading_bot.py --mode backtest --exchange binance --symbol BTC/USDT --timeframe 1h
```

## Run the paper bot

```bash
python trading_bot.py --mode paper --exchange binance --symbol BTC/USDT --timeframe 1h
```

Stop it with Ctrl+C. Adjust `--fast`, `--slow`, `--balance`, `--fee`, and `--stop-loss` as needed. The bot uses completed candles; market-data availability and supported symbols depend on the exchange. No real-money trading mode is included.
