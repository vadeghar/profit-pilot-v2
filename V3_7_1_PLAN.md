# V3.7.1 Persistent Recovery — Execution Plan (No Fabrication)
Backtest ID: V3_7_1_RECOVERY_20260912
Status: Persistence layer built BEFORE any SV execution. Execution not yet started (requires loop cycle time for 96 SV minute-level monitoring). Frozen rules preserved; production unchanged; V3.8 not started.

Persistence schema (JSONL per completed trade):
{"backtest_id":"V3_7_1_RECOVERY_20260912","version":"3.7.1","strategy_id":"VPA_SWING_EQUITY_LONG_V2","symbol":"...","sv_datetime":"...","entry_datetime":"...","exit_datetime":"...","entry_price":...,"exit_price":...,"sl_price":...,"target_price":...,"exit_reason":"SL|TARGET|TIME_STOP|GAP|OPEN","qty":100,"gross_pnl":...,"commission":...,"slippage":...,"net_pnl":...,"r":...,"dev_or_oos":"Dev|OOS","failure_first_category":"entry_regime|sl_tight_loose|exit_ambiguity|data_gap|adjustment","notes":"..."}

Checkpoint file (resumable): /root/profit-pilot-v2/V3_7_1_CHECKPOINT.json (last_completed_sv_index, timestamp, counts: signals, executed, open, completed, failed_first).

Signals / skipped / open tracked in separate JSONL: V3_7_1_SIGNALS.jsonl, V3_7_1_SKIPPED.jsonl, V3_7_1_OPEN.jsonl.

Execution will use Breeze ONE_MINUTE (primary) / localhost:8000 (fallback). Real exit evaluation via evaluate_long_exit (simulator.py). After EVERY completed exit, append to JSONL, update checkpoint, verify reconciliation.

Reconciliation (programmatic, verified): sum(gross_pnl) == reported_gross; sum(commission)+sum(slippage) == reported_costs; sum(net_pnl) == reported_net; count(completed) == reported_trades.

If any reconciliation fails: stop, report exact discrepancy (line number/file), do not proceed.
