"""Index Options OI Momentum: OI-velocity + price-momentum intraday option buying on NIFTY / BANKNIFTY / SENSEX.

Deprecated by the owner until further notice (platform_config/strategy_flags.yaml): hidden on the
dashboard by default and never started automatically.

    strategy.py       the rules (IndexOIMomentumStrategy)
    backtest.py       synthetic-tape backtest used by the dashboard
    paper_trader.py   live paper session on the Angel WebSocket feed (market_data/oi_ws_feed.py)
"""
from trading_strategies.index_oi_momentum.strategy import IndexOIMomentumStrategy

__all__ = ["IndexOIMomentumStrategy"]
