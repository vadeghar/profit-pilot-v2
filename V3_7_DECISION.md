# V3.7 Final Decision — Engine Architecture Milestone

Status: ENGINEERING MILESTONE COMPLETE (not strategy change).

- DualBacktestEngine architecture designed (signal_provider + execution_provider separate)
- Existing BacktestEngine untouched (backward-compatible)
- evaluate_long_exit reused (simulator.py:16-41) — no duplication
- 11 tests written (test_v3_7_engine_architecture.py)
- Blocker identified honestly: 1-minute execution provider not connected (Breeze/local minute endpoint needed)
- Frozen VPA rules unchanged; production untouched; geometry verified; pure VPA maintained

Decision: 2 — Concept B_moderate for controlled V3.7 production experiment (prerequisites: larger/multi-market dataset, statistical confirmation, 1-min execution connected, user approval). No production change made.

Next: connect minute-level provider to DualBacktestEngine.execution_data_provider; run full backtest on 8-stock clean (HDFCBANK excluded) with real execution; report with failure-first analysis (per HERMES.md §5); do not fabricate results.
