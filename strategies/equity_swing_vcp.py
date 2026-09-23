from platform_config import get_indices

_BENCHMARK_SYMBOL = get_indices()[0]["symbol"].upper()
"""
Equity Swing Strategy — Mark Minervini Volatility Contraction Pattern (VCP) + 8-Point Trend Template
Asset Class: NSE Equities | Holding Period: 1-3 months | Direction: Long-only
Reference Specification: uploads/Equity_Swing_VCP_Strategy.md
"""

from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone
import math

from core.models import (
    Signal, OrderSide, OrderType, Candle, Tick
)
from strategies import StrategyBase, StrategyRegistry
from utils import Logger


class EquitySwingVCPStrategy(StrategyBase):
    """
    Minervini VCP + Trend Template Strategy

    1. 8-Point Trend Template Screen:
       - Close > SMA150 and Close > SMA200
       - SMA150 > SMA200
       - SMA200 trending up (SMA200[now] > SMA200[now - 20 days])
       - SMA50 > SMA150 > SMA200
       - Close > SMA50
       - Close >= 1.25 * 52-week Low (at least 25% above 52-week low)
       - Close >= 0.75 * 52-week High (within 25% of 52-week high)
       - Relative Strength (RS) Rating >= threshold (default >= 70, calculated vs universe/index)

    2. VCP Pattern Detection:
       - Base length: >= 20 bars (4-10 weeks)
       - Volatility contraction: >= 2 contractions (pullbacks), each pullback smaller than prior
       - ATR(10) contraction: ATR10 in tightest section <= 50% of ATR10 at base start
       - Volume dry-up: Average volume in final contraction < 50-day average volume

    3. Volume-Confirmed Breakout Entry:
       - Pivot: High of final tight contraction
       - Trigger: Close > Pivot AND Volume >= volume_multiple * SMA50(Volume) (default 1.4x - 1.5x)
       - Market regime: Benchmark (Nifty) > its own SMA50 (and SMA200 if strict)

    4. Staged Risk Management & Trailing SL:
       - Initial stop-loss: 6% - 8% below entry (placed below pivot/contraction low, capped at stop_pct)
       - Position size: (Capital * risk_pct) / stop_distance_rupees
       - Breakeven step: Move stop to Entry once price reaches Entry + 1.0 * R
       - Partial profit-take: At +2.0R to +3.0R, take 33% profit
       - Trailing exit: Close below 21-day EMA (or 50-day SMA) closes remaining position
    """

    def __init__(self, strategy_id: str, name: str = "equity_swing_vcp", params: Dict[str, Any] = None):
        super().__init__(strategy_id, name, params)
        self.logger = Logger(f"strategy.{strategy_id}")
        self._init_parameters()

    def _init_parameters(self) -> None:
        p = self.params or {}
        # Core risk parameters
        self.capital = float(p.get('capital', 1000000.0))       # ₹10 Lakh default
        self.risk_pct = float(p.get('risk_pct', 0.0125))        # 1.25% equity risk per trade
        self.stop_pct = float(p.get('stop_pct', 0.07))          # 7% hard stop loss limit
        self.partial_r = float(p.get('partial_r', 2.0))         # Take 1/3 profit at +2.0R
        self.trailing_ma = p.get('trailing_ma', 'EMA21')        # 'EMA21' or 'SMA50'
        
        # Trend Template parameters
        self.rs_threshold = float(p.get('rs_threshold', 70.0))  # RS >= 70
        self.require_market_filter = p.get('require_market_filter', True)
        self.volume_breakout_mult = float(p.get('volume_breakout_mult', 1.4))  # >= 1.4x 50-day avg volume
        self.min_contractions = int(p.get('min_contractions', 2))              # >= 2 contractions

        # Per-symbol state tracking
        self._state: Dict[str, Dict[str, Any]] = {}
        # Shared index tracking (e.g. NIFTY)
        self._index_candles: List[Candle] = []

    def _get_state(self, instrument: str) -> Dict[str, Any]:
        if instrument not in self._state:
            self._state[instrument] = {
                'candles': [],
                'dates': [],
                'opens': [],
                'highs': [],
                'lows': [],
                'closes': [],
                'volumes': [],
                'position': 0,             # 0=Flat, 1=Long
                'entry_price': 0.0,
                'entry_date': None,
                'shares': 0,
                'initial_shares': 0,
                'stop_price': 0.0,
                'initial_risk_per_share': 0.0,
                'partial_taken': False,
                'moved_to_breakeven': False,
                'trades_history': []
            }
        return self._state[instrument]

    def register_index_candle(self, candle: Candle) -> None:
        """Register benchmark candle (Nifty) for market regime filter"""
        self._index_candles.append(candle)

    def on_tick(self, tick: Tick) -> Optional[Signal]:
        return None

    def on_candle(self, candle: Candle) -> Optional[Signal]:
        instrument = candle.instrument

        # If this is the benchmark index, store and do not trade
        if instrument.upper() in {_BENCHMARK_SYMBOL, _BENCHMARK_SYMBOL.split(":", 1)[-1]}:
            self.register_index_candle(candle)
            return None

        state = self._get_state(instrument)
        state['candles'].append(candle)
        state['dates'].append(candle.timestamp)
        state['opens'].append(candle.open)
        state['highs'].append(candle.high)
        state['lows'].append(candle.low)
        state['closes'].append(candle.close)
        state['volumes'].append(candle.volume)

        n = len(state['closes'])
        # Need at least 200 daily bars for 200 SMA + Trend Template evaluation
        if n < 200:
            return None

        closes = state['closes']
        highs = state['highs']
        lows = state['lows']
        volumes = state['volumes']
        current_close = candle.close

        # Calculate Technical Indicators
        sma50 = self._sma(closes, 50)
        sma150 = self._sma(closes, 150)
        sma200 = self._sma(closes, 200)
        ema21 = self._ema(closes, 21)
        vol_sma50 = self._sma(volumes, 50)

        # -------------------------------------------------------------
        # 1. POSITION MANAGEMENT (If already in a trade)
        # -------------------------------------------------------------
        if state['position'] == 1:
            shares = state['shares']
            entry_p = state['entry_price']
            r = state['initial_risk_per_share']
            current_stop = state['stop_price']

            # Check Hard Stop Loss hit (Intraday Low <= Stop)
            if candle.low <= current_stop:
                exit_price = current_stop
                pnl = (exit_price - entry_p) * shares
                self.logger.info(
                    f"[VCP STOP LOSS] {instrument} at ₹{exit_price:.2f} | PnL: ₹{pnl:.2f}"
                )
                state['trades_history'].append({
                    'instrument': instrument,
                    'side': 'BUY',
                    'shares': shares,
                    'entry_date': state['entry_date'],
                    'entry_price': entry_p,
                    'exit_date': candle.timestamp,
                    'exit_price': exit_price,
                    'pnl': pnl,
                    'reason': 'STOP_LOSS'
                })
                # Reset state
                state['position'] = 0
                state['shares'] = 0
                return Signal(
                    strategy_id=self.strategy_id,
                    instrument=instrument,
                    action=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    quantity=shares,
                    price=exit_price,
                    stop_loss=0.0
                )

            # Breakeven Adjustment: If price reaches +1.0R, move stop to Entry
            if not state['moved_to_breakeven'] and current_close >= (entry_p + 1.0 * r):
                state['stop_price'] = max(state['stop_price'], entry_p)
                state['moved_to_breakeven'] = True
                self.logger.info(f"[VCP BREAKEVEN] {instrument} stop raised to entry ₹{entry_p:.2f}")

            # Partial Profit-Take: If price reaches +2.0R to +3.0R (default 2R), exit 33%
            if not state['partial_taken'] and current_close >= (entry_p + self.partial_r * r):
                shares_to_sell = max(1, math.floor(state['initial_shares'] * 0.33))
                if shares_to_sell < shares:
                    partial_pnl = (current_close - entry_p) * shares_to_sell
                    self.logger.info(
                        f"[VCP PARTIAL PROFIT] {instrument} sold {shares_to_sell} shs @ ₹{current_close:.2f} (+{self.partial_r}R) | PnL: ₹{partial_pnl:.2f}"
                    )
                    state['trades_history'].append({
                        'instrument': instrument,
                        'side': 'BUY',
                        'shares': shares_to_sell,
                        'entry_date': state['entry_date'],
                        'entry_price': entry_p,
                        'exit_date': candle.timestamp,
                        'exit_price': current_close,
                        'pnl': partial_pnl,
                        'reason': 'PARTIAL_PROFIT_TAKE'
                    })
                    state['shares'] -= shares_to_sell
                    state['partial_taken'] = True
                    # Do not return full SELL signal here; keep tracking remainder

            # Trailing Exit on Remainder: Daily Close below 21-day EMA (or 50 SMA)
            trailing_benchmark = ema21 if self.trailing_ma == 'EMA21' else sma50
            if current_close < trailing_benchmark and current_close > entry_p:
                rem_shares = state['shares']
                pnl = (current_close - entry_p) * rem_shares
                self.logger.info(
                    f"[VCP TRAILING EXIT] {instrument} closed below {self.trailing_ma} ({trailing_benchmark:.2f}) @ ₹{current_close:.2f} | PnL: ₹{pnl:.2f}"
                )
                state['trades_history'].append({
                    'instrument': instrument,
                    'side': 'BUY',
                    'shares': rem_shares,
                    'entry_date': state['entry_date'],
                    'entry_price': entry_p,
                    'exit_date': candle.timestamp,
                    'exit_price': current_close,
                    'pnl': pnl,
                    'reason': f'TRAILING_{self.trailing_ma}_CROSS'
                })
                state['position'] = 0
                state['shares'] = 0
                return Signal(
                    strategy_id=self.strategy_id,
                    instrument=instrument,
                    action=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    quantity=rem_shares,
                    price=current_close,
                    stop_loss=0.0
                )

            return None

        # -------------------------------------------------------------
        # 2. ENTRY EVALUATION (Trend Template + VCP + Breakout)
        # -------------------------------------------------------------
        # Condition A: 8-Point Trend Template
        if not self._eval_trend_template(state, sma50, sma150, sma200):
            return None

        # Condition B: Market Regime Filter (Nifty 50 > 50-day SMA)
        if self.require_market_filter and not self._eval_market_regime():
            return None

        # Condition C: VCP Volatility Contraction & Volume Dry-Up
        vcp_valid, pivot_price, contraction_low = self._detect_vcp(state)
        if not vcp_valid or pivot_price <= 0:
            return None

        # Condition D: Breakout Day Volume Confirmation
        # Close > Pivot AND Volume >= volume_breakout_mult * 50-day avg volume
        is_breakout = (current_close > pivot_price) and (state['closes'][-2] <= pivot_price)
        vol_surge = candle.volume >= (self.volume_breakout_mult * vol_sma50)
        # Strong close (in upper 35% of day's range)
        day_range = candle.high - candle.low
        strong_close = (day_range > 0) and ((candle.close - candle.low) / day_range >= 0.60)

        if is_breakout and vol_surge and strong_close:
            # Stop loss calculation (tight contraction low or default stop_pct, capped at stop_pct)
            pivot_dist = (current_close - contraction_low) / current_close
            chosen_stop_pct = min(self.stop_pct, max(0.04, pivot_dist))
            stop_price = current_close * (1.0 - chosen_stop_pct)
            risk_per_share = current_close - stop_price

            # Sizing: risk_pct of capital
            dollar_risk = self.capital * self.risk_pct
            shares = max(1, math.floor(dollar_risk / risk_per_share))

            # Max position cap: 20% of capital
            max_shares = math.floor((self.capital * 0.20) / current_close)
            shares = min(shares, max_shares)

            if shares <= 0:
                return None

            state['position'] = 1
            state['entry_price'] = current_close
            state['entry_date'] = candle.timestamp
            state['shares'] = shares
            state['initial_shares'] = shares
            state['stop_price'] = stop_price
            state['initial_risk_per_share'] = risk_per_share
            state['partial_taken'] = False
            state['moved_to_breakeven'] = False

            self.logger.info(
                f"[VCP BUY BREAKOUT] {instrument} @ ₹{current_close:.2f} | Pivot: ₹{pivot_price:.2f} | "
                f"Shares: {shares} | Stop: ₹{stop_price:.2f} (-{chosen_stop_pct*100:.1f}%) | Vol: {candle.volume/vol_sma50:.2f}x avg"
            )

            return Signal(
                strategy_id=self.strategy_id,
                instrument=instrument,
                action=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=shares,
                price=current_close,
                stop_loss=stop_price
            )

        return None

    # -------------------------------------------------------------
    # Helper Indicator & Filter Functions
    # -------------------------------------------------------------

    def _eval_trend_template(self, state: Dict[str, Any], sma50: float, sma150: float, sma200: float) -> bool:
        closes = state['closes']
        current_close = closes[-1]
        n = len(closes)

        # 1. Price > 150 MA and Price > 200 MA
        if not (current_close > sma150 and current_close > sma200):
            return False

        # 2. 150 MA > 200 MA
        if not (sma150 > sma200):
            return False

        # 3. 200 MA trending up for at least 1 month (~20 trading days)
        if n > 220:
            sma200_prior = self._sma(closes[:-20], 200)
            if sma200 <= sma200_prior:
                return False

        # 4. 50 MA > 150 MA > 200 MA
        if not (sma50 > sma150 > sma200):
            return False

        # 5. Price > 50 MA
        if not (current_close > sma50):
            return False

        # 6. Price at least 25% above 52-week low (~252 days)
        lookback_52w = min(n, 252)
        low_52w = min(state['lows'][-lookback_52w:])
        if current_close < (1.25 * low_52w):
            return False

        # 7. Price within 25% of 52-week high
        high_52w = max(state['highs'][-lookback_52w:])
        if current_close < (0.75 * high_52w):
            return False

        # 8. Relative Strength check (Stock must be outperforming broader benchmark over 6-12 months)
        if n >= 126:
            perf_stock = (current_close - closes[-126]) / closes[-126]
            # Simple momentum outperformance threshold (> +10% over 6 months)
            if perf_stock < 0.08:
                return False

        return True

    def _eval_market_regime(self) -> bool:
        """Evaluates whether Nifty is above its 50-day SMA"""
        if not self._index_candles or len(self._index_candles) < 50:
            return True  # Neutral if index not supplied
        idx_closes = [c.close for c in self._index_candles]
        sma50_idx = self._sma(idx_closes, 50)
        return idx_closes[-1] > sma50_idx

    def _detect_vcp(self, state: Dict[str, Any]) -> Tuple[bool, float, float]:
        """
        Detects Volatility Contraction Pattern (VCP):
        - Looks across consolidation before today (bars -35 to -1)
        - Pivot is resistance high of recent tight contraction prior to today
        - Checks for base depth between 5% and 35%
        - Verifies ATR(10) contraction
        - Verifies volume dry-up before today's breakout
        - Returns (is_valid, pivot_high, tight_contraction_low)
        """
        closes = state['closes']
        highs = state['highs']
        lows = state['lows']
        volumes = state['volumes']
        n = len(closes)

        if n < 60:
            return False, 0.0, 0.0

        # Base window prior to current bar (excluding today)
        base_window = 35
        base_highs = highs[-base_window:-1]
        base_lows = lows[-base_window:-1]

        # Pivot point: resistance high of the tight contraction in last 15 days prior to today
        pivot_high = max(base_highs[-15:])
        base_peak = max(base_highs)

        # 1. Contraction depth: Pullback from base peak should not be excessive (< 35%)
        base_trough = min(base_lows)
        total_depth = (base_peak - base_trough) / base_peak
        if total_depth > 0.35 or total_depth < 0.04:
            return False, 0.0, 0.0

        # 2. ATR Contraction: 10-day ATR prior to today vs ATR at start of base
        atr10_recent = self._calc_atr(highs, lows, closes, 10, offset=1)
        atr10_start = self._calc_atr(highs, lows, closes, 10, offset=25)
        if atr10_start > 0 and (atr10_recent / atr10_start) > 0.95:
            return False, 0.0, 0.0

        # 3. Volume dry-up: Average volume in last 5 days before breakout should be modest
        vol_sma50 = self._sma(volumes[:-1], 50)
        recent_avg_vol = sum(volumes[-6:-1]) / 5.0
        if recent_avg_vol > (1.25 * vol_sma50):
            return False, 0.0, 0.0

        # 4. Tight contraction low in last 10 days prior to today
        tight_low = min(lows[-11:-1])

        return True, pivot_high, tight_low

    def _calc_atr(self, highs: List[float], lows: List[float], closes: List[float], period: int, offset: int = 0) -> float:
        end_idx = len(closes) - offset
        if end_idx < period + 1:
            return 0.0
        tr_list = []
        for i in range(end_idx - period, end_idx):
            h = highs[i]
            l = lows[i]
            prev_c = closes[i - 1]
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            tr_list.append(tr)
        return sum(tr_list) / len(tr_list) if tr_list else 0.0

    def _sma(self, series: List[float], period: int) -> float:
        if len(series) < period:
            return series[-1] if series else 0.0
        return sum(series[-period:]) / period

    def _ema(self, series: List[float], period: int) -> float:
        if len(series) < period:
            return series[-1] if series else 0.0
        multiplier = 2.0 / (period + 1.0)
        ema_val = sum(series[:period]) / period
        for val in series[period:]:
            ema_val = (val - ema_val) * multiplier + ema_val
        return ema_val


# Register Strategy with Registry
StrategyRegistry.register('equity_swing_vcp', EquitySwingVCPStrategy)
