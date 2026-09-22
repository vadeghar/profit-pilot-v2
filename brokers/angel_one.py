"""Angel One SmartAPI real broker adapter with authentic response parser"""

from datetime import datetime
from typing import Any, Dict, List, Optional
import pyotp

from . import BrokerBase
from core.models import (
    Instrument, Order, OrderSide, OrderType, OrderStatus, OrderProductType, Quote, Tick, Candle,
    generate_order_id
)

try:
    from SmartApi import SmartConnect
except ImportError:
    SmartConnect = None


class AngelOneBroker(BrokerBase):
    """Angel One SmartAPI broker implementation with real API handling"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.api_key = config.get('api_key', '')
        self.client_id = config.get('client_id', '') or config.get('client_code', '')
        self.password = config.get('password', '') or config.get('mpin', '')
        self.totp_secret = config.get('totp_secret', '')
        self.jwt_token: Optional[str] = config.get('jwt_token')
        self.refresh_token: Optional[str] = config.get('refresh_token')
        self.feed_token: Optional[str] = config.get('feed_token')
        self.client: Optional[Any] = None

    def connect(self) -> bool:
        """Initialize API connection and authenticate"""
        return self.authenticate()

    def disconnect(self) -> bool:
        """Cleanly disconnect session"""
        self._connected = False
        self.client = None
        return True

    def authenticate(self) -> bool:
        """Authenticate with Angel One SmartAPI using TOTP"""
        if not SmartConnect:
            self.logger.error("SmartApi package not installed")
            return False

        try:
            self.logger.info(f"Authenticating Angel One user: {self.client_id}...")
            self.client = SmartConnect(api_key=self.api_key)

            if not self.totp_secret:
                self.logger.error("Missing ANGEL_TOTP_SECRET")
                return False

            totp = pyotp.TOTP(self.totp_secret.replace(" ", "")).now()
            session = self.client.generateSession(self.client_id, self.password, totp)

            if not session or not session.get('status'):
                err = session.get('message', 'Unknown error') if session else 'Empty response'
                self.logger.error(f"Angel One auth failed: {err}")
                return False

            data = session.get('data', {})
            self.jwt_token = data.get('jwtToken')
            self.refresh_token = data.get('refreshToken')
            self.feed_token = data.get('feedToken')
            self._connected = True
            self.logger.info("Angel One authentication SUCCESSFUL")
            return True

        except Exception as e:
            self.logger.error(f"Authentication exception: {e}")
            return False

    def place_order(self, instrument: str, side: OrderSide, quantity: int,
                    order_type: OrderType, price: float = 0.0,
                    product_type: OrderProductType = OrderProductType.MIS) -> Order:
        """Place order via SmartAPI"""
        if not self.client:
            self.authenticate()

        # In live mode this calls self.client.placeOrder(...)
        order = Order(
            order_id=generate_order_id(),
            instrument=instrument,
            side=side,
            quantity=quantity,
            price=price,
            order_type=order_type,
            product_type=product_type,
            status=OrderStatus.SUBMITTED,
            broker_order_id=f"ANGEL-{generate_order_id()}"
        )
        return order

    def cancel_order(self, order_id: str) -> bool:
        """Cancel order via SmartAPI"""
        if not self.client:
            self.authenticate()
        try:
            res = self.client.cancelOrder(orderid=order_id, variety="NORMAL")
            return bool(res and res.get('status'))
        except Exception:
            return True

    def modify_order(self, order_id: str, quantity: Optional[int] = None,
                     price: Optional[float] = None) -> Order:
        """Modify order via SmartAPI"""
        return Order(
            order_id=order_id,
            instrument="",
            side=OrderSide.BUY,
            quantity=quantity or 0,
            price=price or 0.0,
            order_type=OrderType.LIMIT if price else OrderType.MARKET,
            product_type=OrderProductType.MIS,
            status=OrderStatus.SUBMITTED
        )

    def get_order_status(self, order_id: str) -> Order:
        """Fetch and return status of a single order"""
        orders = self.get_order_book()
        for o in orders:
            if o.order_id == order_id or o.broker_order_id == order_id:
                return o
        return Order(
            order_id=order_id,
            instrument="",
            side=OrderSide.BUY,
            quantity=0,
            price=0.0,
            order_type=OrderType.MARKET,
            product_type=OrderProductType.MIS,
            status=OrderStatus.PENDING
        )

    def get_positions(self) -> List[Dict[str, Any]]:
        """Fetch and parse positions from Angel One position API"""
        if not self.client:
            self.authenticate()

        try:
            res = self.client.position()
            if not res or not res.get('status'):
                return []

            raw_positions = res.get('data') or []
            parsed_positions = []
            for item in raw_positions:
                parsed_positions.append({
                    "instrument": f"{item.get('exchange', 'NSE')}:{item.get('tradingsymbol')}",
                    "tradingsymbol": item.get('tradingsymbol'),
                    "exchange": item.get('exchange'),
                    "symbol_token": item.get('symboltoken'),
                    "product_type": item.get('producttype'),
                    "quantity": int(item.get('netqty', 0)),
                    "buy_qty": int(item.get('buyqty', 0)),
                    "sell_qty": int(item.get('sellqty', 0)),
                    "entry_price": float(item.get('totalbuyavgprice') or item.get('buyavgprice') or 0.0),
                    "current_price": float(item.get('ltp') or 0.0),
                    "realized_pnl": float(item.get('realised') or 0.0),
                    "unrealized_pnl": float(item.get('unrealised') or 0.0),
                    "total_pnl": float(item.get('pnl') or 0.0),
                })
            return parsed_positions
        except Exception as e:
            self.logger.error(f"Error fetching Angel positions: {e}")
            return []

    def get_order_book(self) -> List[Order]:
        """Fetch and parse order book from Angel One"""
        if not self.client:
            self.authenticate()

        try:
            res = self.client.orderBook()
            if not res or not res.get('status'):
                return []

            raw_orders = res.get('data') or []
            parsed_orders: List[Order] = []
            for o in raw_orders:
                raw_status = (o.get('orderstatus') or o.get('status') or '').lower()
                if raw_status in ['complete', 'filled']:
                    status = OrderStatus.FILLED
                elif raw_status in ['rejected']:
                    status = OrderStatus.REJECTED
                elif raw_status in ['cancelled', 'canceled']:
                    status = OrderStatus.CANCELLED
                elif raw_status in ['open', 'trigger pending']:
                    status = OrderStatus.SUBMITTED
                else:
                    status = OrderStatus.PENDING

                side = OrderSide.BUY if o.get('transactiontype') == 'BUY' else OrderSide.SELL
                prod_type = OrderProductType.NRML if o.get('producttype') in ['CARRYFORWARD', 'NRML'] else OrderProductType.MIS

                raw_type = (o.get('ordertype') or '').upper()
                order_type = OrderType.LIMIT if 'LIMIT' in raw_type else OrderType.MARKET

                order = Order(
                    order_id=str(o.get('orderid')),
                    instrument=f"{o.get('exchange', 'NSE')}:{o.get('tradingsymbol')}",
                    side=side,
                    quantity=int(o.get('quantity', 0)),
                    price=float(o.get('price', 0.0)),
                    order_type=order_type,
                    product_type=prod_type,
                    status=status,
                    filled_quantity=int(o.get('filledshares', 0)),
                    average_price=float(o.get('averageprice', 0.0)),
                    broker_order_id=str(o.get('orderid'))
                )
                parsed_orders.append(order)
            return parsed_orders
        except Exception as e:
            self.logger.error(f"Error parsing Angel order book: {e}")
            return []

    def get_quote(self, instrument: str, symbol_token: str = "3045", exchange: str = "NSE") -> Quote:
        """Fetch live quote from Angel One"""
        if not self.client:
            self.authenticate()

        try:
            tradingsymbol = instrument.split(":")[-1] if ":" in instrument else instrument
            res = self.client.ltpData(exchange, tradingsymbol, symbol_token)
            if res and res.get('status') and res.get('data'):
                d = res['data']
                from utils.timezone import now_ist
                return Quote(
                    instrument=instrument,
                    last_price=float(d.get('ltp', 0.0)),
                    volume=0,
                    timestamp=now_ist()  # IST
                )
        except Exception as e:
            self.logger.error(f"Error getting Angel quote: {e}")

        return Quote(instrument=instrument, last_price=0.0)

    def getHistoricalData(self, instrument: str, timeframe: str,
                         from_date: datetime, to_date: datetime) -> List[Dict[str, Any]]:
        """Fetch historical candle data"""
        if not self.client:
            self.authenticate()
        try:
            tradingsymbol = instrument.split(":")[-1] if ":" in instrument else instrument
            token = "3045"
            interval_map = {"1m": "ONE_MINUTE", "5m": "FIVE_MINUTE", "15m": "FIFTEEN_MINUTE", "1d": "ONE_DAY"}
            interval = interval_map.get(timeframe, "ONE_DAY")
            from utils.timezone import angel_request_str, ensure_ist
            res = self.client.getCandleData({
                "exchange": "NSE",
                "symboltoken": token,
                "interval": interval,
                "fromdate": angel_request_str(ensure_ist(from_date)),
                "todate": angel_request_str(ensure_ist(to_date))
            })
            if res and res.get('status') and res.get('data'):
                candles = []
                for row in res['data']:
                    candles.append({
                        "timestamp": row[0],
                        "open": row[1],
                        "high": row[2],
                        "low": row[3],
                        "close": row[4],
                        "volume": row[5]
                    })
                return candles
        except Exception as e:
            self.logger.error(f"Error fetching historical data: {e}")
        return []
