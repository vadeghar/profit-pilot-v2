#!/usr/bin/env python3
"""Interactive Web Dashboard & Control Center for Trading Platform v2"""

import sys
import os
import json
import time
import queue
import threading
from datetime import datetime, timedelta
from utils.timezone import IST, ensure_ist, now_ist
from typing import Any, Dict, List, Optional
from pathlib import Path

# Add platform directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import asyncio
from typing import AsyncGenerator

from core.models import (
    OrderSide, OrderType, OrderProductType, Candle, Trade, BacktestResult, BacktestStatus
)
from strategies import StrategyRegistry
from backtest import BacktestEngine, BacktestConfig
from brokers import MockBroker, BrokerFactory
from execution import ExecutionEngine, RiskManager
from market_data import MarketDataManager, ProviderFactory
from utils import format_inr
import platform_config
from backtest.job_manager import job_manager
from platform_config import (
    get_all_instruments,
    get_strategy_instruments,
    build_dropdown_list,
    get_strategy_defaults,
)

app = FastAPI(title="Trading Strategy Execution Platform", version="2.0.0")

# In-memory session state for interactive platform
mock_broker = MockBroker()
market_data = MarketDataManager(mock_broker)
risk_manager = RiskManager()
execution_engine = ExecutionEngine(mock_broker, {'orderRetryAttempts': 3})

# Running active strategy instances
active_strategies: Dict[str, Any] = {}
recent_backtests: List[Dict[str, Any]] = []


@app.get("/.well-known/appspecific/com.chrome.devtools.json")
def chrome_devtools_config():
    """Return an empty config for Chrome DevTools' optional discovery request."""
    return JSONResponse(content={})


@app.get("/favicon.ico")
def favicon():
    """Serve a small dashboard favicon without requiring a separate asset file."""
    return Response(
        content=(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<rect width="64" height="64" rx="12" fill="#0f172a"/>'
            '<path d="M12 44 24 31l9 7 17-20" fill="none" stroke="#22d3ee" '
            'stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>'
            '<circle cx="50" cy="18" r="4" fill="#34d399"/>'
            '</svg>'
        ).encode("utf-8"),
        media_type="image/svg+xml",
    )

# Dropdown list shown on spot/equity strategy cards from universe.yaml.
GLOBAL_UNIVERSE: list[dict[str, str]] = build_dropdown_list()
EQUITY_UNIVERSE: list[dict[str, str]] = [
  {"label": item["label"], "value": item["symbol"]}
  for item in get_all_instruments()
]
EQUITY_SYMBOLS = ", ".join(item["value"] for item in EQUITY_UNIVERSE)
STRATEGY_CATALOG = {
    "mcx_trend_rider": {
        "id": "mcx_trend_rider",
        "name": "MCX Trend Rider",
        "badge": "Institutional Grade",
        "badge_color": "emerald",
        "icon": "fa-fire-flame-curved",
        "description": "Turtle-inspired dual Donchian (20/55) breakout with ADX(14)>=20 filter, 1% volatility sizing, and Chandelier ATR trailing stop.",
        "asset_class": "MCX Commodities (Futures)",
        "data_provider": "Breeze",
        "default_symbols": "MCX_GOLDM, MCX_SILVERM, MCX_CRUDEOIL",
        "allowed_symbols": [
            {"label": "MCX Trend Basket (GoldM + SilverM + CrudeOil)", "value": "MCX_GOLDM, MCX_SILVERM, MCX_CRUDEOIL"},
            {"label": "Gold Mini Futures (MCX_GOLDM)", "value": "MCX_GOLDM"},
            {"label": "Silver Mini Futures (MCX_SILVERM)", "value": "MCX_SILVERM"},
            {"label": "Crude Oil Futures (MCX_CRUDEOIL)", "value": "MCX_CRUDEOIL"}
        ],
        "default_timeframe": "1d",
        "default_capital": 100000.0,
        "default_start_date": "2026-01-01",
        "default_end_date": "2026-09-23",
        "default_params": {
            "capital": 100000.0,
            "risk_pct": 0.01,
            "use_loser_filter": True,
            "use_sma_filter": False,
            "adx_threshold": 20.0
        },
        "param_schema": [
            {"key": "risk_pct", "label": "Risk % per Trade", "type": "number", "default": 0.01, "step": 0.005},
            {"key": "adx_threshold", "label": "ADX Trend Filter Threshold", "type": "number", "default": 20.0, "step": 1.0},
            {"key": "use_loser_filter", "label": "Skip 20d after loss (Turtle Rule)", "type": "boolean", "default": True}
        ],
        "historical_stats": {
            "return_pct": "+46.67%",
            "win_rate": "44.4%",
            "max_dd": "8.84%",
            "sharpe": "0.79"
        }
    },
    "ema_crossover": {
        "id": "ema_crossover",
        "name": "EMA Crossover Momentum",
        "badge": "Trend Following",
        "badge_color": "cyan",
        "icon": "fa-chart-line",
        "description": "Fast & Slow Exponential Moving Average crossover system with ATR stop and trailing risk management.",
        "asset_class": "NSE Equities / Indices",
        "data_provider": "Breeze",
        "default_symbols": EQUITY_SYMBOLS,
        "use_global_universe": True,
        "allowed_symbols": EQUITY_UNIVERSE,
        "default_timeframe": "1d",
        "default_capital": 100000.0,
        "default_start_date": "2026-01-01",
        "default_end_date": "2026-09-23",
        "default_params": {
            "fast_period": 9,
            "slow_period": 21,
            "quantity": 1
        },
        "param_schema": [
            {"key": "fast_period", "label": "Fast EMA Period", "type": "number", "default": 9, "step": 1},
            {"key": "slow_period", "label": "Slow EMA Period", "type": "number", "default": 21, "step": 1},
            {"key": "quantity", "label": "Order Quantity", "type": "number", "default": 1, "step": 1}
        ],
        "historical_stats": {
            "return_pct": "Benchmarked",
            "win_rate": "48.2%",
            "max_dd": "12.4%",
            "sharpe": "0.85"
        }
    },
    "rsi": {
        "id": "rsi",
        "name": "RSI Mean Reversion",
        "badge": "Counter-Trend",
        "badge_color": "purple",
        "icon": "fa-wave-square",
        "description": "Exploits extreme overbought/oversold swings in oscillator range with trailing breakeven protection.",
        "asset_class": "NSE Equities / Indices",
        "data_provider": "Breeze",
        "default_symbols": EQUITY_SYMBOLS,
        "use_global_universe": True,
        "allowed_symbols": EQUITY_UNIVERSE,
        "default_timeframe": "1d",
        "default_capital": 100000.0,
        "default_start_date": "2026-01-01",
        "default_end_date": "2026-09-23",
        "default_params": {
            "period": 14,
            "oversold": 30.0,
            "overbought": 70.0,
            "quantity": 1
        },
        "param_schema": [
            {"key": "period", "label": "RSI Calculation Period", "type": "number", "default": 14, "step": 1},
            {"key": "oversold", "label": "Oversold Buy Threshold", "type": "number", "default": 30.0, "step": 1},
            {"key": "overbought", "label": "Overbought Sell Threshold", "type": "number", "default": 70.0, "step": 1},
            {"key": "quantity", "label": "Order Quantity", "type": "number", "default": 1, "step": 1}
        ],
        "historical_stats": {
            "return_pct": "Benchmarked",
            "win_rate": "52.0%",
            "max_dd": "9.5%",
            "sharpe": "0.91"
        }
    },
    "breakout": {
        "id": "breakout",
        "name": "Donchian Breakout 20",
        "badge": "Volatility Breakout",
        "badge_color": "amber",
        "icon": "fa-arrows-split-up-and-left",
        "description": "Pure Donchian 20-period price channel breakout taking positions on new periodic high/low closes.",
        "asset_class": "NSE Equities / Multi-Asset",
        "data_provider": "Breeze",
        "default_symbols": EQUITY_SYMBOLS,
        "use_global_universe": True,
        "allowed_symbols": EQUITY_UNIVERSE,
        "default_timeframe": "1d",
        "default_capital": 100000.0,
        "default_start_date": "2026-01-01",
        "default_end_date": "2026-09-23",
        "default_params": {
            "lookback": 20,
            "quantity": 1
        },
        "param_schema": [
            {"key": "lookback", "label": "Channel Lookback Periods", "type": "number", "default": 20, "step": 1},
            {"key": "quantity", "label": "Order Quantity", "type": "number", "default": 1, "step": 1}
        ],
        "historical_stats": {
            "return_pct": "Benchmarked",
            "win_rate": "41.5%",
            "max_dd": "14.2%",
            "sharpe": "0.72"
        }
    },
    "index_oi_momentum": {
        "id": "index_oi_momentum",
        "name": "Index Options OI Momentum",
        "badge": "Intraday Options Buyer",
        "badge_color": "rose",
        "icon": "fa-bolt",
        "description": "OI-velocity + price-momentum intraday option buying on NIFTY / BANKNIFTY / SENSEX ATM options. Auto base/expiry mode per index expiry calendar, Rs-risk sizing, partial-close + trailing, quick exits.",
        "asset_class": "Index Options (Intraday)",
        "data_provider": "Breeze",
        "default_symbols": ", ".join(get_strategy_instruments("index_oi_momentum")),
        "allowed_symbols": [
            {"label": "Nifty 50 Options (NIFTY)", "value": "NIFTY"},
            {"label": "Bank Nifty Options (BANKNIFTY)", "value": "BANKNIFTY"},
            {"label": "Sensex Options (SENSEX)", "value": "SENSEX"}
        ],
        "default_timeframe": "tick",
        "default_capital": 100000.0,
        "default_start_date": "2026-01-01",
        "default_end_date": "2026-09-23",
        "default_params": {
            "capital": 100000.0,
            "k": 4.0,
            "oi_window_s": 75,
            "risk_pct": 0.0075,
            "sl_pct": 0.22,
            "spread_threshold": 0.012,
            "max_trades_day": 7
        },
        "param_schema": [
            {"key": "k", "label": "OI Velocity Multiplier k", "type": "number", "default": 4.0, "step": 0.5},
            {"key": "oi_window_s", "label": "OI Window (seconds)", "type": "number", "default": 75, "step": 5},
            {"key": "risk_pct", "label": "Risk % per Trade", "type": "number", "default": 0.0075, "step": 0.0025},
            {"key": "sl_pct", "label": "Hard SL % of Premium", "type": "number", "default": 0.22, "step": 0.01},
            {"key": "spread_threshold", "label": "Max Spread / LTP", "type": "number", "default": 0.012, "step": 0.002}
        ],
        "historical_stats": {
            "return_pct": "Tick Backtested",
            "win_rate": "30-40% target",
            "max_dd": "Capped -3%/day",
            "sharpe": "Intraday"
        },
        # Auto-start on app startup during market hours (09:15-15:30 IST)
        "auto_start_enabled": True,
        "auto_start_symbols": ["NIFTY", "BANKNIFTY", "SENSEX"],
        "auto_start_variants": ["base", "expiry"],
        "auto_start_capital": 100000.0,
        # Paper-only live strategy - no traditional backtest button in modal
        "paper_only_live": True
    },
    "equity_swing_vcp": {
        "id": "equity_swing_vcp",
        "name": "Equity Swing VCP",
        "badge": "Minervini Swing",
        "badge_color": "emerald",
        "icon": "fa-arrow-trend-up",
        "description": "Mark Minervini 8-Point Trend Template + Volatility Contraction Pattern (VCP) with volume breakout confirmation and staged 21 EMA trailing stop.",
        "asset_class": "NSE Equities (Positional 1-3m)",
        "data_provider": "Breeze",
        "default_symbols": EQUITY_SYMBOLS,
        "use_global_universe": True,
        "allowed_symbols": EQUITY_UNIVERSE,
        "default_timeframe": "1d",
        "default_capital": 100000.0,
        "default_start_date": "2026-01-01",
        "default_end_date": "2026-09-23",
        "default_params": {
            "capital": 100000.0,
            "risk_pct": 0.0125,
            "stop_pct": 0.07,
            "volume_breakout_mult": 1.3,
            "partial_r": 2.0
        },
        "param_schema": [
            {"key": "risk_pct", "label": "Risk % per Trade", "type": "number", "default": 0.0125, "step": 0.0025},
            {"key": "stop_pct", "label": "Stop Loss Limit (0.07 = 7%)", "type": "number", "default": 0.07, "step": 0.01},
            {"key": "volume_breakout_mult", "label": "Volume Surge vs 50 SMA", "type": "number", "default": 1.3, "step": 0.1}
        ],
        "historical_stats": {
            "return_pct": "Multi-Stock Screened",
            "win_rate": "42.0%+",
            "max_dd": "9.03%",
            "sharpe": "Positive Alpha"
        }
    },
    "lorentzian_ml": {
        "id": "lorentzian_ml",
        "name": "Lorentzian Classification ML",
        "badge": "Machine Learning",
        "badge_color": "purple",
        "icon": "fa-brain",
        "description": "Approximate nearest-neighbor classifier with Lorentzian distance over normalized RSI/WaveTrend/CCI/ADX features, Kalman-regime + volatility filters, and Nadaraya-Watson kernel exits. Fed by Breeze OHLC.",
        "asset_class": "NSE Equities / Indices",
        "data_provider": "Breeze",
        "use_global_universe": True,
        "default_symbols": EQUITY_SYMBOLS,
        "allowed_symbols": EQUITY_UNIVERSE,
        "default_timeframe": "1d",
        "default_capital": 100000.0,
        "default_start_date": "2026-01-01",
        "default_end_date": "2026-09-23",
        "default_params": {
            "timeframe": "1d",
            "ticker": "NSE:NIFTY",
            "neighbors_count": 8,
            "max_bars_back": 2000,
            "feature_count": 5,
            "use_volatility_filter": True,
            "use_regime_filter": True,
            "regime_threshold": -0.1,
            "use_kernel_filter": True,
            "use_dynamic_exits": False,
            "bollinger_enabled": False,
            "bollinger_length": 19,
            "bollinger_mult": 2.36,
            "bollinger_offset": 0,
            "bollinger_ma_type": "WMA",
            "quantity": 1,
            "min_history_bars": 60
        },
        "param_schema": [
            {"key": "timeframe", "label": "Timeframe", "type": "select", "options": ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1wk"], "default": "1d"},
            {"key": "neighbors_count", "label": "Neighbors Count (k)", "type": "number", "default": 8, "step": 1},
            {"key": "feature_count", "label": "Feature Count", "type": "number", "default": 5, "step": 1},
            {"key": "use_dynamic_exits", "label": "Use Kernel Dynamic Exits", "type": "boolean", "default": False},
            {"key": "regime_threshold", "label": "Regime Threshold", "type": "number", "default": -0.1, "step": 0.1},
            {"key": "quantity", "label": "Order Quantity", "type": "number", "default": 1, "step": 1}
        ],
        "bollinger_schema": {
            "label": "Bollinger Bands",
            "enable_key": "bollinger_enabled",
            "fields": [
                {"key": "bollinger_length", "label": "Length", "type": "number", "default": 19, "step": 1},
                {"key": "bollinger_mult", "label": "Multiplier (Mult)", "type": "number", "default": 2.36, "step": 0.01},
                {"key": "bollinger_offset", "label": "Offset", "type": "number", "default": 0, "step": 1},
                {"key": "bollinger_ma_type", "label": "MA Type", "type": "select", "options": ["SMA", "WMA", "EMA", "DEMA", "TEMA"], "default": "WMA"}
            ]
        },
        "historical_stats": {
            "return_pct": "KNN Backtested",
            "win_rate": "Signal-Driven",
            "max_dd": "4-Bar Holding",
            "sharpe": "ML"
        }
    }
}


class BacktestRequest(BaseModel):
    strategy_id: str
    instrument: str
    timeframe: str = "1d"
    capital: float = Field(default=100000.0, gt=0, allow_inf_nan=False)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    slippage_percent: float = 0.05
    commission_percent: float = 0.03
    params: Optional[Dict[str, Any]] = None
    data_provider: str = "yfinance"  # breeze, angel, yfinance, csv


class StrategyStartRequest(BaseModel):
    strategy_name: str
    strategy_id: str
    instrument: str = "NSE:NIFTY"
    params: Optional[Dict[str, Any]] = None


class ManualOrderRequest(BaseModel):
    instrument: str
    side: str
    quantity: int
    order_type: str = "MARKET"
    price: Optional[float] = None
    product_type: str = "MIS"


class ForwardTestRegisterRequest(BaseModel):
    strategy_id: str
    instruments: List[str]
    capital: float = 100000.0
    params: Optional[Dict[str, Any]] = None


@app.get("/api/catalog")
def get_strategy_catalog():
    """Return structured strategy cards metadata with default parameters and symbols"""
    return {"catalog": list(STRATEGY_CATALOG.values())}


@app.get("/api/universe")
def get_universe():
    """Return the global symbol universe from platform_config/universe.yaml.
    
    The frontend uses this to populate symbol dropdowns. No hardcoded symbols
    anywhere — all changes go through universe.yaml and are reflected here.
    """
    from platform_config import get_all_instruments
    return {"universe": get_all_instruments()}


@app.get("/api/strategies")
def get_strategies():
    """List registered strategies and active ones"""
    return {
        "strategies": list(STRATEGY_CATALOG.values()),
        "active": [
            {
                "id": sid,
                "name": strat.name,
                "instrument": getattr(strat, "instrument", "NSE:NIFTY"),
                "is_running": strat.is_running,
                "signals_count": len(strat.get_signals()),
                "indicators": strat._indicators
            }
            for sid, strat in active_strategies.items()
        ]
    }


@app.get("/api/status")
def get_platform_status():
    """Return platform, broker, positions, and orders status"""
    positions = mock_broker.get_positions()
    orders = [o.to_dict() for o in mock_broker.orders.values()]
    return {
        "status": "HEALTHY",
        "engine_state": "RUNNING",
        "market_status": "OPEN",
        "broker": "MockBroker & Angel One Provider Ready",
        "active_strategies_count": len(active_strategies),
        "positions_count": len(positions),
        "positions": positions,
        "orders_count": len(orders),
        "orders": orders[-20:],
        "timestamp": now_ist().isoformat()
    }


@app.post("/api/backtest/oi-momentum")
def run_oi_momentum_backtest_api(req: BacktestRequest):
    """Tick-level OI-momentum backtest over one selected index."""
    from datetime import datetime as _dt
    from utils.timezone import IST as _IST, ensure_ist as _eist, now_ist as _now_ist
    now = _now_ist()
    try:
        start = _eist(_dt.strptime(req.start_date, "%Y-%m-%d")) if req.start_date else _dt(2026, 6, 1, tzinfo=_IST)
    except Exception:
        start = _dt(2026, 6, 1, tzinfo=_IST)
    try:
        end = _eist(_dt.strptime(req.end_date, "%Y-%m-%d")) if req.end_date else now
    except Exception:
        end = now
    raw = [s.strip().upper() for s in (req.instrument or "").replace(";", ",").split(",") if s.strip()]
    idx_list = []
    for r in raw:
        for cand in ("NIFTY", "BANKNIFTY", "SENSEX"):
            if cand in r and cand not in idx_list:
                idx_list.append(cand)
    if not idx_list:
        idx_list = ["NIFTY"]
    else:
        idx_list = idx_list[:1]
    from backtest.oi_momentum_backtest import run_backtest as _run_oi
    from strategies.index_oi_momentum import IndexOIMomentumStrategy, is_expiry_day
    try:
        out = _run_oi(IndexOIMomentumStrategy, idx_list, start, end, req.params or {}, capital=req.capital)
        # mode badges per selected symbol as of end date
        badges = {i: ("Expiry Today" if is_expiry_day(i, end) else "Base") for i in idx_list}
        trades = [{"trade_id": f"OI-{n+1:04d}", "instrument": t["index"] + "_OPT",
                     "side": t["side"], "mode": t["mode"],
                     "entry_time": t["entry_time"], "exit_time": t["exit_time"],
                     "entry_price": round(t["entry"], 2), "exit_price": round(t["exit"], 2),
                     "quantity": t["lots"], "pnl": round(t["net"], 2),
                     "reason": t["reason"]} for n, t in enumerate(out["trades"])]
        eq = [{"timestamp": p["t"], "value": round(p["v"], 2)} for p in out["equity_curve"]]
        ret_pct = (out["net_pnl"] / out["capital"] * 100) if out["capital"] else 0
        return {"strategy_id": "index_oi_momentum",
                "strategy_name": "Index Options OI Momentum",
                "instrument": ", ".join(idx_list), "mode_badges": badges,
                "period": f"{start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}",
                "status": "COMPLETED", "error": None,
                "initial_capital": out["capital"], "final_capital": round(out["final_equity"], 2),
                "total_return": round(out["net_pnl"], 2), "total_return_pct": round(ret_pct, 2),
                "max_drawdown": round(out["max_dd_pct"], 2), "sharpe_ratio": 0.0,
                "total_trades": out["total_trades"], "winning_trades": out["wins"],
                "losing_trades": out["total_trades"] - out["wins"],
                "win_rate": round(out["win_rate"], 2), "profit_factor": round(out["profit_factor"] if out["profit_factor"] != float("inf") else 0, 2),
                "base_trades": out["base_trades"], "base_pnl": round(out["base_pnl"], 2),
                "expiry_trades": out["expiry_trades"], "expiry_pnl": round(out["expiry_pnl"], 2),
                "per_index": out["per_index"], "trades": trades, "equity_curve": eq,
                "candles_evaluated": out["total_trades"],
                "executed_at": now.isoformat()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/backtest")
def run_backtest_api(req: BacktestRequest):
    """Execute strategy backtest and return detailed metrics, trade logs, and equity curve"""
    # Parse dates (IST: 'YYYY-MM-DD' means midnight IST)
    from utils.timezone import IST as _IST2, ensure_ist as _eist2, now_ist as _now_ist2
    now = _now_ist2()
    if req.start_date:
        try:
            start = _eist2(datetime.strptime(req.start_date, "%Y-%m-%d"))
        except Exception:
            start = datetime(2024, 1, 1, tzinfo=_IST2)
    else:
        start = datetime(2024, 1, 1, tzinfo=_IST2)

    if req.end_date:
        try:
            end = _eist2(datetime.strptime(req.end_date, "%Y-%m-%d"))
        except Exception:
            end = now
    else:
        end = now

    # Starting cash and capital-based position sizing share one authoritative value.
    params = {**(req.params or {}), "capital": req.capital, "timeframe": req.timeframe}

    from market_data.universe import UniverseManager
    # A backtest is deliberately single-instrument. This keeps the capital
    # ledger and compounding semantics unambiguous.
    requested_instrument = next(
        (part.strip() for part in req.instrument.replace(";", ",").split(",") if part.strip()),
        req.instrument,
    )
    resolved = UniverseManager.resolve_instruments(requested_instrument)
    if not resolved:
        resolved = [requested_instrument]

    bt_config = BacktestConfig(
        strategy_id=req.strategy_id,
        strategy_name=req.strategy_id,
        strategy_params=params,
        instruments=resolved,
        start_date=start,
        end_date=end,
        initial_capital=req.capital,
        timeframe=req.timeframe,
        slippage_percent=req.slippage_percent,
        commission_percent=req.commission_percent
    )

    # use the factory to resolve the data provider
    try:
        data_provider = ProviderFactory.get(req.data_provider)
    except Exception:
        data_provider = None

    error_detail = None
    try:
        engine = BacktestEngine(bt_config, data_provider=data_provider)
        result = engine.run()
        if result.status == BacktestStatus.FAILED:
            error_detail = result.error_message or "Strategy simulation failed to complete."
    except Exception as e:
        error_detail = str(e)
        result = BacktestResult(
            strategy_id=req.strategy_id,
            start_time=start,
            end_time=end,
            initial_capital=req.capital,
            final_capital=req.capital,
            total_return=0,
            total_return_pct=0,
            max_drawdown=0,
            sharpe_ratio=0,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=0,
            avg_win=0,
            avg_loss=0,
            profit_factor=0,
            status=BacktestStatus.FAILED,
            error_message=error_detail
        )

    trades_data = [t.to_dict() for t in getattr(engine, '_trades', [])]
    equity_data = getattr(engine, '_equity_curve', [])
    total_candles = sum(len(c) for c in getattr(engine, '_candle_cache', {}).values())

    if total_candles == 0 and not error_detail:
        error_detail = f"No historical candle data found for {req.instrument} in specified date range."

    response_data = {
        "strategy_id": req.strategy_id,
        "strategy_name": STRATEGY_CATALOG.get(req.strategy_id, {}).get("name", req.strategy_id),
        "instrument": requested_instrument,
        "timeframe": req.timeframe,
        "period": f"{start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}",
        "status": result.status.value,
        "error": error_detail,
        "initial_capital": result.initial_capital,
        "final_capital": result.final_capital,
        "total_return": result.total_return,
        "total_return_pct": result.total_return_pct,
        "max_drawdown": result.max_drawdown,
        "sharpe_ratio": result.sharpe_ratio,
        "total_trades": result.total_trades,
        "winning_trades": result.winning_trades,
        "losing_trades": result.losing_trades,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor,
        "trades": trades_data,
        "equity_curve": equity_data,
        "candles_evaluated": total_candles,
        "executed_at": now_ist().isoformat()
    }

    recent_backtests.insert(0, {
        "id": f"BT-{int(now_ist().timestamp())}",
        "strategy": req.strategy_id,
        "strategy_name": STRATEGY_CATALOG.get(req.strategy_id, {}).get("name", req.strategy_id),
        "instrument": req.instrument,
        "return_pct": result.total_return_pct,
        "trades": result.total_trades,
        "win_rate": result.win_rate,
        "timestamp": now_ist().strftime("%H:%M:%S IST")
    })
    if len(recent_backtests) > 10:
        recent_backtests.pop()

    return response_data


@app.get("/api/backtest/recent")
def get_recent_backtests():
    return {"recent": recent_backtests}


@app.post("/api/backtest/stream")
def start_backtest_stream(req: BacktestRequest, background_tasks: BackgroundTasks):
    """Start a streaming backtest and return job_id for SSE subscription"""
    from utils.timezone import IST as _IST2, ensure_ist as _eist2, now_ist as _now_ist2
    now = _now_ist2()
    
    # Parse dates
    if req.start_date:
        try:
            start = _eist2(datetime.strptime(req.start_date, "%Y-%m-%d"))
        except Exception:
            start = datetime(2024, 1, 1, tzinfo=_IST2)
    else:
        start = datetime(2024, 1, 1, tzinfo=_IST2)

    if req.end_date:
        try:
            end = _eist2(datetime.strptime(req.end_date, "%Y-%m-%d"))
        except Exception:
            end = now
    else:
        end = now

    params = {**(req.params or {}), "capital": req.capital, "timeframe": req.timeframe}
    
    from market_data.universe import UniverseManager
    resolved = UniverseManager.resolve_instruments(req.instrument)
    if not resolved:
        resolved = [req.instrument]

    bt_config = BacktestConfig(
        strategy_id=req.strategy_id,
        strategy_name=req.strategy_id,
        strategy_params=params,
        instruments=resolved,
        start_date=start,
        end_date=end,
        initial_capital=req.capital,
        timeframe=req.timeframe,
        slippage_percent=req.slippage_percent,
        commission_percent=req.commission_percent
    )

    # Get data provider — fail fast with a clear message instead of silently
    # passing None (the engine must never fall back to fabricated prices).
    # Broker-backed providers must additionally prove a live session here:
    # auth failure aborts before any backtest thread/data access starts.
    try:
        data_provider = ProviderFactory.get(req.data_provider or "yfinance")
        gate = getattr(data_provider, "ensure_authenticated", None)
        if callable(gate):
            gate()
    except Exception as e:
        job_id = job_manager.create_job(bt_config, None)
        job = job_manager.get_job(job_id)
        if job:
            job.status = "failed"
        job_manager.add_event(job_id, "backtest_failed", {
            "error": f"Failed to initialize data provider "
                     f"'{req.data_provider or 'yfinance'}': {e}"
        })
        return {"job_id": job_id, "status": "failed",
                "error": str(e)}

    # Create engine and job
    engine = BacktestEngine(bt_config, data_provider=data_provider)
    job_id = job_manager.create_job(bt_config, engine)
    
    # Set up event callback to populate the job's event queue
    def event_handler(event_type: str, payload: Dict[str, Any]):
        job_manager.add_event(job_id, event_type, payload)
    
    engine.set_event_callback(event_handler)
    
    # Run backtest in background
    def run_backtest():
        job = job_manager.get_job(job_id)
        if job:
            job.status = "running"
            try:
                result = engine.run()
                job.result = result
                job.status = "completed" if result.status == BacktestStatus.COMPLETED else "failed"
                if result.status != BacktestStatus.COMPLETED:
                    # Engine caught the error internally (no event emitted by
                    # the engine) — tell SSE clients why the stream ended.
                    job_manager.add_event(job_id, "backtest_failed", {
                        "error": result.error_message or "Backtest failed"
                    })
            except Exception as e:
                job.status = "failed"
                job_manager.add_event(job_id, "backtest_failed", {"error": str(e)})
    
    background_tasks.add_task(run_backtest)
    
    return {"job_id": job_id, "status": "started"}


@app.get("/api/backtest/stream/{job_id}/events")
async def stream_backtest_events(job_id: str):
    """SSE endpoint for streaming backtest events"""
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    async def event_generator():
        """Generate SSE events from the job's queue"""
        last_status = None
        
        while True:
            # Check if job is still active
            current_job = job_manager.get_job(job_id)
            if not current_job:
                yield f"event: end\ndata: {{\"reason\": \"job_removed\"}}\n\n"
                break
            
            # Try to get events from queue (non-blocking)
            try:
                event_data = current_job.event_queue.get(block=False)
                event_json = json.dumps(event_data)
                yield f"event: {event_data['event']}\ndata: {event_json}\n\n"
                
                # If backtest completed or failed, end stream after sending the event
                if event_data['event'] in ['backtest_completed', 'backtest_failed']:
                    await asyncio.sleep(0.1)  # Small delay to ensure client receives
                    break
                    
            except queue.Empty:
                # Send heartbeat to keep connection alive
                if current_job.status != last_status:
                    yield f"event: status\ndata: {{\"status\": \"{current_job.status}\"}}\n\n"
                    last_status = current_job.status
                
                # Check if job finished without events
                if current_job.status in ["completed", "failed"] and current_job.event_queue.empty():
                    break
                    
                await asyncio.sleep(0.5)  # Poll interval
        
        # Clean up job after stream ends
        await asyncio.sleep(1)
        job_manager.remove_job(job_id)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive"
        }
    )


