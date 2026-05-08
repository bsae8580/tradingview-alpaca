import os
import logging
import json
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import alpaca_trade_api as tradeapi
from alpaca_trade_api.rest import APIError

# ──────────────────────────────────────────────
# Logging setup
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("webhook.log"),
    ],
)
log = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Config (set these as environment variables)
# ──────────────────────────────────────────────
ALPACA_API_KEY    = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL   = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
WEBHOOK_PASSPHRASE = os.environ.get("WEBHOOK_PASSPHRASE", "")

if not all([ALPACA_API_KEY, ALPACA_SECRET_KEY, WEBHOOK_PASSPHRASE]):
    raise EnvironmentError(
        "Missing required env vars: ALPACA_API_KEY, ALPACA_SECRET_KEY, WEBHOOK_PASSPHRASE"
    )


# ──────────────────────────────────────────────
# Alpaca client
# ──────────────────────────────────────────────
api = tradeapi.REST(ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL)


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────
def get_position(symbol: str):
    """Return current position for a symbol, or None if flat."""
    try:
        return api.get_position(symbol)
    except APIError as e:
        if "position does not exist" in str(e).lower():
            return None
        raise


def is_market_open() -> bool:
    """Check if the US market is currently open."""
    clock = api.get_clock()
    return clock.is_open


def get_account():
    """Fetch account details."""
    return api.get_account()


def cancel_open_orders(symbol: str):
    """Cancel any open orders for a symbol before placing a new one."""
    orders = api.list_orders(status="open", symbols=[symbol])
    for order in orders:
        api.cancel_order(order.id)
        log.info(f"Cancelled open order {order.id} for {symbol}")


# ──────────────────────────────────────────────
# Order logic
# ──────────────────────────────────────────────
def place_order(symbol: str, side: str, qty: float, order_type: str = "market",
                limit_price: float = None, stop_price: float = None):
    """
    Place a buy or sell order with full validation.

    Args:
        symbol:      Ticker symbol, e.g. 'AAPL'
        side:        'buy' or 'sell'
        qty:         Number of shares (can be fractional)
        order_type:  'market', 'limit', 'stop', 'stop_limit'
        limit_price: Required for limit/stop_limit orders
        stop_price:  Required for stop/stop_limit orders

    Returns:
        Alpaca order object
    """
    symbol = symbol.upper().strip()
    side   = side.lower().strip()

    if side not in ("buy", "sell"):
        raise ValueError(f"Invalid side '{side}'. Must be 'buy' or 'sell'.")
    if qty <= 0:
        raise ValueError(f"Invalid qty '{qty}'. Must be > 0.")

    # ── Market open check ──────────────────────
    if not is_market_open():
        log.warning(f"Market is CLOSED — order for {symbol} queued as day order (will not fill now).")

    # ── Account health check ───────────────────
    account = get_account()
    if account.trading_blocked:
        raise RuntimeError("Account trading is blocked.")
    if account.account_blocked:
        raise RuntimeError("Account is blocked.")

    buying_power = float(account.buying_power)
    log.info(f"Account buying power: ${buying_power:,.2f}")

    # ── Position check ────────────────────────
    existing_position = get_position(symbol)

    if side == "buy" and existing_position:
        existing_qty = float(existing_position.qty)
        log.info(
            f"Already holding {existing_qty} shares of {symbol} "
            f"(avg entry: ${float(existing_position.avg_entry_price):,.2f}). "
            f"Adding {qty} more."
        )

    if side == "sell":
        if not existing_position:
            log.warning(f"No position in {symbol} to sell. Skipping order.")
            return None
        held_qty = float(existing_position.qty)
        if qty > held_qty:
            log.warning(
                f"Requested sell qty ({qty}) > held qty ({held_qty}). "
                f"Adjusting to sell full position."
            )
            qty = held_qty

    # ── Cancel stale open orders ───────────────
    cancel_open_orders(symbol)

    # ── Build order params ────────────────────
    params = dict(
        symbol=symbol,
        qty=qty,
        side=side,
        type=order_type,
        time_in_force="day",
    )
    if limit_price:
        params["limit_price"] = str(limit_price)
    if stop_price:
        params["stop_price"] = str(stop_price)

    # ── Submit ────────────────────────────────
    order = api.submit_order(**params)
    log.info(
        f"✅ Order submitted | id={order.id} | {side.upper()} {qty}x {symbol} "
        f"@ {order_type} | status={order.status}"
    )
    return order


