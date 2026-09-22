"""Lorentzian Classification ML strategy — platform adapter (StrategyBase).

Wraps the standalone `lorentzian_strategy/` pipeline into the platform's
`StrategyBase` contract (`on_candle` / `on_tick` -> `Signal`) so the strategy
is registered in `StrategyRegistry` under 'lorentzian_ml' and appears in the
dashboard catalog / backtest engine / CLI, all fed by the Breeze provider.
"""
from datetime import datetime
from typing import Any, Dict, Optional

import pandas as pd

from strategies import StrategyBase, StrategyRegistry
from core.models import Candle, OrderSide, OrderType, Signal

from lorentzian_strategy.config import Settings
from lorentzian_strategy.main import run_pipeline
from lorentzian_strategy.data_loader import resample_ohlcv


class LorentzianMLStrategy(StrategyBase):
    """Incremental Lorentzian-ML signal generator.

    Accumulates closed candles per instrument; whenever a new closed candle
    arrives at (or below) the configured `timeframe`, the instrument's OHLC
    history is re-evaluated through the strategy's entry/exit flags over causal
    data only (nothing past the current closed bar). Emits BUY/SELL signals on
    `start_long_trade`/`start_short_trade` transitions and exit (flat) signals
    on `end_long_trade`/`end_short_trade`, with a forced flip when the
    opposite entry fires — mirroring the standalone backtest engine.
    """

    def __init__(self, strategy_id: str, name: str, params: Dict[str, Any] = None):
        params = dict(params or {})
        settings = Settings(
            source=params.get("source", "close"),
            neighbors_count=int(params.get("neighbors_count", 8)),
            max_bars_back=int(params.get("max_bars_back", 2000)),
            feature_count=int(params.get("feature_count", 5)),
            use_dynamic_exits=bool(params.get("use_dynamic_exits", False)),
            use_worst_case=bool(params.get("use_worst_case", False)),
            use_volatility_filter=bool(params.get("use_volatility_filter", True)),
            use_regime_filter=bool(params.get("use_regime_filter", True)),
            regime_threshold=float(params.get("regime_threshold", -0.1)),
            use_adx_filter=bool(params.get("use_adx_filter", False)),
            adx_threshold=float(params.get("adx_threshold", 20.0)),
            use_ema_filter=bool(params.get("use_ema_filter", False)),
            ema_period=int(params.get("ema_period", 200)),
            use_sma_filter=bool(params.get("use_sma_filter", False)),
            sma_period=int(params.get("sma_period", 200)),
            use_kernel_filter=bool(params.get("use_kernel_filter", True)),
            use_kernel_smoothing=bool(params.get("use_kernel_smoothing", False)),
            h=int(params.get("kernel_h", 8)),
            r=float(params.get("kernel_r", 8.0)),
            x=int(params.get("kernel_x", 25)),
            lag=int(params.get("kernel_lag", 2)),
            timeframe=params.get("timeframe", "1d"),
            data_provider="breeze",
            ticker=params.get("ticker", "NSE:NIFTY"),
            use_bollinger_bands=False,
        )
        settings.validate()
        super().__init__(strategy_id, name, params)
        self.settings = settings
        self.timeframe = settings.timeframe
        self.quantity = int(params.get("quantity", 1))
        self.min_history_bars = max(int(params.get("min_history_bars", 60)), 10)
        self.replay_max_bars_back = max(int(params.get("replay_max_bars_back", 400)), 50)
        # live path replays only a bounded suffix per candle: run it with a
        # smaller effective lookback so each on_candle stays cheap while the
        # offline full-history run (main.py/cli) keeps the configured lookback.
        self.settings.max_bars_back = min(settings.max_bars_back, self.replay_max_bars_back)
        self._ohlc: Dict[str, pd.DataFrame] = {}
        self._position: Dict[str, int] = {}

    def on_tick(self, tick) -> Optional[Signal]:
        return None

    def _append_candle(self, candle: Candle) -> pd.DataFrame:
        """Append one platform candle; return the strategy-timeframe view."""
        inst = candle.instrument
        ts = candle.timestamp
        if ts is None:
            ts = pd.Timestamp.now()
        ts = pd.Timestamp(ts)
        if getattr(ts, "tzinfo", None) is not None:
            ts = ts.tz_convert(None) if hasattr(ts, "tz_convert") else ts.replace(tzinfo=None)
        row = pd.DataFrame(
            {"open": [float(candle.open)], "high": [float(candle.high)],
             "low": [float(candle.low)], "close": [float(candle.close)]},
            index=pd.DatetimeIndex([ts]),
        )
        raw = self._ohlc.get(inst)
        raw = row if raw is None else pd.concat([raw, row])
        raw = raw[~raw.index.duplicated(keep="last")].sort_index()
        self._ohlc[inst] = raw
        try:
            return resample_ohlcv(raw, self.timeframe)
        except Exception:
            return raw

    def _suffix_window(self, view: pd.DataFrame):
        """Bounded suffix replay window that avoids the O(n^2) blowup.

        The ANN loop costs O(B * maxBarsBack) per evaluate for a history of B
        bars. Replaying the whole growing tail each candle makes a full pass
        O(n^2 * maxBarsBack); we cap the suffix at enough bars to cover the
        neighbor search (maxBarsBack) plus feature/kernel warmup (~300 bars).
        """
        cap = self.settings.max_bars_back + 350
        if len(view) <= cap:
            return view.iloc[:].copy(), 0
        start = len(view) - cap
        return view.iloc[start:].copy(), start

    def on_candle(self, candle: Candle) -> Optional[Signal]:
        inst = candle.instrument
        view = self._append_candle(candle)
        if len(view) < self.min_history_bars + 4:
            # not enough closed bars at this timeframe yet: warm up silently
            return None
        # run the causal pipeline on a bounded suffix through the current bar
        tail, _ = self._suffix_window(view)
        results = run_pipeline(self.settings, df=tail, verbose=False)
        entries = results["entries"]
        if entries.empty:
            return None
        last = entries.iloc[-1]
        pos = self._position.get(inst, 0)
        qty = self.quantity
        px = float(tail["close"].iloc[-1])
        pred = float(results["prediction"].iloc[-1])

        def sig(action, reason):
            return Signal(strategy_id=self.strategy_id, instrument=inst,
                          action=action, quantity=qty,
                          order_type=OrderType.MARKET, price=px,
                          timestamp=candle.timestamp if candle.timestamp else datetime.now(),
                          metadata={"reason": reason, "prediction": pred})

        if bool(last["start_long_trade"]) and pos <= 0:
            self._position[inst] = 1
            return sig(OrderSide.BUY, "lorentzian_new_long")
        if bool(last["start_short_trade"]) and pos >= 0:
            self._position[inst] = -1
            return sig(OrderSide.SELL, "lorentzian_new_short")
        if pos == 1 and bool(last["end_long_trade"]):
            self._position[inst] = 0
            return sig(OrderSide.SELL, "lorentzian_exit_long")
        if pos == -1 and bool(last["end_short_trade"]):
            self._position[inst] = 0
            return sig(OrderSide.BUY, "lorentzian_exit_short")
        return None


StrategyRegistry.register("lorentzian_ml", LorentzianMLStrategy)
StrategyRegistry.register("lorentzian", LorentzianMLStrategy)

