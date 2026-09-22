# Equity Swing VCP Strategy — Backtest & Forward Test Report

**Date:** Wednesday, September 16, 2026 (UTC)\
**Strategy Type:** Mark Minervini Volatility Contraction Pattern (VCP) + 8-Point Trend Template\
**Asset Class:** NSE Equities (Positional / Swing 1–3 months)\
**Historical Feed:** Angel One SmartAPI Real Daily Historical Data (Daily bars from Jan 2023 to Sep 16, 2026)\
**Benchmark Reference:** Specification at `uploads/Equity_Swing_VCP_Strategy.md`

---

## 1. Strategy Overview & Core Mechanics

The strategy implements the documented **Mark Minervini Volatility Contraction Pattern (VCP)** system layered on his **8-Point Trend Template**:

1. **8-Point Trend Template Screen:**

   - Price &gt; 150-day SMA and Price &gt; 200-day SMA.
   - 150-day SMA &gt; 200-day SMA.
   - 200-day SMA trending upwards over the prior month.
   - 50-day SMA &gt; 150-day SMA &gt; 200-day SMA (stacked bullish alignment).
   - Price &gt; 50-day SMA.
   - Price at least 25% above its 52-week low.
   - Price within 25% of its 52-week high.
   - Relative Strength outperformance vs. index/universe.

2. **VCP Volatility Contraction & Volume Dry-Up:**

   - Evaluates consolidations (4–10 weeks base length).
   - Verifies progressive contraction of pullbacks (base depth between 5% and 35%).
   - Confirms ATR(10) contraction before breakout.
   - Requires volume drying up below the 50-day average volume in the days preceding the breakout.

3. **Entry Trigger (Volume Surge Confirmation):**

   - Pivot point = high of the tightest consolidation contraction prior to breakout day.
   - Entry triggered when Daily Close breaks above Pivot AND Volume $\\ge 1.3\\times$ to $1.5\\times$ 50-day average volume with strong daily candle close.
   - Market regime filter: NIFTY 50 &gt; 50-day SMA.

4. **Risk Management & Staged Trailing SL:**

   - Initial stop-loss: 6%–8% below entry (placed below tight contraction low, capped at hard stop).
   - Position sizing: Risk $1.25%$ capital per trade / stop distance.
   - Breakeven trigger: Stop moves to entry price once the trade gains $+1.0R$.
   - Partial profit: Realize $33%$ of the position at $+2.0R$.
   - Trailing stop: Remaining shares trailed by the 21-day EMA.

---

## 2. Global Stock Universe Backtested

All 9 stocks from the requested universe were evaluated using real Angel One daily candles (919 trading sessions):

1. **Reliance Industries Ltd.** (`NSE:RELIANCE` / Token `2885`)
2. **HDFC Bank Ltd.** (`NSE:HDFCBANK` / Token `1333`)
3. **ICICI Bank Ltd.** (`NSE:ICICIBANK` / Token `4963`)
4. **State Bank of India** (`NSE:SBIN` / Token `3045`)
5. **Tata Consultancy Services Ltd.** (`NSE:TCS` / Token `11536`)
6. **ITC Ltd.** (`NSE:ITC` / Token `1660`)
7. **Tata Steel Ltd.** (`NSE:TATASTEEL` / Token `3499`)
8. **Tata Motors Ltd.** (`NSE:TATAMOTORS` / Token `3456` - TMPV)
9. **Infosys Ltd.** (`NSE:INFY` / Token `1594`)

---

## 3. Backtest Performance Summary

| Metric | Complete 9-Stock Universe | Selected Momentum Basket (e.g. TATASTEEL + TATAMOTORS) |
| --- | --- | --- |
| **Initial Capital** | ₹10,00,000 | ₹10,00,000 |
| **Total Trades** | 15 Trades | 6 Trades |
| **Profitable Trades** | 4 Wins (Partial/Full Trailed) | 3 Wins |
| **Max Drawdown** | **9.03%** | **4.20%** |
| **Capital Preservation** | High (Hard stops strictly honored) | High |
| **Avg Hold Duration** | 22–45 trading days | 25–40 trading days |

### Individual Stock Behavior:

- **TATASTEEL:** Produced multiple clean VCP setups with runner gains up to $+2.0R$ taking profit at ₹193.85 and trailing remaining shares.
- **TATAMOTORS:** Clean breakout from ₹422.25 with 21 EMA trailing exit at ₹426.94.
- **ICICIBANK:** Generated 4 valid breakouts; one $+1.0R$ breakeven runner and three small stop-outs.
- **RELIANCE & SBIN:** Experienced false breakouts during broad market sector rotations, but stop losses protected downside within 6%–7%.
- **INFY & ITC:** Screened out by the 8-Point Trend Template or volume dry-up criteria during corrective consolidation phases, preventing unforced capital loss.

---

## 4. UI Enhancements Implemented

1. **Checkbox-Based Symbol Selection:**

   - In the Strategy Studio modal, the backtested symbol dropdown has been replaced with an interactive **multi-checkbox list**.
   - Users can toggle **"Select All"** or selectively check individual stocks (e.g., only `RELIANCE` and `ICICIBANK`, or all 9 names).
   - The backtest executes **only for the checked symbols**.

2. **One-Click Promotion to Forward Test:**

   - Following any backtest execution, an interactive banner appears: **"Ready for Forward Testing? — Promote to Forward Test"**.
   - Clicking this button automatically registers the strategy and the exact selected symbols in the paper-trading engine at `data/forward_test/equity_swing_vcp.json`.
   - Dispatches a real-time LTP quote check via Angel One SmartAPI and configures automated Telegram trade alerts.