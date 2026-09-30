"""NIFTY option scalping strategies driven by tick data (see docs/scalping/)."""
from strategies.scalping.engine import ScalpConfig, ScalpEngine, ScalpTrade
from strategies.scalping.oi_burst import OiVolumeBurst
from strategies.scalping.orderflow import PcrVelocity, StealthAccumulation, TrapFade, WriterSqueeze

SCALP_STRATEGIES: dict[str, type[ScalpEngine]] = {
    cls.strategy_id: cls for cls in (WriterSqueeze, StealthAccumulation, PcrVelocity, TrapFade, OiVolumeBurst)
}

__all__ = ["SCALP_STRATEGIES", "ScalpConfig", "ScalpEngine", "ScalpTrade", "OiVolumeBurst", "PcrVelocity",
           "StealthAccumulation", "TrapFade", "WriterSqueeze"]
