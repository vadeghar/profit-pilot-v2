"""Shared backtest configuration regressions; no network or broker login required."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import ValidationError
import web_app
from backtest import BacktestEngine
from core.models import Candle
from utils.timezone import IST
from datetime import datetime


class BacktestConfigurationTests(unittest.TestCase):
    def test_catalog_has_only_strategy_specific_parameters(self):
        for strategy in web_app.STRATEGY_CATALOG.values():
            with self.subTest(strategy=strategy['id']):
                keys = {p['key'] for p in strategy['param_schema']}
                self.assertTrue(keys.isdisjoint({'capital', 'start_date', 'end_date', 'instrument'}))
                self.assertGreater(strategy['default_capital'], 0)

    def test_capital_must_be_positive_and_finite(self):
        for capital in (0, -1, float('inf'), float('nan')):
            with self.subTest(capital=capital), self.assertRaises(ValidationError):
                web_app.BacktestRequest(strategy_id='lorentzian_ml', instrument='NSE:NIFTY', capital=capital)

    def test_shared_capital_reaches_engine_and_strategy(self):
        for strategy_id in ('lorentzian_ml', 'equity_swing_vcp', 'mcx_trend_rider'):
            with self.subTest(strategy_id=strategy_id):
                instances = []

                def create_engine(config, data_provider):
                    engine = BacktestEngine(config, data_provider)
                    instances.append(engine)
                    return engine

                params = {'capital': 123, 'quantity': 1, 'bollinger_enabled': True}
                request = web_app.BacktestRequest(
                    strategy_id=strategy_id, instrument='NSE:NIFTY', capital=750000,
                    start_date='2026-09-01', end_date='2026-09-02', params=params)
                candle = Candle(instrument='NSE:NIFTY', timestamp=datetime(2026, 9, 1, tzinfo=IST),
                                open=100, high=101, low=99, close=100, volume=1000, timeframe='1d')
                with patch('market_data.breeze_data_provider.BreezeHistoricalDataProvider') as provider, \
                        patch.object(web_app, 'BacktestEngine', side_effect=create_engine):
                    provider.return_value.get_historical_candles.return_value = [candle]
                    result = web_app.run_backtest_api(request)
                engine = instances[0]
                self.assertIsNone(result['error'])
                self.assertEqual(result['initial_capital'], 750000)
                self.assertEqual(engine.config.initial_capital, 750000)
                self.assertEqual(engine._strategy.params['capital'], 750000)
                if strategy_id != 'lorentzian_ml':
                    self.assertEqual(engine._strategy.capital, 750000)
                self.assertEqual(request.params['capital'], 123)  # request not mutated
                self.assertEqual(engine.config.instruments, ['NSE:NIFTY'])
                self.assertEqual(engine.config.start_date.date().isoformat(), '2026-09-01')
                self.assertEqual(engine.config.end_date.date().isoformat(), '2026-09-02')


if __name__ == '__main__':
    unittest.main()
