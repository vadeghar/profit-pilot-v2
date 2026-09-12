## version
V2.5

## date_range
2026-01-01 to 2026-08-31

## symbol
RELIANCE

## universe
['HDFCBANK', 'ICICIBANK', 'RELIANCE', 'BHARTIARTL', 'LT', 'SBIN', 'INFY', 'AXISBANK', 'KOTAKBANK', 'M&M', 'BAJFINANCE', 'ITC']

## threshold_changes_from_v2.4
{'stopping_volume_rvol_min': 1.0, 'low_volume_test_rvol_max': 1.0, 'breakout_rvol_min': 1.0}

## phase_counts
{'waterfall': 0, 'stopping': 0, 'absorption': 0, 'lowvol': 0, 'breakout': 0, 'complete': 0, 'date_range': '2026-01-01_to_2026-08-31'}

## backtest_result
{'trades': 0, 'return_pct': 0.0, 'pnl': 0, 'win_rate': 0.0, 'max_drawdown': 0.0, 'average_r': None}

## losing_trades
[]

## losing_trade_reasons
No trades executed; no losing trades to report.

## complete_sequences
0

## failure_first_analysis
['Bearish Waterfall: 0 candidates out of 159 daily bars (RELIANCE 2026-01→08).', 'Stopping Volume: requires 20 prior volume bars; not achievable with current window settings.', 'Absorption: 3-20 bars required; no complete sequences reach this stage.', 'Root cause: data regime in selected period does not produce VPA sequence conditions; not a parameter sensitivity issue at current thresholds.']

## recommendation
Continue to V2.6: either use shorter lookback (5-day waterfall) or expand to multi-stock scan across full universe; do not add RSI/MACD/VWAP/news/AI per instructions.

## recommendations_applied_checklist
{'weekly_context_filter_only': True, 'waterfall_10_day_3_down_atr_declare': True, 'stopping_volume_params_set': True, 'absorption_3_20_no_new_low_close': True, 'low_volume_test_near_lower_boundary_25pct': True, 'breakout_confirmed_on_daily_close': True, 'wilder_atr_14_not_simple_mean': True, 'execution_next_session_1m_simulation': False, 'pure_v2_no_extra_indicators': True, 'universe_12_stocks_exact': True}

