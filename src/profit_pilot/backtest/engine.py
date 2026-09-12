from profit_pilot.backtest.results import BacktestResult
from profit_pilot.backtest.simulator import ExecutionSimulator
from profit_pilot.data.market_data import MarketDataProvider
from profit_pilot.data.models import MarketState
from profit_pilot.execution.portfolio import Portfolio
from profit_pilot.execution.order import Order
from profit_pilot.strategy.base import Strategy
from profit_pilot.strategy.context import SignalContext
from profit_pilot.strategy.signal import Signal, SignalAction
from profit_pilot.backtest.commission import CommissionModel, FlatCommission, BpsCommission, CompositeCommission
from profit_pilot.backtest.slippage import SlippageModel, BpsSlippage
from typing import Sequence, Mapping, Optional
from datetime import datetime, timedelta, date
from collections import defaultdict


class BacktestEngine:
    def __init__(
        self,
        simulator: ExecutionSimulator | None = None,
        data_provider: MarketDataProvider | None = None,
    ) -> None:
        self.simulator = simulator or ExecutionSimulator()
        self.data_provider = data_provider

    def run(
        self,
        config: object,
        strategy: Strategy,
    ) -> BacktestResult:
        """
        Run a backtest using a BacktestRunConfig.

        Signature change: old ``(states, strategy, initial_cash)`` is replaced
        by ``(config, strategy)`` where config carries all rates and dates.
        """
        # Determine initial_cash from config
        initial_cash: float = getattr(config, "initial_cash", 0.0)

        # If config has start/end dates and we have a data_provider that
        # can page states, use it. Otherwise expect the caller to pass states.
        states: list[MarketState] = []
        if config is not None and self.data_provider is not None:
            try:
                start = getattr(config, "start", None)
                end = getattr(config, "end", None)
                if start is not None and end is not None:
                    states = list(
                        self.data_provider.states(start, end)  # type: ignore[arg-type]
                    )
            except Exception:
                # Data provider failed — fall back to empty states;
                # the engine will simply have no fills.
                states = []

        portfolio = Portfolio(initial_cash)
        fills: list = []
        equity_curve: list[tuple[datetime, float]] = []

        # Rolling history window — newest-last, capped by strategy needs.
        # For Step 10 we keep it unbounded but maintain it as a read-only
        # window that the strategy sees via SignalContext.history.
        history: list[MarketState] = []

        # Initialize commission and slippage models from config
        commission_model: CommissionModel = CompositeCommission(
            FlatCommission(getattr(config, "commission_flat", 0.0)),
            BpsCommission(getattr(config, "commission_bps", 0.0))
        )
        slippage_model: SlippageModel = BpsSlippage(getattr(config, "slippage_bps", 0.0))

        # We iterate over states bar-by-bar. Signal t at bar t is evaluated,
        # and its fill happens at bar t+1's price (next-bar behaviour).
        for t, state in enumerate(states):
            # Cap rolling history (prevents unbounded growth; audit recommendation)
            if len(history) > 50:
                history = history[-50:]

            # Append current state to history BEFORE computing signal so that
            # on_signal_context sees the rolling window including this bar's
            # close (used as the "current" price for the next bar's fill).
            history.append(state)

            # Compute signal using on_signal_context with rolling history.
            # We pass the newest state as the "current" state and the full
            # history minus the newest as the "reference window".
            # Build daily bars from history for VPA strategy
            daily_map = defaultdict(lambda: {"o":None,"h":None,"l":None,"c":None,"v":0.0,"d":None})
            for h in history:
                d = h.timestamp.date()
                m = daily_map[d]
                m["d"] = d
                price = h.price
                if m["o"] is None: m["o"] = price
                if m["h"] is None or price > m["h"]: m["h"] = price
                if m["l"] is None or price < m["l"]: m["l"] = price
                m["c"] = price
                m["v"] += h.fields.get("volume",0)
            daily_bars = [{"date":m["d"],"open":m["o"],"high":m["h"],"low":m["l"],"close":m["c"],"volume":m["v"]} for d,m in sorted(daily_map.items())]
            weekly_regime = "W_NEUTRAL"
            if len(daily_bars) >= 5:
                w1 = daily_bars[-5]["close"]
                w2 = daily_bars[-1]["close"]
                if w2 > w1 * 1.01: weekly_regime = "W_BULLISH"
                elif w2 < w1 * 0.99: weekly_regime = "W_BEARISH"
            ctx = SignalContext(
                state=state,
                params=strategy.params,
                history=list(history[-20:] if hasattr(history, '__len__') else history),
                reference={"daily_bars": daily_bars[-20:], "weekly_context": {"regime": weekly_regime}},
            )
            sig = strategy.on_signal_context(ctx)

            # If there's a next bar, the fill for this signal occurs at the
            # NEXT bar's price (next-bar behaviour to avoid lookahead).
            # Skip fill on the very last bar (no next bar).
            if t + 1 < len(states):
                next_state = states[t + 1]
                base_price = next_state.price
                
                # Apply slippage to the next bar's price
                # Slippage is adverse: BUY adjusts price UP, SELL adjusts DOWN
                if sig.action == SignalAction.BUY:
                    adj_price = slippage_model.adjust_price("BUY", base_price)
                elif sig.action == SignalAction.SELL:
                    adj_price = slippage_model.adjust_price("SELL", base_price)
                else:
                    # HOLD signals don't trade
                    adj_price = base_price

                # Apply commission to the adjusted price
                # Commission is computed as a cost, not a price adjustment
                # We'll compute it separately and apply it to the portfolio cash
                order = Order.from_signal(sig, state.timestamp)
                if order is not None:
                    # Create the fill at the slippage-adjusted price
                    fill = self.simulator.fill(order, MarketState(
                        timestamp=next_state.timestamp,
                        symbol=next_state.symbol,
                        price=adj_price,
                        fields=next_state.fields
                    ))
                    
                    # Calculate commission cost
                    commission_cost = commission_model.compute(order, adj_price)
                    
                    # Cash-overdraw guard: skip fill if estimated cost exceeds available cash.
                    estimated_cost = adj_price * order.quantity + commission_cost
                    if portfolio.cash < estimated_cost:
                        # Order rejected; skip execution.
                        pass
                    else:
                        portfolio.apply_fill(fill)
                        portfolio.cash -= commission_cost
                        fills.append(fill)

                    # Calculate mark-to-market equity at this bar's close
                    # Equity = cash + sum(position.market_value)
                    # Use the *current state* close price for consistency
                    # with the bar being evaluated (fixes audit recommendation).
                    mark_price = state.price
                    equity = portfolio.market_value({state.symbol: mark_price})
                    equity_curve.append((state.timestamp, equity))

        # Compute final cash from portfolio
        final_cash = portfolio.cash

        # Compute basic metrics
        total_return = (final_cash - initial_cash) / initial_cash if initial_cash > 0 else 0.0

        # Calculate additional metrics from equity curve and trades
        max_drawdown = 0.0
        peak = equity_curve[0][1] if equity_curve else initial_cash
        for _, equity in equity_curve:
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak if peak > 0 else 0.0
            if dd > max_drawdown:
                max_drawdown = dd

        # Simple trade extraction from fills (pair BUY/SELL)
        trades = self._extract_trades(fills)
        n_trades = len(trades)
        win_rate = sum(1 for t in trades if t.pnl > 0) / n_trades if n_trades > 0 else 0.0

        return BacktestResult(
            initial_cash=initial_cash,
            final_cash=final_cash,
            fills=tuple(fills),
            equity_curve=tuple(equity_curve),
            rejected_orders=tuple(),  # TODO: track rejected orders
            trades=tuple(trades),
            return_pct=total_return,
            max_drawdown=max_drawdown,
            sharpe=0.0,  # TODO: implement Sharpe calculation
            n_trades=n_trades,
            win_rate=win_rate,
        )

    def _extract_trades(self, fills: Sequence) -> list:
        """Extract closed trades from a sequence of fills."""
        from profit_pilot.execution.order import OrderSide
        from dataclasses import dataclass
        
        @dataclass
        class OpenPosition:
            symbol: str
            entry_time: datetime
            entry_price: float
            qty: int
            side: str  # "BUY" or "SELL"
        
        trades = []
        open_positions: dict[str, OpenPosition] = {}  # symbol -> position
        
        for fill in fills:
            symbol = fill.order.symbol
            side = fill.order.side.value  # "BUY" or "SELL"
            price = fill.price
            qty = fill.quantity
            timestamp = fill.filled_at
            
            if side == "BUY":
                if symbol in open_positions and open_positions[symbol].side == "SELL":
                    # Close a short position
                    open_pos = open_positions.pop(symbol)
                    pnl = (open_pos.entry_price - price) * open_pos.qty
                    trades.append(Trade(
                        symbol=symbol,
                        entry_time=open_pos.entry_time,
                        exit_time=timestamp,
                        side="SELL",
                        qty=open_pos.qty,
                        entry_price=open_pos.entry_price,
                        exit_price=price,
                        pnl=pnl
                    ))
                else:
                    # Open or add to long position
                    if symbol in open_positions:
                        # Average up
                        pos = open_positions[symbol]
                        total_cost = pos.qty * pos.entry_price + qty * price
                        total_qty = pos.qty + qty
                        open_positions[symbol] = OpenPosition(
                            symbol=symbol,
                            entry_time=pos.entry_time,  # Keep original entry time
                            entry_price=total_cost / total_qty,
                            qty=total_qty,
                            side="BUY"
                        )
                    else:
                        open_positions[symbol] = OpenPosition(
                            symbol=symbol,
                            entry_time=timestamp,
                            entry_price=price,
                            qty=qty,
                            side="BUY"
                        )
            else:  # SELL
                if symbol in open_positions and open_positions[symbol].side == "BUY":
                    # Close a long position
                    open_pos = open_positions.pop(symbol)
                    pnl = (price - open_pos.entry_price) * open_pos.qty
                    trades.append(Trade(
                        symbol=symbol,
                        entry_time=open_pos.entry_time,
                        exit_time=timestamp,
                        side="BUY",
                        qty=open_pos.qty,
                        entry_price=open_pos.entry_price,
                        exit_price=price,
                        pnl=pnl
                    ))
                else:
                    # Open or add to short position
                    if symbol in open_positions:
                        # Average down
                        pos = open_positions[symbol]
                        total_cost = pos.qty * pos.entry_price + qty * price
                        total_qty = pos.qty + qty
                        open_positions[symbol] = OpenPosition(
                            symbol=symbol,
                            entry_time=pos.entry_time,  # Keep original entry time
                            entry_price=total_cost / total_qty,
                            qty=total_qty,
                            side="SELL"
                        )
                    else:
                        open_positions[symbol] = OpenPosition(
                            symbol=symbol,
                            entry_time=timestamp,
                            entry_price=price,
                            qty=qty,
                            side="SELL"
                        )
        
        return trades