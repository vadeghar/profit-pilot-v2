#!/usr/bin/env python3
"""Test real broker connections with credentials from .env"""

import os
import sys

# Add project root to path (tests/ -> project root)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

import platform_config
from brokers.angel_one import AngelOneBroker
from brokers.icici import ICICIBroker

def load_env(path: str = None) -> dict:
    """Load environment variables from .env"""
    if path is None:
        path = str(platform_config.ENV_FILE)

    env = {}
    if os.path.exists(path):
        print(f"✓ Loading credentials from: {path}")
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    env[k.strip()] = v.strip()
        print(f"✓ Loaded {len(env)} environment variables")
    else:
        print(f"⚠ Warning: .env not found at {path}")
    return env

def test_angel_one(env: dict):
    print("\n" + "="*60)
    print("TESTING ANGEL ONE (SMARTAPI)")
    print("="*60)

    config = {
        'api_key': env.get('ANGEL_API_KEY'),
        'client_id': env.get('ANGEL_CLIENT_CODE'),
        'password': env.get('ANGEL_PASSWORD_OR_MPIN'),
        'totp_secret': env.get('ANGEL_TOTP_SECRET')
    }

    print(f"API Key: {config['api_key']}")
    print(f"Client ID: {config['client_id']}")

    broker = AngelOneBroker(config)

    print("\n1. Authenticating...")
    auth_ok = broker.authenticate()
    print(f"   Status: {'✅ SUCCESS' if auth_ok else '❌ FAILED'}")

    if auth_ok:
        print("\n2. Fetching positions...")
        positions = broker.get_positions()
        print(f"   Found {len(positions)} positions")
        if positions:
            print(f"   Sample: {positions[0].get('instrument', 'N/A')}")

        print("\n3. Getting quote for SBIN...")
        try:
            quote = broker.get_quote("SBIN", "NSE")
            print(f"   LTP: ₹{quote.last_price:.2f}")
        except Exception as e:
            print(f"   Error: {e}")

    return auth_ok

def test_icici(env: dict):
    print("\n" + "="*60)
    print("TESTING ICICI BREEZE")
    print("="*60)

    config = {
        'api_key': env.get('BREEZE_API_KEY'),
        'api_secret': env.get('BREEZE_API_SECRET'),
        'user_id': env.get('BREEZE_USER_ID'),
        'password': env.get('BREEZE_PASSWORD'),
        'session_token': env.get('BREEZE_SESSION_TOKEN')
    }

    print(f"API Key: {config['api_key']}")
    print(f"User ID: {config['user_id']}")

    broker = ICICIBroker(config)

    print("\n1. Authenticating...")
    auth_ok = broker.authenticate()
    print(f"   Status: {'✅ SUCCESS' if auth_ok else '❌ FAILED'}")

    if auth_ok:
        print("\n2. Fetching positions...")
        positions = broker.get_positions()
        print(f"   Found {len(positions)} positions")

    return auth_ok

if __name__ == '__main__':
    print("="*60)
    print("BROKER CREDENTIALS TEST")
    print("="*60)

    # Load credentials from .env
    env = load_env()

    if not env:
        print("\n❌ No credentials found!")
        print("Make sure .env exists in the project directory.")
        sys.exit(1)

    # Test brokers
    angel_ok = test_angel_one(env)
    icici_ok = test_icici(env)

    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"Angel One: {'✅ WORKING' if angel_ok else '❌ FAILED'}")
    print(f"ICICI Breeze: {'✅ WORKING' if icici_ok else '❌ FAILED'}")
