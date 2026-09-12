# V3.7 Reconciliation — External Session Findings (Breeze + Ablation + HDFCBANK Audit)
Source: /root/.hermes/pastes/paste_19_090953.txt (read, not adopted blindly).
Action: reconciled with V2-V3.7 work; no silent overwrite; production unchanged.

Key reconciled findings:
- Breeze replaces localhost:8000 as data source for runner (external session made this change).
- HDFCBANK excluded from clean benchmark (~50% discontinuity). 7-stock comparison needed.
- Ablation confirms SV/Absorption rules are gatekeepers (0 trades with strict rules; 110 trades / positive PNL without them). This validates V3.2-V3.6 diagnostic conclusion that the bottleneck is Absorption-definition / dataset-regime, not execution.
- Exit engine (evaluate_long_exit) added with SL/2R/time-stop/gap — aligns with V3.7 framework definition; needs 1-minute execution integration before full OOS.
- Current blocker: separate daily + 1-min providers needed for realistic results (matches V3.7 framework note).

Next: confirm Breeze vs localhost cross-check; run 7-stock clean benchmark; then proceed to full execution only if both sources agree and user approves V3.7.
