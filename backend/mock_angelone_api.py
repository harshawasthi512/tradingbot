from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import asyncio
import json
import random
import uuid
from datetime import datetime, timedelta, time
import logging
from enum import Enum
import websockets
import threading
import time as time_module

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="AngelOne Mock API", version="1.0.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mock Data Storage
class MockDataStore:
    def __init__(self):
        self.authenticated_users = {}
        self.orders = {}
        self.trades = {}
        self.positions = {}
        self.margin_data = {}
        self.price_data = {}
        self.candle_data = {}
        self.scrip_master = self._init_scrip_master()
        self.websocket_connections = []
        self.order_counter = 200910000000000
        self.trade_counter = 100000
        
        # Initialize mock stock prices
        self._init_stock_prices()
        
        # Start price update background task
        asyncio.create_task(self._update_prices())

    def _init_scrip_master(self):
        """Initialize mock scrip master data"""
        stocks = [
            {"tradingsymbol": "SBIN-EQ", "symboltoken": "3045", "exchange": "NSE", "name": "STATE BANK OF INDIA"},
            {"tradingsymbol": "RELIANCE-EQ", "symboltoken": "2885", "exchange": "NSE", "name": "RELIANCE INDUSTRIES LTD"},
            {"tradingsymbol": "TCS-EQ", "symboltoken": "11536", "exchange": "NSE", "name": "TATA CONSULTANCY SERVICES LTD"},
            {"tradingsymbol": "INFY-EQ", "symboltoken": "1594", "exchange": "NSE", "name": "INFOSYS LIMITED"},
            {"tradingsymbol": "HDFC-EQ", "symboltoken": "1333", "exchange": "NSE", "name": "HDFC LIMITED"},
            {"tradingsymbol": "HDFCBANK-EQ", "symboltoken": "1333", "exchange": "NSE", "name": "HDFC BANK LIMITED"},
            {"tradingsymbol": "ICICIBANK-EQ", "symboltoken": "4963", "exchange": "NSE", "name": "ICICI BANK LIMITED"},
            {"tradingsymbol": "KOTAKBANK-EQ", "symboltoken": "1922", "exchange": "NSE", "name": "KOTAK MAHINDRA BANK LIMITED"},
            {"tradingsymbol": "LT-EQ", "symboltoken": "11483", "exchange": "NSE", "name": "LARSEN & TOUBRO LIMITED"},
            {"tradingsymbol": "WIPRO-EQ", "symboltoken": "3787", "exchange": "NSE", "name": "WIPRO LIMITED"},
        ]
        return stocks

    def _init_stock_prices(self):
        """Initialize realistic stock prices"""
        base_prices = {
            "3045": 520.50,    # SBIN
            "2885": 2850.75,   # RELIANCE
            "11536": 3890.25,  # TCS
            "1594": 1780.50,   # INFY
            "1333": 2650.75,   # HDFC
            "4963": 1050.25,   # ICICIBANK
            "1922": 1920.50,   # KOTAKBANK
            "11483": 3420.75,  # LT
            "3787": 680.25,    # WIPRO
        }
        
        for token, base_price in base_prices.items():
            self.price_data[token] = {
                "ltp": base_price,
                "open": base_price * (0.98 + random.random() * 0.04),
                "high": base_price * (1.0 + random.random() * 0.03),
                "low": base_price * (0.97 + random.random() * 0.02),
                "close": base_price,
                "volume": random.randint(100000, 1000000),
                "timestamp": datetime.now().isoformat()
            }

    async def _update_prices(self):
        """Update stock prices continuously to simulate real market"""
        while True:
            try:
                current_time = datetime.now().time()
                # Only update prices during market hours (9:15 AM to 3:30 PM)
                market_open = time(9, 15)
                market_close = time(15, 30)
                
                if market_open <= current_time <= market_close:
                    for token, price_info in self.price_data.items():
                        # Simulate price movement (±0.5%)
                        change_percent = (random.random() - 0.5) * 0.01
                        new_ltp = price_info["ltp"] * (1 + change_percent)
                        
                        # Update high/low
                        if new_ltp > price_info["high"]:
                            price_info["high"] = new_ltp
                        if new_ltp < price_info["low"]:
                            price_info["low"] = new_ltp
                            
                        price_info["ltp"] = round(new_ltp, 2)
                        price_info["timestamp"] = datetime.now().isoformat()
                        price_info["volume"] += random.randint(100, 1000)
                
                # Notify WebSocket clients
                await self._broadcast_price_updates()
                
            except Exception as e:
                logger.error(f"Price update error: {e}")
            
            await asyncio.sleep(1)  # Update every second

    async def _broadcast_price_updates(self):
        """Broadcast price updates to WebSocket clients"""
        if self.websocket_connections:
            message = {
                "type": "price_update",
                "data": self.price_data
            }
            disconnected = []
            for websocket in self.websocket_connections:
                try:
                    await websocket.send_json(message)
                except:
                    disconnected.append(websocket)
            
            # Remove disconnected clients
            for ws in disconnected:
                self.websocket_connections.remove(ws)

    def generate_order_id(self):
        """Generate unique order ID"""
        self.order_counter += 1
        return str(self.order_counter)

    def generate_trade_id(self):
        """Generate unique trade ID"""
        self.trade_counter += 1
        return str(self.trade_counter)

