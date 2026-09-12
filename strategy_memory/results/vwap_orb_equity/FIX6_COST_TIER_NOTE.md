FIX 6 — Cost tier computation NOT in runner.py (verified by search).
- gross_pnl computed in _close_position (verified).
- total_cost / net_pnl / r_multiple not computed inside file (verified missing).
- Report claims BASE~0.5% / STRESS~1.5% are approximate friction adjustments applied when analyzing results (external to runner).
- To fully verify 3-tier metrics, add cost model call in _close_position or compute externally from saved gross_pnl + cost-factors.
This is the honest state — not a hidden computation.
