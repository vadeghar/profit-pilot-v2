"""Compare the MCX proxy series with real MCX futures bars from Angel One.

Angel only serves currently listed contracts, so the overlap is the live
contract's own history (a few months). Writes data/strategy_audit/mcx_proxy_validation.json.

    python -m tools.strategy_audit.validate_mcx
"""
from __future__ import annotations

import json
import time
from datetime import date, timedelta

import numpy as np

from tools.strategy_audit import data as D
from tools.strategy_audit.common import AUDIT_DATA_DIR


def main() -> None:
    angel = D._angel()
    out = {}
    end = date.today() - timedelta(days=1)
    for name in D.MCX_PROXY:
        info = angel.mcx_token_map[name]
        res = {}
        for attempt in range(4):
            time.sleep(2 + 3 * attempt)  # SmartAPI rejects bursts ("exceeding access rate")
            try:
                res = angel.broker.client.getCandleData({
                    "exchange": "MCX", "symboltoken": info["token"], "interval": "ONE_DAY",
                    "fromdate": f"{end - timedelta(days=240):%Y-%m-%d} 09:00", "todate": f"{end:%Y-%m-%d} 23:30"})
                break
            except Exception as e:
                print(f"{name}: retry after {e}", flush=True)
        real = {row[0][:10]: float(row[4]) for row in (res.get("data") or []) if float(row[4]) > 0}
        proxy = {c.timestamp.date().isoformat(): c.close for c in D.mcx_proxy(name, end - timedelta(days=260), end)}
        days = sorted(set(real) & set(proxy))
        if len(days) < 20:
            out[name] = {"contract": info["symbol"], "overlap_days": len(days), "note": "insufficient overlap"}
            continue
        r = np.array([real[d] for d in days])
        p = np.array([proxy[d] for d in days])
        rr, pr = np.diff(np.log(r)), np.diff(np.log(p))
        ratio = r / p
        out[name] = {
            "contract": info["symbol"], "overlap_days": len(days), "from": days[0], "to": days[-1],
            "daily_return_correlation": round(float(np.corrcoef(rr, pr)[0, 1]), 4),
            "price_ratio_mean": round(float(ratio.mean()), 4), "price_ratio_std": round(float(ratio.std()), 4),
            "real_period_return": round(float(r[-1] / r[0] - 1), 4),
            "proxy_period_return": round(float(p[-1] / p[0] - 1), 4),
        }
        print(name, out[name], flush=True)
    path = AUDIT_DATA_DIR / "mcx_proxy_validation.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("wrote", path)


if __name__ == "__main__":
    main()
