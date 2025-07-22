from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import asyncio
import websockets
import json
import requests
import pandas as pd
from datetime import datetime, time
import logging
from enum import Enum
import uuid

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Trading Bot Backend", version="1.0.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
class Config:
    ANGEL_API_BASE = "https://apiconnect.angelone.in/rest"
    WEBSOCKET_URL = "wss://smartapisocket.angelone.in/smart-stream"
    ORDER_WEBSOCKET_URL = "wss://tns.angelone.in/smart-order-update"
    SCRIP_MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
    ORDER_TIMEOUT_MINUTES = 5
    AUTO_EXIT_TIME = time(15, 25)  # 3:25 PM
    MAX_TRAILING_POINTS = 4

# Enums
class OrderType(str, Enum):
    LIVE_PRICE = "live_price"
    POINTS_TRIGGER = "points_trigger" 
    PERCENTAGE_TRIGGER = "percentage_trigger"
    CANDLE_TRIGGER = "candle_trigger"

class SellOrderType(str, Enum):
    MANUAL_EXIT = "manual_exit"
    POINTS_STOP = "points_stop"
    PERCENTAGE_STOP = "percentage_stop" 
    CANDLE_STOP = "candle_stop"

class TradeMode(str, Enum):
    SINGLE = "single"
    MULTI = "multi"

# Pydantic Models
class AuthRequest(BaseModel):
    clientcode: str
    password: str
    totp: Optional[str] = None
    api_key: str
    client_local_ip: str
    client_public_ip: str
    mac_address: str

class BuyOrderRequest(BaseModel):
    symbol: str
    exchange: str = "NSE"
    quantity: int
    order_type: OrderType
    trade_mode: TradeMode = TradeMode.SINGLE
    points: Optional[float] = None
    percentage: Optional[float] = None
    candle_size: Optional[str] = None
    target_multiplier: Optional[float] = None
    trailing_points: Optional[float] = None

class SellOrderRequest(BaseModel):
    symbol: str
    exchange: str = "NSE"
    sell_type: SellOrderType
    points: Optional[float] = None
    percentage: Optional[float] = None
    candle_size: Optional[str] = None
    target_multiplier: Optional[float] = None
    trailing_points: Optional[float] = None

class TradePosition(BaseModel):
    symbol: str
    quantity: int
    entry_price: float
    current_price: float
    pnl: float
    timestamp: datetime

# Global state management
class TradingBotState:
    def __init__(self):
        self.authenticated = False
        self.jwt_token = None
        self.feed_token = None
        self.scrip_master = {}
        self.active_positions = {}
        self.active_orders = {}
        self.websocket_connections = []
        self.price_data = {}
        self.candle_data = {}
        self.trailing_stops = {}
        self.bot_active = True

bot_state = TradingBotState()

# Utility Functions
def get_headers():
    """Get standard headers for API requests"""
    if not bot_state.authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    return {
        "Authorization": f"Bearer {bot_state.jwt_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-UserType": "USER",
        "X-SourceID": "WEB"
    }

async def load_scrip_master():
    """Load instrument master data"""
    try:
        response = requests.get(Config.SCRIP_MASTER_URL)
        if response.status_code == 200:
            data = response.json()
            for instrument in data:
                symbol_key = f"{instrument['tradingsymbol']}-{instrument['exchange']}"
                bot_state.scrip_master[symbol_key] = instrument
            logger.info(f"Loaded {len(bot_state.scrip_master)} instruments")
    except Exception as e:
        logger.error(f"Error loading scrip master: {e}")

def get_symbol_token(symbol: str, exchange: str) -> str:
    """Get token for a symbol"""
    key = f"{symbol}-{exchange}"
    if key not in bot_state.scrip_master:
        raise HTTPException(status_code=404, detail=f"Symbol {key} not found")
    return bot_state.scrip_master[key]["symboltoken"]

def calculate_pnl(entry_price: float, current_price: float, quantity: int, transaction_type: str) -> float:
    """Calculate P&L for a position"""
    if transaction_type.upper() == "BUY":
        return (current_price - entry_price) * quantity
    else:
        return (entry_price - current_price) * quantity

