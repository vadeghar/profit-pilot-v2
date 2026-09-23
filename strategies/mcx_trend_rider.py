"""
MCX Trend Rider Strategy
Donchian Breakout + ADX Filter + ATR Trailing Stop
Instruments: MCX Crude Oil, Gold, Silver
Implements exact rules from MCX_Trend_Rider_Strategy.md
"""

from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import math

from core.models import (
    Signal, OrderSide, OrderType, Candle, Tick
)
from strategies import StrategyBase, StrategyRegistry
from utils import Logger


# Commodity specifications
COMMODITY_SPECS = {
    'MCX_CRUDEOIL': {'lot_size': 100, 'point_value': 100, 'tick_size': 1.0, 'name': 'Crude Oil'},
    'CRUDEOIL': {'lot_size': 100, 'point_value': 100, 'tick_size': 1.0, 'name': 'Crude Oil'},
    'MCX:CRUDEOIL': {'lot_size': 100, 'point_value': 100, 'tick_size': 1.0, 'name': 'Crude Oil'},
    'MCX_GOLD': {'lot_size': 1, 'point_value': 1, 'tick_size': 1.0, 'name': 'Gold 1kg'},
    'GOLD': {'lot_size': 1, 'point_value': 1, 'tick_size': 1.0, 'name': 'Gold 1kg'},
    'MCX:GOLD': {'lot_size': 1, 'point_value': 1, 'tick_size': 1.0, 'name': 'Gold 1kg'},
    'MCX_GOLDM': {'lot_size': 100, 'point_value': 10, 'tick_size': 1.0, 'name': 'Gold Mini 100g'},
    'GOLDM': {'lot_size': 100, 'point_value': 10, 'tick_size': 1.0, 'name': 'Gold Mini 100g'},
    'MCX:GOLDM': {'lot_size': 100, 'point_value': 10, 'tick_size': 1.0, 'name': 'Gold Mini 100g'},
    'MCX_SILVER': {'lot_size': 30, 'point_value': 30, 'tick_size': 1.0, 'name': 'Silver 30kg'},
    'SILVER': {'lot_size': 30, 'point_value': 30, 'tick_size': 1.0, 'name': 'Silver 30kg'},
    'MCX:SILVER': {'lot_size': 30, 'point_value': 30, 'tick_size': 1.0, 'name': 'Silver 30kg'},
    'MCX_SILVERM': {'lot_size': 5, 'point_value': 5, 'tick_size': 1.0, 'name': 'Silver Mini 5kg'},
    'SILVERM': {'lot_size': 5, 'point_value': 5, 'tick_size': 1.0, 'name': 'Silver Mini 5kg'},
    'MCX:SILVERM': {'lot_size': 5, 'point_value': 5, 'tick_size': 1.0, 'name': 'Silver Mini 5kg'},
}