mock_store = MockDataStore()

# Pydantic Models
class LoginRequest(BaseModel):
    clientcode: str
    password: str
    totp: Optional[str] = None

class PlaceOrderRequest(BaseModel):
    variety: str
    tradingsymbol: str
    symboltoken: str
    transactiontype: str
    exchange: str
    ordertype: str
    producttype: str
    duration: str
    price: str
    quantity: str
    triggerprice: str = "0"
    squareoff: str = "0"
    stoploss: str = "0"
    trailingStopLoss: str = "0"

class ModifyOrderRequest(BaseModel):
    orderid: str
    variety: str = "NORMAL"
    tradingsymbol: str
    symboltoken: str
    transactiontype: str
    exchange: str
    ordertype: str
    producttype: str
    duration: str
    price: str
    quantity: str
    triggerprice: str = "0"

class CancelOrderRequest(BaseModel):
    orderid: str
    variety: str = "NORMAL"

class CandleDataRequest(BaseModel):
    exchange: str
    symboltoken: str
    interval: str
    fromdate: str
    todate: str

# Utility Functions
def validate_auth_token(authorization: Optional[str] = Header(None)):
    """Validate JWT token"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid or missing authorization token")
    
    token = authorization.replace("Bearer ", "")
    user_found = False
    for user_id, user_data in mock_store.authenticated_users.items():
        if user_data["jwt_token"] == token:
            user_found = True
            break
    
    if not user_found:
        raise HTTPException(status_code=401, detail="Invalid JWT token")
    
    return token

def get_symbol_info(symboltoken: str):
    """Get symbol information from scrip master"""
    for stock in mock_store.scrip_master:
        if stock["symboltoken"] == symboltoken:
            return stock
    return None

def simulate_order_execution(order_data: dict):
    """Simulate order execution with realistic behavior"""
    order_id = order_data["orderid"]
    
    # Simulate processing time
    def process_order():
        time_module.sleep(random.uniform(0.5, 2.0))  # 0.5-2 seconds processing
        
        if order_id in mock_store.orders:
            order = mock_store.orders[order_id]
            
            # 95% success rate for market orders, 90% for limit orders
            success_rate = 0.95 if order["ordertype"] == "MARKET" else 0.90
            
            if random.random() < success_rate:
                # Order executed
                order["status"] = "complete"
                order["orderstatus"] = "executed"
                
                # Get current market price
                current_price = mock_store.price_data.get(order["symboltoken"], {}).get("ltp", 0)
                if order["ordertype"] == "MARKET":
                    execution_price = current_price
                else:
                    execution_price = float(order["price"])
                
                order["price"] = str(execution_price)
                order["updatetime"] = datetime.now().strftime("%d-%b-%Y %H:%M:%S")
                
                # Create trade entry
                trade_id = mock_store.generate_trade_id()
                trade_data = {
                    "tradeid": trade_id,
                    "orderid": order_id,
                    "tradingsymbol": order["tradingsymbol"],
                    "exchange": order["exchange"],
                    "transactiontype": order["transactiontype"],
                    "quantity": order["quantity"],
                    "price": str(execution_price),
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "symboltoken": order["symboltoken"]
                }
                mock_store.trades[trade_id] = trade_data
                
                # Update positions
                position_key = f"{order['symboltoken']}_{order['exchange']}"
                if position_key not in mock_store.positions:
                    mock_store.positions[position_key] = {
                        "symboltoken": order["symboltoken"],
                        "tradingsymbol": order["tradingsymbol"],
                        "exchange": order["exchange"],
                        "netqty": "0",
                        "avgprice": "0",
                        "ltp": str(current_price),
                        "pnl": "0",
                        "buyqty": "0",
                        "sellqty": "0",
                        "buyvalue": "0",
                        "sellvalue": "0"
                    }
                
                position = mock_store.positions[position_key]
                quantity = int(order["quantity"])
                
                if order["transactiontype"] == "BUY":
                    current_net = int(position["netqty"])
                    current_avg = float(position["avgprice"]) if position["avgprice"] != "0" else 0
                    
                    new_net = current_net + quantity
                    if new_net > 0:
                        new_avg = ((current_net * current_avg) + (quantity * execution_price)) / new_net
                    else:
                        new_avg = 0
                    
                    position["netqty"] = str(new_net)
                    position["avgprice"] = str(round(new_avg, 2))
                    position["buyqty"] = str(int(position["buyqty"]) + quantity)
                    position["buyvalue"] = str(float(position["buyvalue"]) + (quantity * execution_price))
                    
                else:  # SELL
                    current_net = int(position["netqty"])
                    new_net = current_net - quantity
                    
                    position["netqty"] = str(new_net)
                    position["sellqty"] = str(int(position["sellqty"]) + quantity)
                    position["sellvalue"] = str(float(position["sellvalue"]) + (quantity * execution_price))
                    
                    if new_net == 0:
                        position["avgprice"] = "0"
                
                # Calculate PnL
                if int(position["netqty"]) != 0:
                    avg_price = float(position["avgprice"])
                    pnl = (current_price - avg_price) * int(position["netqty"])
                    position["pnl"] = str(round(pnl, 2))
                else:
                    position["pnl"] = "0"
                
                position["ltp"] = str(current_price)
                
            else:
                # Order rejected
                order["status"] = "rejected"
                order["orderstatus"] = "rejected"
                order["updatetime"] = datetime.now().strftime("%d-%b-%Y %H:%M:%S")
    
    # Run order processing in background
    threading.Thread(target=process_order).start()

# Authentication Endpoints
@app.post("/rest/auth/angelbroking/user/v1/loginByPassword")
async def login(request: LoginRequest):
    """Mock authentication endpoint"""
    try:
        # Simulate authentication validation
        if request.clientcode and request.password:
            # Generate mock tokens
            user_id = str(uuid.uuid4())
            jwt_token = f"jwt_mock_{user_id}"
            refresh_token = f"refresh_mock_{user_id}"
            feed_token = f"feed_mock_{user_id}"
            
            # Store user session
            mock_store.authenticated_users[user_id] = {
                "clientcode": request.clientcode,
                "jwt_token": jwt_token,
                "refresh_token": refresh_token,
                "feed_token": feed_token,
                "login_time": datetime.now()
            }
            
            # Initialize user's margin data
            mock_store.margin_data[user_id] = {
                "availablecash": str(random.randint(50000, 200000)),
                "collateral": str(random.randint(10000, 50000)),
                "m2munrealized": "0",
                "m2mrealized": "0"
            }
            
            return {
                "status": True,
                "message": "SUCCESS",
                "errorcode": "",
                "data": {
                    "jwtToken": jwt_token,
                    "refreshToken": refresh_token,
                    "feedToken": feed_token
                }
            }
        else:
            return {
                "status": False,
                "message": "Invalid credentials",
                "errorcode": "AG8001",
                "data": None
            }
    except Exception as e:
        logger.error(f"Login error: {e}")
        return {
            "status": False,
            "message": "Authentication failed",
            "errorcode": "AG8002",
            "data": None
        }

# Order Management Endpoints
@app.post("/rest/secure/angelbroking/order/v1/placeOrder")
async def place_order(request: PlaceOrderRequest, authorization: str = Header(...)):
    """Mock place order endpoint"""
    try:
        validate_auth_token(authorization)
        
        order_id = mock_store.generate_order_id()
        
        # Validate symbol token
        symbol_info = get_symbol_info(request.symboltoken)
        if not symbol_info:
            return {
                "status": False,
                "message": "Invalid symbol token",
                "errorcode": "AG8003",
                "data": None
            }
        
        # Create order record
        order_data = {
            "orderid": order_id,
            "variety": request.variety,
            "tradingsymbol": request.tradingsymbol,
            "symboltoken": request.symboltoken,
            "transactiontype": request.transactiontype,
            "exchange": request.exchange,
            "ordertype": request.ordertype,
            "producttype": request.producttype,
            "duration": request.duration,
            "price": request.price,
            "quantity": request.quantity,
            "triggerprice": request.triggerprice,
            "status": "pending",
            "orderstatus": "open",
            "updatetime": datetime.now().strftime("%d-%b-%Y %H:%M:%S"),
            "ordertime": datetime.now().strftime("%d-%b-%Y %H:%M:%S")
        }
        
        mock_store.orders[order_id] = order_data
        
        # Simulate order execution
        simulate_order_execution(order_data)
        
        return {
            "status": True,
            "message": "SUCCESS",
            "errorcode": "",
            "data": {
                "script": request.tradingsymbol,
                "orderid": order_id,
                "uniqueorderid": f"unique_{order_id}"
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Place order error: {e}")
        return {
            "status": False,
            "message": "Order placement failed",
            "errorcode": "AG8004",
            "data": None
        }

@app.post("/rest/secure/angelbroking/order/v1/modifyOrder")
async def modify_order(request: ModifyOrderRequest, authorization: str = Header(...)):
    """Mock modify order endpoint"""
    try:
        validate_auth_token(authorization)
        
        if request.orderid not in mock_store.orders:
            return {
                "status": False,
                "message": "Order not found",
                "errorcode": "AG8005",
                "data": None
            }
        
        order = mock_store.orders[request.orderid]
        
        # Only allow modification if order is still open
        if order["status"] != "pending":
            return {
                "status": False,
                "message": "Cannot modify completed order",
                "errorcode": "AG8006",
                "data": None
            }
        
        # Update order details
        order.update({
            "price": request.price,
            "quantity": request.quantity,
            "triggerprice": request.triggerprice,
            "updatetime": datetime.now().strftime("%d-%b-%Y %H:%M:%S")
        })
        
        return {
            "status": True,
            "message": "SUCCESS",
            "errorcode": "",
            "data": {
                "script": request.tradingsymbol,
                "orderid": request.orderid,
                "uniqueorderid": f"unique_{request.orderid}"
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Modify order error: {e}")
        return {
            "status": False,
            "message": "Order modification failed",
            "errorcode": "AG8007",
            "data": None
        }

@app.post("/rest/secure/angelbroking/order/v1/cancelOrder")
async def cancel_order(request: CancelOrderRequest, authorization: str = Header(...)):
    """Mock cancel order endpoint"""
    try:
        validate_auth_token(authorization)
        
        if request.orderid not in mock_store.orders:
            return {
                "status": False,
                "message": "Order not found",
                "errorcode": "AG8005",
                "data": None
            }
        
        order = mock_store.orders[request.orderid]
        
        # Only allow cancellation if order is still open
        if order["status"] != "pending":
            return {
                "status": False,
                "message": "Cannot cancel completed order",
                "errorcode": "AG8008",
                "data": None
            }
        
        # Cancel the order
        order["status"] = "cancelled"
        order["orderstatus"] = "cancelled"
        order["updatetime"] = datetime.now().strftime("%d-%b-%Y %H:%M:%S")
        
        return {
            "status": True,
            "message": "SUCCESS",
            "errorcode": "",
            "data": {
                "orderid": request.orderid
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Cancel order error: {e}")
        return {
            "status": False,
            "message": "Order cancellation failed",
            "errorcode": "AG8009",
            "data": None
        }

@app.get("/rest/secure/angelbroking/order/v1/getOrderBook")
async def get_order_book(authorization: str = Header(...)):
    """Mock get order book endpoint"""
    try:
        validate_auth_token(authorization)
        
        orders = list(mock_store.orders.values())
        
        return {
            "status": True,
            "message": "SUCCESS",
            "errorcode": "",
            "data": orders
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get order book error: {e}")
        return {
            "status": False,
            "message": "Failed to get order book",
            "errorcode": "AG8010",
            "data": None
        }

@app.get("/rest/secure/angelbroking/order/v1/getTradeBook")
async def get_trade_book(authorization: str = Header(...)):
    """Mock get trade book endpoint"""
    try:
        validate_auth_token(authorization)
        
        trades = list(mock_store.trades.values())
        
        return {
            "status": True,
            "message": "SUCCESS",
            "errorcode": "",
            "data": trades
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get trade book error: {e}")
        return {
            "status": False,
            "message": "Failed to get trade book",
            "errorcode": "AG8011",
            "data": None
        }

@app.get("/rest/secure/angelbroking/order/v1/getPosition")
async def get_positions(authorization: str = Header(...)):
    """Mock get positions endpoint"""
    try:
        validate_auth_token(authorization)
        
        positions = list(mock_store.positions.values())
        
        return {
            "status": True,
            "message": "SUCCESS",
            "errorcode": "",
            "data": positions
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get positions error: {e}")
        return {
            "status": False,
            "message": "Failed to get positions",
            "errorcode": "AG8012",
            "data": None
        }

@app.get("/rest/secure/angelbroking/user/v1/getRMS")
async def get_rms(authorization: str = Header(...)):
    """Mock get RMS/margin endpoint"""
    try:
        token = validate_auth_token(authorization)
        
        # Find user by token
        user_id = None
        for uid, user_data in mock_store.authenticated_users.items():
            if user_data["jwt_token"] == token:
                user_id = uid
                break
        
        if user_id and user_id in mock_store.margin_data:
            margin_data = mock_store.margin_data[user_id]
        else:
            # Default margin data
            margin_data = {
                "availablecash": "100000",
                "collateral": "25000",
                "m2munrealized": "0",
                "m2mrealized": "0"
            }
        
        return {
            "status": True,
            "message": "SUCCESS",
            "errorcode": "",
            "data": margin_data
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get RMS error: {e}")
        return {
            "status": False,
            "message": "Failed to get margin data",
            "errorcode": "AG8013",
            "data": None
        }

@app.post("/rest/secure/angelbroking/historical/v1/getCandleData")
async def get_candle_data(request: CandleDataRequest, authorization: str = Header(...)):
    """Mock get candle data endpoint"""
    try:
        validate_auth_token(authorization)
        
        # Generate mock candle data
        from_date = datetime.strptime(request.fromdate, "%Y-%m-%d %H:%M")
        to_date = datetime.strptime(request.todate, "%Y-%m-%d %H:%M")
        
        # Get base price for the symbol
        base_price = mock_store.price_data.get(request.symboltoken, {}).get("ltp", 100.0)
        
        candles = []
        current_time = from_date
        
        # Generate candle data based on interval
        interval_minutes = {
            "ONE_MINUTE": 1,
            "THREE_MINUTE": 3,
            "FIVE_MINUTE": 5,
            "TEN_MINUTE": 10,
            "FIFTEEN_MINUTE": 15,
            "THIRTY_MINUTE": 30,
            "ONE_HOUR": 60
        }.get(request.interval, 1)
        
        while current_time <= to_date:
            # Generate OHLC data
            open_price = base_price * (0.98 + random.random() * 0.04)
            close_price = open_price * (0.99 + random.random() * 0.02)
            high_price = max(open_price, close_price) * (1.0 + random.random() * 0.01)
            low_price = min(open_price, close_price) * (0.99 - random.random() * 0.01)
            volume = random.randint(1000, 10000)
            
            candle = [
                current_time.strftime("%Y-%m-%d %H:%M:%S"),
                round(open_price, 2),
                round(high_price, 2),
                round(low_price, 2),
                round(close_price, 2),
                volume
            ]
            candles.append(candle)
            
            base_price = close_price  # Next candle starts from previous close
            current_time += timedelta(minutes=interval_minutes)
        
        return {
            "status": True,
            "message": "SUCCESS",
            "errorcode": "",
            "data": candles
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get candle data error: {e}")
        return {
            "status": False,
            "message": "Failed to get candle data",
            "errorcode": "AG8014",
            "data": None
        }

# Scrip Master Endpoint
@app.get("/OpenAPI_File/files/OpenAPIScripMaster.json")
async def get_scrip_master():
    """Mock scrip master endpoint"""
    return mock_store.scrip_master

# WebSocket Endpoints
@app.websocket("/ws/market-data")
async def websocket_market_data(websocket: WebSocket):
    """Mock WebSocket for market data"""
    await websocket.accept()
    mock_store.websocket_connections.append(websocket)
    
    try:
        # Send initial price data
        await websocket.send_json({
            "type": "connection_established",
            "message": "Connected to mock market data feed"
        })
        
        while True:
            # Keep connection alive and handle client messages
            try:
                data = await websocket.receive_text()
                message = json.loads(data)
                
                if message.get("task") == "subscribe":
                    await websocket.send_json({
                        "type": "subscription_confirmed",
                        "channel": message.get("channel"),
                        "token": message.get("token"),
                        "exchange": message.get("exchange")
                    })
                    
            except asyncio.TimeoutError:
                # Send heartbeat
                await websocket.send_json({"type": "heartbeat"})
                
    except WebSocketDisconnect:
        if websocket in mock_store.websocket_connections:
            mock_store.websocket_connections.remove(websocket)

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "active_connections": len(mock_store.websocket_connections),
        "total_orders": len(mock_store.orders),
        "total_trades": len(mock_store.trades)
    }

if __name__ == "__main__":
    import uvicorn
    
    print("🚀 Starting AngelOne Mock API Server...")
    print("📊 Mock stock data initialized with realistic prices")
    print("⚡ Real-time price updates enabled")
    print("🔗 WebSocket support for live data streaming")
    print("📈 Server will be available at: http://localhost:8001")
    print("\n🔧 To test with your trading bot:")
    print("   1. Update your bot's ANGEL_API_BASE to 'http://localhost:8001/rest'")
    print("   2. Use any clientcode/password combination to login") 
    print("   3. Available symbols: SBIN-EQ, RELIANCE-EQ, TCS-EQ, etc.")
    print("\n🎯 Happy Testing!")
    
    uvicorn.run(app, host="0.0.0.0", port=8001)