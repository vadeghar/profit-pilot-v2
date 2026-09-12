import json
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from profit_pilot.backtest.run import BacktestRunConfig
from profit_pilot.backtest.runner import BacktestRunner

load_dotenv()
SYMBOLS = ["AXISBANK", "BAJFINANCE", "ICICIBANK", "ITC", "LT", "RELIANCE", "SBIN"]
START, END = "2025-08-01", "2026-09-11"
STEPS = [
    ("permissive_baseline", {}),
    ("breakout_rvol_0_8", {"require_breakout_rvol": True}),
    ("sv_context", {"require_breakout_rvol": True, "require_sv_context": True}),
    ("moderate_absorption", {"require_breakout_rvol": True, "require_sv_context": True, "require_moderate_absorption": True}),
    ("breakout_close_location_0_60", {"require_breakout_rvol": True, "require_sv_context": True, "require_moderate_absorption": True, "require_breakout_close_location": True}),
]

results = []
for name, params in STEPS:
    for symbol in SYMBOLS:
        config = BacktestRunConfig(symbol=symbol, start=date.fromisoformat(START), end=date.fromisoformat(END), initial_cash=100000, strategy_params={"strategy_id": "VPA_SWING_EQUITY_LONG_V2", **params})
        result = BacktestRunner(config).run()
        results.append({"step": name, "symbol": symbol, "params": params, "trades": result.n_trades, "fills": len(result.fills), "return_pct": result.return_pct, "max_drawdown": result.max_drawdown, "win_rate": result.win_rate, "pnl": sum(t.pnl for t in result.trades)})

out = Path("strategy_memory/results/VPA_SWING_EQUITY_LONG_V2")
out.mkdir(parents=True, exist_ok=True)
(out / "ablation_2026-09-12.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
print(json.dumps(results, indent=2))
