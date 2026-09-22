"""CLI entrypoint (§1.1, §15.5)."""
import argparse
import json

from .config import Settings, TIMEFRAMES, DATA_PROVIDERS, SOURCES, MA_TYPES, FEATURE_INDICATORS
from .main import run_pipeline
from .backtest import report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lorentzian", description="Lorentzian Classification ML strategy backtest")
    p.add_argument("--ticker", default=_DEFAULT_TICKER,
                   help="Ticker/symbol, e.g. NSE:NIFTY, NSE:RELIANCE, MCX:CRUDEOIL (Breeze)")
    p.add_argument("--timeframe", "-tf", default="1h", choices=TIMEFRAMES, help="Bar timeframe (default 1h)")
    p.add_argument("--source", dest="data_source", default="breeze", choices=DATA_PROVIDERS,
                   help="Data provider (default breeze)")
    p.add_argument("--csv-path", default=None, help="CSV path when --source csv")
    p.add_argument("--period", default=None, help="yfinance period override (e.g. 60d, 10y)")
    p.add_argument("--source-price", default="close", choices=SOURCES, help="Price source series")
    p.add_argument("--neighbors", type=int, default=8, help="neighborsCount (1-100)")
    p.add_argument("--max-bars-back", type=int, default=2000)
    p.add_argument("--feature-count", type=int, default=5, choices=range(2, 6))
    p.add_argument("--show-default-exits", action="store_true", help="showDefaultExits")
    p.add_argument("--use-dynamic-exits", action="store_true")
    p.add_argument("--use-worst-case", action="store_true")
    p.add_argument("--include-full-history", action="store_true",
                   help="Deprecated no-op: Pine's ANN loop always iterates from bar 0")
    p.add_argument("--no-volatility-filter", action="store_true")
    p.add_argument("--no-regime-filter", action="store_true")
    p.add_argument("--regime-threshold", type=float, default=-0.1)
    p.add_argument("--use-adx-filter", action="store_true")
    p.add_argument("--adx-threshold", type=float, default=20.0)
    p.add_argument("--use-ema-filter", action="store_true")
    p.add_argument("--ema-period", type=int, default=200)
    p.add_argument("--use-sma-filter", action="store_true")
    p.add_argument("--sma-period", type=int, default=200)
    p.add_argument("--no-kernel-filter", action="store_true")
    p.add_argument("--use-kernel-smoothing", action="store_true")
    p.add_argument("--kernel-h", type=int, default=8)
    p.add_argument("--kernel-r", type=float, default=8.0)
    p.add_argument("--kernel-x", type=int, default=25)
    p.add_argument("--kernel-lag", type=int, default=2)
    p.add_argument("--save-config", default=None, help="Write resolved run config JSON to this path")
    # §15.5 Bollinger (optional, additive)
    p.add_argument("--use-bollinger-bands", action="store_true")
    p.add_argument("--bollinger-length", type=int, default=19)
    p.add_argument("--bollinger-mult", type=float, default=2.36)
    p.add_argument("--bollinger-offset", type=int, default=0)
    p.add_argument("--bollinger-ma-type", default="WMA", choices=MA_TYPES)
    p.add_argument("--bollinger-show-background", action="store_true")
    p.add_argument("--bollinger-show-upper", action="store_true")
    p.add_argument("--bollinger-show-lower", action="store_true")
    return p


def settings_from_args(args) -> Settings:
    return Settings(
        source=args.source_price,
        neighbors_count=args.neighbors,
        max_bars_back=args.max_bars_back,
        feature_count=args.feature_count,
        show_exits=args.show_default_exits,
        use_dynamic_exits=args.use_dynamic_exits,
        use_worst_case=args.use_worst_case,
        include_full_history=args.include_full_history,
        use_volatility_filter=not args.no_volatility_filter,
        use_regime_filter=not args.no_regime_filter,
        regime_threshold=args.regime_threshold,
        use_adx_filter=args.use_adx_filter,
        adx_threshold=args.adx_threshold,
        use_ema_filter=args.use_ema_filter,
        ema_period=args.ema_period,
        use_sma_filter=args.use_sma_filter,
        sma_period=args.sma_period,
        use_kernel_filter=not args.no_kernel_filter,
        use_kernel_smoothing=args.use_kernel_smoothing,
        h=args.kernel_h, r=args.kernel_r, x=args.kernel_x, lag=args.kernel_lag,
        timeframe=args.timeframe,
        data_provider=args.data_source,
        ticker=args.ticker,
        csv_path=args.csv_path,
        period=args.period,
        use_bollinger_bands=args.use_bollinger_bands,
        bollinger_length=args.bollinger_length,
        bollinger_mult=args.bollinger_mult,
        bollinger_offset=args.bollinger_offset,
        bollinger_ma_type=args.bollinger_ma_type,
        bollinger_show_background=args.bollinger_show_background,
        bollinger_show_upper=args.bollinger_show_upper,
        bollinger_show_lower=args.bollinger_show_lower,
    )


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    settings = settings_from_args(args)
    if args.save_config:
        with open(args.save_config, "w") as f:
            json.dump(settings.to_dict(), f, indent=2)
    results = run_pipeline(settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
