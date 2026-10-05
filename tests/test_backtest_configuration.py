"""Shared backtest configuration regressions; no network or broker login required."""
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import ValidationError

import web_app
from backtest import BacktestEngine
from core.models import Candle
from core.strategy import StrategyBase, StrategyRegistry
from utils.timezone import IST


class CapitalAwareStub(StrategyBase):
    """Minimal registry strategy: records the capital the engine hands it and never trades."""

    def _init_indicators(self):
        self.capital = float(self.params.get("capital", 0))

    def on_tick(self, tick):
        return None

    def on_candle(self, candle):
        return None


class BacktestConfigurationTests(unittest.TestCase):
    def setUp(self):
        StrategyRegistry.register("capital_stub", CapitalAwareStub)
        self.addCleanup(StrategyRegistry._strategies.pop, "capital_stub", None)

    def test_catalog_lists_the_scalpers_and_index_oi_momentum(self):
        from scalp_strategies import SCALP_STRATEGIES
        self.assertEqual(set(web_app.STRATEGY_CATALOG), set(SCALP_STRATEGIES) | {"index_oi_momentum"})
        for sid in SCALP_STRATEGIES:
            self.assertTrue(web_app.STRATEGY_CATALOG[sid]["scalper"])

    def test_registry_serves_the_trading_strategies_package(self):
        self.assertIn("index_oi_momentum", StrategyRegistry.list_strategies())

    def test_catalog_has_only_strategy_specific_parameters(self):
        for strategy in web_app.STRATEGY_CATALOG.values():
            with self.subTest(strategy=strategy["id"]):
                keys = {p["key"] for p in strategy["param_schema"]}
                self.assertTrue(keys.isdisjoint({"capital", "start_date", "end_date", "instrument"}))
                self.assertGreater(strategy["default_capital"], 0)

    def test_capital_must_be_positive_and_finite(self):
        for capital in (0, -1, float("inf"), float("nan")):
            with self.subTest(capital=capital), self.assertRaises(ValidationError):
                web_app.BacktestRequest(strategy_id="capital_stub", instrument="NSE:NIFTY", capital=capital)

    def test_shared_capital_reaches_engine_and_strategy(self):
        instances = []

        def create_engine(config, data_provider):
            engine = BacktestEngine(config, data_provider)
            instances.append(engine)
            return engine

        params = {"capital": 123, "quantity": 1}
        request = web_app.BacktestRequest(
            strategy_id="capital_stub", instrument="NSE:NIFTY", capital=750000,
            start_date="2026-09-01", end_date="2026-09-02", params=params)
        candle = Candle(instrument="NSE:NIFTY", timestamp=datetime(2026, 9, 1, tzinfo=IST),
                        open=100, high=101, low=99, close=100, volume=1000, timeframe="1d")
        with patch.object(web_app.ProviderFactory, "get") as get_provider, \
                patch.object(web_app, "BacktestEngine", side_effect=create_engine):
            get_provider.return_value.get_historical_candles.return_value = [candle]
            result = web_app.run_backtest_api(request)
        engine = instances[0]
        self.assertIsNone(result["error"])
        self.assertEqual(result["initial_capital"], 750000)
        self.assertEqual(engine.config.initial_capital, 750000)
        self.assertEqual(engine._strategy.params["capital"], 750000)
        self.assertEqual(engine._strategy.capital, 750000)
        self.assertEqual(request.params["capital"], 123)  # request not mutated
        self.assertEqual(engine.config.instruments, ["NSE:NIFTY"])
        self.assertEqual(engine.config.start_date.date().isoformat(), "2026-09-01")
        self.assertEqual(engine.config.end_date.date().isoformat(), "2026-09-02")


if __name__ == "__main__":
    unittest.main()
