"""NIFTY option scalpers driven by tick data (rules and operation: docs/scalping/).

    engine.py            ScalpEngine: tick aggregation, fills, risk limits, exits - shared by every scalper
    orderflow.py         S1 Writer Squeeze, S2 Stealth Accumulation, S3 Delta-PCR Velocity, S4 Trap Fade
    oi_burst.py          OI + Volume Burst
    expiry_breakout.py   Expiry Trend Breakout (expiry day only)
    expiry_gamma.py      Expiry Gamma Squeeze (expiry day only)
    backtest.py          replay of recorded ticks through the same engines
    paper_trader.py      live paper sessions fed by the tick recorder
    tools/               daily condition report, Telegram summary, Breeze 1-second import
    research/            expiry-day data download and rule studies

SCALP_STRATEGIES maps each strategy id to its class; the dashboard, backtest and paper trader all read it.
"""
from scalp_strategies.engine import ScalpConfig, ScalpEngine, ScalpTrade
from scalp_strategies.expiry_breakout import ExpiryTrendBreakout
from scalp_strategies.expiry_gamma import ExpiryGammaSqueeze
from scalp_strategies.oi_burst import OiVolumeBurst
from scalp_strategies.orderflow import PcrVelocity, StealthAccumulation, TrapFade, WriterSqueeze

SCALP_STRATEGIES: dict[str, type[ScalpEngine]] = {
    cls.strategy_id: cls for cls in (WriterSqueeze, StealthAccumulation, PcrVelocity, TrapFade, OiVolumeBurst,
                                  ExpiryTrendBreakout, ExpiryGammaSqueeze)
}

__all__ = ["SCALP_STRATEGIES", "ScalpConfig", "ScalpEngine", "ScalpTrade", "ExpiryGammaSqueeze", "ExpiryTrendBreakout", "OiVolumeBurst", "PcrVelocity",
           "StealthAccumulation", "TrapFade", "WriterSqueeze"]