@app.post("/api/backtest/stream/{job_id}/cancel")
def cancel_backtest_stream(job_id: str):
    """Cancel a running streaming backtest"""
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Cancel the engine
    if job.engine:
        job.engine.cancel()
    
    job.status = "cancelled"
    job_manager.add_event(job_id, "backtest_cancelled", {"reason": "user_requested"})
    
    return {"status": "cancelled", "job_id": job_id}


@app.post("/api/strategy/start")
def start_strategy(req: StrategyStartRequest):
    """Start an interactive strategy in the live mock engine"""
    if req.strategy_id in active_strategies:
        raise HTTPException(status_code=400, detail=f"Strategy {req.strategy_id} already running")
    
    params = req.params or {}
    params["instrument"] = req.instrument
    
    try:
        strat = StrategyRegistry.create(req.strategy_name, req.strategy_id, params)
        strat.initialize()
        strat.start()
        active_strategies[req.strategy_id] = strat
        
        # Subscribe mock market data
        market_data.subscribe(req.instrument, strat.on_tick)
        return {"status": "SUCCESS", "message": f"Strategy {req.strategy_id} started successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/strategy/stop/{strategy_id}")
def stop_strategy(strategy_id: str):
    """Stop a running strategy"""
    if strategy_id not in active_strategies:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_id} not running")
    
    strat = active_strategies[strategy_id]
    strat.stop()
    del active_strategies[strategy_id]
    return {"status": "SUCCESS", "message": f"Strategy {strategy_id} stopped"}


@app.post("/api/order/place")
def place_order(req: ManualOrderRequest):
    """Place a manual test order to verify execution & risk manager"""
    order = mock_broker.place_order(
        instrument=req.instrument,
        side=OrderSide[req.side],
        order_type=OrderType[req.order_type],
        quantity=req.quantity,
        price=req.price or 100.0,
        product_type=OrderProductType[req.product_type]
    )
    return {"status": "SUCCESS", "order": order.to_dict()}


@app.post("/api/forward-test/register")
def register_forward_test_api(req: ForwardTestRegisterRequest):
    """Register and promote strategy to Forward Testing (Paper trading on live feed)"""
    import json
    import os
    forward_dir = str(platform_config.FORWARD_TEST_DIR)
    os.makedirs(forward_dir, exist_ok=True)

    file_path = os.path.join(forward_dir, f"{req.strategy_id}.json")
    record = {
        "strategy_id": req.strategy_id,
        "strategy_name": STRATEGY_CATALOG.get(req.strategy_id, {}).get("name", req.strategy_id),
        "instruments": req.instruments,
        "capital": req.capital,
        "mode": "PAPER_FORWARD_TEST",
        "status": "ACTIVE",
        "registered_at": now_ist().isoformat(),
        "paper_positions": {},
        "paper_trades_count": 0,
        "paper_trades": [],
        "params": req.params or {}
    }

    with open(file_path, "w") as f:
        json.dump(record, f, indent=2)

    # Trigger single forward test evaluation step immediately
    try:
        from execution.forward_test_runner import ForwardTestRunner
        runner = ForwardTestRunner(
            strategy_id=req.strategy_id,
            instruments=req.instruments,
            capital=req.capital,
            params=req.params
        )
        runner.run_once()
    except Exception as e:
        pass

    return {
        "status": "SUCCESS",
        "message": f"Strategy {req.strategy_id} promoted to Forward Testing with {len(req.instruments)} symbol(s).",
        "record": record
    }


OI_PAPER_SESSIONS: Dict[str, Any] = {}


def _paper_safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        if isinstance(v, str):
            s = v.strip().replace(',', '')
            if s in ('', '-', 'null', 'None', 'N/A', 'nan', 'NaN'):
                return default
            return float(s)
        return float(v)
    except (TypeError, ValueError):
        return default


# Indian market timezone (IST = UTC+05:30) and the daily square-off time
IST = IST  # from utils.timezone (single source of truth)
MARKET_CLOSE_IST_HOUR = 15
MARKET_CLOSE_IST_MINUTE = 30


def _next_ist_close(now: Optional[datetime] = None) -> datetime:
    """Return the next 15:30 IST market-close boundary (aware IST).

    A paper session started during market hours auto-stops at 15:30 IST the
    same day; one started after the close is bounded to the next day's 15:30
    IST, so a session can never run indefinitely without a manual stop.
    """
    now = ensure_ist(now) if now else now_ist()
    ist_now = now
    close = ist_now.replace(hour=MARKET_CLOSE_IST_HOUR, minute=MARKET_CLOSE_IST_MINUTE,
                            second=0, microsecond=0)
    if ist_now >= close:
        close = close + timedelta(days=1)
    return close


def _ist_str(dt: Optional[datetime]) -> Optional[str]:
    """Format an aware datetime in IST, e.g. '2026-09-17 15:30:00 IST'."""
    return ensure_ist(dt).strftime("%Y-%m-%d %H:%M:%S IST") if dt else None


def _paper_load_env() -> Dict[str, str]:
    env: Dict[str, str] = {}
    for p in (str(platform_config.ENV_FILE),
              str(platform_config.PROJECT_ROOT / ".env")):
        try:
            if os.path.exists(p):
                with open(p) as f:
                    for line in f:
                        line = line.strip().rstrip('\\')
                        if line and not line.startswith('#') and '=' in line:
                            k, v = line.split('=', 1)
                            env.setdefault(k.strip(), v.strip())
        except Exception:
            pass
    return env


