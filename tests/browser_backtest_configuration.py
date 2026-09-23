"""Optional browser integration smoke test with mocked historical candles.

Run with the project's interpreter that has Playwright and Chromium installed.
No live broker authentication or orders are performed.
"""
import sys
from pathlib import Path
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright
import web_app
from backtest import BacktestEngine
from core.models import Candle
from utils.timezone import IST


def main():
    engines, payloads = [], []

    def make_engine(config, data_provider):
        engine = BacktestEngine(config, data_provider)
        engines.append(engine)
        return engine

    candle = Candle(instrument='NSE:NIFTY', timestamp=datetime(2026, 9, 1, tzinfo=IST),
                    open=100, high=101, low=99, close=100, volume=1000, timeframe='1d')
    with sync_playwright() as p, \
            patch('market_data.breeze_data_provider.BreezeHistoricalDataProvider') as provider, \
            patch.object(web_app, 'BacktestEngine', side_effect=make_engine):
        provider.return_value.get_historical_candles.return_value = [candle]
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))

        def route(r):
            path = r.request.url.split('http://backtest.local')[-1]
            if path == '/':
                r.fulfill(content_type='text/html', body=web_app.DASHBOARD_HTML)
            elif path == '/api/catalog':
                r.fulfill(json=web_app.get_strategy_catalog())
            elif path == '/api/universe':
                # Populates window.GLOBAL_UNIVERSE for strategies that use the
                # global universe (e.g. lorentzian_ml) in the mocked page.
                r.fulfill(json={'universe': web_app.GLOBAL_UNIVERSE})
            elif path == '/api/backtest':
                payload = r.request.post_data_json
                payloads.append(payload)
                r.fulfill(json=web_app.run_backtest_api(web_app.BacktestRequest(**payload)))
            elif 'backtest.local' in r.request.url:
                r.fulfill(json={})
            else:
                r.continue_()

        page.route('**/*', route)
        page.goto('http://backtest.local/', wait_until='domcontentloaded')
        page.wait_for_function("typeof catalog !== 'undefined' && catalog.length > 0")
        for sid in web_app.STRATEGY_CATALOG:
            page.evaluate('(id)=>openBacktestModal(id)', sid)
            assert page.locator('#modal-capital').is_visible(), sid
            assert page.locator('#param-capital').count() == 0, sid
        page.evaluate("openBacktestModal('lorentzian_ml')")
        page.locator('#modal-capital').fill('750000')
        page.locator('#modal-start-date').fill('2026-09-01')
        page.locator('#modal-end-date').fill('2026-09-02')
        page.locator('#bollinger-enabled').check()
        page.evaluate("""document.querySelectorAll('input[name="symbol-checkbox"]').forEach(
            c=>c.checked=c.value==='NSE:NIFTY'); updateSelectedSymbolsLabel()""")
        page.locator('#modal-run-btn').click()
        page.wait_for_function("document.getElementById('m-capital-label').textContent.includes('7,50,000')")
        payload, engine = payloads[-1], engines[-1]
        assert payload['capital'] == 750000 and 'capital' not in payload['params']
        expected = dict(bollinger_enabled=True, bollinger_length=19, bollinger_mult=2.36,
                        bollinger_offset=0, bollinger_ma_type='WMA')
        assert all(payload['params'][k] == v for k, v in expected.items())
        assert engine.config.initial_capital == engine._strategy.params['capital'] == 750000
        print('PASS: all 7 modals have one capital field; browser POST and engine use 750000')
        print('UI:', page.locator('#m-capital-label').inner_text())
        print('Bollinger parameters submitted:', expected)
        print('Existing adapter applies Bollinger:', engine._strategy.settings.use_bollinger_bands)
        print('Browser errors:', errors)
        browser.close()


if __name__ == '__main__':
    main()
