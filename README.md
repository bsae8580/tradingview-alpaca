# TradingView UTBot → Alpaca Webhook Server

Automatically executes trades on your Alpaca account when TradingView UTBot alerts fire.

---

## Features

- ✅ Secure passphrase authentication on every webhook
- ✅ Market open/closed detection with warnings
- ✅ Account health checks (blocked, buying power)
- ✅ Position checks — won't sell what you don't own; adjusts qty if overselling
- ✅ Cancels stale open orders before placing new ones
- ✅ Supports market, limit, stop, and stop-limit orders
- ✅ Full structured logging to console + `webhook.log`
- ✅ REST endpoints to inspect positions and order history
- ✅ Paper trading by default — one env var switches to live

---

## Setup

### 1. Clone & install

```bash
git clone <your-repo>
cd tradingview-alpaca
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your actual values
```

| Variable              | Description                                      |
|-----------------------|--------------------------------------------------|
| `ALPACA_API_KEY`      | From Alpaca dashboard → API Keys                |
| `ALPACA_SECRET_KEY`   | From Alpaca dashboard → API Keys                |
| `ALPACA_BASE_URL`     | Paper: `https://paper-api.alpaca.markets`       |
| `WEBHOOK_PASSPHRASE`  | A secret string you invent (keep it private)    |

### 3. Run locally

```bash
bash start.sh
# Server starts at http://localhost:8000
```

### 4. Expose to the internet (for local testing)

```bash
# Install ngrok: https://ngrok.com
ngrok http 8000
# Copy the https://xxxxx.ngrok.io URL
```

### 5. Deploy to production (Railway — recommended)

1. Push this folder to a GitHub repo
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Add your env variables in Railway's Variables tab
4. Railway auto-deploys and gives you a public URL

---

## API Endpoints

### `GET /health`
Returns account status and market open/closed state.

### `POST /webhook`
Receives TradingView alerts and places orders.

**Request body:**
```json
{
  "passphrase":  "your_secret",
  "symbol":      "AAPL",
  "side":        "buy",
  "qty":         5,
  "order_type":  "market"
}
```

For limit orders, add:
```json
  "order_type":  "limit",
  "limit_price": 150.00
```

For stop orders, add:
```json
  "order_type":  "stop",
  "stop_price":  148.00
```

### `GET /positions`
Returns all current open positions with P&L.

### `GET /orders?status=all&limit=20`
Returns recent orders. `status` can be `open`, `closed`, or `all`.

---

## TradingView Alert Setup

### UTBot Buy Alert message:
```json
{
  "passphrase": "your_secret",
  "symbol":     "{{ticker}}",
  "side":       "buy",
  "qty":        5
}
```

### UTBot Sell Alert message:
```json
{
  "passphrase": "your_secret",
  "symbol":     "{{ticker}}",
  "side":       "sell",
  "qty":        5
}
```

In TradingView:
1. Right-click chart → Add Alert
2. Set condition to your UTBot signal
3. Notifications tab → enable **Webhook URL**
4. Paste: `https://your-server.railway.app/webhook`
5. Paste the JSON above into the **Message** field
6. Click Create

---

## Testing

### Test the webhook manually with curl:
```bash
curl -X POST http://localhost:8000/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "passphrase": "your_secret",
    "symbol": "AAPL",
    "side": "buy",
    "qty": 1
  }'
```

### Check positions:
```bash
curl http://localhost:8000/positions
```

### Check recent orders:
```bash
curl http://localhost:8000/orders?status=all&limit=10
```

---

## Going Live

When you're satisfied with paper trading results:

1. Change `ALPACA_BASE_URL` in your `.env` to:
   ```
   ALPACA_BASE_URL=https://api.alpaca.markets
   ```
2. Replace your paper API keys with your live API keys
3. Start with small position sizes (`"qty": 1`)
4. Monitor `webhook.log` closely for the first few days

---

## Log format

Every event is logged like:
```
2026-05-08 14:32:01 | INFO | 📩 Webhook received: {"passphrase": "...", "symbol": "AAPL", "side": "buy", "qty": 5}
2026-05-08 14:32:01 | INFO | Account buying power: $98,432.00
2026-05-08 14:32:01 | INFO | ✅ Order submitted | id=abc123 | BUY 5x AAPL @ market | status=accepted
```

---

## Disclaimer

This software is for educational purposes. Automated trading carries significant financial risk.
Always test thoroughly on paper trading before using real money.
