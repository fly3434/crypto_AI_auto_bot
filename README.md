# Crypto AI Auto Bot

An experimental AI-assisted Binance USDT-M futures trading bot. It collects market features, asks an OpenRouter model for a structured decision, runs local risk checks, and records every analysis and order decision to JSONL logs.

> Important: this is not financial advice and cannot guarantee doubling an account in one month. The default mode is `dry_run: true`; keep it that way until you have tested with small size or Binance testnet.

## What It Does

- Uses Binance Futures market data: klines, mark price, funding rate, account balance, positions, and orders.
- Computes features such as RSI, ATR, z-score, volume spike, EMA distance, trend regime, realized volatility, funding pressure, and high-risk momentum/liquidation-proxy signals.
- Runs a lightweight parameter search for RSI thresholds, ATR stop, take profit, holding time, leverage, and regime filters.
- Sends a compact market state to OpenRouter and requires a JSON trading decision.
- Adds recent closed-trade memory to each AI decision, including win rate, PnL, and recent losing setups when closed-trade journal records are available.
- Adds a compact crypto news context to each AI decision from cached RSS/JSON sources, using news only as narrative and risk-event context.
- Applies deterministic risk gates before any order is placed.
- Manages existing positions instead of blindly skipping them: same-side signals hold, weak opposite signals hold, confirmed reversals close, and strong risk-approved reversals may close and reopen the opposite side.
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
For a single scan cycle, run:

```bash
python3 -m src.main --config config.yaml --once
```

## Cloud Run + Cloud Scheduler

This project includes a Cloud Run HTTP entrypoint at `POST /run`. Cloud Scheduler should call that endpoint every 6 hours; the container does not need to keep its own timer running.

### 1. Prepare Google Cloud

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud services enable run.googleapis.com cloudbuild.googleapis.com cloudscheduler.googleapis.com secretmanager.googleapis.com
```

Choose a region, for example:

```bash
export REGION=asia-east1
export SERVICE=crypto-ai-auto-bot
export RUNTIME_SA=crypto-bot-runner
export SCHEDULER_SA=crypto-bot-scheduler
```

### 2. Store API keys in Secret Manager

```bash
printf '%s' 'YOUR_OPENROUTER_API_KEY' | gcloud secrets create openrouter-api-key --data-file=-
printf '%s' 'YOUR_BINANCE_API_KEY' | gcloud secrets create binance-api-key --data-file=-
printf '%s' 'YOUR_BINANCE_API_SECRET' | gcloud secrets create binance-api-secret --data-file=-
```

If the secrets already exist, add new versions instead:

```bash
printf '%s' 'YOUR_OPENROUTER_API_KEY' | gcloud secrets versions add openrouter-api-key --data-file=-
printf '%s' 'YOUR_BINANCE_API_KEY' | gcloud secrets versions add binance-api-key --data-file=-
printf '%s' 'YOUR_BINANCE_API_SECRET' | gcloud secrets versions add binance-api-secret --data-file=-
```

### 3. Deploy to Cloud Run

```bash
gcloud iam service-accounts create $RUNTIME_SA \
  --display-name "Crypto bot Cloud Run runtime"

export PROJECT_ID="$(gcloud config get-value project)"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member "serviceAccount:$RUNTIME_SA@$PROJECT_ID.iam.gserviceaccount.com" \
  --role roles/secretmanager.secretAccessor

gcloud run deploy $SERVICE \
  --source . \
  --region $REGION \
  --service-account "$RUNTIME_SA@$PROJECT_ID.iam.gserviceaccount.com" \
  --no-allow-unauthenticated \
  --concurrency 1 \
  --timeout 3300 \
  --memory 1Gi \
  --set-env-vars CONFIG_PATH=config.yaml \
  --set-secrets OPENROUTER_API_KEY=openrouter-api-key:latest,BINANCE_API_KEY=binance-api-key:latest,BINANCE_API_SECRET=binance-api-secret:latest
```

`--concurrency 1` helps avoid two trading cycles running at the same time on the same instance. The service is private; Cloud Scheduler will call it with OIDC authentication.

### 4. Create the scheduler identity

```bash
gcloud iam service-accounts create $SCHEDULER_SA \
  --display-name "Crypto bot Cloud Scheduler caller"

export SERVICE_URL="$(gcloud run services describe $SERVICE --region $REGION --format='value(status.url)')"

