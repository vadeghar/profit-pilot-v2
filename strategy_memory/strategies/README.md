# Strategy Memory

One folder per strategy: `strategy_memory/strategies/<strategy-name>/`
  - `spec.md`  — formalized spec + stated assumptions
  - `runs.md`  — every backtest round: params used, metrics, reasoning for the next change

Raw trade-by-trade output for each run goes in
`strategy_memory/results/<strategy-name>/<run-id>.json` (not here — keep this
directory to reasoning + summaries only, so it stays fast to read).