class OIPaperSession:
    """PAPER-ONLY live monitor: Angel WebSocket2 QUOTE-mode (mode=2) real OI ticks
    on FUT + ATM CE/PE tokens per index, fed to IndexOIMomentumStrategy.
    NEVER places real orders (LIVE_TRADING_ENABLED=False hard lock)."""
    SPOT = {"NIFTY": [("NSE", "99926000", "Nifty 50")],
            "BANKNIFTY": [("NSE", "99926009", "Nifty Bank")],
            "SENSEX": [("BSE", "99919012", "SENSEX"), ("BSE", "99926012", "SENSEX"),
                        ("NSE", "99926012", "SENSEX")]}
    LIVE_TRADING_ENABLED = False  # hard lock: paper only

    def __init__(self, indices: List[str], modes: List[str], capital: float, params: Dict[str, Any]):
        from strategies.index_oi_momentum import IndexOIMomentumStrategy, is_expiry_day
        self.id = f"PAPER-{int(now_ist().timestamp())}"
        self.indices = indices
        self.modes = modes  # e.g. ["base", "expiry"]
        self.capital = capital
        self.params = params or {}
        self.created_at = now_ist().isoformat()
        self.stop_at: Optional[datetime] = None
        self.stop_reason: Optional[str] = None
        self.running = True
        self.ticks_seen = 0
        self.paper_trades: List[Dict[str, Any]] = []
        self.errors: List[str] = []
        self.expiry_flags = {i: bool(is_expiry_day(i, now_ist())) for i in indices}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._strats: Dict[str, Any] = {}
        for m in modes:
            s = IndexOIMomentumStrategy("index_oi_momentum", "Index OI Momentum",
                                        {**self.params, "capital": capital, "mode_override": m})
            s.initialize()
            self._strats[m] = s
        self._broker = None
        try:
            from brokers.angel_one import AngelOneBroker
            env = _paper_load_env()
            self._broker = AngelOneBroker({
                'api_key': env.get('ANGEL_API_KEY'), 'client_id': env.get('ANGEL_CLIENT_CODE'),
                'password': env.get('ANGEL_PASSWORD_OR_MPIN'), 'totp_secret': env.get('ANGEL_TOTP_SECRET')})
            if not self._broker.authenticate():
                self.errors.append("Angel authentication failed; running in quote-retry mode.")
        except Exception as e:
            self.errors.append(f"Angel init failed: {e}")
        # open paper position tracker per (mode, index)
        self._open: Dict[str, Dict[str, Any]] = {}
        self._live: Dict[str, Dict[str, Any]] = {}
        # human-readable WS subscription ledger per index (INFO, not errors)
        self.subscriptions: Dict[str, Dict[str, Any]] = {}
        # WS health pulse: last tick time per index + last error per index
        self.ws_health: Dict[str, Dict[str, Any]] = {}
        self.ws_connected_at: Optional[str] = None
        self.last_tick_at: Optional[str] = None
        self._feed = None
        self._feed_mode: str = "init"

    def start(self):
        # Auto square-off at market close (15:30 IST) unless stopped manually first
        self.stop_at = _next_ist_close()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self, reason: str = "manual"):
        if self.stop_reason is None:
            self.stop_reason = reason
        self.running = False
        self._stop.set()
        try:
            if self._feed:
                self._feed.stop()
        except Exception:
            pass

    # ---- live state fed by WebSocket SNAP_QUOTE ticks (Angel One WS2, mode=3) ----
    def _on_ws_tick(self, wt: Dict[str, Any]):
        """Route one real SNAP_QUOTE-mode tick (FUT or CE/PE, with live OI) into strategy state."""
        from core.models import Tick as _Tick
        try:
            idx = (wt.get("index") or "").upper()
            if idx not in self.indices:
                return
            role = wt.get("role", "FUT")  # FUT | CE | PE
            ltp = _paper_safe_float(wt.get("ltp"), 0.0)
            oi = _paper_safe_float(wt.get("oi"), 0.0)
            if ltp <= 0:
                return
            st = self._live.setdefault(idx, {"fut_px": 0.0, "fut_oi": 0.0,
                                              "ce_px": 0.0, "ce_oi": 0.0, "ce_bid": 0.0, "ce_ask": 0.0,
                                              "pe_px": 0.0, "pe_oi": 0.0, "pe_bid": 0.0, "pe_ask": 0.0})
            _now = now_ist().isoformat()
            self.last_tick_at = _now
            _hh = self.ws_health.setdefault(idx, {})
            _hh["last_tick_at"] = _now
            _hh["ticks"] = int(_hh.get("ticks", 0)) + 1
            _hh["last_ltp"] = round(ltp, 2)
            _hh["last_oi"] = oi
            if role == "FUT":
                st["fut_px"] = ltp
                if oi > 0: st["fut_oi"] = oi
            elif role == "CE":
                st["ce_px"] = ltp
                if oi > 0: st["ce_oi"] = oi
                st["ce_bid"] = _paper_safe_float(wt.get("bid"), ltp * 0.999)
                st["ce_ask"] = _paper_safe_float(wt.get("ask"), ltp * 1.001)
            elif role == "PE":
                st["pe_px"] = ltp
                if oi > 0: st["pe_oi"] = oi
                st["pe_bid"] = _paper_safe_float(wt.get("bid"), ltp * 0.999)
                st["pe_ask"] = _paper_safe_float(wt.get("ask"), ltp * 1.001)
            # need FUT + at least one leg before feeding strategy
            if st["fut_px"] <= 0 or (st["ce_px"] <= 0 and st["pe_px"] <= 0):
                return
            for m, strat in self._strats.items():
                # feed CE leg tick and PE leg tick (each with true OI)
                for leg, pxk, oik, bk, ak in (("CE", "ce_px", "ce_oi", "ce_bid", "ce_ask"),
                                                ("PE", "pe_px", "pe_oi", "pe_bid", "pe_ask")):
                    px = st[pxk]
                    if px <= 0:
                        continue
                    tick = _Tick(instrument=f"{idx}_{leg}", last_price=px,
                                 bid_price=st[bk] or px * 0.999, ask_price=st[ak] or px * 1.001,
                                 last_quantity=0, volume=int(_paper_safe_float(wt.get("volume"), 0)),
                                 timestamp=now_ist())  # IST
                    tick.metadata = {"index": idx, "side_hint": leg,
                                     "underlying_price": st["fut_px"],
                                     "underlying_oi": st["fut_oi"],
                                     "strike_oi": st[oik],
                                     "opp_strike_oi": st["pe_oi"] if leg == "CE" else st["ce_oi"],
                                     "volume": _paper_safe_float(wt.get("volume"), 0.0),
                                     "bid": st[bk] or px * 0.999, "ask": st[ak] or px * 1.001,
                                     "depth_qty": 1e9, "wall_px": None, "wall_oi": 0.0,
                                     "live_oi": True, "feed": "angel_ws_snap_quote"}
                    try:
                        sig = strat.on_tick(tick)
                    except Exception as e:
                        self.errors.append(f"{idx}/{m}/{leg} tick parse error: {e}")
                        continue
                    self.ticks_seen += 1
                    if sig is None:
                        continue
                    r = (sig.metadata or {}).get("reason", "")
                    key = f"{m}:{idx}"
                    if r == "oi_momentum_entry":
                        self._open[key] = {"entry": _paper_safe_float(sig.metadata.get("entry")),
                                           "qty": int(sig.quantity or 0), "mode": m,
                                           "side": str(sig.metadata.get("side") or ""),
                                           "time": now_ist().isoformat()}
                    elif r.startswith("exit_") and key in self._open:
                        op = self._open.pop(key)
                        exit_px = _paper_safe_float(sig.metadata.get("exit"))
                        net = (exit_px - op["entry"]) * op["qty"]  # paper PnL
                        self.paper_trades.append({
                            "paper_id": f"{self.id}-{len(self.paper_trades)+1:03d}",
                            "index": idx, "variant": m, "qty": op["qty"],
                            "entry": round(op["entry"], 2), "exit": round(exit_px, 2),
                            "paper_pnl": round(net, 2), "reason": r,
                            "entry_time": op["time"],
                            "exit_time": now_ist().isoformat(),
                            "live_order_placed": False})
        except Exception as e:
            self.errors.append(f"WS tick route error: {e}")

    def _loop(self):
        """Start WS feed. ALL legs (FUT + CE + PE) stream via WS SNAP_QUOTE ticks;
        the strategy evaluates every tick (OI velocity needs 60-90s tick windows).
        No REST polling feeds the strategy -- this loop is keep-alive only."""
        if not self._start_ws_feed():
            self.errors.append("WebSocket feed unavailable; cannot run paper session without live ticks.")
            self.stop("feed_unavailable")
            return
        while not self._stop.is_set():
            if self.stop_at is not None:
                # both aware IST after the fix; ensure_ist guards legacy values
                remaining = (ensure_ist(self.stop_at) - now_ist()).total_seconds()
                if remaining <= 0:
                    # Hard square-off: never let a paper session run past 15:30 IST
                    self.stop("market_close_15:30_IST")
                    break
                self._stop.wait(min(20.0, remaining))
            else:
                self._stop.wait(20.0)
        self.running = False

    def _start_ws_feed(self) -> bool:
        """Authenticate, discover FUT + ATM CE/PE tokens, subscribe SNAP_QUOTE mode (3).
        Spot is fetched ONCE at startup for ATM-strike selection only."""
        try:
            from market_data.oi_ws_feed import OIWebSocketFeed, EXCH_TYPE, QUOTE_MODE
            from strategies.index_oi_momentum import INDEX_SPECS
            if not (self._broker and getattr(self._broker, 'client', None)):
                if not (self._broker and self._broker.authenticate()):
                    self.errors.append("Angel auth failed; cannot start WS feed.")
                    return False
            client = self._broker.client
            auth = self._broker.jwt_token
            feed = OIWebSocketFeed(auth, self._broker.api_key, self._broker.client_id,
                                   self._broker.feed_token, on_tick=self._on_ws_tick)
            FUT_EXCH = {"NIFTY": "NFO", "BANKNIFTY": "NFO", "SENSEX": "BFO"}
            OPT_EXCH = {"NIFTY": "NFO", "BANKNIFTY": "NFO", "SENSEX": "BFO"}
            ok_any = False
            for idx in self.indices:
                try:
                    spec = INDEX_SPECS[idx]
                    interval = int(spec.get("strike_interval", 50))
                    # 1) FUT token FIRST -- futures LTP is the live underlier.
                    #    Never trust the index-spot token blindly: the pinned SENSEX
                    #    token (99919012) prints a stale 63k vs live FUT ~75k.
                    fut_tok, fut_sym, fut_ex = None, None, FUT_EXCH[idx]
                    fut_px = 0.0
                    try:
                        r = client.searchScrip(fut_ex, idx)
                        _futs = [d for d in ((r or {}).get("data") or [])
                                if "FUT" in str(d.get("tradingsymbol", ""))
                                and idx in str(d.get("tradingsymbol", "")).replace(" ", "")]
                        _futs.sort(key=lambda d: str(d.get("tradingsymbol", "")))
                        if _futs:
                            fut_tok, fut_sym = str(_futs[0]["symboltoken"]), str(_futs[0].get("tradingsymbol", ""))
                            try:
                                _q = client.ltpData(fut_ex, fut_sym, fut_tok)
                                fut_px = _paper_safe_float(((_q or {}).get("data") or {}).get("ltp"), 0.0)
                            except Exception as _fqe:
                                self.errors.append(f"{idx} FUT quote: {_fqe}")
                    except Exception as e:
                        self.errors.append(f"{idx} FUT discovery: {e}")
                    # 2) ATM basis: live FUT px wins; index-spot token only if sane
                    #    (within 15% of FUT, guarding stale-token drift like SENSEX 63k).
                    spot = self._fetch_spot(idx)
                    basis, basis_src = fut_px, f"FUT {fut_sym or fut_tok}"
                    if not (basis > 0):
                        basis, basis_src = spot, "index-spot token"
                    elif spot > 0 and abs(spot - basis) / basis > 0.15:
                        self.errors.append(
                            f"{idx}: spot token {spot:.0f} deviates >15% from FUT {basis:.0f}; using FUT as ATM basis.")
                    elif spot <= 0:
                        self.errors.append(f"{idx}: spot quote failed; using FUT {basis:.0f} as ATM basis.")
                    if not (basis > 0):
                        basis = float(spec.get("spot_ref", 25000.0))
                        basis_src = "static spot_ref"
                        self.errors.append(f"{idx}: no live basis; ATM ref {basis}.")
                    atm = int(round(basis / interval) * interval)
                    # 3) ATM CE/PE tokens: searchScrip first, then official scrip-master
                    #    fallback (searchScrip is fuzzy; BFO/SENSEX often returns nothing
                    #    for strike queries). Scrip master gives exact tradingsymbol match.
                    ce_tok = pe_tok = None
                    ce_sym = pe_sym = None
                    try:
                        rows: List[Dict] = []
                        for q in (f"{idx} {atm}", f"{idx}", str(atm)):
                            try:
                                r = client.searchScrip(OPT_EXCH[idx], q)
                                rows = ((r or {}).get("data") or [])
                                if rows:
                                    break
                            except Exception:
                                continue

                        def _pick(rows: List[Dict], right: str):
                            c = [d for d in rows
                                 if str(atm) in str(d.get("tradingsymbol", ""))
                                 and str(d.get("tradingsymbol", "")).strip().endswith(right)]
                            if not c:
                                return None, None
                            c.sort(key=lambda d: str(d.get("tradingsymbol", "")))
                            return str(c[0]["symboltoken"]), str(c[0].get("tradingsymbol", ""))
                        ce_tok, ce_sym = _pick(rows, "CE")
                        pe_tok, pe_sym = _pick(rows, "PE")
                        if (not ce_tok or not pe_tok):
                            # fallback: Angel scrip master JSON (snaps to nearest listed strike)
                            try:
                                _sm = self._scrip_master(OPT_EXCH[idx])
                                if not ce_tok:
                                    _r = self._pick_from_master(_sm, idx, atm, "CE")
                                    if _r:
                                        ce_tok, ce_sym = _r[0], _r[1]
                                        if len(_r) > 2:
                                            atm = int(_r[2])
                                if not pe_tok:
                                    _r = self._pick_from_master(_sm, idx, atm, "PE")
                                    if _r:
                                        pe_tok, pe_sym = _r[0], _r[1]
                                        if len(_r) > 2:
                                            atm = int(_r[2])
                                if ce_tok or pe_tok:
                                    self.errors.append(f"{idx}: option tokens resolved via scrip-master (strike {atm}).")
                            except Exception as _sme:
                                self.errors.append(f"{idx} scrip-master fallback: {_sme}")
                        if not ce_tok:
                            self.errors.append(f"{idx} CE discovery: no {atm}CE found (search + scrip-master).")
                        if not pe_tok:
                            self.errors.append(f"{idx} PE discovery: no {atm}PE found (search + scrip-master).")
                    except Exception as e:
                        self.errors.append(f"{idx} option discovery: {e}")
                    # 4) subscribe SNAP_QUOTE mode (3) = only WS2 mode carrying OI
                    toks, meta = [], {}
                    fx = fut_ex
                    if fut_tok:
                        toks.append(fut_tok)
                        meta[f"{EXCH_TYPE.get(fx, 2)}:{fut_tok}"] = {"index": idx, "role": "FUT", "symbol": fut_sym}
                    ox = OPT_EXCH[idx]
                    if ce_tok:
                        toks.append(ce_tok)
                        meta[f"{EXCH_TYPE.get(ox, 2)}:{ce_tok}"] = {"index": idx, "role": "CE", "strike": atm}
                    if pe_tok:
                        toks.append(pe_tok)
                        meta[f"{EXCH_TYPE.get(ox, 2)}:{pe_tok}"] = {"index": idx, "role": "PE", "strike": atm}
                    # FUT and OPT may live on different exchangeTypes; group by exchange
                    groups: Dict[str, List[str]] = {}
                    if fut_tok: groups.setdefault(fx, []).append(fut_tok)
                    if ce_tok or pe_tok:
                        groups.setdefault(ox, []).extend([t for t in (ce_tok, pe_tok) if t])
                    for ex, tl in groups.items():
                        m2 = {k: v for k, v in meta.items()}
                        feed.add_tokens(ex, tl, meta=m2)
                    # record subscription ledger (INFO channel, not errors)
                    self.subscriptions[idx] = {
                        "index": idx, "exchange": OPT_EXCH[idx], "fut_exchange": fut_ex,
                        "fut_symbol": fut_sym, "fut_token": fut_tok,
                        "atm_strike": atm, "spot_ref": round(basis, 2),
                        "atm_basis": basis_src,
                        "ce_symbol": ce_sym, "ce_token": ce_tok,
                        "pe_symbol": pe_sym, "pe_token": pe_tok,
                        "ws_mode": "SNAP_QUOTE(3)", "feed": "Angel One WebSocket2",
                    }
                    if toks:
                        ok_any = True
                    else:
                        self.errors.append(f"{idx}: no tokens discovered; skipped.")
                except Exception as e:
                    self.errors.append(f"{idx} WS setup error: {e}")
            if ok_any:
                feed.start()
                self._feed = feed
                self._feed_mode = "angel_ws_snap_quote"
                import time as _wt
                for _ in range(30):
                    _wt.sleep(1.0)
                    if getattr(feed, 'connected', False) or getattr(feed, 'ticks_seen', 0) > 0:
                        break
                for _fe in getattr(feed, 'errors', []) or []:
                    self.errors.append(f"WS: {_fe}")
                if getattr(feed, 'connected', False):
                    self.ws_connected_at = getattr(feed, 'connected_at', None) or now_ist().isoformat()
                else:
                    self.errors.append("WS handshake not confirmed within 30s (connecting…).")
                return True
            self._feed_mode = "quote_fallback"
            return False
        except Exception as e:
            self.errors.append(f"WS feed init failed: {e}")
            self._feed_mode = "quote_fallback"
            return False

    _SCRIP_MASTER_CACHE: Dict[str, Any] = {}

    def _scrip_master(self, exchange: str) -> List[Dict]:
        """Fetch Angel scrip-master JSON for an exchange (cached per process)."""
        import json as _js
        import urllib.request as _url
        if exchange in OIPaperSession._SCRIP_MASTER_CACHE:
            return OIPaperSession._SCRIP_MASTER_CACHE[exchange]
        _URLS = {"NFO": "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json",
                 "BFO": "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json",
                 "NSE": "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"}
        with _url.urlopen(_URLS.get(exchange, _URLS["NFO"]), timeout=30) as _r:
            _all = _js.loads(_r.read().decode("utf-8"))
        _SEG = {"NFO": "NFO", "BFO": "BFO", "NSE": "NSE"}
        _rows = [d for d in _all if str(d.get("exch_seg", "")).upper() == _SEG.get(exchange, exchange)]
        OIPaperSession._SCRIP_MASTER_CACHE[exchange] = _rows
        return _rows

    @staticmethod
    def _abs_strike_static(d: Dict):
        try:
            return float(str(d.get("strike", "0")).replace(",", "") or 0) / 100.0
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _pick_from_master(rows: List[Dict], idx: str, atm: int, right: str):
        """Nearest-expiry option for index + strike + CE/PE from scrip master.
        Master schema: symbol='SENSEX5026SEP2663400CE', token='…', strike='6340000.000000'
        (paise x100), expiry='26NOV2026', instrumenttype='OPTIDX'. Match trailing
        {strike}{CE|PE} on symbol, or numeric strike/100 == atm as fallback."""
        from datetime import datetime as _mdt
        _tail = f"{atm}{right}".upper()
        _abs_strike = OIPaperSession._abs_strike_static
        _pool = [d for d in rows
                 if idx in (str(d.get("symbol", "")) + str(d.get("name", ""))).replace(" ", "").upper()
                 and str(d.get("instrumenttype", "")).upper().startswith("OPT")]
        _c = [d for d in _pool
              if (str(d.get("symbol", "")).strip().upper().endswith(_tail)
                  or (_abs_strike(d) is not None and abs(_abs_strike(d) - atm) < 0.01
                      and str(d.get("symbol", "")).strip().upper().endswith(right)))]
        # strike grid may not list our exact ATM (e.g. SENSEX 100-pt grid vs 63426 spot):
        # snap to nearest listed strike for this index instead of giving up.
        _snapped = atm
        if not _c and _pool:
            def _strike_of(d):
                return _abs_strike(d)
            _with = [(abs(s - atm), s) for s in (_strike_of(d) for d in _pool) if s is not None]
            if _with:
                _with.sort()
                _snapped = _with[0][1]
                _c = [d for d in _pool
                      if _abs_strike(d) is not None and abs(_abs_strike(d) - _snapped) < 0.01
                      and str(d.get("symbol", "")).strip().upper().endswith(right)]
        if not _c:
            return None, None
        def _exp(d):
            try:
                return _mdt.strptime(str(d.get("expiry", "")), "%d%b%Y").date().toordinal()
            except Exception:
                return 99999999
        _today = _mdt.now().date().toordinal()
        _c.sort(key=lambda d: (_exp(d) < _today, _exp(d), str(d.get("symbol", ""))))
        _tok, _sym = str(_c[0].get("token", "")), str(_c[0].get("symbol", ""))
        return (_tok, _sym, int(_snapped)) if _snapped != atm else (_tok, _sym)

    def _fetch_spot(self, idx: str) -> float:
        if not (self._broker and getattr(self._broker, 'client', None)):
            return 0.0
        for exch, token, sym in self.SPOT[idx]:
            try:
                res = self._broker.client.ltpData(exch, sym, token)
                if isinstance(res, dict):
                    if res.get('status') and res.get('data'):
                        px = _paper_safe_float((res['data'] or {}).get('ltp'), 0.0)
                        if px > 0:
                            # pin working candidate first for next polls
                            cands = self.SPOT[idx]
                            used = (exch, token, sym)
                            if cands[0] != used:
                                cands.remove(used)
                                cands.insert(0, used)
                            return px
                    else:
                        self.errors.append(f"{idx} quote rejected on {exch}/{token}: {(res.get('message') or res.get('errorcode'))}")
            except Exception as e:
                self.errors.append(f"{idx} quote parse error on {exch}/{token}: {e}")
        return 0.0

    def live_prices(self) -> Dict[str, Dict[str, Any]]:
        """Latest WS CMP per index leg (FUT/CE/PE) + tick age. No REST polling."""
        from datetime import datetime as _pdt
        from utils.timezone import ensure_ist as _eist3, now_ist as _now_ist3
        out: Dict[str, Dict[str, Any]] = {}
        for idx in self.indices:
            st = self._live.get(idx, {})
            hh = (getattr(self, 'ws_health', {}) or {}).get(idx, {})
            try:
                _age = max(0, int((_now_ist3() - _eist3(_pdt.fromisoformat(str(hh.get('last_tick_at'))))).total_seconds())) if hh.get('last_tick_at') else None
            except Exception:
                _age = None
            out[idx] = {"fut": round(float(st.get('fut_px', 0.0) or 0.0), 2),
                        "ce": round(float(st.get('ce_px', 0.0) or 0.0), 2),
                        "pe": round(float(st.get('pe_px', 0.0) or 0.0), 2),
                        "fut_oi": st.get('fut_oi', 0.0), "ce_oi": st.get('ce_oi', 0.0), "pe_oi": st.get('pe_oi', 0.0),
                        "ticks": int(hh.get('ticks', 0)), "tick_age_s": _age}
        return out

    def _open_position_details(self, live_px: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        """Snapshot of in-progress paper positions: entry, qty, option side, entry time
        plus mark-to-market (unrealized) PnL from the latest WS tick prices."""
        lp = live_px if live_px is not None else self.live_prices()
        out: List[Dict[str, Any]] = []
        for key, pos in self._open.items():
            if ":" in key:
                variant, idx = key.split(":", 1)
            else:
                variant, idx = "base", key
            side = str(pos.get("side") or "").upper()
            entry = _paper_safe_float(pos.get("entry"))
            qty = int(pos.get("qty") or 0)
            mark = None
            try:
                leg = (lp.get(idx) or {}).get("ce" if side == "CE" else "pe") if side in ("CE", "PE") else None
                if leg:
                    mark = round(float(leg), 2)
            except Exception:
                mark = None
            unreal = round((mark - entry) * qty, 2) if (mark and entry) else None
            out.append({"key": key, "index": idx, "variant": variant or "base", "side": side,
                        "entry": entry, "qty": qty, "entry_time": pos.get("time"),
                        "mark": mark, "unrealized_pnl": unreal})
        return out

    def status(self) -> Dict[str, Any]:
        feed_ticks = getattr(self._feed, 'ticks_seen', 0) if self._feed else 0
        for _fe in (getattr(self._feed, 'errors', []) or [])[-5:]:
            _m = f"WS: {_fe}"
            if _m not in self.errors:
                self.errors.append(_m)
        if getattr(self._feed, 'connected_at', None) and not getattr(self, 'ws_connected_at', None):
            self.ws_connected_at = self._feed.connected_at
        live_px = self.live_prices()  # single pass; reused for the payload + open-position marking
        return {"session_id": self.id, "running": self.running,
                "strategy_id": "index_oi_momentum",
                "feed_source": "Angel One WebSocket2 (SNAP_QUOTE mode=3)",
                "feed_mode": getattr(self, '_feed_mode', 'init'),
                "subscriptions": getattr(self, 'subscriptions', {}),
                "ws_health": getattr(self, 'ws_health', {}),
                "ws_connected_at": getattr(self, 'ws_connected_at', None),
                "last_tick_at": getattr(self, 'last_tick_at', None),
                "ws_ticks": feed_ticks,
                "live_prices": live_px,
                "indices": self.indices, "variants": self.modes,
                "expiry_today": self.expiry_flags, "capital": self.capital,
                "ticks_seen": self.ticks_seen, "paper_trades": self.paper_trades[-50:],
                "paper_trades_count": len(self.paper_trades),
                "open_paper_positions": list(self._open.keys()),
                "open_paper_position_details": self._open_position_details(live_px),
                "live_trading": False, "live_order_placed": False,
                "errors": self.errors[-10:], "created_at": self.created_at,
                "stop_at_ist": _ist_str(self.stop_at), "stop_reason": self.stop_reason,
                "market_close_ist": "15:30"}


class OIPaperStartRequest(BaseModel):
    indices: List[str] = ["NIFTY", "BANKNIFTY", "SENSEX"]
    variants: List[str] = ["base", "expiry"]
    capital: float = 100000.0
    params: Optional[Dict[str, Any]] = None


@app.post("/api/paper/oi-momentum/start")
def start_oi_paper(req: OIPaperStartRequest):
    """Start PAPER-ONLY live monitoring session (base + expiry variants). No real orders."""
    idx = [s.strip().upper() for s in req.indices if s.strip().upper() in ("NIFTY", "BANKNIFTY", "SENSEX")]
    if not idx:
        raise HTTPException(status_code=400, detail="Select at least one index.")
    modes = [m for m in (req.variants or []) if m in ("base", "expiry")]
    if not modes:
        raise HTTPException(status_code=400, detail="Confirm at least one variant (base / expiry).")
    sess = OIPaperSession(idx, modes, req.capital, req.params or {})
    OI_PAPER_SESSIONS[sess.id] = sess
    sess.start()
    return {"status": "PAPER_RUNNING", "live_trading": False, **sess.status()}


@app.get("/api/paper/oi-momentum/status/{session_id}")
def paper_status(session_id: str):
    sess = OI_PAPER_SESSIONS.get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Paper session not found")
    return sess.status()


@app.get("/api/paper/oi-momentum/sessions")
def paper_sessions():
    return {"sessions": [s.status() for s in OI_PAPER_SESSIONS.values()][-20:]}


@app.post("/api/paper/oi-momentum/stop/{session_id}")
def paper_stop(session_id: str):
    sess = OI_PAPER_SESSIONS.get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Paper session not found")
    sess.stop("manual")
    return {"status": "STOPPED", **sess.status()}


def _is_market_hours_ist() -> bool:
    """Check if current time is within market hours (09:15 - 15:30 IST)."""
    from utils.timezone import now_ist, EQUITY_OPEN_MIN, EQUITY_CLOSE_MIN
    ist_now = now_ist()
    minutes = ist_now.hour * 60 + ist_now.minute
    return EQUITY_OPEN_MIN <= minutes < EQUITY_CLOSE_MIN


def _get_auto_start_config() -> Optional[Dict[str, Any]]:
    """Get auto-start configuration for index_oi_momentum strategy."""
    strat_config = STRATEGY_CATALOG.get("index_oi_momentum")
    if not strat_config or not strat_config.get("auto_start_enabled"):
        return None
    return {
        "indices": strat_config.get("auto_start_symbols", ["NIFTY", "BANKNIFTY", "SENSEX"]),
        "variants": strat_config.get("auto_start_variants", ["base", "expiry"]),
        "capital": strat_config.get("auto_start_capital", 100000.0),
        "params": strat_config.get("default_params", {})
    }


def start_auto_paper_session():
    """Start OI paper session automatically on app startup if market is open."""
    if not _is_market_hours_ist():
        return None
    
    config = _get_auto_start_config()
    if not config:
        return None
    
    # Check if there's already an active session
    if OI_PAPER_SESSIONS:
        return None
    
    try:
        sess = OIPaperSession(
            indices=config["indices"],
            modes=config["variants"],
            capital=config["capital"],
            params=config["params"]
        )
        OI_PAPER_SESSIONS[sess.id] = sess
        sess.start()
        return sess.status()
    except Exception as e:
        print(f"Auto-start failed: {e}")
        return None


@app.post("/api/paper/oi-momentum/auto-start")
def trigger_auto_start():
    """Manually trigger auto-start (useful for testing)."""
    return start_auto_paper_session()


@app.get("/api/paper/oi-momentum/auto-start/config")
def get_auto_start_config():
    """Get auto-start configuration for index_oi_momentum."""
    config = _get_auto_start_config()
    if not config:
        return {"enabled": False}
    return {"enabled": True, **config}


@app.on_event("startup")
async def startup_event():
    """Start OI paper session automatically on app startup if market is open."""
    # Run in a thread pool since start() is blocking
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: start_auto_paper_session())


