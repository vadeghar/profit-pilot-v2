#!/usr/bin/env python3
"""Headless-browser data-provider connectivity test for the streaming backtest.

Drives the REAL FastAPI dashboard (uvicorn, started in-process) with a headless
Chromium: for every entry of the modal's "Data Provider" dropdown
(``yfinance`` / ``breeze`` / ``angel``) it wipes the historical candle cache,
opens the strategy card, selects the provider, clicks **RUN BACKTEST** and
verifies that

  * the streaming SSE backtest completed (no ``backtest_failed`` event),
  * the provider really fetched live data (cache was empty before the click,
    the provider's cache file exists afterwards),
  * every batch handed to the engine is a ``market_data.normalize.NormalizedCandle``
    with the right provider/instrument/timeframe, IST tz-aware + sorted unique
    timestamps, provenance (source_symbol) and sane OHLCV,
  * the engine actually consumed them (``candles_evaluated`` > 0).

It also opens every strategy card modal to prove the shared backtest form
still renders for all registered strategies.

Usage:
    python tests/browser_data_provider_connectivity.py
    python tests/browser_data_provider_connectivity.py --providers breeze,angel
    python tests/browser_data_provider_connectivity.py --no-cache-wipe
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import platform_config  # noqa: E402
from market_data.normalize import NormalizedCandle  # noqa: E402
from utils.timezone import IST  # noqa: E402

# ---------------------------------------------------------------------------
# Scenarios — one per option of the modal's "Data Provider" dropdown.
# NSE:NIFTY is the single instrument all three providers serve natively.
# ---------------------------------------------------------------------------
SCENARIOS = {
    "yfinance": {
        "strategy_id": "ema_crossover",
        "instrument": "NSE:NIFTY",
        "timeframe": "1d",
        "start": "2026-08-01",
        "end": "2026-09-16",
        "cache_file": "_NSEI_1d.csv",
    },
    "breeze": {
        "strategy_id": "ema_crossover",
        "instrument": "NSE:NIFTY",
        "timeframe": "1d",
        "start": "2026-08-01",
        "end": "2026-09-16",
        "cache_file": "NSE_NIFTY_1d.json",
    },
    "angel": {
        "strategy_id": "ema_crossover",
        "instrument": "NSE:NIFTY",
        "timeframe": "1d",
        "start": "2026-08-01",
        "end": "2026-09-16",
        "cache_file": "NSE_NIFTY_1d.json",
    },
}
PROVIDER_ORDER = ["yfinance", "breeze", "angel"]


# ---------------------------------------------------------------------------
# Cache handling
# ---------------------------------------------------------------------------

def wipe_historical_cache(verbose: bool = True) -> int:
    """Delete every cached candle file under data/historical (recursively)."""
    base = Path(platform_config.HISTORICAL_DATA_DIR)
    removed = 0
    if base.exists():
        for path in sorted(base.rglob("*")):
            if path.is_file():
                path.unlink()
                removed += 1
    if verbose:
        print(f"  cache: wiped {removed} file(s) from {base}")
    return removed


def cache_files_present() -> set[str]:
    base = Path(platform_config.HISTORICAL_DATA_DIR)
    if not base.exists():
        return set()
    return {p.name for p in base.rglob("*") if p.is_file()}


# ---------------------------------------------------------------------------
# In-process instrumentation (the dashboard runs inside this process)
# ---------------------------------------------------------------------------

def install_instrumentation():
    """Record every provider candle batch + every job event emitted by the app."""
    import web_app
    from market_data.factory import ProviderFactory

    calls: list[dict] = []
    events: dict[str, list[tuple[str, dict]]] = {}

    real_get = ProviderFactory.get

    class _RecordingProvider:
        """Thin delegating proxy that captures the normalized candle batches."""

        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, item):
            return getattr(self._inner, item)

        def get_historical_candles(self, instrument, timeframe,
                                   start_date=None, end_date=None):
            candles = self._inner.get_historical_candles(
                instrument, timeframe, start_date, end_date)
            calls.append({
                "provider": getattr(self._inner, "name", "?"),
                "instrument": instrument,
                "timeframe": timeframe,
                "candles": list(candles),
            })
            return candles

    def recording_get(provider_name, **kwargs):
        return _RecordingProvider(real_get(provider_name, **kwargs))

    ProviderFactory.get = staticmethod(recording_get)

    real_add_event = web_app.job_manager.add_event

    def recording_add_event(job_id, event_type, payload):
        events.setdefault(job_id, []).append((event_type, payload))
        return real_add_event(job_id, event_type, payload)

    web_app.job_manager.add_event = recording_add_event
    return calls, events


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_server(port: int):
    """Start the real dashboard (uvicorn) on a background thread."""
    import uvicorn

    import web_app

    config = uvicorn.Config(web_app.app, host="127.0.0.1", port=port,
                            log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True,
                              name="uvicorn-dashboard")
    thread.start()
    deadline = time.time() + 30
    while not server.started and time.time() < deadline:
        time.sleep(0.1)
    if not server.started:
        raise RuntimeError("uvicorn dashboard failed to start")
    return server


# ---------------------------------------------------------------------------
# Normalization contract checks
# ---------------------------------------------------------------------------

def check_normalization(candles: list, scenario: dict, provider: str) -> list[str]:
    """Return a list of normalization problems (empty list == fully compliant)."""
    problems: list[str] = []
    if not candles:
        return ["provider returned no candles"]

    expected_offset = IST.utcoffset(None)
    for idx, candle in enumerate(candles):
        where = f"candle[{idx}]"
        if not isinstance(candle, NormalizedCandle):
            problems.append(f"{where} is {type(candle).__name__}, not NormalizedCandle")
            continue
        if candle.provider != provider:
            problems.append(f"{where}.provider={candle.provider!r} != {provider!r}")
        if candle.instrument != scenario["instrument"]:
            problems.append(f"{where}.instrument={candle.instrument!r} "
                            f"!= {scenario['instrument']!r}")
        if candle.timeframe != scenario["timeframe"]:
            problems.append(f"{where}.timeframe={candle.timeframe!r} "
                            f"!= {scenario['timeframe']!r}")
        if candle.timestamp.tzinfo is None:
            problems.append(f"{where}.timestamp is tz-naive")
        elif candle.timestamp.utcoffset() != expected_offset:
            problems.append(f"{where}.timestamp offset "
                            f"{candle.timestamp.utcoffset()} is not IST")
        if not candle.source_symbol:
            problems.append(f"{where}.source_symbol is empty")
        ohlc = (candle.open, candle.high, candle.low, candle.close)
        if min(ohlc) <= 0:
            problems.append(f"{where} has a non-positive price: {ohlc}")
        if candle.high < max(candle.open, candle.close) or \
                candle.low > min(candle.open, candle.close):
            problems.append(f"{where} inconsistent OHLC "
                            f"O={candle.open} H={candle.high} "
                            f"L={candle.low} C={candle.close}")

    stamps = [c.timestamp for c in candles if isinstance(c, NormalizedCandle)]
    if stamps != sorted(stamps):
        problems.append("timestamps are not sorted ascending")
    if len(set(stamps)) != len(stamps):
        problems.append("duplicate timestamps present")
    return problems


# ---------------------------------------------------------------------------
# Browser flow — real clicks on the dashboard
# ---------------------------------------------------------------------------

def fetch_catalog(page, base_url: str) -> dict:
    page.goto(base_url + "/", wait_until="domcontentloaded")
    page.wait_for_function(
        "typeof catalog !== 'undefined' && catalog.length > 0", timeout=30000)
    return {s["id"]: s for s in page.evaluate("catalog")}


def open_strategy_card(page, strategy_name: str) -> None:
    """Click the strategy card (same entry point a user clicks)."""
    page.locator(
        f"#strategy-cards-grid > div:has(h3:text-is('{strategy_name}'))"
    ).first.click()
    page.wait_for_selector("#backtest-modal:not(.hidden)", timeout=15000)


def run_provider_backtest(page, base_url, provider, scenario, catalog,
                          events, calls, timeout_ms):
    """Select `provider` in the modal, click RUN BACKTEST, collect the evidence."""
    strategy_name = catalog[scenario["strategy_id"]]["name"]
    jobs_before = set(events)
    calls_before = len(calls)
    cache_before = sorted(cache_files_present())

    page.goto(base_url + "/", wait_until="domcontentloaded")
    page.wait_for_function(
        "typeof catalog !== 'undefined' && catalog.length > 0", timeout=30000)
    open_strategy_card(page, strategy_name)

    page.select_option("#modal-data-provider", provider)
    page.fill("#modal-start-date", scenario["start"])
    page.fill("#modal-end-date", scenario["end"])
    page.evaluate(
        """(sym) => {
            document.querySelectorAll('input[name="symbol-checkbox"]').forEach(c => {
                c.checked = (c.value === sym);
            });
            updateSelectedSymbolsLabel();
        }""",
        scenario["instrument"],
    )
    print(f"   modal: strategy={strategy_name!r} provider={provider} "
          f"symbols={page.locator('#modal-symbols-label').inner_text()!r} "
          f"range={scenario['start']}..{scenario['end']}")
    print("   clicking 'RUN BACKTEST' ...")

    page.click("#modal-run-btn")
    page.wait_for_function(
        """() => {
            const label = document.getElementById('modal-progress-label');
            const banner = document.getElementById('modal-error-banner');
            const done = !!(label && label.textContent.includes('Backtest completed'));
            const failed = !!(banner && !banner.classList.contains('hidden'));
            return done || failed;
        }""",
        timeout=timeout_ms,
    )

    # gather the SSE events of the job started by this click
    job_id, job_events = None, []
    deadline = time.time() + 15
    while time.time() < deadline:
        new_jobs = [j for j in events if j not in jobs_before]
        if new_jobs:
            job_id = new_jobs[0]
            job_events = events[job_id]
            if any(ev in ("backtest_completed", "backtest_failed")
                   for ev, _ in job_events):
                break
        time.sleep(0.25)

    return {
        "provider": provider,
        "strategy": strategy_name,
        "instrument": scenario["instrument"],
        "progress_label": page.locator("#modal-progress-label").inner_text(),
        "progress_pct": page.locator("#modal-progress-pct").inner_text(),
        "return_pct": page.locator("#m-return-pct").inner_text(),
        "error_banner_visible": page.evaluate(
            "() => !document.getElementById('modal-error-banner')"
            ".classList.contains('hidden')"),
        "error_banner_text": page.locator("#modal-error-msg").inner_text(),
        "event_types": [ev for ev, _ in job_events],
        "job_events": job_events,
        "provider_calls": calls[calls_before:],
        "cache_before": cache_before,
        "cache_after": sorted(cache_files_present()),
    }


REQUIRED_EVENTS = ["backtest_started", "candles_loaded", "progress",
                   "metrics_update", "backtest_completed"]


def evaluate_scenario(outcome: dict, scenario: dict) -> tuple[list[str], int]:
    """Streaming + normalization contract checks for one provider run."""
    provider = outcome["provider"]
    failures: list[str] = []
    events = outcome["event_types"]

    for event in REQUIRED_EVENTS:
        if event not in events:
            failures.append(f"missing SSE event {event!r} (got {events or 'none'})")
    if "backtest_failed" in events:
        payloads = [p for ev, p in outcome["job_events"] if ev == "backtest_failed"]
        failures.append(f"backtest_failed event: {payloads}")
    if outcome["error_banner_visible"]:
        failures.append(f"UI error banner: {outcome['error_banner_text']}")
    if "Backtest completed" not in outcome["progress_label"]:
        failures.append(f"progress label: {outcome['progress_label']!r}")

    candles_evaluated = 0
    for event, payload in outcome["job_events"]:
        if event == "backtest_completed":
            candles_evaluated = (payload.get("result") or {}).get(
                "candles_evaluated") or 0
    if not candles_evaluated:
        failures.append(
            f"engine consumed no candles (candles_evaluated={candles_evaluated!r})")

    cache_file = scenario["cache_file"]
    if cache_file in outcome["cache_before"]:
        failures.append(f"{cache_file} was already cached before the click — "
                        f"not a live fetch")
    if cache_file not in outcome["cache_after"]:
        failures.append(f"{cache_file} missing after the run — provider cache "
                        f"was not written (live fetch?)")

    batches = [c for c in outcome["provider_calls"] if c["provider"] == provider]
    if not batches:
        failures.append(f"'{provider}' provider never served historical candles")
    for call in batches:
        for problem in check_normalization(call["candles"], scenario, provider):
            failures.append(f"{provider} {call['instrument']} {call['timeframe']}: "
                            f"{problem}")
    return failures, int(candles_evaluated)


def smoke_strategy_modals(page, base_url, catalog) -> list[str]:
    """Open every strategy card modal and validate the shared backtest form."""
    failures: list[str] = []
    page.goto(base_url + "/", wait_until="domcontentloaded")
    page.wait_for_function(
        "typeof catalog !== 'undefined' && catalog.length > 0", timeout=30000)
    for strat_id, meta in catalog.items():
        open_strategy_card(page, meta["name"])
        symbol_count = page.locator("input[name='symbol-checkbox']").count()
        param_count = page.locator("#modal-params-grid > div").count()
        provider_visible = page.locator("#modal-provider-wrap").is_visible()
        # paper-only live strategies (index_oi_momentum) intentionally hide the
        # backtest button — they stream a live OI paper session instead.
        expect_run_button = not bool(meta.get("paper_only_live"))
        checks = {
            "capital field": page.locator("#modal-capital").is_visible(),
            "run button": page.locator("#modal-run-btn").is_visible() == expect_run_button,
            "symbol checkboxes": symbol_count > 0,
            "parameter fields": param_count > 0,
            # index_oi_momentum drives the tick-level OI backtest (no provider select)
            "provider dropdown": provider_visible == (strat_id != "index_oi_momentum"),
        }
        bad = [name for name, ok in checks.items() if not ok]
        if bad:
            failures.append(f"{strat_id}: {', '.join(bad)}")
        print(f"   modal {strat_id:<18} symbols={symbol_count:<3} "
              f"params={param_count:<3} "
              f"backtest_btn={'shown' if expect_run_button else 'hidden'} "
              f"provider_dropdown={'shown' if provider_visible else 'hidden'}"
              f"{'  <-- ' + ', '.join(bad) if bad else ''}")
        page.evaluate("closeBacktestModal()")
        page.wait_for_function(
            "() => document.getElementById('backtest-modal')"
            ".classList.contains('hidden')", timeout=10000)
    return failures


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Headless browser data-provider connectivity test")
    parser.add_argument("--providers", default=",".join(PROVIDER_ORDER),
                        help=f"comma separated subset of {PROVIDER_ORDER}")
    parser.add_argument("--timeout", type=int, default=300_000,
                        help="per-provider browser wait in ms (default 300000)")
    parser.add_argument("--no-cache-wipe", action="store_true",
                        help="keep the historical candle cache between runs")
    parser.add_argument("--skip-strategy-smoke", action="store_true")
    args = parser.parse_args()

    providers = [p.strip().lower() for p in args.providers.split(",") if p.strip()]
    unknown = [p for p in providers if p not in SCENARIOS]
    if unknown:
        print(f"unknown provider(s) {unknown}; known: {list(SCENARIOS)}")
        return 2

    from playwright.sync_api import sync_playwright

    # The dashboard runs inside THIS process so the instrumentation records the
    # exact provider batches / SSE events the browser click triggered.
    calls, events = install_instrumentation()
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    server = start_server(port)

    print("=" * 78)
    print("DATA PROVIDER CONNECTIVITY — headless browser 'RUN BACKTEST' validation")
    print(f"dashboard  : {base_url}")
    print(f"providers  : {', '.join(providers)}")
    print(f"cache wipe : {'disabled' if args.no_cache_wipe else 'before every provider'}")
    print("=" * 78)

    all_failures: dict[str, list[str]] = {}
    rows: list[dict] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1680, "height": 1050})
        page_errors: list[str] = []
        console_errors: list[str] = []
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("console",
                lambda m: console_errors.append(m.text) if m.type == "error" else None)

        catalog = fetch_catalog(page, base_url)
        print(f"\n[1] strategy catalog: {len(catalog)} strategies "
              f"({', '.join(catalog)})")

        if not args.skip_strategy_smoke:
            smoke_failures = smoke_strategy_modals(page, base_url, catalog)
            if smoke_failures:
                all_failures["strategy-modals"] = smoke_failures
            print(f"    -> {'PASS' if not smoke_failures else 'FAIL'}: "
                  f"backtest form renders for all registered strategies")

        for provider in providers:
            scenario = SCENARIOS[provider]
            print(f"\n[2] provider '{provider}' — {scenario['instrument']} "
                  f"{scenario['timeframe']} ({scenario['start']}..{scenario['end']})")
            if not args.no_cache_wipe:
                wipe_historical_cache()
            try:
                outcome = run_provider_backtest(page, base_url, provider, scenario,
                                                catalog, events, calls, args.timeout)
            except Exception as exc:  # browser timeout / navigation failure
                all_failures[provider] = [f"browser flow failed: {exc!r}"]
                print(f"    -> FAIL: {exc!r}")
                continue

            failures, candles = evaluate_scenario(outcome, scenario)
            if failures:
                all_failures[provider] = failures
            rows.append({
                "provider": provider,
                "status": "PASS" if not failures else "FAIL",
                "candles": candles,
                "return_pct": outcome["return_pct"],
                "events": len(outcome["event_types"]),
                "cache": scenario["cache_file"] in outcome["cache_after"],
                "source_symbols": sorted({
                    c.source_symbol for call in outcome["provider_calls"]
                    for c in call["candles"] if isinstance(c, NormalizedCandle)
                }),
            })
            print(f"    -> {'PASS' if not failures else 'FAIL'}: "
                  f"candles_evaluated={candles} return={outcome['return_pct']} "
                  f"sse_events={len(outcome['event_types'])}")
            for problem in failures:
                print(f"       - {problem}")

        # An uncaught JS exception (e.g. a results renderer crashing on a
        # malformed SSE payload) means the UI silently stopped working even
        # when the server-side backtest succeeded. Fail the run on those.
        if page_errors:
            all_failures["browser-page-errors"] = list(page_errors)
        if page_errors or console_errors:
            print(f"\n[browser page errors]   {page_errors}")
            print(f"[browser console errors] {console_errors}")
        browser.close()

    print("\n" + "=" * 78)
    print("SUMMARY — data providers through the streaming backtest")
    print("=" * 78)
    print(f"{'provider':<10} {'status':<7} {'candles':<9} {'return %':<10} "
          f"{'cache':<7} events  source_symbol")
    for row in rows:
        print(f"{row['provider']:<10} {row['status']:<7} {row['candles']:<9} "
              f"{row['return_pct']:<10} {str(row['cache']):<7} "
              f"{row['events']:<7} {', '.join(row['source_symbols'])}")
    tested = {row["provider"] for row in rows}
    for provider in providers:
        if provider not in tested:
            print(f"{provider:<10} FAIL    (no data collected)")
    if "strategy-modals" in all_failures:
        print("\nstrategy modal failures:")
        for problem in all_failures["strategy-modals"]:
            print(f"  - {problem}")
    if "browser-page-errors" in all_failures:
        print("\nbrowser page errors (uncaught JS exceptions):")
        for problem in all_failures["browser-page-errors"]:
            print(f"  - {problem}")

    print("\n" + ("ALL DATA PROVIDERS PASSED" if not all_failures
                  else "FAILURES PRESENT — see details above"))

    server.should_exit = True
    time.sleep(0.5)
    return 1 if all_failures else 0


if __name__ == "__main__":
    sys.exit(main())