# Authentication Endpoints
@app.post("/auth/login")
async def login(auth_request: AuthRequest):
    """Authenticate with AngelOne API"""
    try:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-UserType": "USER", 
            "X-SourceID": "WEB",
            "X-ClientLocalIP": auth_request.client_local_ip,
            "X-ClientPublicIP": auth_request.client_public_ip,
            "X-MACAddress": auth_request.mac_address,
            "X-PrivateKey": auth_request.api_key
        }
        
        payload = {
            "clientcode": auth_request.clientcode,
            "password": auth_request.password
        }
        
        if auth_request.totp:
            payload["totp"] = auth_request.totp
        
        response = requests.post(
            f"{Config.ANGEL_API_BASE}/auth/angelbroking/user/v1/loginByPassword",
            headers=headers,
            json=payload
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get("status"):
                bot_state.authenticated = True
                bot_state.jwt_token = data["data"]["jwtToken"]
                bot_state.feed_token = data["data"]["feedToken"]
                
                # Load scrip master after authentication
                await load_scrip_master()
                
                return {"status": "success", "message": "Authentication successful"}
            else:
                raise HTTPException(status_code=401, detail=data.get("message", "Authentication failed"))
        else:
            raise HTTPException(status_code=response.status_code, detail="Authentication request failed")
            
    except Exception as e:
        logger.error(f"Authentication error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Order Management Endpoints
@app.post("/orders/buy")
async def place_buy_order(order_request: BuyOrderRequest):
    """Place buy order with specified trigger conditions"""
    try:
        symbol_token = get_symbol_token(order_request.symbol, order_request.exchange)
        
        if order_request.order_type == OrderType.LIVE_PRICE:
            # Immediate market order
            return await execute_market_order(
                symbol=order_request.symbol,
                exchange=order_request.exchange,
                symbol_token=symbol_token,
                quantity=order_request.quantity,
                transaction_type="BUY"
            )
        else:
            # Set up trigger monitoring
            trigger_id = str(uuid.uuid4())
            bot_state.active_orders[trigger_id] = {
                "type": "buy_trigger",
                "symbol": order_request.symbol,
                "exchange": order_request.exchange,
                "symbol_token": symbol_token,
                "quantity": order_request.quantity,
                "order_type": order_request.order_type,
                "trade_mode": order_request.trade_mode,
                "points": order_request.points,
                "percentage": order_request.percentage,
                "candle_size": order_request.candle_size,
                "target_multiplier": order_request.target_multiplier,
                "trailing_points": order_request.trailing_points,
                "initial_price": bot_state.price_data.get(symbol_token, {}).get("ltp", 0),
                "created_at": datetime.now(),
                "status": "active"
            }
            
            return {
                "status": "success",
                "trigger_id": trigger_id,
                "message": f"Buy trigger set for {order_request.symbol}"
            }
            
    except Exception as e:
        logger.error(f"Buy order error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/orders/sell")
async def place_sell_order(sell_request: SellOrderRequest):
    """Place sell order or set sell conditions"""
    try:
        if sell_request.sell_type == SellOrderType.MANUAL_EXIT:
            # Immediate market sell
            symbol_token = get_symbol_token(sell_request.symbol, sell_request.exchange)
            return await execute_market_order(
                symbol=sell_request.symbol,
                exchange=sell_request.exchange,
                symbol_token=symbol_token,
                quantity=0,  # Will be determined from positions
                transaction_type="SELL"
            )
        else:
            # Set up stop loss conditions
            trigger_id = str(uuid.uuid4())
            symbol_token = get_symbol_token(sell_request.symbol, sell_request.exchange)
            
            bot_state.active_orders[trigger_id] = {
                "type": "sell_trigger", 
                "symbol": sell_request.symbol,
                "exchange": sell_request.exchange,
                "symbol_token": symbol_token,
                "sell_type": sell_request.sell_type,
                "points": sell_request.points,
                "percentage": sell_request.percentage,
                "candle_size": sell_request.candle_size,
                "target_multiplier": sell_request.target_multiplier,
                "trailing_points": sell_request.trailing_points,
                "created_at": datetime.now(),
                "status": "active"
            }
            
            return {
                "status": "success",
                "trigger_id": trigger_id,
                "message": f"Sell trigger set for {sell_request.symbol}"
            }
            
    except Exception as e:
        logger.error(f"Sell order error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

async def execute_market_order(symbol: str, exchange: str, symbol_token: str, 
                              quantity: int, transaction_type: str):
    """Execute immediate market order"""
    try:
        # If selling, get quantity from positions
        if transaction_type == "SELL" and quantity == 0:
            positions = await get_positions()
            for pos in positions.get("data", []):
                if pos["symboltoken"] == symbol_token and int(pos["netqty"]) > 0:
                    quantity = int(pos["netqty"])
                    break
            
            if quantity == 0:
                raise HTTPException(status_code=400, detail="No position found to sell")
        
        payload = {
            "variety": "NORMAL",
            "tradingsymbol": symbol,
            "symboltoken": symbol_token,
            "transactiontype": transaction_type,
            "exchange": exchange,
            "ordertype": "MARKET",
            "producttype": "INTRADAY",
            "duration": "DAY",
            "price": "0",
            "quantity": str(quantity),
            "triggerprice": "0"
        }
        
        response = requests.post(
            f"{Config.ANGEL_API_BASE}/secure/angelbroking/order/v1/placeOrder",
            headers=get_headers(),
            json=payload
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get("status"):
                return {
                    "status": "success",
                    "order_id": data["data"]["orderid"],
                    "message": f"{transaction_type} order placed for {symbol}"
                }
            else:
                raise HTTPException(status_code=400, detail=data.get("message", "Order failed"))
        else:
            raise HTTPException(status_code=response.status_code, detail="Order request failed")
            
    except Exception as e:
        logger.error(f"Market order execution error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Position and Data Endpoints
@app.get("/positions")
async def get_positions():
    """Get current positions"""
    try:
        response = requests.get(
            f"{Config.ANGEL_API_BASE}/secure/angelbroking/order/v1/getPosition",
            headers=get_headers()
        )
        
        if response.status_code == 200:
            return response.json()
        else:
            raise HTTPException(status_code=response.status_code, detail="Failed to get positions")
            
    except Exception as e:
        logger.error(f"Get positions error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/orders")
async def get_order_book():
    """Get order book"""
    try:
        response = requests.get(
            f"{Config.ANGEL_API_BASE}/secure/angelbroking/order/v1/getOrderBook",
            headers=get_headers()
        )
        
        if response.status_code == 200:
            return response.json()
        else:
            raise HTTPException(status_code=response.status_code, detail="Failed to get orders")
            
    except Exception as e:
        logger.error(f"Get orders error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/trades")
async def get_trade_book():
    """Get trade book for PnL calculation"""
    try:
        response = requests.get(
            f"{Config.ANGEL_API_BASE}/secure/angelbroking/order/v1/getTradeBook",
            headers=get_headers()
        )
        
        if response.status_code == 200:
            return response.json()
        else:
            raise HTTPException(status_code=response.status_code, detail="Failed to get trades")
            
    except Exception as e:
        logger.error(f"Get trades error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/margin")
async def get_margin():
    """Get RMS/Margin details"""
    try:
        response = requests.get(
            f"{Config.ANGEL_API_BASE}/secure/angelbroking/user/v1/getRMS",
            headers=get_headers()
        )
        
        if response.status_code == 200:
            return response.json()
        else:
            raise HTTPException(status_code=response.status_code, detail="Failed to get margin")
            
    except Exception as e:
        logger.error(f"Get margin error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/candles")
async def get_candle_data(symbol: str, exchange: str = "NSE", interval: str = "ONE_MINUTE", 
                         fromdate: str = None, todate: str = None):
    """Get historical candle data"""
    try:
        symbol_token = get_symbol_token(symbol, exchange)
        
        payload = {
            "exchange": exchange,
            "symboltoken": symbol_token,
            "interval": interval,
            "fromdate": fromdate or datetime.now().strftime("%Y-%m-%d 09:15"),
            "todate": todate or datetime.now().strftime("%Y-%m-%d 15:30")
        }
        
        response = requests.post(
            f"{Config.ANGEL_API_BASE}/secure/angelbroking/historical/v1/getCandleData",
            headers=get_headers(),
            json=payload
        )
        
        if response.status_code == 200:
            return response.json()
        else:
            raise HTTPException(status_code=response.status_code, detail="Failed to get candle data")
            
    except Exception as e:
        logger.error(f"Get candle data error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Control Endpoints
@app.post("/bot/start")
async def start_bot():
    """Start the trading bot"""
    bot_state.bot_active = True
    return {"status": "success", "message": "Bot started"}

@app.post("/bot/stop")
async def stop_bot():
    """Stop the trading bot"""
    bot_state.bot_active = False
    # Cancel all active triggers
    bot_state.active_orders.clear()
    return {"status": "success", "message": "Bot stopped"}

@app.get("/bot/status")
async def get_bot_status():
    """Get bot status and active triggers"""
    return {
        "bot_active": bot_state.bot_active,
        "authenticated": bot_state.authenticated,
        "active_triggers": len(bot_state.active_orders),
        "active_positions": len(bot_state.active_positions),
        "triggers": list(bot_state.active_orders.keys())
    }

@app.post("/exit/{trigger_id}")
async def cancel_trigger(trigger_id: str):
    """Cancel a specific trigger"""
    if trigger_id in bot_state.active_orders:
        del bot_state.active_orders[trigger_id]
        return {"status": "success", "message": f"Trigger {trigger_id} cancelled"}
    else:
        raise HTTPException(status_code=404, detail="Trigger not found")

# WebSocket Endpoints
@app.websocket("/ws/market-data")
async def websocket_market_data(websocket: WebSocket):
    """WebSocket for real-time market data"""
    await websocket.accept()
    bot_state.websocket_connections.append(websocket)
    
    try:
        while True:
            # Send current price data to client
            await websocket.send_json({
                "type": "price_update",
                "data": bot_state.price_data
            })
            await asyncio.sleep(1)
            
    except WebSocketDisconnect:
        bot_state.websocket_connections.remove(websocket)

# Background Tasks
@app.on_event("startup")
async def startup_event():
    """Start background monitoring tasks"""
    asyncio.create_task(price_monitor())
    asyncio.create_task(trigger_monitor()) 
    asyncio.create_task(auto_exit_monitor())

async def price_monitor():
    """Monitor real-time price data via WebSocket"""
    while True:
        try:
            if bot_state.authenticated and bot_state.feed_token:
                # WebSocket connection logic would go here
                # This is a placeholder for the actual WebSocket implementation
                await asyncio.sleep(1)
            else:
                await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Price monitor error: {e}")
            await asyncio.sleep(5)

async def trigger_monitor():
    """Monitor and execute trigger conditions"""
    while True:
        try:
            if not bot_state.bot_active:
                await asyncio.sleep(1)
                continue
                
            current_time = datetime.now()
            triggers_to_remove = []
            
            for trigger_id, trigger in bot_state.active_orders.items():
                if trigger["status"] != "active":
                    continue
                    
                # Check for order timeout
                if (current_time - trigger["created_at"]).total_seconds() > Config.ORDER_TIMEOUT_MINUTES * 60:
                    triggers_to_remove.append(trigger_id)
                    continue
                
                symbol_token = trigger["symbol_token"]
                current_price = bot_state.price_data.get(symbol_token, {}).get("ltp", 0)
                
                if current_price == 0:
                    continue
                
                # Check trigger conditions
                if trigger["type"] == "buy_trigger":
                    if await check_buy_trigger(trigger, current_price):
                        await execute_trigger(trigger_id, trigger)
                        if trigger["trade_mode"] == TradeMode.SINGLE:
                            triggers_to_remove.append(trigger_id)
                            
                elif trigger["type"] == "sell_trigger":
                    if await check_sell_trigger(trigger, current_price):
                        await execute_trigger(trigger_id, trigger)
                        triggers_to_remove.append(trigger_id)
            
            # Remove completed/expired triggers
            for trigger_id in triggers_to_remove:
                if trigger_id in bot_state.active_orders:
                    del bot_state.active_orders[trigger_id]
                    
        except Exception as e:
            logger.error(f"Trigger monitor error: {e}")
            
        await asyncio.sleep(1)

async def check_buy_trigger(trigger: dict, current_price: float) -> bool:
    """Check if buy trigger conditions are met"""
    try:
        order_type = trigger["order_type"]
        initial_price = trigger["initial_price"]
        
        if order_type == OrderType.POINTS_TRIGGER:
            target_price = initial_price + trigger["points"]
            return current_price >= target_price
            
        elif order_type == OrderType.PERCENTAGE_TRIGGER:
            target_price = initial_price * (1 + trigger["percentage"] / 100)
            return current_price >= target_price
            
        elif order_type == OrderType.CANDLE_TRIGGER:
            # Candle analysis logic would go here
            # This is a simplified version
            return False  # Placeholder
            
    except Exception as e:
        logger.error(f"Buy trigger check error: {e}")
        return False

async def check_sell_trigger(trigger: dict, current_price: float) -> bool:
    """Check if sell trigger conditions are met"""
    try:
        sell_type = trigger["sell_type"]
        
        # Get entry price from positions
        positions = await get_positions()
        entry_price = 0
        for pos in positions.get("data", []):
            if pos["symboltoken"] == trigger["symbol_token"]:
                entry_price = float(pos["avgprice"])
                break
                
        if entry_price == 0:
            return False
        
        if sell_type == SellOrderType.POINTS_STOP:
            stop_price = entry_price - trigger["points"]
            return current_price <= stop_price
            
        elif sell_type == SellOrderType.PERCENTAGE_STOP:
            stop_price = entry_price * (1 - trigger["percentage"] / 100)
            return current_price <= stop_price
            
        elif sell_type == SellOrderType.CANDLE_STOP:
            # Candle analysis logic would go here
            return False  # Placeholder
            
    except Exception as e:
        logger.error(f"Sell trigger check error: {e}")
        return False

async def execute_trigger(trigger_id: str, trigger: dict):
    """Execute a triggered order"""
    try:
        if trigger["type"] == "buy_trigger":
            await execute_market_order(
                symbol=trigger["symbol"],
                exchange=trigger["exchange"],
                symbol_token=trigger["symbol_token"],
                quantity=trigger["quantity"],
                transaction_type="BUY"
            )
        elif trigger["type"] == "sell_trigger":
            await execute_market_order(
                symbol=trigger["symbol"],
                exchange=trigger["exchange"], 
                symbol_token=trigger["symbol_token"],
                quantity=0,  # Will be determined from positions
                transaction_type="SELL"
            )
            
        logger.info(f"Executed trigger {trigger_id} for {trigger['symbol']}")
        
    except Exception as e:
        logger.error(f"Execute trigger error: {e}")

async def auto_exit_monitor():
    """Monitor for 3:25 PM auto exit"""
    while True:
        try:
            current_time = datetime.now().time()
            
            if current_time >= Config.AUTO_EXIT_TIME and bot_state.bot_active:
                logger.info("Auto exit time reached - closing all positions")
                
                # Get all positions and close them
                positions = await get_positions()
                for pos in positions.get("data", []):
                    if int(pos["netqty"]) > 0:
                        try:
                            await execute_market_order(
                                symbol=pos["tradingsymbol"],
                                exchange=pos["exchange"],
                                symbol_token=pos["symboltoken"],
                                quantity=0,
                                transaction_type="SELL"
                            )
                        except Exception as e:
                            logger.error(f"Auto exit error for {pos['tradingsymbol']}: {e}")
                
                # Stop the bot
                bot_state.bot_active = False
                bot_state.active_orders.clear()
                
                # Wait until next day
                await asyncio.sleep(300)  # Wait 5 minutes before checking again
            else:
                await asyncio.sleep(60)  # Check every minute
                
        except Exception as e:
            logger.error(f"Auto exit monitor error: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
