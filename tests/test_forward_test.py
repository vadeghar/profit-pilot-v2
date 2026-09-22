import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import platform_config


def test_forward_test_registration():
    forward_dir = str(platform_config.FORWARD_TEST_DIR)
    os.makedirs(forward_dir, exist_ok=True)
    
    config = {
        "strategy_id": "equity_swing_vcp",
        "strategy_name": "Equity Swing VCP",
        "instruments": ["NSE:RELIANCE", "NSE:ICICIBANK"],
        "capital": 1000000.0,
        "mode": "PAPER_FORWARD_TEST",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "status": "ACTIVE",
        "paper_positions": [],
        "paper_trades": [],
        "params": {
            "capital": 1000000.0,
            "risk_pct": 0.0125,
            "stop_pct": 0.07,
            "volume_breakout_mult": 1.3
        }
    }
    
    file_path = os.path.join(forward_dir, "equity_swing_vcp.json")
    with open(file_path, "w") as f:
        json.dump(config, f, indent=2)
    print("Forward test config saved successfully:", file_path)

if __name__ == "__main__":
    test_forward_test_registration()
