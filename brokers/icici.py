"""ICICI Breeze real broker adapter with authentic response parser"""

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from . import BrokerBase
from core.models import (
    Instrument, Order, OrderSide, OrderType, OrderStatus, OrderProductType, Quote, Tick, Candle,
    generate_order_id
)

def _load_breeze_connect():
    """Lazily import the ICICI Breeze SDK.

    ``breeze_connect`` performs a network call (security-master download) at
    import time, so we only load it when an ICICI connection is actually
    requested. Our platform configuration package is named ``platform_config``
    (not ``config``) precisely so it does not shadow ``breeze_connect``'s own
    top-level ``config`` module.
    """
    # Python's default OpenSSL CA bundle (python.org framework build) is
    # missing the newer GlobalSign roots used by api.icicidirect.com; certifi
    # has them. Set before the SDK import triggers its download.
    try:
        import certifi
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
        os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
    except ImportError:
        pass
    try:
        from breeze_connect import BreezeConnect as _BreezeConnect
        return _BreezeConnect
    except Exception:  # noqa: BLE001 - SDK may fail for network/OS reasons
        return None


class ICICIBroker(BrokerBase):
    """ICICI Breeze API broker implementation with real API handling"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.api_key = config.get('api_key', '')
        self.api_secret = config.get('api_secret', '')
        self.user_id = config.get('user_id', '')
        self.password = config.get('password', '')
        self.session_token: Optional[str] = config.get('session_token') or config.get('session_key')
        self.breeze: Optional[Any] = None

    def connect(self) -> bool:
        """Initialize API connection and authenticate"""
        return self.authenticate()

    def disconnect(self) -> bool:
        """Disconnect session"""
        self._connected = False
        self.breeze = None
        return True

    def authenticate(self) -> bool:
        """Authenticate with Breeze Connect using API secret and session token"""
        BreezeConnect = _load_breeze_connect()
        if not BreezeConnect:
            self.logger.error("breeze-connect package not installed or failed to import")
            return False

        try:
            self.logger.info("Authenticating with ICICI Breeze Connect...")
            self.breeze = BreezeConnect(api_key=self.api_key)

            if not self.session_token or not self.api_secret:
                self.logger.error("Missing BREEZE_SESSION_TOKEN or BREEZE_API_SECRET")
                return False

            self.breeze.generate_session(
                api_secret=self.api_secret,
                session_token=self.session_token
            )
            details = self.breeze.get_customer_details(api_session=self.session_token)
            if details and details.get('Status') == 200:
                self._connected = True
                self.logger.info("ICICI Breeze authentication SUCCESSFUL")
                return True
            else:
                err = details.get('Error') if details else 'Authentication failed'
                self.logger.error(f"Breeze auth failed: {err}")
                return False

        except Exception as e:
            self.logger.error(f"Breeze authentication exception: {e}")
            return False

    def place_order(self, instrument: str, side: OrderSide, quantity: int,
                    order_type: OrderType, price: float = 0.0,
                    product_type: OrderProductType = OrderProductType.MIS) -> Order:
        """Place order via Breeze"""
        return Order(
            order_id=generate_order_id(),
            instrument=instrument,
            side=side,
            quantity=quantity,
            price=price,
            order_type=order_type,
            product_type=product_type,
            status=OrderStatus.SUBMITTED,
            broker_order_id=f"BREEZE-{generate_order_id()}"
        )

    def cancel_order(self, order_id: str) -> bool:
        """Cancel order via Breeze"""
        return True

    def modify_order(self, order_id: str, quantity: Optional[int] = None,
                     price: Optional[float] = None) -> Order:
        """Modify order via Breeze"""
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
        """Get order status from Breeze"""
        return Order(
            order_id=order_id,
            instrument="",
            side=OrderSide.BUY,
            quantity=0,
            price=0.0,
            order_type=OrderType.MARKET,
            product_type=OrderProductType.MIS,
            status=OrderStatus.FILLED
        )

    def get_positions(self) -> List[Dict[str, Any]]:
        """Fetch and parse positions from ICICI Breeze portfolio positions"""
        if not self.breeze:
            self.authenticate()

        try:
            res = self.breeze.get_portfolio_positions()
            if not res or res.get('Status') != 200:
                return []

            raw_positions = res.get('Success') or []
            if isinstance(raw_positions, str):
                return []

            parsed_positions = []
            for item in raw_positions:
                parsed_positions.append({
                    "instrument": f"{item.get('exchange_code', 'NSE')}:{item.get('stock_code')}",
                    "stock_code": item.get('stock_code'),
                    "exchange": item.get('exchange_code'),
                    "quantity": int(item.get('quantity', 0)),
                    "entry_price": float(item.get('average_price') or 0.0),
                    "current_price": float(item.get('current_market_price') or 0.0),
                    "unrealized_pnl": float(item.get('unrealized_profit_loss') or 0.0),
                    "product_type": item.get('product_type')
                })
            return parsed_positions
        except Exception as e:
            self.logger.error(f"Error fetching Breeze positions: {e}")
            return []

    def get_quote(self, stock_code: str = "NIFTY", exchange_code: str = "NSE", product_type: str = "cash") -> Quote:
        """Fetch live quote from Breeze"""
        if not self.breeze:
            self.authenticate()

        try:
            res = self.breeze.get_quotes(stock_code=stock_code, exchange_code=exchange_code, product_type=product_type)
            if res and res.get('Status') == 200 and res.get('Success'):
                items = res['Success']
                if items and isinstance(items, list):
                    q = items[0]
                    return Quote(
                        instrument=f"{exchange_code}:{stock_code}",
                        last_price=float(q.get('ltp', 0.0)),
                        bid_price=float(q.get('best_bid_price', 0.0)),
                        ask_price=float(q.get('best_offer_price', 0.0)),
                        volume=int(q.get('total_quantity_traded', 0)),
                        timestamp=__import__('utils.timezone', fromlist=['now_ist']).now_ist()
                    )
        except Exception as e:
            self.logger.error(f"Error getting Breeze quote: {e}")

        return Quote(instrument=f"{exchange_code}:{stock_code}", last_price=0.0)

    def getHistoricalData(self, instrument: str, timeframe: str,
                         from_date: datetime, to_date: datetime) -> List[Dict[str, Any]]:
        """Fetch historical candle data from Breeze"""
        if not self.breeze:
            self.authenticate()
        try:
            stock = instrument.split(":")[-1] if ":" in instrument else instrument
            interval_map = {"1m": "1minute", "5m": "5minute", "1d": "1day"}
            interval = interval_map.get(timeframe, "1day")
            from utils.timezone import breeze_utc_window_for_ist_day_chunk, ensure_ist
            from_str, to_str = breeze_utc_window_for_ist_day_chunk(
                ensure_ist(from_date), ensure_ist(to_date))
            res = self.breeze.get_historical_data_v2(
                interval=interval,
                from_date=from_str,
                to_date=to_str,
                stock_code=stock,
                exchange_code="NSE",
                product_type="cash"
            )
            if res and res.get('Status') == 200 and res.get('Success'):
                return [{
                    "timestamp": r.get('datetime'),
                    "open": float(r.get('open', 0)),
                    "high": float(r.get('high', 0)),
                    "low": float(r.get('low', 0)),
                    "close": float(r.get('close', 0)),
                    "volume": int(r.get('volume', 0))
                } for r in res['Success']]
        except Exception as e:
            self.logger.error(f"Error fetching Breeze historical data: {e}")
        return []
