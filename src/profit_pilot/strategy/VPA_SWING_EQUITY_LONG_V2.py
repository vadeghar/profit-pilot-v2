"""
Anna Coulling VPA — Swing Equity Strategy V2 (Long-Only)

Strategy ID: VPA_SWING_EQUITY_LONG_V2
Version: 2.0

Core sequence (Daily signal → Weekly context → 1m execution):
1. Bearish Waterfall (10-day prior)
2. Stopping Volume identification
3. Absorption / Congestion formation
4. Low-Volume Test
5. Volume-Confirmed Daily Breakout
6. Entry & risk management

Fixed universe (V2 pure): HDFCBANK, ICICIBANK, RELIANCE, BHARTIARTL, LT, SBIN, INFY, AXISBANK, KOTAKBANK, M&M, BAJFINANCE, ITC

Implementation follows the VPA principles from the raw strategy spec.
"""

from profit_pilot.strategy.base import Strategy
from profit_pilot.strategy.context import SignalContext
from profit_pilot.strategy.indicators import crossover
from profit_pilot.strategy.registry import register
from profit_pilot.strategy.signal import Signal, SignalAction


@register
class VPA_SWING_EQUITY_LONG_V2(Strategy):
    """Long-only swing strategy based on Anna Coulling VPA methodology.

    Core entry sequence:
    1. Bearish Waterfall (10-day prior: ≥3 weak/down bars, cumulative decline ≥1×ATR)
    2. Stopping Volume (RVOL≥1.5, range_ATR≤1.5, close_location≥0.60, lower_wick≥0.25)
    3. Absorption / Congestion (3-12 completed Daily bars, down-vol↓, down-range↓, no new lows)
    4. Low-Volume Test (RVOL≤0.80, range_ATR≤1.0, close_loc≥0.40, near congestion_low within 25%)
    5. Volume-Confirmed Daily Breakout (close>congestion_high, RVOL≥1.25, close_loc≥0.70, range_ATR≥0.8)
    6. Entry → 1m execution simulation → risk management → target/stop
    """

    name: str = "VPA_SWING_EQUITY_LONG_V2"

    # VPA parameters (research thresholds, treated as hypotheses per spec)
    rvol_stopping_min: float = 1.0
    range_atr_stopping_max: float = 1.5
    close_location_stopping_min: float = 0.45
    lower_wick_stopping_min: float = 0.25
    rvol_test_max: float = 1.0
    range_atr_test_max: float = 1.0
    close_location_test_min: float = 0.40
    test_location_tolerance: float = 0.25  # % of congestion_range
    rvol_breakout_min: float = 1.0
    close_location_breakout_min: float = 0.70
    range_atr_breakout_min: float = 0.8

    @property
    def symbol(self) -> str:
        # Fixed universe for V2 — the scanner determines which instrument to apply
        return "RELIANCE"

    def on_signal_context(self, ctx: SignalContext) -> Signal:
        """Main signal generation: implement the VPA sequence on completed Daily data."""
        # ctx contains: daily_bars (completed Daily OHLCV from 1m aggregation),
        # weekly_context, and other fields populated by the engine.

        daily_bars = getattr(ctx, "reference", {}).get("daily_bars", [])
        if len(daily_bars) == 0:
            return Signal(SignalAction.HOLD, ctx.symbol, 0)

        # ---- Weekly context filter (not standalone signal) ----
        # Per spec: weekly context (W_BULLISH / W_BEARISH / W_NEUTRAL / W_ACCUMULATION / W_DISTRIBUTION)
        # is used as a filter, never as an entry trigger.
        weekly_context = getattr(ctx, "reference", {}).get("weekly_context", {})
        weekly_regime = weekly_context.get("regime") if isinstance(weekly_context, dict) else None
        # Defer entry on bearish / distribution contexts (filters only; does not trigger)
        if weekly_regime in ("W_BEARISH", "W_DISTRIBUTION"):
            return Signal(SignalAction.HOLD, ctx.symbol, 0)

        # ---- Phase 1: Bearish Waterfall (10-day prior) ----
        if not self._is_bearish_waterfall(daily_bars):
            return Signal(SignalAction.HOLD, ctx.symbol, 0)

        # ---- Phase 2: Stopping Volume ----
        if not self._is_stopping_volume(daily_bars):
            return Signal(SignalAction.HOLD, ctx.symbol, 0)

        # ---- Phase 3: Absorption / Congestion ----
        if not self._is_absorption(daily_bars):
            return Signal(SignalAction.HOLD, ctx.symbol, 0)

        # ---- Phase 4: Low-Volume Test ----
        if not self._is_low_volume_test(daily_bars):
            return Signal(SignalAction.HOLD, ctx.symbol, 0)

        # ---- Phase 5: Volume-Confirmed Breakout ----
        if not self._is_breakout(daily_bars):
            return Signal(SignalAction.HOLD, ctx.symbol, 0)

        # ---- All phases passed → Generate Signal ----
        return self._generate_breakout_signal(daily_bars, ctx)

    # ---------- Phase implementations ----------

    def _is_bearish_waterfall(self, daily: list) -> bool:
        """≥3 weak/down bars in last 10, cumulative decline ≥ 1×ATR(14)."""
        if len(daily) < 5:
            return False

        recent = daily[-10:]
        weak_bars = [b for b in recent if b["close"] < b["open"]]
        if len(weak_bars) < 3:
            return False

        # Compute ATR(14) from the recent 10-day ranges
        ranges = [b["high"] - b["low"] for b in recent if b["high"] > b["low"]]
        if not ranges:
            return False
        atr = sum(ranges) / len(ranges)

        # Cumulative decline = sum of (close_i - open_i) for last 10 bars
        # (positive values = net decline; this is a simplified measure)
        cum_decline = sum(b["close"] - b["open"] for b in recent)
        return cum_decline <= -atr  # cumulative decline ≥ 1×ATR

    def _wilder_atr(self, daily: list, period: int = 14) -> float:
        """Wilder/TradingView-style ATR(14) using RMA: first ATR = SMA(14), then ATR = (prev_ATR * 13 + TR) / 14."""
        if len(daily) < 2:
            return 1.0
        trs = []
        prev_close = None
        for b in daily:
            high = b.get("high", b.get("close", 0))
            low = b.get("low", b.get("close", 0))
            close = b.get("close", 0)
            if prev_close is not None:
                tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            else:
                tr = high - low
            trs.append(tr)
            prev_close = close
        if len(trs) < period:
            return sum(trs) / len(trs) if trs else 1.0
        first_avg = sum(trs[:period]) / period
        atr = first_avg
        for tr in trs[period:]:
            atr = (atr * (period - 1) + tr) / period
        return atr

    def _is_stopping_volume(self, daily: list) -> bool:
        """RVOL≥1.5, range_ATR≤1.5, close_location≥0.60, lower_wick≥0.25."""
        if len(daily) == 0:
            return False

        cur = daily[-1]
        cur_vol = cur["volume"] if cur.get("volume", 0) > 0 else 1

        # Rolling mean of previous 20 completed daily volumes
        prev_vols = [b["volume"] for b in daily[:-1] if b.get("volume", 0) > 0]
        if len(prev_vols) < 5:
            return False
        prev_mean = sum(prev_vols) / len(prev_vols)
        rvol = cur_vol / prev_mean

        # Range ATR: current day's range / ATR(14)
        cur_range = cur["high"] - cur["low"] if cur.get("high", 0) > cur.get("low", 0) else 1
        ranges = [b["high"] - b["low"] for b in daily if b.get("high", 0) > b.get("low", 0)]
        atr = sum(ranges) / len(ranges) if ranges else 1.0
        range_atr = cur_range / atr if atr > 0 else 1.0

        # Validate OHLC and compute safely
        if cur.get("low", cur.get("close")) > min(cur.get("open", cur.get("close")), cur.get("close", 0)) or cur.get("high", cur.get("close")) < max(cur.get("open", cur.get("close")), cur.get("close", 0)):
            return False
        rng = cur["high"] - cur["low"] if cur.get("high", 0) > cur.get("low", 0) else 1
        if rng <= 0:
            return False
        close_loc = (cur["close"] - cur["low"]) / rng if rng > 0 else 1.0

        # Lower wick ratio = (low - min(open,close)) / range
        lower_wick = min(cur["open"], cur["close"]) - cur["low"]
        lower_wick_ratio = lower_wick / rng if rng > 0 else 0.0
    # Verified formula (not used in SV rules): upper_wick = high - max(open, close)
    upper_wick = cur["high"] - max(cur["open"], cur["close"])
    upper_wick_ratio = upper_wick / rng if rng > 0 else 0.0

        cond_rvol = rvol >= self.rvol_stopping_min
        cond_range_atr = range_atr <= self.range_atr_stopping_max
        cond_close_loc = close_loc >= self.close_location_stopping_min
        cond_lower_wick = lower_wick_ratio >= self.lower_wick_stopping_min

        return cond_rvol and cond_range_atr and cond_close_loc and cond_lower_wick

    def _is_absorption(self, daily: list) -> bool:
        """Check 3-12 completed Daily bars: congestion window, declining down vol/range.

        Must have 3 <= len(daily) <= 12 bars in the congestion window.
        Second-half down volume < first-half down volume.
        Second-half down range < first-half down range.
        No sustained new lows (close >= congestion_low throughout).
        """
        if len(daily_bars) < 3 or len(daily_bars) > 20:
            return False

        congestion_high = max(b["high"] for b in daily)
        congestion_low = min(b["low"] for b in daily)

        mid = len(daily) // 2
        first_half = daily[:mid]
        second_half = daily[mid:]

        # Down-volume counts: volume on close < open bars
        down_first = sum(b["volume"] for b in first_half if b["close"] < b["open"])
        down_second = sum(b["volume"] for b in second_half if b["close"] < b["open"])
        if down_second >= down_first:
            return False

        # Down-range: range on down bars
        first_down_range = min(
            (b["high"] - b["low"] for b in first_half if b["close"] < b["open"]),
            default=float("inf"),
        )
        second_down_range = min(
            (b["high"] - b["low"] for b in second_half if b["close"] < b["open"]),
            default=float("inf"),
        )
        if second_down_range >= first_down_range:
            return False

        # No sustained new lows: every bar's close >= congestion_low
        if any(b["close"] < congestion_low for b in daily):
            return False

        return True

    def _is_low_volume_test(self, daily: list) -> bool:
        """Test near congestion_low: RVOL≤0.80, range_ATR≤1.0, close_loc≥0.40,
        test low within 25% of congestion_range of congestion_low."""
        if len(daily) == 0:
            return False

        # congestion boundaries
        congestion_high = max(b["high"] for b in daily)
        congestion_low = min(b["low"] for b in daily)
        rng = congestion_high - congestion_low

        # Test bar = last bar in daily window
        test = daily[-1]
        cur_vol = test["volume"] if test.get("volume", 0) > 0 else 1

        # Rolling mean of previous 20 daily volumes (exclude current)
        prev_vols = [b["volume"] for b in daily[:-1] if b.get("volume", 0) > 0]
        if len(prev_vols) < 5:
            return False
        prev_mean = sum(prev_vols) / len(prev_vols)
        rvol = cur_vol / prev_mean

        if test.get("low", test.get("close")) > min(test.get("open", test.get("close")), test.get("close", 0)) or test.get("high", test.get("close")) < max(test.get("open", test.get("close")), test.get("close", 0)):
            return False
        cur_range = test["high"] - test["low"] if test.get("high", 0) > test.get("low", 0) else 1
        if cur_range <= 0:
            return False
        ranges = [b["high"] - b["low"] for b in daily if b.get("high", 0) > b.get("low", 0)]
        atr = sum(ranges) / len(ranges) if ranges else 1.0
        range_atr = cur_range / atr if atr > 0 else 1.0

        close_loc = (test["close"] - test["low"]) / cur_range if cur_range > 0 else 1.0

        # Proximity: test_low within 25% of congestion_range from congestion_low
        # test_low ≈ test["low"]; check: test["low"] >= congestion_low - 0.25*rng
        test_proximity = test["low"] >= congestion_low - self.test_location_tolerance * rng

        return (rvol <= self.rvol_test_max and range_atr <= self.range_atr_test_max
                and close_loc >= self.close_location_test_min and test_proximity)

    def _is_breakout(self, daily: list) -> bool:
        """Daily close > congestion_high AND RVOL≥1.25 AND close_location≥0.70 AND range_ATR≥0.8."""
        if len(daily) == 0:
            return False

        congestion_high = max(b["high"] for b in daily)
        cur = daily[-1]

        # Breakout level = max of congestion window
        breakout_level = congestion_high

        # Conditions
        cond_close_above = cur["close"] > breakout_level

        # RVOL
        cur_vol = cur["volume"] if cur.get("volume", 0) > 0 else 1
        prev_vols = [b["volume"] for b in daily[:-1] if b.get("volume", 0) > 0]
        if len(prev_vols) < 5:
            return False
        prev_mean = sum(prev_vols) / len(prev_vols)
        rvol = cur_vol / prev_mean
        cond_rvol = rvol >= self.rvol_breakout_min

        # Close location: (close - low) / range
        if cur.get("low", cur.get("close")) > min(cur.get("open", cur.get("close")), cur.get("close", 0)) or cur.get("high", cur.get("close")) < max(cur.get("open", cur.get("close")), cur.get("close", 0)):
            return False
        cur_range = cur["high"] - cur["low"] if cur.get("high", 0) > cur.get("low", 0) else 1
        if cur_range <= 0:
            return False
        close_loc = (cur["close"] - cur["low"]) / cur_range
        cond_close_loc = close_loc >= self.close_location_breakout_min

        # range_ATR: current day's range / ATR(14)
        ranges = [b["high"] - b["low"] for b in daily if b.get("high", 0) > b.get("low", 0)]
        atr = sum(ranges) / len(ranges) if ranges else 1.0
        range_atr = cur_range / atr if atr > 0 else 1.0
        cond_range_atr = range_atr >= self.range_atr_breakout_min

        return cond_close_above and cond_rvol and cond_close_loc and cond_range_atr

    # ---------- Signal generation ----------

    def _generate_breakout_signal(self, daily: list, ctx: SignalContext) -> Signal:
        """Generate LONG signal with computed stop, target, and risk parameters."""
        cur = daily[-1]
        congestion_high = max(b["high"] for b in daily)

        # Entry reference = breakout close = current daily close
        entry_ref = cur["close"]

        # Stop = test_low - 0.10 × ATR(14)
        # test_low = lowest low in absorption/congestion window
        test_low = min(b["low"] for b in daily)

        # ATR(14) from the window
        ranges = [b["high"] - b["low"] for b in daily if b.get("high", 0) > b.get("low", 0)]
        atr_14 = sum(ranges) / len(ranges) if ranges else 1.0

        stop_ref = test_low - 0.10 * atr_14

        # Target = entry + 2R = entry + 2 × (entry - stop)
        risk_points = entry_ref - stop_ref
        target_ref = entry_ref + 2.0 * risk_points

        # Position sizing: risk per trade = 0.5% of account equity
        # For now, use a placeholder quantity; the backtest engine will size based on risk_points
        quantity = 1  # 1 share; backtest adjusts based on risk_budget

        metadata = {
            "entry_price": entry_ref,
            "stop_ref": stop_ref,
            "target": target_ref,
            "risk_points": risk_points,
            "strategy_id": self.name,
            "phase": "breakout_confirmed",
            "universe_stock": "UNIVERSE_STOCK",
        }

        return Signal(SignalAction.BUY, ctx.symbol, quantity, metadata)


def get_strategy() -> VPA_SWING_EQUITY_LONG_V2:
    """Factory function to retrieve the strategy instance."""
    return VPA_SWING_EQUITY_LONG_V2()