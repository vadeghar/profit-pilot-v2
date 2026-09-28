"""NIFTY No Brainer monthly call spread.

This module deliberately keeps market-data and broker concerns behind small
protocols.  The strategy can therefore be backtested with a deterministic
option-price feed or run live with adapters around the existing broker layer.
It never invents an option symbol: Angel resolution is delegated to
``market_data.option_symbol``.
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Protocol, Sequence
from zoneinfo import ZoneInfo

from market_data.option_symbol import get_option_symbol
from core.models import Candle, Tick, BacktestResult, BacktestStatus, Trade, TradeStatus, OrderSide
from strategies import StrategyBase
from market_data.normalize import ensure_normalized_candles
from platform_config import get_index_lot_size
from strategies.nifty_no_brainer_reference import (
    EntryDecision, Strikes, atm_from_spot, hedge_strike, select_strikes,
)

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_CAPITAL = 1_000_000.0
DEFAULT_LOT_SIZE = get_index_lot_size("NSE:NIFTY", 1)
HOLIDAY_FILE = Path(__file__).resolve().parents[1] / "nse_holidays.json"


def _as_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.astimezone(IST).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


class CalendarEngine:
    """NSE calendar with holiday/weekend backward adjustment."""

    def __init__(self, holidays: Iterable[date | str] = (), holiday_file: str | Path = HOLIDAY_FILE):
        self.holidays = {_as_date(d) for d in holidays}
        path = Path(holiday_file)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            values = data.get("holidays", data) if isinstance(data, dict) else data
            self.holidays.update(_as_date(d["date"] if isinstance(d, dict) else d) for d in values)

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays

    def previous_valid_day(self, day: date) -> date:
        while not self.is_trading_day(day):
            day -= timedelta(days=1)
        return day

    def get_monthly_expiry(self, year: int, month: int,
                           contract_master: Optional[Iterable[Mapping[str, Any]]] = None) -> date:
        if contract_master is not None:
            rows = [r for r in contract_master
                    if str(r.get("underlying", "NIFTY")).upper() == "NIFTY"
                    and str(r.get("expiry_type", "MONTHLY")).upper() == "MONTHLY"
                    and str(r.get("expiry", "")).startswith(f"{year:04d}-{month:02d}")]
            if not rows:
                raise ValueError("monthly NIFTY expiry missing from contract master")
            return min(_as_date(r["expiry"]) for r in rows)
        if year < 2024 or (year == 2024 and month < 1):
            raise ValueError("historical NIFTY expiry requires a contract master")
        # NSE changed NIFTY monthly expiry from Thursday to Tuesday beginning
        # September 2025.  Holidays are applied after selecting the weekday.
        expiry_weekday = 3 if (year, month) < (2025, 9) else 1
        if month == 12:
            day = date(year, 12, 31)
        else:
            day = date(year, month + 1, 1) - timedelta(days=1)
        day -= timedelta(days=(day.weekday() - expiry_weekday) % 7)
        return self.previous_valid_day(day)

    def get_entry_date(self, year: int, month: int) -> date:
        if month == 12:
            day = date(year, 12, 31)
        else:
            day = date(year, month + 1, 1) - timedelta(days=1)
        day -= timedelta(days=(day.weekday() - 4) % 7)  # Friday
        return self.previous_valid_day(day)

    def next_month(self, year: int, month: int) -> tuple[int, int]:
        return (year + 1, 1) if month == 12 else (year, month + 1)


def get_monthly_expiry(year: int, month: int, holidays: Iterable[date | str] = ()) -> date:
    return CalendarEngine(holidays=holidays).get_monthly_expiry(year, month)


def get_entry_date(year: int, month: int, holidays: Iterable[date | str] = ()) -> date:
    return CalendarEngine(holidays=holidays).get_entry_date(year, month)


def round_to_50(spot: float) -> int:
    """Compatibility name retained for callers; strategy selection is 100-grid."""
    return atm_from_spot(spot)


@dataclass(frozen=True)
class Leg:
    name: str
    strike: int
    lots: int
    side: str
    symbol: Any
    entry_price: float

    @property
    def quantity(self) -> int:
        return self.lots


@dataclass
class TradeLog:
    entry_date: str
    entry_time: str
    expiry: str
    spot: float
    strikes: Dict[str, int]
    leg_prices: Dict[str, float]
    net_debit: float
    sets: int
    exit_reason: str = ""
    exit_date: str = ""
    exit_time: str = ""
    realized_pnl: float = 0.0
    skipped: bool = False


class PriceFeed(Protocol):
    def option_price(self, symbol: Any, at: datetime) -> float: ...


class ExecutionAdapter(Protocol):
    def buy(self, symbol: Any, quantity: int, at: datetime) -> float: ...
    def sell(self, symbol: Any, quantity: int, at: datetime) -> float: ...


class OrderManager:
    def __init__(self, broker: str = "angel", symbol_kwargs: Optional[Mapping[str, Any]] = None):
        self.broker, self.symbol_kwargs = broker, dict(symbol_kwargs or {})

    def resolve_legs(self, expiry: date, spot: float, prices: Mapping[str, float], sets: int = 1,
                     near_offset: int = 300) -> list[Leg]:
        selected = select_strikes(spot, near_offset=near_offset)
        specs = (("leg1", selected.near_buy, 1, "BUY"),
                 ("leg2", selected.sell, 2, "SELL"),
                 ("leg3", selected.hedge, 1, "BUY"))
        legs = []
        for name, strike, lots, side in specs:
            symbol = get_option_symbol(self.broker, "NIFTY", expiry.isoformat(), strike, "CE", **self.symbol_kwargs)
            legs.append(Leg(name, strike, lots * sets, side, symbol, float(prices[name])))
        return legs

    def execute_entry(self, legs: Sequence[Leg], adapter: ExecutionAdapter, at: datetime) -> list[float]:
        return [(adapter.buy if leg.side == "BUY" else adapter.sell)(leg.symbol, leg.lots, at) for leg in legs]

    def execute_exit(self, legs: Sequence[Leg], adapter: ExecutionAdapter, at: datetime) -> list[float]:
        return [(adapter.sell if leg.side == "BUY" else adapter.buy)(leg.symbol, leg.lots, at) for leg in legs]


class RiskEngine:
    def __init__(self, capital: float = DEFAULT_CAPITAL, lot_size: int = DEFAULT_LOT_SIZE):
        if capital <= 0 or lot_size <= 0:
            raise ValueError("capital and lot_size must be positive")
        self.capital, self.lot_size = float(capital), int(lot_size)

    def margin_per_set(self, net_debit: float) -> float:
        """Conservative broker-style margin estimate for the complete 1:-2:1.

        A live broker's exact SPAN/exposure margin varies with volatility and
        is not available from the repository's broker abstraction.  The worst
        expiry loss of this structure is 700 points before debit (the upper
        long is 1,000 points above the short and the lower long is 300 points
        above the short).  Including the debit and one lot gives a safe,
        deterministic upper bound for backtests and sizing.
        """
        return (700.0 + max(float(net_debit), 0.0)) * self.lot_size

    def sets_for(self, net_debit: float) -> int:
        # A zero/negative result is a credit or zero-cost entry.  It is not
        # rejected by the stated 1% risk rule; use one set because sizing a
        # credit spread from a debit budget would otherwise be undefined.
        return max(1, math.floor(self.capital / self.margin_per_set(net_debit)))

    def entry_allowed(self, net_debit: float, sets: int) -> bool:
        return sets > 0 and net_debit * self.lot_size * sets <= self.capital * 0.01 + 1e-9 and self.margin_per_set(net_debit) * sets <= self.capital + 1e-9

    def exit_reason(self, mtm: float, entry_at: datetime, now: datetime, expiry: date,
                    max_hold_days: int = 18) -> Optional[str]:
        if mtm >= self.capital * 0.025: return "Target"
        if mtm <= -self.capital * 0.03: return "SL"
        if now.date() >= entry_at.date() + timedelta(days=max_hold_days) and now.timetz().replace(tzinfo=None) >= time(15, 16): return "Max-Hold Exit"
        if now.date() >= expiry and now.timetz().replace(tzinfo=None) >= time(15, 16): return "Expiry"
        return None


class NiftyNoBrainer:
    """Monthly lifecycle coordinator; prices are option premiums per unit."""
    def __init__(self, capital: float = DEFAULT_CAPITAL, lot_size: int = DEFAULT_LOT_SIZE,
                 broker: str = "angel", holidays: Iterable[date | str] = (),
                 symbol_kwargs: Optional[Mapping[str, Any]] = None,
                 max_shift_steps: Optional[int] = None,
                 hedge_recalc_on_shift: bool = True,
                 max_hold_days: int = 18,
                 margin_method: str = "broker_margin_required"):
        self.calendar = CalendarEngine(holidays)
        self.risk = RiskEngine(capital, lot_size)
        self.orders = OrderManager(broker, symbol_kwargs)
        self.trade: Optional[TradeLog] = None
        self.legs: list[Leg] = []
        self.entry_at: Optional[datetime] = None
        self.max_shift_steps = max_shift_steps
        self.hedge_recalc_on_shift = hedge_recalc_on_shift
        self.max_hold_days = max_hold_days
        self.margin_method = margin_method

    def enter(self, at: datetime, spot: float, prices: Mapping[str, float]) -> Optional[TradeLog]:
        at = at.astimezone(IST) if at.tzinfo else at.replace(tzinfo=IST)
        y, m = self.calendar.next_month(at.year, at.month)
        expiry = self.calendar.get_monthly_expiry(y, m)
        selected = select_strikes(spot)
        debit = float(prices["leg1"]) + float(prices["leg3"]) - 2 * float(prices["leg2"])
        sets = self.risk.sets_for(debit)
        log = TradeLog(at.date().isoformat(), at.isoformat(), expiry.isoformat(), float(spot),
                       {"leg1": selected.near_buy, "leg2": selected.sell, "leg3": selected.hedge},
                       {k: float(prices[k]) for k in ("leg1", "leg2", "leg3")}, debit, sets)
        if not self.risk.entry_allowed(debit, sets):
            log.skipped = True
            self.trade = log
            return None
        self.legs = self.orders.resolve_legs(expiry, spot, prices, sets)
        self.trade, self.entry_at = log, at
        return log

    def mark_to_market(self, prices: Mapping[str, float]) -> float:
        if not self.legs: return 0.0
        return sum((1 if l.side == "BUY" else -1) * (float(prices[l.name]) - l.entry_price) * l.lots * self.risk.lot_size for l in self.legs)

    def should_exit(self, now: datetime, prices: Mapping[str, float]) -> Optional[str]:
        if not self.trade or not self.entry_at: return None
        return self.risk.exit_reason(self.mark_to_market(prices), self.entry_at, now.astimezone(IST),
                                     _as_date(self.trade.expiry), self.max_hold_days)

    def close(self, now: datetime, prices: Mapping[str, float], reason: str) -> TradeLog:
        if not self.trade or not self.legs: raise RuntimeError("No open trade")
        self.trade.exit_reason, self.trade.exit_date, self.trade.exit_time = reason, now.date().isoformat(), now.isoformat()
        self.trade.realized_pnl = self.mark_to_market(prices)
        self.legs = []
        return self.trade

    def write_log(self, path: str | Path) -> None:
        if not self.trade: return
        p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
        fields = list(asdict(self.trade))
        with p.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            if fh.tell() == 0: writer.writeheader()
            writer.writerow(asdict(self.trade))


class NiftyNoBrainerStrategy(StrategyBase):
    """Strategy-registry adapter for Strategy Studio/backtest discovery.

    The complete multi-leg lifecycle is provided by :class:`NiftyNoBrainer`.
    The platform's legacy ``BacktestEngine`` supplies one underlying candle at
    a time and has no synchronized option-chain feed, so this adapter records
    the candle stream without fabricating option premiums or signals.  Live
    and dedicated multi-leg backtests should call ``NiftyNoBrainer`` directly
    with a three-leg option-price feed.
    """

    def _init_indicators(self) -> None:
        self.timeframe = self.params.get("timeframe", "1m")
        self.candles_seen = 0
        self.store_indicator("mode", "multi_leg_option_lifecycle")

    def on_tick(self, tick: Tick):
        return None

    def on_candle(self, candle: Candle):
        self.candles_seen += 1
        self.store_indicator("candles_seen", self.candles_seen)
        return None


def run_nifty_no_brainer_backtest(provider: Any, start: datetime, end: datetime,
                                  timeframe: str, capital: float, lot_size: int,
                                  event_callback=None, broker: str = "angel") -> BacktestResult:
    """Run the real month-by-month multi-leg historical backtest.

    The generic engine cannot load dynamic option symbols, so this runner loads
    spot data first, resolves each month's three contracts, then aligns all
    four candle streams by timestamp and evaluates the strategy on candle
    closes. Missing option candles cause that month to be skipped explicitly;
    no synthetic prices are created.
    """
    calendar = CalendarEngine()
    risk = RiskEngine(capital, lot_size)
    trades, equity = [], []
    months = []
    cursor = date(start.year, start.month, 1)
    while cursor <= end.date():
        months.append(cursor)
        cursor = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)
    def skip(month: date, reason: str) -> None:
        if event_callback:
            event_callback("nifty_month_skipped", {"month": month.strftime("%Y-%m"), "reason": reason})

    spot_rows = provider.get_historical_candles("NSE:NIFTY", timeframe, start, end)
    spot = ensure_normalized_candles(spot_rows, context="NIFTY spot")
    spot_by_time = {c.timestamp: c for c in spot}
    for month in months:
        entry_day = calendar.get_entry_date(month.year, month.month)
        if not (start.date() <= entry_day <= end.date()):
            continue
        entry_candidates = sorted(
            ((ts, c) for ts, c in spot_by_time.items()
             if ts.date() == entry_day and ts.hour * 60 + ts.minute >= 15 * 60 + 16),
            key=lambda item: item[0],
        )
        if entry_candidates:
            entry_ts_candidate, entry_spot = entry_candidates[0]
        else:
            # R3 fallback: if the spot feed has no candle after 15:16, use the
            # last available spot close of that trading day, rather than
            # silently dropping the month.
            day_candidates = sorted((ts, c) for ts, c in spot_by_time.items()
                                    if ts.date() == entry_day)
            if not day_candidates:
                skip(month, f"No NIFTY spot candle on entry day {entry_day}")
                continue
            entry_ts_candidate, entry_spot = day_candidates[-1]
        y, m = calendar.next_month(month.year, month.month)
        expiry = calendar.get_monthly_expiry(y, m)
        selected = select_strikes(entry_spot.close)
        strikes = {"leg1": selected.near_buy, "leg2": selected.sell, "leg3": selected.hedge}
        symbols = {}
        try:
            for name, strike in strikes.items():
                resolved = get_option_symbol(broker, "NIFTY", expiry.isoformat(), strike, "CE")
                symbols[name] = resolved.symbol if hasattr(resolved, "symbol") else resolved
            streams = {name: ensure_normalized_candles(
                provider.get_historical_candles(symbol, timeframe, start, end),
                context=f"{name} option") for name, symbol in symbols.items()}
        except Exception as exc:
            skip(month, f"Option symbol/data load failed for expiry {expiry}: {exc}")
            continue
        by_time = {name: {c.timestamp: c for c in rows} for name, rows in streams.items()}
        common = sorted(set.intersection(*(set(v) for v in by_time.values())))
        common = [ts for ts in common if ts >= entry_spot.timestamp and ts.date() <= expiry]
        if not common:
            skip(month, f"No timestamp common to all three option legs through expiry {expiry}")
            continue
        prices = {name: by_time[name][entry_ts_candidate].close for name in by_time if entry_ts_candidate in by_time[name]}
        if len(prices) != 3:
            # Use the first aligned candle at/after the fixed entry time.
            first = next((ts for ts in common if ts.date() == entry_day and ts >= entry_ts_candidate), None)
            if first is None:
                skip(month, "Option candles do not contain an aligned entry candle at/after spot entry timestamp")
                continue
            entry_ts = first
            prices = {name: by_time[name][entry_ts].close for name in by_time}
        else:
            entry_ts = entry_spot.timestamp
        debit = prices["leg1"] + prices["leg3"] - 2 * prices["leg2"]
        sets = risk.sets_for(debit)
        if not risk.entry_allowed(debit, sets):
            skip(month, f"Net debit risk filter rejected entry: debit={debit:.2f}, sets={sets}")
            continue
        lifecycle = NiftyNoBrainer(capital, lot_size, broker=broker)
        lifecycle.enter(entry_ts, entry_spot.close, prices)
        reason = None; exit_ts = entry_ts; exit_prices = prices
        for ts in common:
            if ts <= entry_ts: continue
            current = {name: by_time[name][ts].close for name in by_time}
            equity.append({"timestamp": ts.isoformat(), "value": capital + lifecycle.mark_to_market(current)})
            reason = lifecycle.should_exit(ts, current)
            if reason:
                exit_ts, exit_prices = ts, current
                break
        if not reason:
            reason, exit_ts = "Expiry", common[-1]
            exit_prices = {name: by_time[name][exit_ts].close for name in by_time}
        log = lifecycle.close(exit_ts, exit_prices, reason)
        trades.append(Trade(trade_id=f"NBN-{entry_ts:%Y%m}", strategy_id="nifty_no_brainer",
                            instrument="NIFTY-MONTHLY-1:-2:1", quantity=sets,
                            entry_price=debit, exit_price=0.0, status=TradeStatus.CLOSED,
                            pnl=log.realized_pnl, entry_time=entry_ts, exit_time=exit_ts,
                            side=OrderSide.BUY, metadata={"legs": log.strikes, "exit_reason": reason}))
        if event_callback:
            event_callback("trade", trades[-1].to_dict())
    pnl = sum(t.pnl for t in trades)
    return BacktestResult(strategy_id="nifty_no_brainer", start_time=start, end_time=end,
                          initial_capital=capital, final_capital=capital + pnl,
                          total_return=pnl, total_return_pct=pnl / capital * 100,
                          max_drawdown=0, sharpe_ratio=0, total_trades=len(trades),
                          winning_trades=sum(t.pnl > 0 for t in trades),
                          losing_trades=sum(t.pnl <= 0 for t in trades),
                          win_rate=(sum(t.pnl > 0 for t in trades) / len(trades) * 100) if trades else 0,
                          avg_win=0, avg_loss=0, profit_factor=0,
                          status=BacktestStatus.COMPLETED, trades=trades, equity_curve=equity)

__all__ = ["CalendarEngine", "OrderManager", "RiskEngine", "NiftyNoBrainer", "NiftyNoBrainerStrategy", "TradeLog", "get_monthly_expiry", "get_entry_date", "round_to_50"]