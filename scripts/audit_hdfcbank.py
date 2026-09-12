from datetime import date

from dotenv import load_dotenv

from profit_pilot.data.breeze_client import BreezeMarketDataProvider

load_dotenv()
rows = sorted(
    BreezeMarketDataProvider("HDFCBANK", "1day").candles(
        date(2025, 8, 1), date(2026, 9, 11)
    ),
    key=lambda row: row["datetime"],
)
print(f"rows={len(rows)} first={rows[0]['datetime']} last={rows[-1]['datetime']}")
gaps = []
for previous, current in zip(rows, rows[1:]):
    gaps.append(
        {
            "datetime": current["datetime"],
            "previous_close": previous["close"],
            "current_open": current["open"],
            "current_close": current["close"],
            "volume": current.get("volume"),
            "overnight_gap_pct": current["open"] / previous["close"] - 1,
        }
    )
print("largest absolute overnight gaps")
for gap in sorted(gaps, key=lambda item: abs(item["overnight_gap_pct"]), reverse=True)[:10]:
    print(gap)
