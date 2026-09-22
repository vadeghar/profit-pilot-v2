import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from market_data.angel_data_provider import AngelHistoricalDataProvider
from backtest import BacktestEngine, BacktestConfig
from config import get_strategy_instruments

dp = AngelHistoricalDataProvider()
universe = get_strategy_instruments("equity_swing_vcp")  # top equities from universe.yaml

cfg = BacktestConfig(
    strategy_id='equity_swing_vcp',
    strategy_name='equity_swing_vcp',
    strategy_params={
        'capital': 1000000.0,
        'volume_breakout_mult': 1.3,
        'stop_pct': 0.07,
        'risk_pct': 0.0125,
        'partial_r': 2.0,
        'trailing_ma': 'EMA21'
    },
    instruments=universe,
    start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
    end_date=datetime(2026, 9, 16, tzinfo=timezone.utc),
    initial_capital=1000000.0,
    timeframe='1d'
)

engine = BacktestEngine(cfg, data_provider=dp)
res = engine.run()
print(f"Total Trades: {res.total_trades}")
print(f"Win Rate: {res.win_rate:.1f}%")
print(f"Winning Trades: {res.winning_trades}, Losing Trades: {res.losing_trades}")
print(f"Total Return: ₹{res.total_return:,.2f} ({res.total_return_pct:.2f}%)")
print(f"Max Drawdown: {res.max_drawdown:.2f}%")
print(f"Sharpe Ratio: {res.sharpe_ratio:.2f}")
print(f"Profit Factor: {res.profit_factor:.2f}")
print("\nIndividual Trades:")
for t in engine._trades:
    print(f"  {t.trade_id} | {t.instrument:15} | Entry: {t.entry_time.strftime('%Y-%m-%d')} (₹{t.entry_price:.2f}) -> Exit: {t.exit_time.strftime('%Y-%m-%d')} (₹{t.exit_price:.2f}) | Qty: {t.quantity} | PnL: ₹{t.realized_pnl:+,.2f}")