class MCXTrendRiderStrategy(StrategyBase):
    """
    MCX Trend Rider Strategy
    
    1. Donchian Breakout:
       - Fast channel: 20-period highest high / lowest low
       - Slow confirmation channel: 55-period
    2. Trend Strength Filter:
       - ADX(14) >= 20
    3. Macro Regime Filter (Optional):
       - Longs only above 200 SMA, Shorts only below 200 SMA
    4. Loser-Skip Rule (Turtle filter):
       - If immediately prior 20-day signal was stopped out for a loss, skip next 20-day breakout
         and require 55-day breakout instead.
    5. Volatility Sizing (1% equity risk / 2*ATR20):
       - Stop distance = 2 * ATR(20)
       - Lots = floor((Capital * Risk%) / (2*ATR20 * point_value))
    6. Stop Loss & Chandelier Trailing Exit:
       - Initial stop = Entry ± 2 * ATR(20)
       - Move to breakeven when profit >= +1.0 * ATR
       - Chandelier Trailing when profit >= +2.0 * ATR:
         Long trailing: HighestHigh - (3 * ATR14)
         Short trailing: LowestLow + (3 * ATR14)
       - Channel exit: Long closes below 10-day low, Short closes above 10-day high
    """

    def __init__(self, strategy_id: str, name: str = "mcx_trend_rider", params: Dict[str, Any] = None):
        super().__init__(strategy_id, name, params)
        self.logger = Logger(f"strategy.{strategy_id}")
        self._init_indicators()

    def _init_indicators(self) -> None:
        # Configuration parameters
        self.fast_donchian = self.params.get('fast_donchian', 20)
        self.slow_donchian = self.params.get('slow_donchian', 55)
        self.exit_donchian = self.params.get('exit_donchian', 10)
        self.adx_period = self.params.get('adx_period', 14)
        self.adx_threshold = self.params.get('adx_threshold', 20.0)
        self.atr14_period = self.params.get('atr14_period', 14)
        self.atr20_period = self.params.get('atr20_period', 20)
        self.sma_period = self.params.get('sma_period', 200)
        self.use_sma_filter = self.params.get('use_sma_filter', True)
        self.use_loser_filter = self.params.get('use_loser_filter', True)
        self.risk_pct = self.params.get('risk_pct', 0.01) # 1% risk per trade
        self.capital = self.params.get('capital', 100000.0)

        # Instrument state tracking: instrument -> state dict
        self._state: Dict[str, Dict[str, Any]] = {}

    def _get_state(self, instrument: str) -> Dict[str, Any]:
        if instrument not in self._state:
            self._state[instrument] = {
                'candles': [],
                'highs': [],
                'lows': [],
                'closes': [],
                'position': 0, # +1 long, -1 short, 0 flat
                'entry_price': 0.0,
                'entry_bar': 0,
                'entry_date': None,
                'quantity': 0,
                'initial_stop': 0.0,
                'current_stop': 0.0,
                'highest_high_since_entry': 0.0,
                'lowest_low_since_entry': 0.0,
                'entry_atr': 0.0,
                'last_trade_was_loss': False,
                'trailing_mode': 'INITIAL', # INITIAL, BREAKEVEN, CHANDELIER
                'trades_history': []
            }
        return self._state[instrument]

    def on_entry_fill(self, instrument: str, quantity: int, price: float) -> None:
        self._get_state(instrument)['quantity'] = quantity

    def on_tick(self, tick: Tick) -> Optional[Signal]:
        # Daily strategy primarily evaluates on candle close
        return None

    def on_candle(self, candle: Candle) -> Optional[Signal]:
        instrument = candle.instrument
        state = self._get_state(instrument)

        state['candles'].append(candle)
        state['highs'].append(candle.high)
        state['lows'].append(candle.low)
        state['closes'].append(candle.close)

        n = len(state['closes'])
        # Require enough warmup for SMA 200 and Donchian 55
        warmup_required = max(self.sma_period, self.slow_donchian, 30)
        if n < warmup_required:
            return None

        # Calculate indicators
        atr20 = self._calculate_atr(state['highs'], state['lows'], state['closes'], self.atr20_period)
        atr14 = self._calculate_atr(state['highs'], state['lows'], state['closes'], self.atr14_period)
        adx14 = self._calculate_adx(state['highs'], state['lows'], state['closes'], self.adx_period)
        sma200 = sum(state['closes'][-self.sma_period:]) / self.sma_period if n >= self.sma_period else state['closes'][-1]

        # Donchian channels over previous closed bars (exclude current bar to prevent lookahead)
        donchian_high_20 = max(state['highs'][-self.fast_donchian-1:-1])
        donchian_low_20 = min(state['lows'][-self.fast_donchian-1:-1])
        donchian_high_55 = max(state['highs'][-self.slow_donchian-1:-1])
        donchian_low_55 = min(state['lows'][-self.slow_donchian-1:-1])
        donchian_high_10 = max(state['highs'][-self.exit_donchian-1:-1])
        donchian_low_10 = min(state['lows'][-self.exit_donchian-1:-1])

        # Store indicators for dashboard/audit
        self.store_indicator(f"{instrument}_atr20", atr20)
        self.store_indicator(f"{instrument}_adx14", adx14)
        self.store_indicator(f"{instrument}_sma200", sma200)

        # ------------------------------------------------------------------
        # 1. MANAGE EXISTING POSITION (Exits & Trailing Stops)
        # ------------------------------------------------------------------
        if state['position'] != 0:
            pos = state['position']
            close = candle.close
            entry_p = state['entry_price']
            entry_atr = state['entry_atr'] or atr20
            exit_signal = None
            exit_reason = ""

            # Update highest/lowest since entry
            if pos == 1:
                state['highest_high_since_entry'] = max(state['highest_high_since_entry'], candle.high)
                profit_distance = close - entry_p

                # Breakeven check (+1.0 * ATR)
                if state['trailing_mode'] == 'INITIAL' and profit_distance >= entry_atr:
                    state['trailing_mode'] = 'BREAKEVEN'
                    state['current_stop'] = max(state['current_stop'], entry_p)

                # Chandelier trailing check (+2.0 * ATR)
                if profit_distance >= 2 * entry_atr:
                    state['trailing_mode'] = 'CHANDELIER'

                if state['trailing_mode'] == 'CHANDELIER':
                    chandelier_stop = state['highest_high_since_entry'] - (3.0 * atr14)
                    state['current_stop'] = max(state['current_stop'], chandelier_stop)

                # Priority 1: Current Hard / Trailing Stop hit
                if candle.low <= state['current_stop'] or close <= state['current_stop']:
                    exit_reason = f"long_stop_hit (stop: {state['current_stop']:.1f}, mode: {state['trailing_mode']})"
                    exit_signal = Signal(
                        strategy_id=self.strategy_id,
                        instrument=instrument,
                        action=OrderSide.SELL,
                        quantity=state['quantity'],
                        order_type=OrderType.MARKET,
                        metadata={'reason': exit_reason, 'exit_price': min(candle.open, state['current_stop'])}
                    )

                # Priority 2: 10-day channel low exit
                elif close < donchian_low_10:
                    exit_reason = f"long_10day_channel_exit (level: {donchian_low_10:.1f})"
                    exit_signal = Signal(
                        strategy_id=self.strategy_id,
                        instrument=instrument,
                        action=OrderSide.SELL,
                        quantity=state['quantity'],
                        order_type=OrderType.MARKET,
                        metadata={'reason': exit_reason, 'exit_price': close}
                    )

                # Priority 3: Time-based safety exit (>90 calendar days and < 0.5 ATR profit)
                elif state['entry_date'] and (candle.timestamp - state['entry_date']).days >= 90 and profit_distance < 0.5 * entry_atr:
                    exit_reason = "long_90day_stagnant_exit"
                    exit_signal = Signal(
                        strategy_id=self.strategy_id,
                        instrument=instrument,
                        action=OrderSide.SELL,
                        quantity=state['quantity'],
                        order_type=OrderType.MARKET,
                        metadata={'reason': exit_reason, 'exit_price': close}
                    )

            elif pos == -1: # Short position
                state['lowest_low_since_entry'] = min(state['lowest_low_since_entry'], candle.low)
                profit_distance = entry_p - close

                # Breakeven check (+1.0 * ATR)
                if state['trailing_mode'] == 'INITIAL' and profit_distance >= entry_atr:
                    state['trailing_mode'] = 'BREAKEVEN'
                    state['current_stop'] = min(state['current_stop'], entry_p)

                # Chandelier trailing check (+2.0 * ATR)
                if profit_distance >= 2 * entry_atr:
                    state['trailing_mode'] = 'CHANDELIER'

                if state['trailing_mode'] == 'CHANDELIER':
                    chandelier_stop = state['lowest_low_since_entry'] + (3.0 * atr14)
                    state['current_stop'] = min(state['current_stop'], chandelier_stop)

                # Priority 1: Current Hard / Trailing Stop hit
                if candle.high >= state['current_stop'] or close >= state['current_stop']:
                    exit_reason = f"short_stop_hit (stop: {state['current_stop']:.1f}, mode: {state['trailing_mode']})"
                    exit_signal = Signal(
                        strategy_id=self.strategy_id,
                        instrument=instrument,
                        action=OrderSide.BUY,
                        quantity=state['quantity'],
                        order_type=OrderType.MARKET,
                        metadata={'reason': exit_reason, 'exit_price': max(candle.open, state['current_stop'])}
                    )

                # Priority 2: 10-day channel high exit
                elif close > donchian_high_10:
                    exit_reason = f"short_10day_channel_exit (level: {donchian_high_10:.1f})"
                    exit_signal = Signal(
                        strategy_id=self.strategy_id,
                        instrument=instrument,
                        action=OrderSide.BUY,
                        quantity=state['quantity'],
                        order_type=OrderType.MARKET,
                        metadata={'reason': exit_reason, 'exit_price': close}
                    )

                # Priority 3: Time-based safety exit (>90 calendar days and < 0.5 ATR profit)
                elif state['entry_date'] and (candle.timestamp - state['entry_date']).days >= 90 and profit_distance < 0.5 * entry_atr:
                    exit_reason = "short_90day_stagnant_exit"
                    exit_signal = Signal(
                        strategy_id=self.strategy_id,
                        instrument=instrument,
                        action=OrderSide.BUY,
                        quantity=state['quantity'],
                        order_type=OrderType.MARKET,
                        metadata={'reason': exit_reason, 'exit_price': close}
                    )

            if exit_signal:
                # Record trade outcome for loser filter
                realized_pnl = (close - entry_p) if pos == 1 else (entry_p - close)
                state['last_trade_was_loss'] = (realized_pnl < 0)
                state['position'] = 0
                state['quantity'] = 0
                state['trailing_mode'] = 'INITIAL'
                self.logger.info(f"EXIT {instrument}: {exit_reason}, PnL pts: {realized_pnl:.1f}")
                return exit_signal

        # ------------------------------------------------------------------
        # 2. ENTRY EVALUATION (Only if flat)
        # ------------------------------------------------------------------
        if state['position'] == 0:
            close = candle.close

            # Loser-skip rule: If previous trade was a loss, require 55-day breakout
            require_55 = state['last_trade_was_loss'] and self.use_loser_filter

            # Filter checks
            adx_ok = adx14 >= self.adx_threshold
            sma_long_ok = (close > sma200) if self.use_sma_filter else True
            sma_short_ok = (close < sma200) if self.use_sma_filter else True

            # Breakout levels
            breakout_high = donchian_high_55 if require_55 else donchian_high_20
            breakout_low = donchian_low_55 if require_55 else donchian_low_20

            # Volatility Sizing calculation:
            # Risk capital = 1% of allocated capital
            # Stop distance = 2 * ATR(20)
            # Lots = (Capital * Risk%) / (2*ATR20 * point_value)
            spec = COMMODITY_SPECS.get(instrument, {'lot_size': 1, 'point_value': 1})
            point_val = spec['point_value']
            stop_distance = 2.0 * atr20
            risk_amount = self.capital * self.risk_pct
            risk_per_lot = stop_distance * point_val

            lots = math.floor(risk_amount / risk_per_lot) if risk_per_lot > 0 else 1
            if lots < 1:
                lots = 1 # Minimum 1 contract for validation

            # LONG ENTRY
            if close > breakout_high and adx_ok and sma_long_ok:
                state['position'] = 1
                state['entry_price'] = close
                state['entry_bar'] = n
                state['entry_date'] = candle.timestamp
                state['quantity'] = lots
                state['entry_atr'] = atr20
                state['initial_stop'] = close - (2.0 * atr20)
                state['current_stop'] = state['initial_stop']
                state['highest_high_since_entry'] = candle.high
                state['trailing_mode'] = 'INITIAL'

                reason = f"long_breakout_{55 if require_55 else 20}d (ADX: {adx14:.1f}, ATR20: {atr20:.1f}, Lots: {lots})"
                self.logger.info(f"BUY SIGNAL {instrument} at {close:.1f}, SL: {state['initial_stop']:.1f}, {reason}")

                return Signal(
                    strategy_id=self.strategy_id,
                    instrument=instrument,
                    action=OrderSide.BUY,
                    quantity=lots,
                    order_type=OrderType.MARKET,
                    metadata={
                        'reason': reason,
                        'entry_price': close,
                        'stop_loss': state['initial_stop'],
                        'atr20': atr20,
                        'adx14': adx14
                    }
                )

            # SHORT ENTRY
            elif close < breakout_low and adx_ok and sma_short_ok:
                state['position'] = -1
                state['entry_price'] = close
                state['entry_bar'] = n
                state['entry_date'] = candle.timestamp
                state['quantity'] = lots
                state['entry_atr'] = atr20
                state['initial_stop'] = close + (2.0 * atr20)
                state['current_stop'] = state['initial_stop']
                state['lowest_low_since_entry'] = candle.low
                state['trailing_mode'] = 'INITIAL'

                reason = f"short_breakout_{55 if require_55 else 20}d (ADX: {adx14:.1f}, ATR20: {atr20:.1f}, Lots: {lots})"
                self.logger.info(f"SELL SIGNAL {instrument} at {close:.1f}, SL: {state['initial_stop']:.1f}, {reason}")

                return Signal(
                    strategy_id=self.strategy_id,
                    instrument=instrument,
                    action=OrderSide.SELL,
                    quantity=lots,
                    order_type=OrderType.MARKET,
                    metadata={
                        'reason': reason,
                        'entry_price': close,
                        'stop_loss': state['initial_stop'],
                        'atr20': atr20,
                        'adx14': adx14
                    }
                )

        return None

    # ------------------------------------------------------------------
    # Mathematical Indicator Helpers
    # ------------------------------------------------------------------
    def _calculate_atr(self, highs: List[float], lows: List[float], closes: List[float], period: int) -> float:
        if len(closes) < 2:
            return highs[-1] - lows[-1] if highs else 1.0
        
        tr_list = []
        for i in range(1, len(closes)):
            h = highs[i]
            l = lows[i]
            prev_c = closes[i-1]
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            tr_list.append(tr)
            
        if len(tr_list) < period:
            return sum(tr_list) / len(tr_list)
        
        # Wilder's Smoothing for ATR
        atr = sum(tr_list[:period]) / period
        for tr in tr_list[period:]:
            atr = (atr * (period - 1) + tr) / period
        return atr

    def _calculate_adx(self, highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
        if len(closes) < period * 2:
            return 25.0 # default neutral trend value during initial warmup
            
        tr_list = []
        plus_dm = []
        minus_dm = []
        
        for i in range(1, len(closes)):
            h = highs[i]
            l = lows[i]
            prev_h = highs[i-1]
            prev_l = lows[i-1]
            prev_c = closes[i-1]
            
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            tr_list.append(tr)
            
            up_move = h - prev_h
            down_move = prev_l - l
            
            if up_move > down_move and up_move > 0:
                plus_dm.append(up_move)
            else:
                plus_dm.append(0.0)
                
            if down_move > up_move and down_move > 0:
                minus_dm.append(down_move)
            else:
                minus_dm.append(0.0)
                
        # Wilder's smoothed values
        smooth_tr = sum(tr_list[:period])
        smooth_plus = sum(plus_dm[:period])
        smooth_minus = sum(minus_dm[:period])
        
        dx_list = []
        for i in range(period, len(tr_list)):
            smooth_tr = smooth_tr - (smooth_tr / period) + tr_list[i]
            smooth_plus = smooth_plus - (smooth_plus / period) + plus_dm[i]
            smooth_minus = smooth_minus - (smooth_minus / period) + minus_dm[i]
            
            di_plus = (100 * smooth_plus / smooth_tr) if smooth_tr > 0 else 0
            di_minus = (100 * smooth_minus / smooth_tr) if smooth_tr > 0 else 0
            
            denom = di_plus + di_minus
            dx = (100 * abs(di_plus - di_minus) / denom) if denom > 0 else 0
            dx_list.append(dx)
            
        if not dx_list:
            return 25.0
            
        if len(dx_list) < period:
            return sum(dx_list) / len(dx_list)
            
        adx = sum(dx_list[:period]) / period
        for dx in dx_list[period:]:
            adx = (adx * (period - 1) + dx) / period
            
        return adx


# Register strategy with StrategyRegistry
StrategyRegistry.register('mcx_trend_rider', MCXTrendRiderStrategy)
