# Crypto AI Auto Bot

An experimental AI-assisted Binance USDT-M futures trading bot. It collects market features, asks an OpenRouter model for a structured decision, runs local risk checks, and records every analysis and order decision to JSONL logs.

> Important: this is not financial advice and cannot guarantee doubling an account in one month. The default mode is `dry_run: true`; keep it that way until you have tested with small size or Binance testnet.

## What It Does

- Uses Binance Futures market data: klines, mark price, funding rate, account balance, positions, and orders.
- Computes features such as RSI, ATR, z-score, volume spike, EMA distance, trend regime, realized volatility, funding pressure, and high-risk momentum/liquidation-proxy signals.
- Runs a lightweight parameter search for RSI thresholds, ATR stop, take profit, holding time, leverage, and regime filters.
- Sends a compact market state to OpenRouter and requires a JSON trading decision.
- Applies deterministic risk gates before any order is placed.
- Logs every AI decision, feature snapshot, risk rejection, and order result to `logs/trading_journal.jsonl`.

## Quick Start

```bash
python3 -m pip install --user -r requirements.txt
cp .env.example .env
```

Edit `.env` and `config.yaml`, then run:

```bash
python3 -m src.main --config config.yaml
```

By default the bot runs in dry-run mode and will not place live orders.

## Environment Variables

```bash
OPENROUTER_API_KEY=...
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
```

## Safety Defaults

The design allows aggressive objectives, but the runtime still enforces:

- dry-run by default
- maximum account risk per trade
- maximum leverage
- cooldown after stop loss
- max daily loss circuit breaker
- model output validation
- reduce-only exits for closing positions

## Useful Files

- `config.yaml`: symbols, intervals, model, risk controls, optimizer settings.
- `src/main.py`: loop entrypoint.
- `src/ai_agent.py`: OpenRouter integration and JSON decision schema.
- `src/features.py`: technical feature generation.
- `src/optimizer.py`: local historical parameter search.
- `src/risk.py`: deterministic trade approval/rejection.
- `logs/trading_journal.jsonl`: created at runtime.
