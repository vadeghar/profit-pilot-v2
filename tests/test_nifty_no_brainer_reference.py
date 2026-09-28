from datetime import date, datetime, time

from strategies.nifty_no_brainer_reference import (
    Strikes, atm_from_spot, entry_decision, exit_evaluator, hedge_strike,
    last_friday, monthly_expiry_from_master, net_premium_pct, payoff_table,
    select_strikes,
)


def test_atm_and_structure_vectors():
    # Video rule: nearest 50, then promote a 50-multiple to the next 100.
    assert [atm_from_spot(x) for x in (24830, 24880, 24850, 24820, 25000, 26050)] == [
        24900, 24900, 24900, 24800, 25000, 26100,
    ]
    assert select_strikes(25000) == Strikes(25000, 25300, 25600, 26500)
    assert select_strikes(24850) == Strikes(24900, 25200, 25500, 26500)
    assert select_strikes(24900) == Strikes(24900, 25200, 25500, 26500)


def test_hedge_vectors_and_ambiguous_video_case():
    assert hedge_strike(25600) == 26500
    assert hedge_strike(25500) == 26500
    assert hedge_strike(26700) == 27500
    assert hedge_strike(24700) == 25500  # formal rule wins over video example


def test_premium_debit_credit_and_shift():
    prices = {"near_buy": 100, "sell": 100, "hedge": 90}
    assert net_premium_pct(prices, 1000) == 0.01
    assert entry_decision(25000, prices, 1000).decision == "TRADE"
    debit = {"near_buy": 120, "sell": 95, "hedge": 90}
    assert entry_decision(25000, debit, 1000).decision == "SKIP_DEBIT"
    def shifted(strikes):
        # Credit declines on each 100-point shift.
        credit = max(2, 20 - (strikes.near_buy - 25300) // 100 * 5)
        return {"near_buy": 100, "sell": 95 + credit / 2, "hedge": 90}
    result = entry_decision(25000, {"near_buy": 100, "sell": 111, "hedge": 90}, 1000,
                            price_for=shifted)
    assert result.decision == "TRADE"
    assert result.strikes.near_buy > 25300


def test_exit_variants_and_gap_through():
    entry = datetime(2026, 1, 30, 15, 16)
    assert exit_evaluator(25, 1000, entry, datetime(2026, 1, 31, 15, 16), date(2026, 2, 26)).reason == "TARGET"
    gap = exit_evaluator(-40, 1000, entry, datetime(2026, 2, 3, 9, 16), date(2026, 2, 26), gap_through_pnl=-42)
    assert gap.reason == "STOP_LOSS" and gap.fill_pnl == -42
    assert exit_evaluator(0, 1000, entry, datetime(2026, 2, 17, 15, 16), date(2026, 2, 26), max_hold_days=18).reason == "MAX_HOLD"
    assert exit_evaluator(0, 1000, entry, datetime(2026, 2, 18, 15, 16), date(2026, 2, 26), max_hold_days=19).reason == "MAX_HOLD"


def test_payoff_calendar_and_contract_master():
    strikes = Strikes(25000, 25300, 25600, 26500)
    rows = payoff_table(strikes, {"near_buy": 100, "sell": 90, "hedge": 90}, [0, 27000], 1, 1000)
    assert rows[0]["pnl"] == -10
    assert rows[1]["pnl"] == -610
    assert last_friday(2026, 1) == date(2026, 1, 30)
    assert monthly_expiry_from_master(2026, 2, [{"underlying": "NIFTY", "expiry_type": "MONTHLY", "expiry": "2026-02-24"}]) == date(2026, 2, 24)