# ──────────────────────────────────────────────
# FastAPI app
# ──────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("🚀 TradingView→Alpaca webhook server starting up")
    log.info(f"   Base URL : {ALPACA_BASE_URL}")
    log.info(f"   Paper    : {'YES' if 'paper' in ALPACA_BASE_URL else 'NO — LIVE TRADING'}")
    yield
    log.info("Server shutting down")

app = FastAPI(title="TradingView→Alpaca Webhook", lifespan=lifespan)


# ──────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────
@app.get("/health")
def health():
    """Quick liveness check."""
    try:
        account = get_account()
        clock   = api.get_clock()
        return {
            "status"       : "ok",
            "market_open"  : clock.is_open,
            "next_open"    : str(clock.next_open),
            "buying_power" : account.buying_power,
            "equity"       : account.equity,
            "paper"        : "paper" in ALPACA_BASE_URL,
            "timestamp"    : datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        log.error(f"Health check failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/webhook")
async def webhook(request: Request):
    """
    Receives TradingView alerts and places Alpaca orders.

    Expected JSON payload:
    {
        "passphrase": "your_secret",
        "symbol":     "AAPL",
        "side":       "buy" | "sell",
        "qty":        5,
        "order_type": "market",       # optional, default: market
        "limit_price": 150.00,        # optional, for limit orders
        "stop_price":  148.00         # optional, for stop orders
    }
    """
    # ── Parse body ────────────────────────────
    try:
        data = await request.json()
    except Exception:
        log.warning("Received malformed JSON payload")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    log.info(f"📩 Webhook received: {json.dumps(data)}")

    # ── Auth ──────────────────────────────────
    if data.get("passphrase") != WEBHOOK_PASSPHRASE:
        log.warning("⛔ Webhook rejected — bad passphrase")
        raise HTTPException(status_code=403, detail="Forbidden")

    # ── Validate required fields ──────────────
    required = ("symbol", "side", "qty")
    missing  = [f for f in required if f not in data]
    if missing:
        raise HTTPException(status_code=422, detail=f"Missing fields: {missing}")

    # ── Place order ───────────────────────────
    try:
        order = place_order(
            symbol      = data["symbol"],
            side        = data["side"],
            qty         = float(data["qty"]),
            order_type  = data.get("order_type", "market"),
            limit_price = data.get("limit_price"),
            stop_price  = data.get("stop_price"),
        )

        if order is None:
            return JSONResponse({"status": "skipped", "reason": "no position to sell"})

        return JSONResponse({
            "status"   : "order_submitted",
            "order_id" : order.id,
            "symbol"   : order.symbol,
            "side"     : order.side,
            "qty"      : order.qty,
            "type"     : order.type,
            "order_status": order.status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    except ValueError as e:
        log.error(f"Validation error: {e}")
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        log.error(f"Account error: {e}")
        raise HTTPException(status_code=403, detail=str(e))
    except APIError as e:
        log.error(f"Alpaca API error: {e}")
        raise HTTPException(status_code=502, detail=f"Alpaca error: {e}")
    except Exception as e:
        log.exception(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/positions")
def list_positions():
    """Return all current open positions."""
    try:
        positions = api.list_positions()
        return [
            {
                "symbol"          : p.symbol,
                "qty"             : p.qty,
                "side"            : p.side,
                "avg_entry_price" : p.avg_entry_price,
                "current_price"   : p.current_price,
                "unrealized_pl"   : p.unrealized_pl,
                "unrealized_plpc" : p.unrealized_plpc,
            }
            for p in positions
        ]
    except Exception as e:
        log.error(f"Failed to fetch positions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/orders")
def list_orders(status: str = "all", limit: int = 20):
    """Return recent orders. status: open | closed | all"""
    try:
        orders = api.list_orders(status=status, limit=limit)
        return [
            {
                "id"          : o.id,
                "symbol"      : o.symbol,
                "side"        : o.side,
                "qty"         : o.qty,
                "filled_qty"  : o.filled_qty,
                "type"        : o.type,
                "status"      : o.status,
                "filled_at"   : str(o.filled_at),
                "filled_avg_price": o.filled_avg_price,
            }
            for o in orders
        ]
    except Exception as e:
        log.error(f"Failed to fetch orders: {e}")
        raise HTTPException(status_code=500, detail=str(e))
