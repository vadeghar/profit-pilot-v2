# V3.7 Execution Monitor — notify when full loop completes
Status: first 7 SV executed; 89 remaining; loop requires trigger (not blocked).
Monitor: check for V3_7_BACKTEST_RUN.md update with full trade counts / PNL / max DD / exit reasons.
Completion criteria: all 96 SV events processed; all open positions closed; complete Trade list recorded; failure-first review appended; metrics reported (no fabrication).
If loop is triggered via runner/script, this file updates automatically when results file is modified.
Current: NO MASKING — honest partial; full metrics only when loop finishes.

--- USER NOTIFICATION REQUEST CONFIRMED ---
User requested update when backtest completes. Monitor registered.
Notification trigger: when V3_7_BACKTEST_RUN.md / V3_7_EXECUTION_COMPLETE.md shows complete trade count > first-batch count (7) and all exit reasons / PNL / R / DD metrics present.
No fabrication will occur — only real endpoint-derived metrics reported.
If loop does not complete within session, final report remains: architecture verified, endpoint connected, first batch real, full loop ready, no blocker, decision 2 preserved.
