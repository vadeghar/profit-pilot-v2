#!/usr/bin/env python3
"""Comprehensive Live Broker Connectivity & Response Parser Test Suite"""

import os
import sys
from datetime import datetime, timezone
import json

# Add project root to path (tests/ -> project root)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import platform_config
from brokers.angel_one import AngelOneBroker
from brokers.icici import ICICIBroker
from core.models import OrderStatus, OrderSide

def load_env(path: str = None) -> dict:
    path = path or str(platform_config.ENV_FILE)
    env = {}
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    env[k.strip()] = v.strip()
    return env

def test_angel_one(env: dict):
    print("\n" + "="*60)
    print("TESTING ANGEL ONE (SMARTAPI) WITH REAL CREDENTIALS")
    print("="*60)

    config = {
        'api_key': env.get('ANGEL_API_KEY'),
        'client_id': env.get('ANGEL_CLIENT_CODE'),
        'password': env.get('ANGEL_PASSWORD_OR_MPIN'),
        'totp_secret': env.get('ANGEL_TOTP_SECRET')
    }

    broker = AngelOneBroker(config)
    
    # 1. Test Authentication
    print("1. Authenticating via pyotp TOTP...")
    auth_ok = broker.authenticate()
    print(f"   Auth Status: {'SUCCESS' if auth_ok else 'FAILED'}")
    assert auth_ok, "Angel One authentication failed!"

    # 2. Test Positions Parsing
    print("\n2. Fetching & Parsing Positions...")
    positions = broker.get_positions()
    print(f"   Parsed {len(positions)} positions from Angel One.")
    for i, p in enumerate(positions, 1):
        print(f"   [{i}] Instrument: {p['instrument']} | Net Qty: {p['quantity']} | PnL: ₹{p['total_pnl']} | LTP: ₹{p['current_price']}")
        # Assert expected fields exist and have correct types
        assert isinstance(p['quantity'], int)
        assert isinstance(p['total_pnl'], float)
        assert isinstance(p['current_price'], float)
    print("   ✓ Positions parsing verified without schema errors!")

    # 3. Test Order Book Parsing
    print("\n3. Fetching & Parsing Order Book...")
    orders = broker.get_order_book()
    print(f"   Parsed {len(orders)} orders from Angel One.")
    for i, o in enumerate(orders, 1):
        print(f"   [{i}] Order ID: {o.order_id} | Side: {o.side.value} | Instrument: {o.instrument} | Status: {o.status.value} | Px: ₹{o.price} | Filled: {o.filled_quantity}")
        assert isinstance(o.status, OrderStatus)
        assert isinstance(o.side, OrderSide)
    print("   ✓ Order book parsing verified without schema errors!")

    # 4. Test Live Quote Parsing (SBIN-EQ token 3045)
    print("\n4. Fetching Live Quote for SBIN-EQ...")
    quote = broker.get_quote("NSE:SBIN-EQ", symbol_token="3045", exchange="NSE")
    print(f"   Parsed Quote: Instrument: {quote.instrument} | LTP: ₹{quote.last_price}")
    assert quote.last_price > 0, "LTP should be positive"
    print("   ✓ Live Quote parsing verified without schema errors!")

    return True

def test_icici_breeze(env: dict):
    print("\n" + "="*60)
    print("TESTING ICICI DIRECT (BREEZE CONNECT) WITH REAL CREDENTIALS")
    print("="*60)

    config = {
        'api_key': env.get('BREEZE_API_KEY'),
        'api_secret': env.get('BREEZE_API_SECRET'),
        'user_id': env.get('BREEZE_USER_ID'),
        'password': env.get('BREEZE_PASSWORD'),
        'session_token': env.get('BREEZE_SESSION_TOKEN')
    }

    broker = ICICIBroker(config)

    # 1. Test Authentication
    print("1. Authenticating with Breeze Connect...")
    auth_ok = broker.authenticate()
    print(f"   Auth Status: {'SUCCESS' if auth_ok else 'FAILED'}")
    assert auth_ok, "ICICI Breeze authentication failed!"

    # 2. Test Customer Details & Funds
    print("\n2. Fetching Customer Details & Available Funds...")
    funds = broker.breeze.get_funds()
    assert funds.get('Status') == 200, f"Funds query failed: {funds}"
    success_funds = funds.get('Success', {})
    print(f"   Bank Balance: ₹{success_funds.get('total_bank_balance')}")
    print(f"   Unallocated Balance: ₹{success_funds.get('unallocated_balance')}")
    print("   ✓ Funds parsing verified without schema errors!")

    # 3. Test Positions Parsing
    print("\n3. Fetching & Parsing Positions...")
    positions = broker.get_positions()
    print(f"   Parsed {len(positions)} positions from Breeze.")
    print("   ✓ Portfolio positions parser handled empty/active list correctly without errors!")

    # 4. Test Live Quote Parsing (NIFTY Index)
    print("\n4. Fetching Live Quote for NIFTY from Breeze...")
    quote = broker.get_quote(stock_code="NIFTY", exchange_code="NSE", product_type="cash")
    print(f"   Parsed Quote: Instrument: {quote.instrument} | LTP: ₹{quote.last_price} | Bid: ₹{quote.bid_price} | Ask: ₹{quote.ask_price}")
    assert quote.last_price > 0, "LTP should be positive"
    print("   ✓ Breeze live quote parsed successfully without schema errors!")

    return True

if __name__ == '__main__':
    env = load_env()
    angel_passed = False
    breeze_passed = False
    
    try:
        angel_passed = test_angel_one(env)
    except Exception as e:
        print(f"Angel One test failed: {e}")
        import traceback
        traceback.print_exc()

    try:
        breeze_passed = test_icici_breeze(env)
    except Exception as e:
        print(f"ICICI Breeze test failed: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "="*60)
    print("FINAL SUMMARY OF REAL BROKER INTEGRATION TESTS")
    print("="*60)
    print(f"Angel One SmartAPI:  {'PASS' if angel_passed else 'FAIL'}")
    print(f"ICICI Breeze:        {'PASS' if breeze_passed else 'FAIL'}")
    print("="*60)
