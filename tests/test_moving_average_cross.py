from __future__ import annotations

from profit_pilot.data.models import MarketState
from profit_pilot.strategy.context import SignalContext
from profit_pilot.strategy.indicators import crossover, simple_moving_average
from profit_pilot.strategy.registry import get_strategy
from profit_pilot.strategy.signal import SignalAction

from strategies.ma.moving_average_cross import MovingAverageCrossStrategy


def make_state(price: float, ts) -> MarketState:
    return MarketState(ts, "NIFTY", price)


def test_sma_basic() -> None:
    assert simple_moving_average([1, 2, 3, 4], 2) == 3.5
    assert simple_moving_average([1, 2], 4) is None


def test_indicator_crossover_detects_up_and_down() -> None:
    # Single-bar jump off a flat plateau: fast (3) rises above slow (5) on the
    # last bar while the previous bars are all equal (prev fast <= prev slow).
    up = [10, 10, 10, 10, 10, 10, 10, 10, 10, 40]
    assert crossover(up, fast=3, slow=5) == 1
    # Single-bar drop: fast falls below slow while prior bars are equal.
    down = [50, 50, 50, 50, 50, 50, 50, 50, 50, 10]
    assert crossover(down, fast=3, slow=5) == -1


def test_ma_strategy_emits_buy_on_up_cross() -> None:
    strategy = MovingAverageCrossStrategy(fast_window=2, slow_window=3, quantity=5)
    # History needs at least `slow_window` prior bars for a prev slow MA.
    history = [
        make_state(10.0, i) for i, _ in enumerate(range(3))
    ]
    state = make_state(10.0, 3)
    ctx = SignalContext(state=state, history=history, params=strategy.params)
    # No cross yet -> hold.
    assert strategy.on_signal_context(ctx).action is SignalAction.HOLD

    rising = [make_state(10.0, i) for i in range(3)]
    rising.append(make_state(40.0, 3))
    ctx2 = SignalContext(state=rising[-1], history=rising[:-1], params=strategy.params)
    sig = strategy.on_signal_context(ctx2)
    assert sig.action is SignalAction.BUY
    assert sig.quantity == 5


def test_ma_strategy_sells_on_one_bar_down_cross() -> None:
    strategy = MovingAverageCrossStrategy(fast_window=2, slow_window=3, quantity=1)
    falling = [make_state(50.0, i) for i in range(3)]
    # Latest bar dives to 10: fast (2) < slow (3) and prev fast >= prev slow.
    state = make_state(10.0, 3)
    ctx = SignalContext(state=state, history=falling, params=strategy.params)
    sig = strategy.on_signal_context(ctx)
    assert sig.action is SignalAction.SELL


def test_ma_strategy_registered() -> None:
    got = get_strategy("MovingAverageCrossStrategy")
    assert got is MovingAverageCrossStrategy


def test_ma_strategy_runs_over_frozen_lake(frozen_nifty) -> None:
    """End-to-end over a real slice of the validated data lake.

    Walks the frozen series bar-by-bar, letting a MA strategy with a small
    window emit signals; confirms it produces a buy or sell at least once (i.e.
    the cross logic engages on real prices) and never emits anything malformed.
    """
    strategy = MovingAverageCrossStrategy(fast_window=5, slow_window=20, quantity=1)
    ctx = SignalContext(state=frozen_nifty[0], history=[], params=strategy.params)
    signals_seen = {"BUY", "SELL"}
    seen = set()
    for state in frozen_nifty:
        ctx = SignalContext(state=state, history=ctx.history[-20:] + [ctx.state], params=strategy.params)
        sig = strategy.on_signal_context(ctx)
        assert sig.symbol == "NIFTY"
        assert sig.quantity >= 0
        if sig.action.value in signals_seen:
            seen.add(sig.action.value)
    assert seen, "expected at least one BUY/SELL signal over the frozen window"