@app.get("/api/forward-test/status/{strategy_id}")
def get_forward_test_status(strategy_id: str):
    """Get status of registered forward test for a strategy"""
    import json
    import os
    file_path = str(platform_config.FORWARD_TEST_DIR / f"{strategy_id}.json")
    if os.path.exists(file_path):
        with open(file_path) as f:
            return {"status": "ACTIVE", "data": json.load(f)}
    return {"status": "INACTIVE", "data": None}


@app.get("/", response_class=HTMLResponse)
def index_page():
    """Interactive Web Dashboard HTML.

    The global symbol universe (platform_config/universe.yaml) is injected as
    ``window.__GLOBAL_UNIVERSE__`` so symbol autocomplete lists are populated without
    an extra round-trip.
    """
    response = HTMLResponse(
        DASHBOARD_HTML.replace(
            "window.__GLOBAL_UNIVERSE__ || []",
            json.dumps(GLOBAL_UNIVERSE),
        )
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Trading Strategy Execution Platform | v2.0</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Inter:wght@400;500;600;700&display=swap');
    body { font-family: 'Inter', sans-serif; background-color: #0b0f19; color: #e2e8f0; }
    .font-mono { font-family: 'JetBrains Mono', monospace; }
    .glass-card { background: rgba(17, 24, 39, 0.75); backdrop-filter: blur(12px); border: 1px solid rgba(255, 255, 255, 0.08); }
    .modal-backdrop { background-color: rgba(3, 7, 18, 0.85); backdrop-filter: blur(8px); }
    .glow-cyan { box-shadow: 0 0 25px -5px rgba(6, 182, 212, 0.35); }
    .glow-emerald { box-shadow: 0 0 25px -5px rgba(16, 185, 129, 0.35); }
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: #0f172a; }
    ::-webkit-scrollbar-thumb { background: #334155; border-radius: 3px; }
  </style>
</head>
<body class="min-h-screen flex flex-col antialiased">

  <!-- Header -->
  <header class="border-b border-gray-800 bg-gray-950/80 sticky top-0 z-40 backdrop-blur-md px-6 py-3 flex items-center justify-between">
    <div class="flex items-center space-x-4">
      <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-cyan-500 to-indigo-600 flex items-center justify-center text-white font-black text-xl shadow-lg shadow-cyan-500/20">
        <i class="fa-solid fa-chart-line"></i>
      </div>
      <div>
        <div class="flex items-center space-x-2">
          <h1 class="font-bold text-lg text-white tracking-wide">QUANT<span class="text-cyan-400">PULSE</span></h1>
          <span class="text-xs px-2 py-0.5 rounded-full bg-cyan-950 text-cyan-400 border border-cyan-800 font-mono">v2.0 Arch</span>
          <span class="text-xs px-2 py-0.5 rounded-full bg-emerald-950 text-emerald-400 border border-emerald-800 font-mono">Angel One Connected</span>
        </div>
        <p class="text-xs text-gray-400">Institutional Strategy Backtester & Live Execution Platform (MCX / NSE / NFO)</p>
      </div>
    </div>

    <!-- Live Architecture Highlights -->
    <div class="hidden lg:flex items-center space-x-6 text-xs font-mono">
      <div class="flex items-center space-x-2">
        <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
        <span class="text-gray-400">IPC Socket:</span>
        <span class="text-emerald-400 font-semibold">/tmp/trading_platform.sock</span>
      </div>
      <div class="flex items-center space-x-2">
        <span class="w-2 h-2 rounded-full bg-cyan-400"></span>
        <span class="text-gray-400">Market Feed:</span>
        <span class="text-cyan-400 font-semibold">WebSocket + CandleBuilder</span>
      </div>
      <div class="flex items-center space-x-2">
        <span class="w-2 h-2 rounded-full bg-indigo-400"></span>
        <span class="text-gray-400">Storage:</span>
        <span class="text-indigo-300 font-semibold">JSONL Audit + Snapshots</span>
      </div>
    </div>

    <!-- Quick Refresh Button -->
    <div class="flex items-center space-x-3">
      <button onclick="refreshLiveStatus()" class="p-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 text-xs transition" title="Refresh Live State">
        <i class="fa-solid fa-arrows-rotate" id="refresh-icon"></i>
      </button>
    </div>
  </header>

  <!-- Navigation Tabs -->
  <nav class="bg-gray-900/60 border-b border-gray-800/80 px-6 flex space-x-8 text-sm">
    <button onclick="switchTab('cards')" id="tab-btn-cards" class="py-3 px-1 border-b-2 border-cyan-400 text-cyan-400 font-medium flex items-center space-x-2">
      <i class="fa-solid fa-layer-group"></i>
      <span>Strategy Studio (Backtest Cards)</span>
    </button>
    <button onclick="switchTab('live')" id="tab-btn-live" class="py-3 px-1 border-b-2 border-transparent text-gray-400 hover:text-gray-200 font-medium flex items-center space-x-2">
      <i class="fa-solid fa-microchip"></i>
      <span>Live Strategy Daemons & Orders</span>
    </button>
    <button onclick="switchTab('terminal')" id="tab-btn-terminal" class="py-3 px-1 border-b-2 border-transparent text-gray-400 hover:text-gray-200 font-medium flex items-center space-x-2">
      <i class="fa-solid fa-terminal"></i>
      <span>CLI Quick Reference</span>
    </button>
  </nav>

  <!-- Main Content Body -->
  <main class="flex-1 p-6 max-w-7xl w-full mx-auto space-y-6">

    <!-- ==================== TAB 1: STRATEGY CARDS & STUDIO ==================== -->
    <div id="tab-content-cards" class="space-y-6">

      <!-- Section Header -->
      <div class="flex items-center justify-between">
        <div>
          <h2 class="text-base font-bold text-white flex items-center space-x-2">
            <i class="fa-solid fa-vial-circle-check text-cyan-400"></i>
            <span>Select Strategy to Configure & Backtest</span>
          </h2>
          <p class="text-xs text-gray-400 mt-0.5">Click on any strategy card to open the interactive simulation popup with custom date ranges, parameters, and real-time streaming trade logs.</p>
        </div>
        <span class="text-xs font-mono bg-gray-800 text-gray-300 px-3 py-1 rounded-lg border border-gray-700">
          4 Registered Strategies
        </span>
      </div>

      <!-- Strategy Cards Grid (Fixed Size Cards) -->
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5" id="strategy-cards-grid">
        <!-- Dynamically injected via JavaScript -->
      </div>

      <!-- Recent Simulation Activity Banner -->
      <div class="glass-card p-4 rounded-xl border border-gray-800 flex items-center justify-between">
        <div class="flex items-center space-x-3">
          <div class="w-8 h-8 rounded-lg bg-cyan-950 text-cyan-400 border border-cyan-800 flex items-center justify-center text-sm">
            <i class="fa-solid fa-clock-rotate-left"></i>
          </div>
          <div>
            <div class="text-xs font-semibold text-white">Latest Verified Benchmark: MCX Trend Rider</div>
            <div class="text-[11px] text-gray-400">Evaluated on real Angel One historical feed (2024–2026): +46.67% Return, 44.4% Win Rate, Max DD 8.84%</div>
          </div>
        </div>
        <button onclick="openBacktestModal('mcx_trend_rider')" class="px-3.5 py-1.5 bg-gradient-to-r from-emerald-500 to-teal-600 hover:from-emerald-400 hover:to-teal-500 text-gray-950 font-bold text-xs rounded-lg shadow transition flex items-center space-x-1.5">
          <i class="fa-solid fa-play"></i>
          <span>Run MCX Backtest</span>
        </button>
      </div>

    </div>

    <!-- ==================== TAB 2: LIVE STRATEGY DAEMONS & POSITIONS ==================== -->
    <div id="tab-content-live" class="hidden space-y-6">

      <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <!-- Start Strategy Form -->
        <div class="glass-card p-5 rounded-2xl border border-gray-800 space-y-4">
          <h2 class="text-sm font-semibold text-white flex items-center space-x-2">
            <i class="fa-solid fa-circle-play text-emerald-400"></i>
            <span>Deploy Strategy to Engine</span>
          </h2>
          <div class="space-y-3 text-xs">
            <div>
              <label class="block text-gray-400 mb-1">Strategy Name</label>
              <select id="deploy-strat-name" class="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-white font-mono text-xs">
                <option value="mcx_trend_rider">mcx_trend_rider (MCX Futures)</option>
                <option value="ema_crossover">ema_crossover (NSE)</option>
                <option value="rsi">rsi (NSE)</option>
                <option value="breakout">breakout (NSE)</option>
                <option value="equity_swing_vcp">equity_swing_vcp (NSE Equities)</option>
                <option value="index_oi_momentum">index_oi_momentum (NSE/BSE Index Options)</option>
              </select>
            </div>
            <div>
              <label class="block text-gray-400 mb-1">Instance Unique ID</label>
              <input type="text" id="deploy-strat-id" value="mcx_tr_live_01" class="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-white font-mono text-xs">
            </div>
            <div>
              <label class="block text-gray-400 mb-1">Instrument Target</label>
              <select id="deploy-strat-inst" class="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-white font-mono text-xs">
                <option value="MCX_GOLDM">MCX_GOLDM (Gold Mini)</option>
                <option value="MCX_SILVERM">MCX_SILVERM (Silver Mini)</option>
                <option value="MCX_CRUDEOIL">MCX_CRUDEOIL (Crude Oil)</option>
                <option value="NSE:NIFTY">NSE:NIFTY</option>
              </select>
            </div>
            <button onclick="deployStrategy()" class="w-full py-2.5 bg-emerald-500 hover:bg-emerald-400 text-gray-950 font-bold rounded-xl text-xs transition flex items-center justify-center space-x-2">
              <i class="fa-solid fa-bolt"></i>
              <span>START STRATEGY INSTANCE</span>
            </button>
          </div>
        </div>

        <!-- Active Instances List -->
        <div class="lg:col-span-2 glass-card p-5 rounded-2xl border border-gray-800 flex flex-col">
          <div class="flex items-center justify-between mb-3">
            <h2 class="text-sm font-semibold text-white flex items-center space-x-2">
              <i class="fa-solid fa-server text-cyan-400"></i>
              <span>Running Strategy Daemons</span>
            </h2>
            <span id="active-strat-count" class="text-xs bg-gray-800 text-cyan-400 px-2 py-0.5 rounded font-mono">0 active</span>
          </div>
          <div id="active-strategies-list" class="space-y-3 flex-1 overflow-y-auto max-h-72">
            <div class="p-6 text-center text-gray-500 text-xs">No active strategy running. Start one from the left form.</div>
          </div>
        </div>
      </div>

      <!-- Live Positions & Orders Table -->
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div class="glass-card rounded-2xl border border-gray-800 overflow-hidden">
          <div class="p-4 border-b border-gray-800 bg-gray-950/40 flex items-center justify-between">
            <h3 class="text-xs font-semibold text-white flex items-center space-x-2">
              <i class="fa-solid fa-briefcase text-cyan-400"></i>
              <span>Current Open Positions</span>
            </h3>
            <span id="positions-count-tag" class="text-xs font-mono text-gray-400">0 positions</span>
          </div>
          <div class="overflow-x-auto max-h-56">
            <table class="w-full text-left text-xs font-mono">
              <thead class="bg-gray-900/60 text-gray-400 uppercase text-[10px]">
                <tr>
                  <th class="py-2 px-3">Instrument</th>
                  <th class="py-2 px-3">Qty</th>
                  <th class="py-2 px-3">Entry</th>
                  <th class="py-2 px-3">Current</th>
                  <th class="py-2 px-3 text-right">PnL</th>
                </tr>
              </thead>
              <tbody id="positions-tbody" class="divide-y divide-gray-800 text-gray-300">
                <tr><td colspan="5" class="text-center py-6 text-gray-500">No open positions.</td></tr>
              </tbody>
            </table>
          </div>
        </div>

        <div class="glass-card rounded-2xl border border-gray-800 overflow-hidden">
          <div class="p-4 border-b border-gray-800 bg-gray-950/40 flex items-center justify-between">
            <h3 class="text-xs font-semibold text-white flex items-center space-x-2">
              <i class="fa-solid fa-clock-rotate-left text-indigo-400"></i>
              <span>Recent Engine Orders</span>
            </h3>
            <span id="orders-count-tag" class="text-xs font-mono text-gray-400">0 orders</span>
          </div>
          <div class="overflow-x-auto max-h-56">
            <table class="w-full text-left text-xs font-mono">
              <thead class="bg-gray-900/60 text-gray-400 uppercase text-[10px]">
                <tr>
                  <th class="py-2 px-3">Order ID</th>
                  <th class="py-2 px-3">Side</th>
                  <th class="py-2 px-3">Instrument</th>
                  <th class="py-2 px-3">Qty</th>
                  <th class="py-2 px-3 text-right">Status</th>
                </tr>
              </thead>
              <tbody id="orders-tbody" class="divide-y divide-gray-800 text-gray-300">
                <tr><td colspan="5" class="text-center py-6 text-gray-500">No recent orders.</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

    </div>

    <!-- ==================== TAB 3: CLI & TERMINAL CHEAT SHEET ==================== -->
    <div id="tab-content-terminal" class="hidden space-y-6">
      <div class="glass-card p-6 rounded-2xl border border-gray-800 space-y-4">
        <div class="flex items-center justify-between">
          <h2 class="text-sm font-semibold text-white flex items-center space-x-2">
            <i class="fa-solid fa-terminal text-cyan-400"></i>
            <span>CLI Quick Execution Reference</span>
          </h2>
          <span class="text-xs text-gray-400 font-mono">Executable: trading-platform or ./run_strategy.sh</span>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs font-mono">
          <div class="p-4 bg-gray-950/80 rounded-xl border border-gray-800 space-y-2">
            <span class="text-cyan-400 font-semibold text-[11px] uppercase">1. Run Strategy Backtests</span>
            <pre class="text-gray-300 bg-gray-900 p-2.5 rounded border border-gray-800/80 overflow-x-auto"># Backtest MCX Trend Rider on one symbol
trading-platform backtest mcx_trend_rider --instrument "MCX_GOLDM" --capital 100000

# Backtest EMA Crossover on NIFTY
trading-platform backtest ema_crossover

# Backtest RSI on Bank Nifty
trading-platform backtest rsi --instrument NSE:BANKNIFTY --timeframe 15m</pre>
          </div>

          <div class="p-4 bg-gray-950/80 rounded-xl border border-gray-800 space-y-2">
            <span class="text-emerald-400 font-semibold text-[11px] uppercase">2. Forward Testing & Daemons</span>
            <pre class="text-gray-300 bg-gray-900 p-2.5 rounded border border-gray-800/80 overflow-x-auto"># Run paper trading forward test with Angel One & Telegram
python3 execution/forward_test_runner.py

# Check platform status
trading-platform status</pre>
          </div>
        </div>
      </div>
    </div>

  </main>

  <!-- ==================== MODAL POPUP: STRATEGY BACKTEST STUDIO ==================== -->
  <div id="backtest-modal" class="fixed inset-0 z-50 flex items-center justify-center p-4 modal-backdrop hidden">
    <div class="bg-gray-900 border border-gray-700 w-full max-w-5xl rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[92vh]">
      
      <!-- Modal Header -->
      <div class="px-6 py-4 border-b border-gray-800 bg-gray-950/90 flex items-center justify-between">
        <div class="flex items-center space-x-3">
          <div id="modal-icon-badge" class="w-9 h-9 rounded-lg bg-emerald-950 text-emerald-400 border border-emerald-800 flex items-center justify-center text-base">
            <i class="fa-solid fa-fire-flame-curved" id="modal-strat-icon"></i>
          </div>
          <div>
            <div class="flex items-center space-x-2">
              <h3 class="font-bold text-base text-white" id="modal-strat-title">MCX Trend Rider</h3>
              <span id="modal-strat-badge" class="text-[10px] font-mono px-2 py-0.5 rounded-full bg-emerald-950 text-emerald-400 border border-emerald-800">
                Institutional Grade
              </span>
            </div>
            <p class="text-xs text-gray-400" id="modal-strat-desc">Dual Donchian (20/55) Breakout + ADX(14) filter + Chandelier Trailing Stop</p>
          </div>
        </div>
        <button onclick="closeBacktestModal()" class="w-8 h-8 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-400 hover:text-white flex items-center justify-center text-sm transition">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>

      <!-- Modal Body (Scrollable) -->
      <div class="p-6 overflow-y-auto space-y-6 flex-1 text-xs">

        <!-- Shared backtest configuration, independent of strategy parameters -->
        <h3 class="font-semibold text-gray-300">Backtest Configuration</h3>
        <div class="grid grid-cols-1 md:grid-cols-5 gap-4 p-4 bg-gray-950/60 rounded-xl border border-gray-800">
          
          <!-- Start Date -->
          <div>
            <label class="block text-gray-400 mb-1 font-medium">Start Date</label>
            <input type="date" id="modal-start-date" value="2026-01-01" class="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-white font-mono text-xs focus:outline-none focus:border-cyan-500">
          </div>

          <!-- End Date -->
          <div>
            <label class="block text-gray-400 mb-1 font-medium">End Date</label>
            <input type="date" id="modal-end-date" value="2026-09-23" class="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-white font-mono text-xs focus:outline-none focus:border-cyan-500">
          </div>

          <!-- Initial Capital -->
          <div id="modal-capital-wrap">
            <label class="block text-gray-400 mb-1 font-medium">Initial Capital (₹)</label>
            <input type="number" id="modal-capital" value="100000" min="0.01" required step="any" class="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-white font-mono text-xs focus:outline-none focus:border-cyan-500">
          </div>

          <!-- Backtest Timeframe -->
          <div>
            <label class="block text-gray-400 mb-1 font-medium">Timeframe</label>
            <select id="modal-timeframe" class="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-white font-mono text-xs focus:outline-none focus:border-cyan-500">
              <option value="5m">5 Minutes</option>
              <option value="10m">10 Minutes</option>
              <option value="15m">15 Minutes</option>
              <option value="30m">30 Minutes</option>
              <option value="1h">1 Hour</option>
              <option value="4h">4 Hours</option>
              <option value="1d" selected>Daily</option>
              <option value="1mo">Monthly</option>
            </select>
          </div>

          <!-- Data Provider -->
          <div id="modal-provider-wrap">
            <label class="block text-gray-400 mb-1 font-medium">Data Provider</label>
            <select id="modal-data-provider" class="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-white font-mono text-xs focus:outline-none focus:border-cyan-500">
              <option value="yfinance">yfinance (NSE/BSE)</option>
              <option value="breeze">Breeze (ICICI)</option>
              <option value="angel">Angel One</option>
            </select>
          </div>

          <!-- Autocomplete single backtest symbol -->
          <div class="relative">
            <div class="flex items-center justify-between mb-1">
              <label class="block text-gray-400 font-medium">Select Backtest Symbol</label>
            </div>
            <div class="relative">
              <input id="modal-symbol-input" type="text" autocomplete="off"
                placeholder="Type to search symbols..."
                oninput="filterSymbolOptions(this.value)"
                onfocus="showSymbolOptions()"
                onkeydown="handleSymbolInputKeydown(event)"
                class="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 pr-8 text-white font-mono text-xs focus:outline-none focus:border-cyan-500">
              <i class="fa-solid fa-magnifying-glass absolute right-3 top-2.5 text-gray-500 text-[10px]"></i>
              <input id="modal-selected-symbol" type="hidden">
              <div id="modal-symbol-options" class="hidden absolute left-0 right-0 top-full mt-1 max-h-60 overflow-y-auto bg-gray-950 border border-gray-700 rounded-xl shadow-2xl p-1.5 z-50"></div>
            </div>
          </div>

          <!-- Run Simulation Action Button -->
          <div class="flex flex-col justify-end space-y-2">
              <button onclick="executeModalBacktest()" id="modal-run-btn" class="w-full py-2 bg-gradient-to-r from-cyan-500 to-emerald-500 hover:from-cyan-400 hover:to-emerald-400 text-gray-950 font-bold rounded-lg shadow-lg transition flex items-center justify-center space-x-2">
                <i class="fa-solid fa-play"></i>
                <span>RUN BACKTEST</span>
              </button>
            <div id="oi-variant-box" class="hidden p-2 bg-gray-950/60 border border-rose-500/30 rounded-lg text-[11px] space-y-1.5">
              <div class="text-rose-300 font-bold flex items-center space-x-1.5"><i class="fa-solid fa-flask"></i><span>PAPER-ONLY live run — confirm variants (no real orders)</span></div>
              <label class="flex items-center space-x-2 text-gray-200"><input type="checkbox" name="oi-variant" value="base" checked class="accent-emerald-400"> <span>Regular <span class="text-gray-500 font-mono">(base variant)</span></span></label>
              <label class="flex items-center space-x-2 text-gray-200"><input type="checkbox" name="oi-variant" value="expiry" checked class="accent-amber-400"> <span>Expiry-only <span class="text-gray-500 font-mono">(SENSEX expiry today)</span></span></label>
              <button onclick="startOiPaperSession()" id="btn-oi-paper" class="w-full py-1.5 mt-1 bg-gradient-to-r from-amber-500 to-rose-500 hover:from-amber-400 hover:to-rose-400 text-gray-950 font-bold rounded-lg shadow transition flex items-center justify-center space-x-2">
                <i class="fa-solid fa-satellite-dish"></i><span>RUN PAPER LIVE (confirm)</span>
              </button>
            </div>
          </div>

        </div>

        <!-- Strategy Default Parameters Accordion / Inspector -->
        <div class="p-3 bg-gray-950/40 rounded-xl border border-gray-800">
          <div class="flex items-center justify-between mb-2">
            <span class="font-semibold text-gray-300 flex items-center space-x-1.5">
              <i class="fa-solid fa-sliders text-cyan-400"></i>
              <span>Strategy Parameters (Configured for Strategy Rules)</span>
            </span>
            <span class="text-[11px] text-gray-500 font-mono">Locked to Strategy Core</span>
          </div>
          <div id="modal-params-grid" class="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
            <!-- Dynamic parameter fields -->
          </div>
        </div>

        <!-- Bollinger Bands Configuration Section -->
        <div id="modal-bollinger-section" class="hidden">
          <div class="p-3 bg-gray-950/40 rounded-xl border border-gray-800">
            <div class="flex items-center justify-between mb-2">
              <span class="font-semibold text-gray-300 flex items-center space-x-1.5">
                <i class="fa-solid fa-chart-line text-rose-400"></i>
                <span>Bollinger Bands (Analysis Overlay)</span>
              </span>
              <label class="flex items-center space-x-1.5 cursor-pointer">
                <input type="checkbox" id="bollinger-enabled" onchange="toggleBollingerFields()" class="w-3.5 h-3.5 rounded bg-gray-900 border-gray-600 text-rose-400 focus:ring-0 focus:ring-offset-0 cursor-pointer">
                <span class="text-[10px] text-gray-500 font-mono">Enable</span>
              </label>
            </div>
            <div id="modal-bollinger-grid" class="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
              <div>
                <label class="block text-gray-400 text-[10px] mb-1">Length</label>
                <input type="number" id="bollinger_length" value="19" step="1" class="w-full bg-gray-900 border border-gray-700 rounded-lg px-2 py-1 text-white font-mono text-xs focus:outline-none focus:border-rose-500">
              </div>
              <div>
                <label class="block text-gray-400 text-[10px] mb-1">Multiplier (Mult)</label>
                <input type="number" id="bollinger_mult" value="2.36" step="0.01" class="w-full bg-gray-900 border border-gray-700 rounded-lg px-2 py-1 text-white font-mono text-xs focus:outline-none focus:border-rose-500">
              </div>
              <div>
                <label class="block text-gray-400 text-[10px] mb-1">Offset</label>
                <input type="number" id="bollinger_offset" value="0" step="1" class="w-full bg-gray-900 border border-gray-700 rounded-lg px-2 py-1 text-white font-mono text-xs focus:outline-none focus:border-rose-500">
              </div>
              <div>
                <label class="block text-gray-400 text-[10px] mb-1">MA Type</label>
                <select id="bollinger_ma_type" class="w-full bg-gray-900 border border-gray-700 rounded-lg px-2 py-1 text-white font-mono text-xs focus:outline-none focus:border-rose-500">
                  <option value="SMA">SMA</option>
                  <option value="WMA" selected>WMA</option>
                  <option value="EMA">EMA</option>
                  <option value="DEMA">DEMA</option>
                  <option value="TEMA">TEMA</option>
                </select>
              </div>
            </div>
            <div class="mt-1.5 text-[9px] text-gray-500">
              Display overlay only — does not affect strategy signals or backtest calculations.
            </div>
          </div>
        </div>

        <!-- Progress Bar & Status Streamer -->
        <div id="modal-progress-container" class="hidden space-y-1.5">
          <div class="flex items-center justify-between text-[11px] font-mono">
            <span id="modal-progress-label" class="text-cyan-400 flex items-center space-x-2">
              <i class="fa-solid fa-spinner fa-spin"></i>
              <span>Streaming candles & evaluating signal triggers...</span>
            </span>
            <span id="modal-progress-pct" class="text-gray-400">0%</span>
          </div>
          <div class="w-full bg-gray-800 h-1.5 rounded-full overflow-hidden">
            <div id="modal-progress-bar" class="bg-gradient-to-r from-cyan-500 to-emerald-400 h-full w-0 transition-all duration-300"></div>
          </div>
        </div>

        <!-- Backend Error Banner in Modal -->
        <div id="modal-error-banner" class="hidden p-4 bg-rose-950/80 border border-rose-700/80 rounded-xl text-xs space-y-1">
          <div class="flex items-center justify-between">
            <div class="flex items-center space-x-2 text-rose-300 font-bold">
              <i class="fa-solid fa-triangle-exclamation text-sm text-rose-400"></i>
              <span id="modal-error-title">Backend Execution Error</span>
            </div>
            <button onclick="document.getElementById('modal-error-banner').classList.add('hidden')" class="text-rose-400 hover:text-white text-xs">
              <i class="fa-solid fa-xmark"></i>
            </button>
          </div>
          <p id="modal-error-msg" class="text-rose-200 font-mono text-[11px] leading-relaxed break-words"></p>
        </div>

        <!-- Promote to Forward Test Banner (Post-Backtest Action) -->
        <div id="modal-forward-test-bar" class="hidden p-3.5 bg-gradient-to-r from-emerald-950/90 to-cyan-950/90 border border-emerald-500/40 rounded-xl flex flex-col sm:flex-row items-center justify-between gap-3 shadow-lg">
          <div class="flex items-center space-x-3">
            <div class="w-8 h-8 rounded-lg bg-emerald-500/20 text-emerald-400 flex items-center justify-center font-bold text-sm">
              <i class="fa-solid fa-satellite-dish"></i>
            </div>
            <div>
              <div class="flex items-center space-x-2">
                <h4 class="font-bold text-white text-xs">Ready for Forward Testing?</h4>
                <span class="text-[10px] px-1.5 py-0.5 rounded bg-emerald-900/60 text-emerald-300 font-mono">Paper Live</span>
              </div>
              <p class="text-[11px] text-gray-300">Promote this strategy with your backtested stock selection to forward paper trading on live Angel One quotes.</p>
            </div>
          </div>
          <div class="flex items-center space-x-2 w-full sm:w-auto">
            <button onclick="promoteToForwardTest()" id="btn-promote-forward" class="w-full sm:w-auto px-4 py-2 bg-gradient-to-r from-emerald-500 to-teal-400 hover:from-emerald-400 hover:to-teal-300 text-gray-950 font-bold rounded-lg shadow-md transition flex items-center justify-center space-x-1.5 text-xs">
              <i class="fa-solid fa-paper-plane text-xs"></i>
              <span>Promote to Forward Test</span>
            </button>
          </div>
        </div>

        <!-- Forward Test Success Alert + live WS health + stop -->
        <div id="modal-forward-success-alert" class="hidden p-3 bg-emerald-950/80 border border-emerald-500 rounded-xl text-xs space-y-1">
          <div class="flex items-center justify-between">
            <span class="text-emerald-300 font-bold flex items-center space-x-1.5">
              <i class="fa-solid fa-circle-check text-emerald-400"></i>
              <span>Forward Test Activated Successfully!</span>
            </span>
            <div class="flex items-center space-x-2">
              <button onclick="stopOiPaperSession()" id="btn-oi-stop" class="px-2 py-0.5 bg-rose-500/20 hover:bg-rose-500 text-rose-300 hover:text-white font-bold rounded text-[10px] flex items-center space-x-1">
                <i class="fa-solid fa-stop"></i><span>STOP</span>
              </button>
              <button onclick="document.getElementById('modal-forward-success-alert').classList.add('hidden')" class="text-emerald-400 hover:text-white">
                <i class="fa-solid fa-xmark"></i>
              </button>
            </div>
          </div>
          <p id="modal-forward-success-msg" class="text-emerald-200 text-[11px] font-mono"></p>
          <!-- Live WebSocket health pulse -->
          <div id="oi-ws-health" class="mt-1 p-2 bg-gray-950/70 border border-cyan-500/30 rounded-lg">
            <div class="flex items-center justify-between mb-1">
              <span class="font-bold text-cyan-300 flex items-center space-x-1.5">
                <span id="oi-ws-dot" class="inline-block w-2 h-2 rounded-full bg-amber-400 animate-pulse"></span>
                <span>LIVE WEBSOCKET HEALTH</span>
              </span>
              <span id="oi-ws-summary" class="font-mono text-[10px] text-gray-400">connecting…</span>
            </div>
            <div id="oi-ws-rows" class="font-mono text-[10px] space-y-0.5 text-gray-300">Waiting for first tick…</div>
          </div>
        </div>

        <!-- Results Section: Metrics Cards -->
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-3" id="modal-metrics-grid">
          
          <!-- Total Return -->
          <div class="glass-card p-3.5 rounded-xl border border-gray-800">
            <div class="text-gray-400 text-[11px] flex items-center justify-between">
              <span>Total Return</span>
              <i class="fa-solid fa-wallet text-gray-500"></i>
            </div>
            <div class="my-1.5">
              <div class="text-lg font-bold font-mono" id="m-return">--</div>
              <div class="text-[11px] text-gray-400 font-mono" id="m-return-pct">--</div>
            </div>
            <span class="text-[10px] text-gray-500" id="m-capital-label">Initial: ₹20,00,000</span>
          </div>

          <!-- Win Rate -->
          <div class="glass-card p-3.5 rounded-xl border border-gray-800">
            <div class="text-gray-400 text-[11px] flex items-center justify-between">
              <span>Win Rate</span>
              <i class="fa-solid fa-trophy text-amber-500/80"></i>
            </div>
            <div class="my-1.5">
              <div class="text-lg font-bold font-mono text-cyan-400" id="m-winrate">--</div>
              <div class="text-[11px] text-gray-400 font-mono" id="m-trades-detail">--</div>
            </div>
            <span class="text-[10px] text-gray-500">Completed Trades</span>
          </div>

          <!-- Max Drawdown -->
          <div class="glass-card p-3.5 rounded-xl border border-gray-800">
            <div class="text-gray-400 text-[11px] flex items-center justify-between">
              <span>Max Drawdown</span>
              <i class="fa-solid fa-arrow-trend-down text-rose-500"></i>
            </div>
            <div class="my-1.5">
              <div class="text-lg font-bold font-mono text-rose-400" id="m-drawdown">--</div>
              <div class="text-[11px] text-gray-400 font-mono">Peak-to-Trough</div>
            </div>
            <span class="text-[10px] text-gray-500">Capital Risk Control</span>
          </div>

          <!-- Sharpe Ratio -->
          <div class="glass-card p-3.5 rounded-xl border border-gray-800">
            <div class="text-gray-400 text-[11px] flex items-center justify-between">
              <span>Sharpe Ratio</span>
              <i class="fa-solid fa-scale-balanced text-indigo-400"></i>
            </div>
            <div class="my-1.5">
              <div class="text-lg font-bold font-mono text-indigo-400" id="m-sharpe">--</div>
              <div class="text-[11px] text-gray-400 font-mono" id="m-pf">PF: --</div>
            </div>
            <span class="text-[10px] text-gray-500">Annualized Risk-Adj</span>
          </div>

        </div>

        <!-- Equity Curve & Portfolio Progression Chart -->
        <div class="glass-card p-4 rounded-xl border border-gray-800 flex flex-col">
          <div class="flex items-center justify-between mb-2">
            <div class="flex items-center space-x-2">
              <i class="fa-solid fa-chart-area text-emerald-400 text-xs"></i>
              <h4 class="text-xs font-semibold text-gray-200">Equity Curve & Portfolio Progression</h4>
            </div>
            <span id="modal-chart-label" class="text-[11px] text-gray-400 font-mono">Awaiting backtest run...</span>
          </div>
          <div class="relative w-full h-[220px]">
            <canvas id="modalEquityChart"></canvas>
          </div>
        </div>

        <!-- Trades Log Table Streamer -->
        <div class="glass-card rounded-xl border border-gray-800 overflow-hidden">
          <div class="px-4 py-2.5 border-b border-gray-800 bg-gray-950/60 flex items-center justify-between">
            <div class="flex items-center space-x-2">
              <i class="fa-solid fa-list-check text-cyan-400 text-xs"></i>
              <h4 class="text-xs font-semibold text-white">Simulated Trade Fills & Executions</h4>
            </div>
            <span id="modal-trades-count" class="text-[11px] font-mono text-gray-400">0 records</span>
          </div>
          <div class="overflow-x-auto max-h-60">
            <table class="w-full text-left text-xs font-mono">
              <thead class="bg-gray-900/80 text-gray-400 uppercase text-[10px] sticky top-0">
                <tr>
                  <th class="py-2 px-3">Trade ID</th>
                  <th class="py-2 px-3">Type</th>
                  <th class="py-2 px-3">Instrument</th>
                  <th class="py-2 px-3">Qty</th>
                  <th class="py-2 px-3">Entry Time</th>
                  <th class="py-2 px-3">Entry Px</th>
                  <th class="py-2 px-3">Exit Time</th>
                  <th class="py-2 px-3">Exit Px</th>
                  <th class="py-2 px-3 text-right" id="modal-pnl-th">Realized PnL (₹)</th>
                </tr>
              </thead>
              <tbody id="modal-trades-tbody" class="divide-y divide-gray-800/60 text-gray-300">
                <tr>
                  <td colspan="9" class="text-center py-6 text-gray-500">Click "Run Backtest" above to execute the simulation and stream trade logs.</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

      </div>

      <!-- Modal Footer -->
      <div class="px-6 py-3 border-t border-gray-800 bg-gray-950/90 flex items-center justify-between text-xs text-gray-400">
        <div>
          <span class="text-emerald-400 font-semibold">Feed Source:</span> Angel One Historical Data Provider (Rate-limit compliant)
        </div>
        <button onclick="closeBacktestModal()" class="px-4 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-200 rounded-lg transition font-medium">
          Close
        </button>
      </div>

    </div>
  </div>

  <!-- Footer -->
  <footer class="mt-auto border-t border-gray-800/80 py-4 px-6 text-center text-xs text-gray-500 font-mono">
    Trading Strategy Execution Platform • Complete v2 Architecture • Python 3.12 Sandboxed Runtime
  </footer>

  <!-- Application Logic -->
  <script>
    let catalog = [];
    let currentModalStrat = null;
    let modalEquityChart = null;

    document.addEventListener('DOMContentLoaded', () => {
      loadCatalog();
      refreshLiveStatus();

      // Close autocomplete suggestions when clicking outside the control.
      document.addEventListener('click', (e) => {
        const input = document.getElementById('modal-symbol-input');
        const options = document.getElementById('modal-symbol-options');
        if (options && !options.classList.contains('hidden')) {
          if (!input.contains(e.target) && !options.contains(e.target)) {
            options.classList.add('hidden');
          }
        }
      });
    });

    async function loadCatalog() {
      try {
        const res = await fetch('/api/catalog');
        const data = await res.json();
        catalog = data.catalog;
        window._oiRunning = window._oiRunning || {};
        // Expose the universe for autocomplete symbol fields.
        // Server-injected from platform_config/universe.yaml (window.__GLOBAL_UNIVERSE__);
        // the /api/universe fetch is the fallback for pages served without the
        // injection (e.g. mocked/test HTML).
        window.GLOBAL_UNIVERSE = window.__GLOBAL_UNIVERSE__ || [];
        if (!window.GLOBAL_UNIVERSE.length) {
          try {
            const ures = await fetch('/api/universe');
            const udata = await ures.json();
            window.GLOBAL_UNIVERSE = udata.universe || [];
          } catch (uerr) {
            console.warn('Failed to load universe:', uerr);
          }
        }
        renderStrategyCards();
        refreshOiRunningState();
        setInterval(refreshOiRunningState, 30000);
      } catch (err) {
        console.error('Failed to load strategy catalog:', err);
      }
    }

    function renderStrategyCards() {
      const grid = document.getElementById('strategy-cards-grid');
      grid.innerHTML = '';

      catalog.forEach(s => {
        const card = document.createElement('div');
        // Fixed size responsive card with cursor pointer
        const isOIPaperRunning = !!(window._oiRunning && window._oiRunning[s.id]);
        const cardBorderClass = isOIPaperRunning ? 'border-emerald-500/60' : 'border-gray-800 hover:border-cyan-500/60';
        // Card stays clickable while running so the live paper trades can be inspected in the modal.
        // Only the "Run Paper Live" action is disabled while a session is active.
        card.className = `glass-card p-5 rounded-2xl border ${cardBorderClass} transition-all duration-200 hover:-translate-y-1 cursor-pointer flex flex-col justify-between h-[340px] group`;
        card.onclick = () => openBacktestModal(s.id);

        const badgeBg = s.badge_color === 'emerald' ? 'bg-emerald-950 text-emerald-400 border-emerald-800' :
                        s.badge_color === 'cyan' ? 'bg-cyan-950 text-cyan-400 border-cyan-800' :
                        s.badge_color === 'purple' ? 'bg-purple-950 text-purple-400 border-purple-800' :
                        'bg-amber-950 text-amber-400 border-amber-800';

        card.innerHTML = `
          <div>
            <div class="flex items-center justify-between mb-3">
              <div class="w-10 h-10 rounded-xl bg-gray-900 border border-gray-700 flex items-center justify-center text-cyan-400 group-hover:scale-110 transition">
                <i class="fa-solid ${s.icon} text-base"></i>
              </div>
              <div class="flex items-center gap-1.5">
                <span class="text-[10px] font-mono px-2 py-0.5 rounded-full border bg-gray-900 text-gray-300 border-gray-700" title="Data provider: ${s.data_provider || 'Breeze'}">
                  <i class="fa-solid fa-database text-[8px] mr-0.5"></i>${s.data_provider || 'Breeze'}
                </span>
                <span class="text-[10px] font-mono px-2 py-0.5 rounded-full border ${badgeBg}">
                  ${s.badge}
                </span>
              </div>
            </div>

            <h3 class="font-bold text-sm text-white group-hover:text-cyan-400 transition mb-1">${s.name}</h3>
            <p class="text-[11px] text-gray-400 line-clamp-3 mb-3">${s.description}</p>

            <div class="space-y-1.5 text-[11px] font-mono bg-gray-950/60 p-2.5 rounded-lg border border-gray-800/80 mb-3">
              <div class="flex justify-between text-gray-400">
                <span>Asset:</span>
                <span class="text-gray-300 truncate max-w-[120px]">${s.asset_class}</span>
              </div>
              <div class="flex justify-between text-gray-400">
                <span>Default Symbol:</span>
                <span class="text-cyan-400 truncate max-w-[120px]" title="${s.default_symbols}">${s.default_symbols}</span>
              </div>
              <div class="flex justify-between text-gray-400">
                <span>Timeframe:</span>
                <span class="text-emerald-400">${s.default_timeframe}</span>
              </div>
            </div>
          </div>

          <div class="pt-2 border-t border-gray-800 flex items-center justify-between">
            <div class="text-[11px] font-mono">
              <span class="text-gray-500">Benchmark:</span>
              <span class="font-bold text-emerald-400 ml-1">${s.historical_stats.return_pct}</span>
              ${(window._oiRunning && window._oiRunning[s.id]) ? '<div class="mt-1 text-[10px] font-bold text-emerald-300 flex items-center space-x-1"><span class="inline-block w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span><span>RUNNING</span></div>' : ''}
               ${(s.paper_only_live && !(window._oiRunning && window._oiRunning[s.id])) ? '<div class="mt-1 text-[9px] text-amber-500">Paper Live Only</div>' : ''}
            </div>
            ${(window._oiRunning && window._oiRunning[s.id])
              ? `<button onclick="event.stopPropagation(); cardStopOiPaper('${s.id}')" class="px-3 py-1 bg-rose-500/20 hover:bg-rose-500 text-rose-300 hover:text-white font-bold text-xs rounded-lg transition flex items-center space-x-1"><i class="fa-solid fa-stop text-[10px]"></i><span>Stop</span></button>`
              : `<button class="px-3 py-1 bg-cyan-500/20 hover:bg-cyan-500 group-hover:bg-cyan-500 text-cyan-300 group-hover:text-gray-950 font-bold text-xs rounded-lg transition flex items-center space-x-1"><span>Test</span><i class="fa-solid fa-arrow-right text-[10px]"></i></button>`}
          </div>
        `;
        grid.appendChild(card);
      });
    }

    function openBacktestModal(stratId) {
      const s = catalog.find(item => item.id === stratId);
      if (!s) return;

      currentModalStrat = s;

      // Populate Header
      document.getElementById('modal-strat-title').textContent = s.name;
      document.getElementById('modal-strat-desc').textContent = s.description;
      document.getElementById('modal-strat-badge').textContent = s.badge;
      document.getElementById('modal-strat-icon').className = `fa-solid ${s.icon}`;

      // Populate Inputs
      document.getElementById('modal-start-date').value = s.default_start_date;
      document.getElementById('modal-end-date').value = s.default_end_date;
      document.getElementById('modal-timeframe').value = s.default_timeframe || '1d';

      // Populate Initial Capital field
      const capField = document.getElementById('modal-capital');
      if (capField) {
        capField.value = s.default_capital || 100000;
        capField.step = 50000;
      }

      // Hide provider dropdown for tick-based strategies
      const providerWrap = document.getElementById('modal-provider-wrap');
      if (providerWrap) {
        if (s.id === 'index_oi_momentum') {
          providerWrap.classList.add('hidden');
        } else {
          providerWrap.classList.remove('hidden');
          document.getElementById('modal-data-provider').value = s.data_provider || 'yfinance';
        }
      }

      // Populate predefined autocomplete options in ascending order.
      const symbolSource = s.use_global_universe ? (window.GLOBAL_UNIVERSE || []) : (s.allowed_symbols || []);
      window.modalSymbolOptions = [...symbolSource]
        .sort((a, b) => String(a.label || a.value).localeCompare(
          String(b.label || b.value), undefined, { sensitivity: 'base' }
        ));
      document.getElementById('modal-symbol-input').value = '';
      document.getElementById('modal-selected-symbol').value = '';
      renderSymbolOptions(window.modalSymbolOptions);

      // Populate Dynamic Parameters Grid
      const paramsGrid = document.getElementById('modal-params-grid');
      paramsGrid.innerHTML = '';
      s.param_schema.forEach(p => {
        const div = document.createElement('div');
        div.className = 'bg-gray-900/80 p-2 rounded-lg border border-gray-800';
        if (p.type === 'boolean') {
          div.innerHTML = `
            <label class="block text-gray-400 text-[10px] mb-1">${p.label}</label>
            <select id="param-${p.key}" class="w-full bg-gray-950 border border-gray-700 rounded px-2 py-1 text-white font-mono text-xs">
              <option value="true" ${p.default ? 'selected' : ''}>True (Active)</option>
              <option value="false" ${!p.default ? 'selected' : ''}>False</option>
            </select>
          `;
        } else if (p.type === 'select') {
          const opts = (p.options || []).map(o =>
            `<option value="${o}" ${String(o) === String(p.default) ? 'selected' : ''}>${o}</option>`).join('');
          div.innerHTML = `
            <label class="block text-gray-400 text-[10px] mb-1">${p.label}</label>
            <select id="param-${p.key}" class="w-full bg-gray-950 border border-gray-700 rounded px-2 py-1 text-white font-mono text-xs">${opts}</select>
          `;
        } else {
          div.innerHTML = `
            <label class="block text-gray-400 text-[10px] mb-1">${p.label}</label>
            <input type="number" id="param-${p.key}" value="${p.default}" step="${p.step || 1}" class="w-full bg-gray-950 border border-gray-700 rounded px-2 py-1 text-white font-mono text-xs">
          `;
        }
                paramsGrid.appendChild(div);
      });

      // Show Bollinger Bands section if strategy defines bollinger_schema
      const bbSection = document.getElementById('modal-bollinger-section');
      if (bbSection) {
        if (s.bollinger_schema) {
          bbSection.classList.remove('hidden');
          const bs = s.bollinger_schema;
          const enableKey = bs.enable_key || 'bollinger_enabled';
          const bbEnable = document.getElementById('bollinger-enabled');
          if (bbEnable) bbEnable.checked = s.default_params?.[enableKey] || false;
          bs.fields.forEach(f => {
            const el = document.getElementById(`bollinger_${f.key}`);
            if (el) el.value = s.default_params?.[f.key] || f.default;
          });
          toggleBollingerFields();
        } else {
          bbSection.classList.add('hidden');
        }
      }

      // Show variant confirm box only for OI momentum (paper live run)
      const _vb = document.getElementById('oi-variant-box');
      if (_vb) { if (s.id === 'index_oi_momentum') _vb.classList.remove('hidden'); else _vb.classList.add('hidden'); }
      // Re-attach to a running paper session so the trades table keeps streaming after the modal reopen
      window._oiPaperSessionId = (window._oiRunning && window._oiRunning[s.id]) || null;
      if (window._oiPaperSessionId) { setTimeout(pollOiPaperStatus, 300); }

      // For paper-only strategies (index_oi_momentum), hide the Run Backtest button
      // and show appropriate status. For other strategies, ensure the button is visible.
      const runBtn = document.getElementById('modal-run-btn');
      if (runBtn) {
        if (s.paper_only_live) {
          // Live tick-based paper strategy: no backtest in the modal at all (running or not)
          runBtn.classList.add('hidden');
        } else {
          // Every other strategy: guarantee a visible, working RUN BACKTEST button
          runBtn.classList.remove('hidden');
          runBtn.disabled = false;
          runBtn.innerHTML = '<i class="fa-solid fa-play"></i><span>RUN BACKTEST</span>';
          runBtn.className = 'w-full py-2 bg-gradient-to-r from-cyan-500 to-emerald-500 hover:from-cyan-400 hover:to-emerald-400 text-gray-950 font-bold rounded-lg shadow-lg transition flex items-center justify-center space-x-2';
        }
      }
      const oiVariantBox = document.getElementById('oi-variant-box');
      if (oiVariantBox) {
        const isRunning = !!(window._oiRunning && window._oiRunning[s.id]);
        // Keep "Run Paper Live" disabled for a live session; enabled once stopped
        syncOiPaperControls(isRunning);
        const statusEl = oiVariantBox.querySelector('.paper-status');
        if (isRunning) {
          if (!statusEl) {
            const statusDiv = document.createElement('div');
            statusDiv.className = 'paper-status mt-2 p-2 bg-emerald-950/60 border border-emerald-500/30 rounded-lg text-[11px] space-y-1.5';
              statusDiv.innerHTML = `
                <div class="text-emerald-300 font-bold flex items-center space-x-1.5"><i class="fa-solid fa-circle-check"></i><span>PAPER SESSION RUNNING</span></div>
                <div class="text-gray-300">Live tick-based testing via Angel One WebSocket2 \u2014 entries, exits and PnL stream into the trades table below (no backtest needed)</div>
                <button onclick="stopOiPaperSession()" class="w-full py-1.5 mt-1 bg-gradient-to-r from-rose-500 to-rose-600 hover:from-rose-400 hover:to-rose-500 text-gray-950 font-bold rounded-lg shadow transition flex items-center justify-center space-x-2">
                  <i class="fa-solid fa-stop"></i><span>STOP PAPER SESSION</span>
                </button>
                <button onclick="closeBacktestModal()" class="w-full text-[10px] text-gray-400 hover:text-gray-200 underline">Hide this window (session keeps running)</button>
              `;
            oiVariantBox.appendChild(statusDiv);
          }
        } else {
          // Remove any existing status element
          const existingStatus = oiVariantBox.querySelector('.paper-status');
          if (existingStatus) existingStatus.remove();
        }
      }

      // Reset Modal Metrics & Forward Test Controls
      document.getElementById('modal-error-banner').classList.add('hidden');
      document.getElementById('modal-forward-test-bar').classList.add('hidden');
      document.getElementById('modal-forward-success-alert').classList.add('hidden');
      document.getElementById('m-return').textContent = '--';
      document.getElementById('m-return-pct').textContent = '--';
      document.getElementById('m-winrate').textContent = '--';
      document.getElementById('m-trades-detail').textContent = '--';
      document.getElementById('m-drawdown').textContent = '--';
      document.getElementById('m-sharpe').textContent = '--';
      document.getElementById('m-pf').textContent = 'PF: --';
      document.getElementById('m-capital-label').textContent = `Initial: ₹${s.default_capital.toLocaleString('en-IN')}`;
      document.getElementById('modal-trades-count').textContent = '0 records';
      const _pnlTh = document.getElementById('modal-pnl-th');
      if (_pnlTh) _pnlTh.textContent = 'Realized PnL (₹)';
      document.getElementById('modal-trades-tbody').innerHTML = s.paper_only_live
        ? '<tr><td colspan="9" class="text-center py-6 text-gray-500">Waiting for live paper trades \u2014 entries, exits and PnL stream in here from Angel One WebSocket2 ticks.</td></tr>'
        : '<tr><td colspan="9" class="text-center py-6 text-gray-500">Click "Run Backtest" above to execute the simulation.</td></tr>';
      document.getElementById('modal-progress-container').classList.add('hidden');

      if (modalEquityChart) {
        modalEquityChart.destroy();
        modalEquityChart = null;
      }
      document.getElementById('modal-chart-label').textContent = 'Awaiting simulation run...';

      // Show Modal
      document.getElementById('backtest-modal').classList.remove('hidden');

      // A live paper session keeps its WS health panel + Stop control visible
      // (the reset above hides it for idle/backtest views)
      if (window._oiPaperSessionId) {
        document.getElementById('modal-forward-test-bar').classList.remove('hidden');
        const label = document.getElementById('modal-progress-label');
        if (label) label.innerHTML = '<i class="fa-solid fa-satellite-dish fa-spin text-cyan-400"></i><span>Reconnected to live PAPER session \u2014 loading ticks & trades...</span>';
        const pc = document.getElementById('modal-progress-container');
        if (pc) pc.classList.remove('hidden');
        renderPaperTradesTable({ paper_trades: [], open_paper_position_details: [] });
      }
    }

    function closeBacktestModal() {
      document.getElementById('backtest-modal').classList.add('hidden');
      closeSymbolOptions();
      refreshOiRunningState();
    }
    async function cardStopOiPaper(strategyId) {
      const sid = (window._oiRunning && window._oiRunning[strategyId]) || window._oiPaperSessionId;
      if (!sid) { refreshOiRunningState(); return; }
      if (!confirm(`Stop PAPER session ${sid}?`)) return;
      try {
        await fetch(`/api/paper/oi-momentum/stop/${sid}`, { method: 'POST' });
        if (window._oiPaperSessionId === sid) window._oiPaperSessionId = null;
        if (window._oiRunning) delete window._oiRunning[strategyId];
        syncOiPaperControls(false);  // re-enable "Run Paper Live" after stop
        refreshOiRunningState();
      } catch (e) { alert('Stop failed: ' + (e.message || e)); }
    }

    function getSelectedSymbols() {
      const selected = document.getElementById('modal-selected-symbol').value;
      return selected ? [selected] : [];
    }

    function closeSymbolOptions() {
      const options = document.getElementById('modal-symbol-options');
      if (options) options.classList.add('hidden');
    }

    function showSymbolOptions() {
      filterSymbolOptions(document.getElementById('modal-symbol-input').value);
    }

    function filterSymbolOptions(query) {
      const normalized = String(query || '').trim().toLowerCase();
      const options = (window.modalSymbolOptions || []).filter(sym => {
        const text = `${sym.label || ''} ${sym.value || ''}`.toLowerCase();
        return !normalized || text.includes(normalized);
      });
      renderSymbolOptions(options);
    }

    function renderSymbolOptions(options) {
      const container = document.getElementById('modal-symbol-options');
      if (!container) return;
      container.innerHTML = '';
      options.forEach(sym => {
        const option = document.createElement('button');
        option.type = 'button';
        option.className = 'block w-full text-left px-3 py-2 rounded hover:bg-gray-800 text-gray-200 text-xs font-mono';
        option.textContent = `${sym.label} (${sym.value})`;
        option.onclick = () => selectAutocompleteSymbol(sym);
        container.appendChild(option);
      });
      container.classList.toggle('hidden', options.length === 0);
    }

    function selectAutocompleteSymbol(symbol) {
      document.getElementById('modal-symbol-input').value = `${symbol.label} (${symbol.value})`;
      document.getElementById('modal-selected-symbol').value = symbol.value;
      closeSymbolOptions();
    }

    function handleSymbolInputKeydown(event) {
      if (event.key === 'Escape') {
        closeSymbolOptions();
      } else if (event.key === 'Enter') {
        const first = (window.modalSymbolOptions || []).find(sym => {
          const text = `${sym.label || ''} ${sym.value || ''}`.toLowerCase();
          return text.includes(event.target.value.trim().toLowerCase());
        });
        if (first) {
          event.preventDefault();
          selectAutocompleteSymbol(first);
        }
      }
    }

    function toggleBollingerFields() {
      const enable = document.getElementById('bollinger-enabled');
      const grid = document.getElementById('modal-bollinger-grid');
      if (enable && grid) {
        grid.classList.toggle('opacity-30', !enable.checked);
      }
    }

    async function executeModalBacktest() {
      if (!currentModalStrat) return;

      const runBtn = document.getElementById('modal-run-btn');
      const origText = runBtn.innerHTML;
      runBtn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i><span>RUNNING...</span>`;
      runBtn.disabled = true;

      const progressBox = document.getElementById('modal-progress-container');
      const progressBar = document.getElementById('modal-progress-bar') || { style: {} };
      const progressLabel = document.getElementById('modal-progress-label') || { innerHTML: '' };
      const progressPct = document.getElementById('modal-progress-pct') || { textContent: '' };

      progressBox.classList.remove('hidden');
      progressBar.style.width = '15%';
      progressPct.textContent = '15%';
      progressLabel.innerHTML = `<i class="fa-solid fa-database fa-spin"></i><span>Loading historical candles from Breeze cache...</span>`;

      const startDate = document.getElementById('modal-start-date').value;
      const endDate = document.getElementById('modal-end-date').value;
      
      const selectedSymbols = getSelectedSymbols();
      if (selectedSymbols.length === 0) {
        showModalError('Selection Required', 'Choose one symbol from the autocomplete list before running the backtest.');
        runBtn.innerHTML = origText;
        runBtn.disabled = false;
        progressBox.classList.add('hidden');
        return;
      }
      const instrument = selectedSymbols.join(', ');

      // Extract parameters
      let params = {};
      currentModalStrat.param_schema.forEach(p => {
        const el = document.getElementById(`param-${p.key}`);
        if (el) {
          if (p.type === 'boolean') {
            params[p.key] = el.value === 'true';
          } else if (p.type === 'select') {
            params[p.key] = el.value;
          } else {
            params[p.key] = parseFloat(el.value);
          }
        }
      });

      // Read Initial Capital from the dedicated modal field
      const capital = parseFloat(document.getElementById('modal-capital').value);
      if (!Number.isFinite(capital) || capital <= 0 || !startDate || !endDate || startDate > endDate) {
        showModalError('Invalid Backtest Configuration', 'Enter positive initial capital and valid start/end dates (start must not be after end).');
        runBtn.innerHTML = origText;
        runBtn.disabled = false;
        progressBox.classList.add('hidden');
        return;
      }

      // Extract Bollinger Bands params if the section is visible
      const bbSection = document.getElementById('modal-bollinger-section');
      if (bbSection && !bbSection.classList.contains('hidden')) {
        const bbEnable = document.getElementById('bollinger-enabled');
        params.bollinger_enabled = bbEnable ? bbEnable.checked : false;
        if (params.bollinger_enabled) {
          params.bollinger_length = parseFloat(document.getElementById('bollinger_length')?.value || 19);
          params.bollinger_mult = parseFloat(document.getElementById('bollinger_mult')?.value || 2.36);
          params.bollinger_offset = parseFloat(document.getElementById('bollinger_offset')?.value || 0);
          params.bollinger_ma_type = document.getElementById('bollinger_ma_type')?.value || 'WMA';
        }
      }

      document.getElementById('modal-error-banner').classList.add('hidden');

      // Get data provider
      const dataProvider = document.getElementById('modal-data-provider')?.value || 'yfinance';
      const timeframe = document.getElementById('modal-timeframe')?.value || '1d';

      // Decide whether to use streaming or legacy endpoint
      const useStreaming = currentModalStrat.id !== 'index_oi_momentum'; // Streaming for all non-tick strategies

      if (useStreaming) {
        // STREAMING MODE: Use SSE endpoint
        try {
          // Start the streaming backtest
          const res = await fetch('/api/backtest/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              strategy_id: currentModalStrat.id,
              instrument: instrument,
              timeframe: timeframe,
              capital: capital,
              start_date: startDate,
              end_date: endDate,
              params: params,
              data_provider: dataProvider
            })
          });

          if (!res.ok) {
            const errJson = await res.json().catch(() => ({}));
            throw new Error(errJson.detail || ('Backtest HTTP ' + res.status));
          }

          const { job_id } = await res.json();

          // Subscribe to SSE stream
          const eventSource = new EventSource(`/api/backtest/stream/${job_id}/events`);
          
          // Initialize streaming state
          let streamingTrades = [];
          let streamingMetrics = {};
          let streamingEquity = [];
          // Set to true once backtest_completed/backtest_failed arrives, so the
          // native EventSource 'error' fired when the server closes the stream
          // is not mistaken for a real connection failure.
          let streamFinished = false;

          progressBar.style.width = '0%';
          progressPct.textContent = '0%';
          progressLabel.innerHTML = `<i class="fa-solid fa-database fa-spin"></i><span>Initializing streaming backtest...</span>`;

          eventSource.addEventListener('backtest_started', (e) => {
            progressLabel.innerHTML = `<i class="fa-solid fa-play text-cyan-400"></i><span>Backtest started</span>`;
          });

          eventSource.addEventListener('candles_loaded', (e) => {
            progressLabel.innerHTML = `<i class="fa-solid fa-chart-line text-emerald-400"></i><span>Historical data loaded</span>`;
          });

          eventSource.addEventListener('progress', (e) => {
            const data = JSON.parse(e.data);
            const pct = data.payload.progress || 0;
            progressBar.style.width = `${pct}%`;
            progressPct.textContent = `${pct.toFixed(1)}%`;
            progressLabel.innerHTML = `<i class="fa-solid fa-microchip fa-spin"></i><span>Processing: ${data.payload.instrument || ''}</span>`;
          });

          eventSource.addEventListener('equity_update', (e) => {
            const data = JSON.parse(e.data);
            const point = data.payload.point;
            if (!point) return;
            streamingEquity.push(point);
            renderModalChart(streamingEquity);
            const label = document.getElementById('modal-chart-label');
            if (label) {
              label.textContent = `Streaming portfolio progression | Portfolio: ₹${(point.total_equity || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
            }
          });

          eventSource.addEventListener('trade_entry', (e) => {
            const data = JSON.parse(e.data);
            const trade = data.payload;
            
            // Add trade to streaming array (entry only)
            streamingTrades.push({
              instrument: trade.instrument,
              side: trade.side,
              entry_price: trade.entry_price,
              quantity: trade.quantity,
              entry_time: trade.timestamp,
              status: 'OPEN',
              pnl: 0
            });

            // Append row to table
            renderStreamingTradeEntry(trade, streamingTrades.length);
          });

          eventSource.addEventListener('trade_exit', (e) => {
            const data = JSON.parse(e.data);
            const trade = data.payload;
            
            const lastTrade = [...streamingTrades].reverse().find(t =>
              t.status === 'OPEN' && t.instrument === trade.instrument
            );
            if (lastTrade) {
              lastTrade.exit_price = trade.exit_price;
              lastTrade.pnl = trade.pnl;
              lastTrade.status = 'CLOSED';
              lastTrade.exit_time = trade.timestamp;
            }

            // Update the table row
            renderStreamingTradeExit(trade);
          });

          eventSource.addEventListener('metrics_update', (e) => {
            const data = JSON.parse(e.data);
            streamingMetrics = data.payload.metrics || {};
            
            // Update metrics cards incrementally
            updateStreamingMetrics(streamingMetrics);
          });

          eventSource.addEventListener('backtest_completed', (e) => {
            const data = JSON.parse(e.data);
            streamFinished = true;
            
            progressBar.style.width = '100%';
            progressPct.textContent = '100%';
            progressLabel.innerHTML = `<i class="fa-solid fa-check text-emerald-400"></i><span>Backtest completed (${streamingTrades.length} trades)</span>`;

            // Final render with complete data. The server payload now carries
            // the authoritative result (trades, equity_curve, candles_evaluated,
            // period); streamed client-side trades are only a live-preview
            // fallback for older server responses.
            const result = data.payload.result || {};
            const finalData = {
              ...result,
              trades: (Array.isArray(result.trades) && result.trades.length > 0)
                        ? result.trades : streamingTrades,
              equity_curve: (Array.isArray(result.equity_curve) && result.equity_curve.length > 0)
                        ? result.equity_curve : streamingEquity,
              metrics: streamingMetrics
            };
            
            renderModalResults(finalData);
            document.getElementById('modal-forward-test-bar').classList.remove('hidden');

            eventSource.close();
            runBtn.innerHTML = origText;
            runBtn.disabled = false;
          });

          eventSource.addEventListener('backtest_failed', (e) => {
            const data = JSON.parse(e.data);
            streamFinished = true;
            
            progressBar.style.width = '100%';
            progressPct.textContent = 'Failed';
            progressLabel.innerHTML = `<i class="fa-solid fa-circle-xmark text-rose-400"></i><span class="text-rose-400 font-bold">Backtest failed</span>`;
            showModalError('Streaming Backtest Error', data.payload.error || 'Unknown error');

            eventSource.close();
            runBtn.innerHTML = origText;
            runBtn.disabled = false;
          });

          eventSource.addEventListener('error', (e) => {
            if (streamFinished) {
              // Server closed the stream after completion/failure — expected,
              // not a connection problem.
              eventSource.close();
              return;
            }
            if (eventSource.readyState === EventSource.CLOSED) {
              // Fatal: browser gave up (e.g., HTTP error on reconnect).
              progressLabel.innerHTML = `<i class="fa-solid fa-circle-xmark text-rose-400"></i><span class="text-rose-400">Stream connection error</span>`;
              eventSource.close();
              runBtn.innerHTML = origText;
              runBtn.disabled = false;
            } else {
              // readyState === CONNECTING: EventSource retries automatically;
              // keep the stream alive and inform the user transiently.
              progressLabel.innerHTML = `<i class="fa-solid fa-plug text-amber-400"></i><span class="text-amber-300">Connection interrupted — reconnecting...</span>`;
            }
          });

        } catch (err) {
          progressBar.style.width = '100%';
          progressPct.textContent = 'Failed';
          progressLabel.innerHTML = `<i class="fa-solid fa-circle-xmark text-rose-400"></i><span class="text-rose-400 font-bold">Failed to start</span>`;
          showModalError('Streaming Start Error', err.message || err);
          runBtn.innerHTML = origText;
          runBtn.disabled = false;
        }

      } else {
        // LEGACY MODE: Use one-shot endpoint for index_oi_momentum
        const progressTimer = setTimeout(() => {
          progressBar.style.width = '55%';
          progressPct.textContent = '55%';
          progressLabel.innerHTML = `<i class="fa-solid fa-microchip fa-spin"></i><span>Computing indicator arrays & running trade triggers...</span>`;
        }, 250);

        try {
          const endpoint = '/api/backtest/oi-momentum';
          const res = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              strategy_id: currentModalStrat.id,
              instrument: instrument,
              timeframe: timeframe,
              capital: capital,
              start_date: startDate,
              end_date: endDate,
              params: params
            })
          });

          clearTimeout(progressTimer);

          if (!res.ok) {
            const errJson = await res.json().catch(() => ({}));
            throw new Error(errJson.detail || ('Backtest HTTP ' + res.status));
          }

          const data = await res.json();

          if (data.error) {
            progressBar.style.width = '100%';
            progressPct.textContent = 'Error';
            progressLabel.innerHTML = `<i class="fa-solid fa-triangle-exclamation text-rose-400"></i><span class="text-rose-400 font-bold">Execution encountered an issue.</span>`;
            showModalError('Backend Warning / Data Notice', data.error);
          } else {
            progressBar.style.width = '100%';
            progressPct.textContent = '100%';
            progressLabel.innerHTML = `<i class="fa-solid fa-check text-emerald-400"></i><span>Backtest completed successfully (${data.trades.length} trades evaluated).</span>`;
          }

          renderModalResults(data);
          document.getElementById('modal-forward-test-bar').classList.remove('hidden');

        } catch (err) {
          clearTimeout(progressTimer);
          progressBar.style.width = '100%';
          progressPct.textContent = 'Failed';
          progressLabel.innerHTML = `<i class="fa-solid fa-circle-xmark text-rose-400"></i><span class="text-rose-400 font-bold">Simulation aborted.</span>`;
          showModalError('Backend Execution Exception', err.message || err);
        } finally {
          runBtn.innerHTML = origText;
          runBtn.disabled = false;
        }
      }
    }

    function showModalError(title, msg) {
      const banner = document.getElementById('modal-error-banner');
      document.getElementById('modal-error-title').textContent = title;
      document.getElementById('modal-error-msg').textContent = msg;
      banner.classList.remove('hidden');
    }

    function renderStreamingTradeEntry(trade, tradeNum) {
      // Append a new row for trade entry
      const tbody = document.getElementById('modal-trades-tbody');
      
      // Clear placeholder if first trade
      if (tradeNum === 1) {
        tbody.innerHTML = '';
      }
      
      const row = document.createElement('tr');
      row.id = `stream-trade-${tradeNum}`;
      row.className = 'border-b border-gray-800/50 hover:bg-gray-800/30 transition font-mono text-[11px]';
      
      const sideBadge = trade.side === 'BUY' 
        ? '<span class="inline-block px-2 py-0.5 rounded-full text-[9px] font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/50">BUY</span>'
        : '<span class="inline-block px-2 py-0.5 rounded-full text-[9px] font-bold bg-rose-500/20 text-rose-300 border border-rose-500/50">SELL</span>';
      
      row.dataset.instrument = trade.instrument;
      row.dataset.status = 'OPEN';
      row.innerHTML = `
        <td class="py-2 px-2 text-gray-400">${tradeNum}</td>
        <td class="py-2 px-2">${sideBadge}</td>
        <td class="py-2 px-2 text-cyan-400">${trade.instrument}</td>
        <td class="py-2 px-2 text-right">${trade.quantity}</td>
        <td class="py-2 px-2 text-gray-400">${formatTradeDateTime(trade.timestamp)}</td>
        <td class="py-2 px-2 text-right text-emerald-400">₹${trade.entry_price.toFixed(2)}</td>
        <td class="py-2 px-2 text-right text-gray-500">-</td>
        <td class="py-2 px-2 text-right text-gray-500">-</td>
        <td class="py-2 px-2 text-right"><span class="text-yellow-400 text-[10px]"><i class="fa-solid fa-spinner fa-spin"></i> OPEN</span></td>
      `;
      
      tbody.insertBefore(row, tbody.firstChild);
      
      // Auto-scroll to bottom
      const tradesContainer = tbody.closest('.overflow-y-auto');
      if (tradesContainer) {
        tradesContainer.scrollTop = tradesContainer.scrollHeight;
      }
    }

    function renderStreamingTradeExit(trade) {
      // Update the existing row with exit data
      const row = [...document.querySelectorAll('[id^="stream-trade-"]')].reverse().find(candidate =>
        candidate.dataset.status === 'OPEN' && candidate.dataset.instrument === trade.instrument
      );
      if (!row) return;
      
      const exitTimeCell = row.cells[6];
      const exitCell = row.cells[7];
      const pnlCell = row.cells[8];
      
      if (exitTimeCell) {
        exitTimeCell.textContent = formatTradeDateTime(trade.timestamp);
        exitTimeCell.className = 'py-2 px-2 text-right text-gray-400';
      }
      if (exitCell) {
        exitCell.textContent = `₹${trade.exit_price.toFixed(2)}`;
        exitCell.className = 'py-2 px-2 text-right text-rose-400';
      }
      
      if (pnlCell) {
        const isProfit = trade.pnl >= 0;
        pnlCell.innerHTML = `<span class="font-bold ${isProfit ? 'text-emerald-400' : 'text-rose-400'}">${isProfit ? '+' : ''}₹${trade.pnl.toFixed(2)}</span>`;
      }
      row.dataset.status = 'CLOSED';
    }

    function formatTradeDateTime(value) {
      if (!value) return '--';
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return String(value);
      return date.toLocaleString('en-IN', {
        day: '2-digit', month: '2-digit', year: 'numeric',
        hour: '2-digit', minute: '2-digit', second: '2-digit',
        hour12: false, timeZone: 'Asia/Kolkata'
      });
    }

    function updateStreamingMetrics(metrics) {
      // Update metrics cards incrementally
      if (metrics.total_return !== undefined) {
        const isProfit = metrics.total_return >= 0;
        const returnEl = document.getElementById('m-return');
        returnEl.textContent = (isProfit ? '+' : '') + '₹' + metrics.total_return.toLocaleString('en-IN', { maximumFractionDigits: 2 });
        returnEl.className = 'text-lg font-bold font-mono ' + (isProfit ? 'text-emerald-400' : 'text-rose-400');
      }
      
      if (metrics.total_return_pct !== undefined) {
        const isProfit = metrics.total_return_pct >= 0;
        const returnPctEl = document.getElementById('m-return-pct');
        returnPctEl.textContent = (isProfit ? '+' : '') + metrics.total_return_pct.toFixed(2) + '% Total Return';
        returnPctEl.className = 'text-[11px] font-mono ' + (isProfit ? 'text-emerald-400' : 'text-rose-400');
      }
      
      if (metrics.win_rate !== undefined) {
        document.getElementById('m-winrate').textContent = metrics.win_rate.toFixed(1) + '%';
      }
      
      if (metrics.total_trades !== undefined) {
        document.getElementById('m-trades-detail').textContent = `${metrics.total_trades} trades (${metrics.winning_trades || 0}W / ${metrics.losing_trades || 0}L)`;
        document.getElementById('modal-trades-count').textContent = `${metrics.total_trades} records`;
      }
      
      if (metrics.max_drawdown !== undefined) {
        document.getElementById('m-drawdown').textContent = metrics.max_drawdown.toFixed(2) + '%';
      }
      
      if (metrics.sharpe_ratio !== undefined) {
        document.getElementById('m-sharpe').textContent = metrics.sharpe_ratio.toFixed(2);
      }
      
      if (metrics.profit_factor !== undefined) {
        document.getElementById('m-pf').textContent = 'PF: ' + metrics.profit_factor.toFixed(2);
      }
    }

    function renderModalResults(data) {
      // 1. Metrics Cards
      const isProfit = data.total_return >= 0;
      const returnEl = document.getElementById('m-return');
      returnEl.textContent = (isProfit ? '+' : '') + '₹' + data.total_return.toLocaleString('en-IN', { maximumFractionDigits: 2 });
      returnEl.className = 'text-lg font-bold font-mono ' + (isProfit ? 'text-emerald-400' : 'text-rose-400');

      const returnPctEl = document.getElementById('m-return-pct');
      returnPctEl.textContent = (isProfit ? '+' : '') + data.total_return_pct.toFixed(2) + '% Total Return';
      returnPctEl.className = 'text-[11px] font-mono ' + (isProfit ? 'text-emerald-400' : 'text-rose-400');

      document.getElementById('m-winrate').textContent = data.win_rate.toFixed(1) + '%';
      document.getElementById('m-trades-detail').textContent = `${data.total_trades} trades (${data.winning_trades}W / ${data.losing_trades}L)`;
      document.getElementById('m-drawdown').textContent = data.max_drawdown.toFixed(2) + '%';
      document.getElementById('m-sharpe').textContent = data.sharpe_ratio.toFixed(2);
      document.getElementById('m-pf').textContent = 'PF: ' + (data.profit_factor ? data.profit_factor.toFixed(2) : '0.00');
      document.getElementById('m-capital-label').textContent = `Initial: ₹${data.initial_capital.toLocaleString('en-IN')}`;

      // 2. Chart Progression
      document.getElementById('modal-chart-label').textContent = `${data.candles_evaluated} candles (${data.period}) | Portfolio: ₹${data.final_capital.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
      renderModalChart(data.equity_curve);

      // 3. Trade Stream Log Table
      const tbody = document.getElementById('modal-trades-tbody');
      tbody.innerHTML = '';
      document.getElementById('modal-trades-count').textContent = `${data.trades.length} records`;

      if (data.trades.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" class="text-center py-6 text-gray-500">No trade signals triggered within selected dates.</td></tr>';
      } else {
        if (data.mode_badges) {
          const b = Object.entries(data.mode_badges).map(([k, v]) => `${k}: ${v}`).join('  |  ');
          const _pl = document.getElementById('modal-progress-label');
          if (_pl) _pl.innerHTML += `<div class="text-[11px] text-amber-300 mt-1">Modes — ${b}</div>`;
          const extra = (data.base_trades !== undefined) ? ` | Base ${data.base_trades} (${(data.base_pnl>=0?'+':'')+'₹'+data.base_pnl.toLocaleString('en-IN')}) / Expiry ${data.expiry_trades} (${(data.expiry_pnl>=0?'+':'')+'₹'+data.expiry_pnl.toLocaleString('en-IN')})` : '';
          document.getElementById('modal-chart-label').textContent += extra;
        }
        [...data.trades].sort((a, b) =>
          new Date(b.exit_time || b.entry_time || 0) - new Date(a.exit_time || a.entry_time || 0)
        ).forEach(t => {
          const row = document.createElement('tr');
          const pnlVal = (t.pnl !== undefined ? t.pnl : t.realized_pnl) || 0;
          const pnlClass = pnlVal >= 0 ? 'text-emerald-400' : 'text-rose-400';
          row.className = 'hover:bg-gray-800/40 transition';
          row.innerHTML = `
            <td class="py-2 px-3 text-cyan-400 font-semibold">${t.trade_id}</td>
            <td class="py-2 px-3">${t.side || (t.action === 'SELL' ? 'SELL' : 'BUY')}</td>
            <td class="py-2 px-3">${t.instrument}</td>
            <td class="py-2 px-3">${t.quantity}</td>
            <td class="py-2 px-3 text-gray-400">${formatTradeDateTime(t.entry_time)}</td>
            <td class="py-2 px-3">₹${(t.entry_price || 0).toFixed(2)}</td>
            <td class="py-2 px-3 text-gray-400">${formatTradeDateTime(t.exit_time)}</td>
            <td class="py-2 px-3">₹${(t.exit_price || 0).toFixed(2)}</td>
            <td class="py-2 px-3 text-right font-bold ${pnlClass}">₹${pnlVal.toFixed(2)}</td>
          `;
          tbody.appendChild(row);
        });
      }
    }

    function renderModalChart(curve) {
      const ctx = document.getElementById('modalEquityChart').getContext('2d');
      if (modalEquityChart) {
        modalEquityChart.destroy();
      }

      const labels = curve.map(pt => pt.timestamp || pt.t || '');
      const values = curve.map(pt => (pt.total_equity !== undefined ? pt.total_equity : pt.value));
      const crosshairPlugin = {
        id: 'modalEquityCrosshair',
        afterDraw(chart) {
          if (chart._activeCrosshairX === undefined) return;
          const {ctx, chartArea} = chart;
          if (!chartArea) return;
          ctx.save();
          ctx.strokeStyle = 'rgba(148, 163, 184, 0.8)';
          ctx.lineWidth = 1;
          ctx.setLineDash([4, 4]);
          ctx.beginPath();
          ctx.moveTo(chart._activeCrosshairX, chartArea.top);
          ctx.lineTo(chart._activeCrosshairX, chartArea.bottom);
          ctx.stroke();
          ctx.restore();
        }
      };

      modalEquityChart = new Chart(ctx, {
        plugins: [crosshairPlugin],
        type: 'line',
        data: {
          labels: labels,
          datasets: [{
            label: 'Portfolio Equity (₹)',
            data: values,
            borderColor: '#10b981',
            backgroundColor: 'rgba(16, 185, 129, 0.1)',
            fill: true,
            tension: 0.2,
            borderWidth: 2,
            pointRadius: 0
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          interaction: {
            mode: 'index',
            intersect: false
          },
          onHover(event, active) {
            const chart = event.chart;
            chart._activeCrosshairX = active.length ? active[0].element.x : undefined;
            chart.draw();
          },
          plugins: {
            legend: { display: false },
            tooltip: {
              mode: 'index',
              intersect: false,
              callbacks: {
                title: (items) => items.length ? formatTradeDateTime(items[0].label) : '',
                label: (ctx) => `Equity: ₹${ctx.parsed.y.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`
              }
            }
          },
          scales: {
            x: {
              display: true,
              ticks: {
                color: '#94a3b8',
                maxTicksLimit: 8,
                maxRotation: 0,
                callback: (value, index) => {
                  const label = labels[index];
                  if (!label) return '';
                  const date = new Date(label);
                  return Number.isNaN(date.getTime()) ? label : formatTradeDateTime(label);
                }
              },
              grid: { color: 'rgba(255, 255, 255, 0.05)' }
            },
            y: {
              grid: { color: 'rgba(255, 255, 255, 0.05)' },
              ticks: {
                color: '#94a3b8',
                font: { family: 'JetBrains Mono', size: 10 },
                callback: (val) => '₹' + val.toLocaleString('en-IN')
              }
            }
          }
        }
      });
    }

    // "Run Paper Live" is disabled while this strategy has an active paper session
    // (one live session at a time) and re-enabled as soon as it is stopped.
    function syncOiPaperControls(isRunning) {
      const btn = document.getElementById('btn-oi-paper');
      if (btn) {
        btn.disabled = !!isRunning;
        btn.className = isRunning
          ? 'w-full py-1.5 mt-1 bg-gray-700 text-gray-400 font-bold rounded-lg shadow transition flex items-center justify-center space-x-2 cursor-not-allowed'
          : 'w-full py-1.5 mt-1 bg-gradient-to-r from-amber-500 to-rose-500 hover:from-amber-400 hover:to-rose-400 text-gray-950 font-bold rounded-lg shadow transition flex items-center justify-center space-x-2';
        btn.innerHTML = isRunning
          ? '<i class="fa-solid fa-circle-check"></i><span>SESSION RUNNING \u2014 STOP TO RESTART</span>'
          : '<i class="fa-solid fa-satellite-dish"></i><span>RUN PAPER LIVE (confirm)</span>';
      }
      // Variants define the running session's subscriptions, so lock them while live
      document.querySelectorAll('input[name="oi-variant"]').forEach(cb => { cb.disabled = !!isRunning; });
    }

    async function startOiPaperSession() {
      if (!currentModalStrat) return;
      
      // Check if already running
      const isAlreadyRunning = !!(window._oiRunning && window._oiRunning[currentModalStrat.id]);
      if (isAlreadyRunning) {
        showModalError('Already Running', 'This strategy is already running in paper live mode. Please stop it first before starting a new session.');
        return;
      }
      
      const boxes = Array.from(document.querySelectorAll('input[name="oi-variant"]:checked')).map(c => c.value);
      if (boxes.length === 0) { showModalError('Confirm Variants', 'Check at least one variant: Regular (base) and/or Expiry-only.'); return; }
      const ok = confirm(`Start PAPER-ONLY live monitoring for ${currentModalStrat.name} on [${getSelectedSymbols().join(', ')}] with variant(s) [${boxes.join(', ')}]?\n\nSTRICTLY PAPER TRADES — no real orders will be placed.`);
      if (!ok) return;
      const btn = document.getElementById('btn-oi-paper');
      const orig = btn ? btn.innerHTML : '';
      if (btn) { btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i><span>STARTING PAPER...</span>'; btn.disabled = true; }
      try {
        const params = {};
        currentModalStrat.param_schema.forEach(p => {
          const el = document.getElementById(`param-${p.key}`);
          if (el) params[p.key] = (p.type === 'boolean') ? (el.value === 'true') : ((p.type === 'select') ? el.value : parseFloat(el.value));
        });
        const res = await fetch('/api/paper/oi-momentum/start', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ indices: getSelectedSymbols(), variants: boxes, capital: params.capital || currentModalStrat.default_capital, params: params })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Paper session failed to start');
        const bar = document.getElementById('modal-forward-test-bar');
        if (bar) bar.classList.remove('hidden');
        const alert = document.getElementById('modal-forward-success-alert');
        const msg = document.getElementById('modal-forward-success-msg');
        if (alert && msg) {
          const exp = Object.entries(data.expiry_today || {}).map(([k,v]) => `${k}: ${v ? 'EXPIRY TODAY' : 'base'}`).join(' | ');
          const autoStopAt = data.stop_at_ist || '15:30 IST';
          msg.innerHTML = `<div class="font-bold text-emerald-300">Forward Test Activated Successfully!</div><div class="mt-1">PAPER session ${data.session_id} RUNNING (paper only, no real orders). Variants: ${(data.variants||[]).join(', ')}. ${exp}. Feed: Angel One WebSocket2 SNAP_QUOTE(mode=3) live ticks on FUT+CE+PE (strategy evaluates every tick). Auto square-off: ${autoStopAt} (or press Stop). Status: /api/paper/oi-momentum/status/${data.session_id}</div><div id="oi-subs-table" class="mt-2 text-[11px]"></div>`;
          alert.classList.remove('hidden');
        }
        window._oiPaperSessionId = data.session_id;
        window._oiRunning = window._oiRunning || {};
        window._oiRunning[currentModalStrat.id] = data.session_id;
        syncOiPaperControls(true);
        pollOiPaperStatus();
        setTimeout(refreshOiRunningState, 1000);
      } catch (err) {
        showModalError('Paper Session Error', err.message || err);
      } finally {
        // Restore the idle label only when no session ended up running
        if (btn && !(window._oiRunning && window._oiRunning[currentModalStrat.id])) {
          btn.innerHTML = orig;
          btn.disabled = false;
          syncOiPaperControls(false);
        }
      }
    }

    function oiWsAgeSec(iso) {
      if (!iso) return null;
      try { return Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000)); } catch (e) { return null; }
    }
    // Per-leg live cache: each FUT/CE/PE leg paints independently the moment its
    // tick arrives. A fresh poll only overwrites legs present in the payload --
    // legs with no new tick keep their last CMP instead of blanking to '—'.
    window._oiLegCache = window._oiLegCache || {};
    function oiLegTick() {
      const el = document.getElementById('oi-tick-flash');
      if (!el) return;
      el.style.opacity = '1';
      setTimeout(() => { el.style.opacity = '0.25'; }, 250);
    }
    function renderOiWsHealth(data) {
      const dot = document.getElementById('oi-ws-dot');
      const summary = document.getElementById('oi-ws-summary');
      const rows = document.getElementById('oi-ws-rows');
      if (!dot || !summary || !rows) return;
      const h = data.ws_health || {};
      const age = oiWsAgeSec(data.last_tick_at);
      const alive = data.running && age !== null && age < 90;
      const degraded = data.running && (age === null || age >= 90);
      dot.className = 'inline-block w-2 h-2 rounded-full ' + (alive ? 'bg-emerald-400 animate-pulse' : (degraded ? 'bg-amber-400 animate-ping' : 'bg-rose-500'));
      const state = !data.running ? 'STOPPED' : (alive ? `LIVE · last tick ${age}s ago` : (age === null ? 'CONNECTING · no ticks yet' : `STALE · ${age}s since tick`));
      summary.innerHTML = `${data.feed_source || 'Angel One WebSocket2'} | WS msgs: ${data.ws_ticks ?? 0} | ${state} <span id="oi-tick-flash" class="inline-block w-1.5 h-1.5 rounded-full bg-cyan-300 ml-1" style="opacity:0.25;transition:opacity 0.3s"></span>`;
      const idxs = data.indices || [];
      if (!idxs.length) { rows.innerHTML = '<div class="text-gray-500">No indices in session.</div>'; return; }
      const px = data.live_prices || {};
      const fmt = (v) => (v && v > 0) ? `₹${Number(v).toLocaleString('en-IN', { maximumFractionDigits: 2 })}` : '<span class="text-gray-500">—</span>';
      const cache = window._oiLegCache;
      // merge: only overwrite a leg when the payload carries a fresh (>0) price
      idxs.forEach(ix => {
        const lp = px[ix] || {};
        const c = cache[ix] = cache[ix] || { fut: 0, ce: 0, pe: 0, ticks: 0, tick_age_s: null };
        ['fut', 'ce', 'pe'].forEach(leg => { if (lp[leg] && lp[leg] > 0) { if (c[leg] !== lp[leg]) { c[leg] = lp[leg]; c[leg + '_flash'] = true; } } });
        if ((lp.ticks ?? 0) > (c.ticks || 0)) { c.ticks = lp.ticks; oiLegTick(); }
        if (lp.tick_age_s !== undefined && lp.tick_age_s !== null) c.tick_age_s = lp.tick_age_s;
      });
      rows.innerHTML = idxs.map(ix => {
        const s = (data.subscriptions || {})[ix] || {};
        const hh = h[ix] || {};
        const c = cache[ix] || {};
        const hAge = (c.tick_age_s !== undefined && c.tick_age_s !== null) ? c.tick_age_s : oiWsAgeSec(hh.last_tick_at);
        const pill = (!data.running) ? '<span class="text-gray-500">■ stopped</span>' : (hAge !== null && hAge < 90) ? `<span class="text-emerald-400">● live (${hAge}s)</span>` : '<span class="text-amber-300">● waiting</span>';
        const flash = (leg) => c[leg + '_flash'] ? ' style="background:rgba(6,182,212,0.25);border-radius:4px;padding:0 4px;"' : '';
        const legRow = (label, sym, val) => `<div>${label}: ${sym} (<span class="text-emerald-300 font-bold"${flash(val)}>${fmt(c[val])}</span>)</div>`;
        const row = `<div class="border-t border-white/5 pt-1 pb-1"><div class="flex flex-wrap gap-x-3 items-center"><span class="text-cyan-300 font-bold w-20">${ix}</span><span>${pill}</span><span class="text-gray-400">ticks:${hh.ticks ?? c.ticks ?? 0}</span><span class="text-gray-500">ATM:${s.atm_strike ?? '—'}</span></div><div class="mt-0.5 space-y-0.5 text-gray-200">${legRow('FUT', s.fut_symbol || s.fut_token || '—', 'fut')}${legRow('CE', s.ce_symbol || s.ce_token || '<span class="text-amber-300">resolving…</span>', 'ce')}${legRow('PE', s.pe_symbol || s.pe_token || '<span class="text-amber-300">resolving…</span>', 'pe')}</div></div>`;
        ['fut', 'ce', 'pe'].forEach(leg => { c[leg + '_flash'] = false; });
        return row;
      }).join('');
    }
    async function stopOiPaperSession() {
      const sid = window._oiPaperSessionId;
      if (!sid) return;
      if (!confirm(`Stop PAPER session ${sid}? Live monitoring halts; paper trades are kept.`)) return;
      try {
        const res = await fetch(`/api/paper/oi-momentum/stop/${sid}`, { method: 'POST' });
        await res.json();
        window._oiPaperSessionId = null;
        if (window._oiRunning) delete window._oiRunning['index_oi_momentum'];
        syncOiPaperControls(false);  // re-enable "Run Paper Live" after stop
        const label = document.getElementById('modal-progress-label');
        if (label) label.innerHTML = '<span class="text-gray-400">Paper session stopped by user.</span>';
        const dot = document.getElementById('oi-ws-dot');
        if (dot) dot.className = 'inline-block w-2 h-2 rounded-full bg-rose-500';
        refreshOiRunningState();
      } catch (e) { showModalError('Stop Failed', e.message || e); }
    }
    async function refreshOiRunningState() {
      try {
        const res = await fetch('/api/paper/oi-momentum/sessions');
        const data = await res.json();
        window._oiRunning = {};
        (data.sessions || []).forEach(s => { if (s.running) window._oiRunning[s.strategy_id || 'index_oi_momentum'] = s.session_id; });
        renderStrategyCards();
        const sid = window._oiPaperSessionId;
        if (sid && !window._oiRunning['index_oi_momentum']) window._oiPaperSessionId = null;
      } catch (e) { /* silent */ }
    }
    async function pollOiPaperStatus() {
      const sid = window._oiPaperSessionId;
      if (!sid) return;
      try {
        const res = await fetch(`/api/paper/oi-momentum/status/${sid}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'status failed');
        renderOiWsHealth(data);
        const label = document.getElementById('modal-progress-label');
        if (label && data.running) {
          const autoStopRaw = data.stop_at_ist || '15:30 IST';
          const autoStop = autoStopRaw.split(' ').slice(1).join(' ') || autoStopRaw;
          label.innerHTML = `<i class="fa-solid fa-satellite-dish fa-spin text-cyan-400"></i><span>PAPER live: ${data.ticks_seen} ticks | ${data.paper_trades_count} paper trades | open: ${(data.open_paper_positions||[]).join(', ')||'flat'} | auto-stop ${autoStop} | NO real orders</span>`;
        }
        const subsEl = document.getElementById('oi-subs-table');
        if (subsEl && data.subscriptions) {
          const rows = Object.values(data.subscriptions).map(s => `<tr class="border-t border-white/10"><td class="pr-2 py-0.5 font-bold text-cyan-300">${s.index||''}</td><td class="pr-2 py-0.5">FUT: ${s.fut_symbol||s.fut_token||'—'}</td><td class="pr-2 py-0.5">ATM ${s.atm_strike??'—'} CE: ${s.ce_symbol||s.ce_token||'<span class="text-red-400">pending</span>'} </td><td class="py-0.5">PE: ${s.pe_symbol||s.pe_token||'<span class="text-red-400">pending</span>'}</td></tr>`).join('');
          subsEl.innerHTML = `<div class="text-slate-300 font-semibold mb-1">Feed Source: ${data.feed_source||'Angel One WebSocket2'} | WS ticks: ${data.ws_ticks??0}</div><table class="w-full">${rows||'<tr><td class="text-amber-300">Discovering tokens…</td></tr>'}</table>`;
        }
        if (data.errors && data.errors.length) {
          const last = data.errors[data.errors.length-1];
          if (/fail|error|reject|no tokens|skipped|unavailable/i.test(last)) showModalError('Paper Session Error (live, paper-only)', last);
        }
        if (data.running) {
          syncOiPaperControls(true);
          setTimeout(pollOiPaperStatus, 3000);
          // Render paper trades in the modal trades table (entries, exits, PnL)
          renderPaperTradesTable(data);
        } else {
          // Session ended (manual stop or 15:30 IST auto square-off) -- report why
          const endLabel = document.getElementById('modal-progress-label');
          if (endLabel) {
            const why = (data.stop_reason === 'market_close_15:30_IST')
              ? 'Auto square-off at market close (15:30 IST). Paper trades are kept.'
              : (data.stop_reason === 'feed_unavailable'
                  ? 'Stopped: live WebSocket feed unavailable.'
                  : 'Paper session stopped by user.');
            endLabel.innerHTML = `<span class="text-gray-400">${why}</span>`;
          }
          const endDot = document.getElementById('oi-ws-dot');
          if (endDot) endDot.className = 'inline-block w-2 h-2 rounded-full bg-rose-500';
          window._oiPaperSessionId = null;
          syncOiPaperControls(false);
          // Session over: drop the stale "running" panel so the modal shows idle state
          const staleStatus = document.querySelector('#oi-variant-box .paper-status');
          if (staleStatus) staleStatus.remove();
          refreshOiRunningState();
        }
      } catch (e) { /* silent: session view only */ }
        // keep per-index tick ages fresh between polls (1s repaint, no fetch)
        if (!window._oiAgeTimer) { window._oiAgeTimer = setInterval(() => {
          const sid = window._oiPaperSessionId;
          if (!sid) return;
          const rowsEl = document.getElementById('oi-ws-rows');
          if (!rowsEl || rowsEl.textContent.includes('Waiting for first tick')) return;
          const cache = window._oiLegCache || {};
          Object.values(cache).forEach(c => { if (typeof c.tick_age_s === 'number') c.tick_age_s += 1; });
        }, 1000); }
    }

    // Format an ISO (IST) timestamp for the trades table (HH:MM:SS)
    function fmtPaperTime(iso) {
      if (!iso) return '--';
      try {
        const d = new Date(iso);
        if (isNaN(d.getTime())) return String(iso).slice(11, 19) || String(iso);
        return d.toLocaleTimeString('en-IN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: 'Asia/Kolkata' });
      } catch (e) { return String(iso); }
    }

    // Live paper trades: in-progress positions first (entry + mark-to-market PnL),
    // then closed trades with realized PnL. Fed by pollOiPaperStatus() every 3s.
    function renderPaperTradesTable(data) {
      const tbody = document.getElementById('modal-trades-tbody');
      if (!tbody) return;
      const trades = data.paper_trades || [];
      const opens = data.open_paper_position_details || [];
      const countEl = document.getElementById('modal-trades-count');
      if (countEl) countEl.textContent = `${trades.length} closed | ${opens.length} open`;
      // The column carries unrealized marks on in-progress rows, so relabel it for live sessions
      const pnlTh = document.getElementById('modal-pnl-th');
      if (pnlTh) pnlTh.textContent = 'PnL (₹)';

      // ₹ formatter that keeps the sign *before* the currency symbol (-₹300, not ₹-300)
      const inr = (v) => {
        const n = Number(v || 0);
        const body = Math.abs(n).toLocaleString('en-IN', { maximumFractionDigits: 2 });
        return (n < 0 ? '-\u20B9' : '\u20B9') + body;
      };
      const rows = [];

      // Trades in progress: entry known, exit pending, unrealized PnL marked from live ticks
      opens.forEach(op => {
        const unreal = (op.unrealized_pnl === null || op.unrealized_pnl === undefined) ? null : Number(op.unrealized_pnl);
        const pnlCell = (unreal === null)
          ? '<span class="text-amber-300">marking...</span>'
          : `<span class="${unreal >= 0 ? 'text-emerald-400' : 'text-rose-400'}">${unreal >= 0 ? '+' : ''}${inr(unreal)}</span> <span class="text-amber-400 text-[10px]">(unrealized)</span>`;
        rows.push(`<tr class="hover:bg-gray-800/40 transition bg-amber-950/20">
          <td class="py-1.5 px-3 text-amber-300 font-bold">OPEN</td>
          <td class="py-1.5 px-3">${op.side || '--'}</td>
          <td class="py-1.5 px-3">${op.index || '--'} <span class="text-cyan-400">${op.side || ''}</span> <span class="text-gray-500">(${op.variant || 'base'})</span></td>
          <td class="py-1.5 px-3">${op.qty ?? '--'}</td>
          <td class="py-1.5 px-3">${formatTradeDateTime(op.entry_time)}</td>
          <td class="py-1.5 px-3">${op.entry ? inr(op.entry) : '--'}</td>
          <td class="py-1.5 px-3 text-amber-400">IN PROGRESS</td>
          <td class="py-1.5 px-3">${op.mark ? inr(op.mark) : '--'}</td>
          <td class="py-1.5 px-3 text-right">${pnlCell}</td>
        </tr>`);
      });

      // Closed paper trades with realized PnL
      [...trades].sort((a, b) =>
        new Date(b.exit_time || b.entry_time || 0) - new Date(a.exit_time || a.entry_time || 0)
      ).forEach(t => {
        const pnl = Number(t.paper_pnl || 0);
        rows.push(`<tr class="hover:bg-gray-800/40 transition">
          <td class="py-1.5 px-3 text-cyan-300">${t.paper_id || t.trade_id || '--'}</td>
          <td class="py-1.5 px-3">${t.side || '--'}</td>
          <td class="py-1.5 px-3">${t.index || '--'} <span class="text-gray-500">(${t.variant || 'base'})</span></td>
          <td class="py-1.5 px-3">${t.qty ?? '--'}</td>
          <td class="py-1.5 px-3">${formatTradeDateTime(t.entry_time)}</td>
          <td class="py-1.5 px-3">${t.entry ? inr(t.entry) : '--'}</td>
          <td class="py-1.5 px-3">${formatTradeDateTime(t.exit_time)}</td>
          <td class="py-1.5 px-3">${t.exit ? inr(t.exit) : '--'}</td>
          <td class="py-1.5 px-3 text-right font-bold ${pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}">${pnl >= 0 ? '+' : ''}${inr(pnl)}<div class="text-[9px] text-gray-500">${t.reason || ''}</div></td>
        </tr>`);
      });

      tbody.innerHTML = rows.length
        ? rows.join('')
        : '<tr><td colspan="9" class="text-center py-6 text-gray-500">No paper trades yet. Waiting for OI momentum signals on live Angel One WebSocket2 ticks...</td></tr>';

      const scrollBox = tbody.closest('.overflow-x-auto');
      if (scrollBox) scrollBox.scrollTop = scrollBox.scrollHeight;
    }

    async function promoteToForwardTest() {
      if (!currentModalStrat) return;

      const btn = document.getElementById('btn-promote-forward');
      const origText = btn.innerHTML;
      btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i><span>Deploying...</span>`;
      btn.disabled = true;

      const selectedSymbols = getSelectedSymbols();
      const stratCapital = currentModalStrat.default_capital;

      try {
        const res = await fetch('/api/forward-test/register', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            strategy_id: currentModalStrat.id,
            instruments: selectedSymbols,
            capital: stratCapital,
            params: currentModalStrat.default_params
          })
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Forward test promotion failed');

        const successAlert = document.getElementById('modal-forward-success-alert');
        const successMsg = document.getElementById('modal-forward-success-msg');
        successMsg.textContent = `Strategy '${currentModalStrat.name}' is now active in forward test mode on ${selectedSymbols.length} symbol(s) (${selectedSymbols.join(', ')}). Telegram alerts active.`;
        successAlert.classList.remove('hidden');

        // Refresh main platform status
        refreshLiveStatus();
      } catch (err) {
        showModalError('Forward Test Promotion Error', err.message || err);
      } finally {
        btn.innerHTML = origText;
        btn.disabled = false;
      }
    }

    function switchTab(tabId) {
      ['cards', 'live', 'terminal'].forEach(t => {
        document.getElementById(`tab-content-${t}`).classList.add('hidden');
        document.getElementById(`tab-btn-${t}`).classList.remove('border-cyan-400', 'text-cyan-400');
        document.getElementById(`tab-btn-${t}`).classList.add('border-transparent', 'text-gray-400');
      });

      document.getElementById(`tab-content-${tabId}`).classList.remove('hidden');
      document.getElementById(`tab-btn-${tabId}`).classList.add('border-cyan-400', 'text-cyan-400');
      document.getElementById(`tab-btn-${tabId}`).classList.remove('border-transparent', 'text-gray-400');

      if (tabId === 'live') {
        refreshLiveStatus();
      }
    }

    async function deployStrategy() {
      const name = document.getElementById('deploy-strat-name').value;
      const id = document.getElementById('deploy-strat-id').value;
      const inst = document.getElementById('deploy-strat-inst').value;

      try {
        const res = await fetch('/api/strategy/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ strategy_name: name, strategy_id: id, instrument: inst })
        });
        const d = await res.json();
        if (res.ok) {
          alert('Strategy instance deployed successfully!');
          refreshLiveStatus();
        } else {
          alert('Error: ' + d.detail);
        }
      } catch (e) {
        alert('Network error: ' + e);
      }
    }

    async function stopStrategy(id) {
      try {
        const res = await fetch('/api/strategy/stop/' + encodeURIComponent(id), { method: 'POST' });
        if (res.ok) {
          refreshLiveStatus();
        }
      } catch (e) {
        console.error(e);
      }
    }

    async function refreshLiveStatus() {
      const icon = document.getElementById('refresh-icon');
      icon.classList.add('fa-spin');

      try {
        const [stratRes, statusRes] = await Promise.all([
          fetch('/api/strategies'),
          fetch('/api/status')
        ]);
        const stratData = await stratRes.json();
        const statusData = await statusRes.json();

        // Render active strategy list
        const actList = document.getElementById('active-strategies-list');
        document.getElementById('active-strat-count').textContent = `${stratData.active.length} active`;

        if (stratData.active.length === 0) {
          actList.innerHTML = '<div class="p-6 text-center text-gray-500 text-xs">No active strategy running. Start one from the left form.</div>';
        } else {
          actList.innerHTML = '';
          stratData.active.forEach(s => {
            const el = document.createElement('div');
            el.className = 'p-3.5 bg-gray-950/60 rounded-xl border border-gray-800 flex items-center justify-between';
            el.innerHTML = `
              <div>
                <div class="flex items-center space-x-2">
                  <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
                  <span class="font-bold text-xs text-white font-mono">${s.id}</span>
                  <span class="text-[10px] bg-gray-800 text-cyan-300 px-2 py-0.5 rounded font-mono">${s.instrument}</span>
                </div>
                <div class="text-[11px] text-gray-400 mt-1">Signals fired: ${s.signals_count} | Status: RUNNING</div>
              </div>
              <button onclick="stopStrategy('${s.id}')" class="px-3 py-1 bg-rose-950 hover:bg-rose-900 border border-rose-800 text-rose-300 text-xs font-semibold rounded-lg transition">
                Stop
              </button>
            `;
            actList.appendChild(el);
          });
        }

        // Render Positions
        const posBody = document.getElementById('positions-tbody');
        document.getElementById('positions-count-tag').textContent = `${statusData.positions.length} positions`;
        if (statusData.positions.length === 0) {
          posBody.innerHTML = '<tr><td colspan="5" class="text-center py-6 text-gray-500">No open positions.</td></tr>';
        } else {
          posBody.innerHTML = '';
          statusData.positions.forEach(p => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
              <td class="py-2 px-3 font-semibold text-cyan-400">${p.instrument}</td>
              <td class="py-2 px-3">${p.quantity}</td>
              <td class="py-2 px-3">₹${(p.entry_price || 0).toFixed(2)}</td>
              <td class="py-2 px-3">₹${(p.current_price || p.entry_price || 0).toFixed(2)}</td>
              <td class="py-2 px-3 text-right font-bold text-emerald-400">₹${(p.unrealized_pnl || 0).toFixed(2)}</td>
            `;
            posBody.appendChild(tr);
          });
        }

        // Render Orders
        const ordBody = document.getElementById('orders-tbody');
        document.getElementById('orders-count-tag').textContent = `${statusData.orders.length} orders`;
        if (statusData.orders.length === 0) {
          ordBody.innerHTML = '<tr><td colspan="5" class="text-center py-6 text-gray-500">No recent orders.</td></tr>';
        } else {
          ordBody.innerHTML = '';
          statusData.orders.forEach(o => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
              <td class="py-2 px-3 font-mono text-cyan-400">${o.order_id}</td>
              <td class="py-2 px-3 font-bold ${o.side === 'BUY' ? 'text-emerald-400' : 'text-rose-400'}">${o.side}</td>
              <td class="py-2 px-3">${o.instrument}</td>
              <td class="py-2 px-3">${o.quantity}</td>
              <td class="py-2 px-3 text-right text-gray-400">${o.status}</td>
            `;
            ordBody.appendChild(tr);
          });
        }

      } catch (err) {
        console.error('Refresh status failed:', err);
      } finally {
        setTimeout(() => icon.classList.remove('fa-spin'), 400);
      }
    }
  </script>

</body>
</html>
"""

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 9090))
    uvicorn.run(app, host="0.0.0.0", port=port)
