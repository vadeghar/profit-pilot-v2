from profit_pilot.backtest.commission import FlatCommission, BpsCommission, CompositeCommission
from profit_pilot.execution.order import Order, OrderSide
from datetime import datetime


def test_flat_commission() -> None:
    order = Order("NIFTY", OrderSide.BUY, 5, datetime.now())
    assert FlatCommission(10.0).compute(order, 25000.0) == 10.0
    assert FlatCommission(0.0).compute(order, 25000.0) == 0.0


def test_bps_commission() -> None:
    order = Order("NIFTY", OrderSide.BUY, 5, datetime.now())
    # 10 bps = 0.0010 on notional of 5 * 25000 = 125000
    assert abs(BpsCommission(10.0).compute(order, 25000.0) - 125.0) < 0.001
    assert BpsCommission(0.0).compute(order, 25000.0) == 0.0


def test_composite_commission() -> None:
    order = Order("NIFTY", OrderSide.BUY, 5, datetime.now())
    comp = CompositeCommission(FlatCommission(5.0), BpsCommission(10.0))
    assert abs(comp.compute(order, 25000.0) - (5.0 + 125.0)) < 0.001


def test_slippage_adverse_buy_sell() -> None:
    from profit_pilot.backtest.slippage import BpsSlippage
    sl = BpsSlippage(10.0)  # 10 bps
    buy_price = sl.adjust_price("BUY", 25000.0)
    sell_price = sl.adjust_price("SELL", 25000.0)
    assert buy_price > 25000.0
    assert sell_price < 25000.0
    assert abs(buy_price - 25000.0 * 1.001) < 0.01
    assert abs(sell_price - 25000.0 * 0.999) < 0.01