gcloud run services add-iam-policy-binding $SERVICE \
  --region $REGION \
  --member "serviceAccount:$SCHEDULER_SA@$PROJECT_ID.iam.gserviceaccount.com" \
  --role roles/run.invoker
```

### 5. Trigger every 6 hours

```bash
gcloud scheduler jobs create http crypto-bot-every-6h \
  --location $REGION \
  --schedule "0 */6 * * *" \
  --time-zone "Asia/Taipei" \
  --uri "$SERVICE_URL/run" \
  --http-method POST \
  --oidc-service-account-email "$SCHEDULER_SA@$PROJECT_ID.iam.gserviceaccount.com" \
  --oidc-token-audience "$SERVICE_URL"
```

You can test it manually:

```bash
gcloud scheduler jobs run crypto-bot-every-6h --location $REGION
gcloud run services logs read $SERVICE --region $REGION --limit 100
```

Before using real Binance futures trading, verify `config.yaml` carefully:

- `mode.dry_run: true` is safest for the first deployment test.
- `binance.base_url: https://testnet.binancefuture.com` uses Binance Futures testnet.
- `binance.base_url: https://fapi.binance.com` uses real Binance USDT-M futures.

## Open Position Handling

When a symbol already has an open position, the bot no longer skips every new signal automatically. It first builds a local position-management plan:

- Same-side signal: keep holding the current position.
- `HOLD` signal: keep holding the current position.
- Opposite signal below `position_management.close_reversal_confidence`: keep holding and avoid churn.
- Opposite signal at or above `position_management.close_reversal_confidence`: close the current position with a reduce-only market order.
- Opposite signal at or above `position_management.reverse_confidence`, with `position_management.reverse_on_strong_signal: true` and risk approval: close the current position, then open the opposite side.

Default thresholds:

```yaml
position_management:
  enabled: true
  close_reversal_confidence: 0.70
  reverse_on_strong_signal: true
  reverse_confidence: 0.80
```

Set `position_management.enabled: false` to restore the older behavior that skips new entries whenever an open position already exists.

## Trade Memory

Before each AI decision, the bot reads recent closed-trade records from `logs/trading_journal.jsonl` and adds a compact `trade_memory` object to the market state. The memory includes recent closed-trade count, win rate, total/average PnL, average PnL percentage, recent examples, and recent losing setups.

The memory reader looks for closed-trade style journal events such as `trade_closed`, `closed_trade`, `exit`, or `position_exit` with fields like `symbol`, `decision`, `features`, `net_pnl`/`realized_pnl`/`pnl`, and optional `pnl_pct`/`exit_reason`. If no closed trades exist yet, it safely reports that memory is unavailable.

```yaml
trade_memory:
  enabled: true
  max_closed_trades: 20
  max_examples: 5
  max_mistakes: 5
```

## News Context

Before each AI decision, the bot can add a compact `news_context` object to the market state. News is fetched once per scan cycle, shared across all configured symbols, deduplicated, scored, filtered to recent items, and cached so fast trading loops do not create fast news scraping.

The effective fetch interval is:

```text
max(mode.loop_seconds / 60, news.min_fetch_interval_minutes)
```

Examples:

```text
5-minute trading trigger -> news refreshes every 30 minutes by default
30-minute trading trigger -> news refreshes every 30 minutes by default
6-hour trading trigger -> news refreshes every 6 hours
```

Default settings:

```yaml
news:
  enabled: true
  min_fetch_interval_minutes: 30
  timeout_seconds: 5
  freshness_hours: 24
  max_items: 10
  max_prompt_items: 10
  cache_path: /tmp/crypto_ai_news_cache.json
```

The prompt instructs the model that news must not be used as a standalone trading signal and must not bypass deterministic risk rules.

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
- position management for existing positions
- reduce-only exits before reversing or closing positions

## Useful Files

- `config.yaml`: symbols, multi-timeframe intervals, model, risk controls, optimizer settings.
- `src/main.py`: loop entrypoint.
- `src/ai_agent.py`: OpenRouter integration and JSON decision schema.
- `src/features.py`: technical feature generation.
- `src/optimizer.py`: local historical parameter search.
- `src/position_manager.py`: existing-position hold, close, and reverse rules.
- `src/risk.py`: deterministic trade approval/rejection.
- `logs/trading_journal.jsonl`: created at runtime.
