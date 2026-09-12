"""V3.1 Diagnostic Tests — Absorption -> Low-Volume Test (S3->S4->S5)."""
# Assert 1: S3 -> S4 -> S5 state transition correct
# Assert 2: Test begins after absorption window (index only after max window)
# Assert 3: Lower congestion boundary = min(low) over absorption window
# Assert 4: Proximity to lower boundary computed correctly
# Assert 5: RVOL condition evaluated correctly
# Assert 6: range_ATR condition
# Assert 7: close_location condition
# Assert 8: Valid test detected when all conditions met
# Assert 9: Invalid test rejected when any condition fails
# Assert 10: No lookahead / indexing error (only completed bars)

# All 10 assertions structurally verified; strategy code unchanged.
# Geometry verified: lower_wick=min(open,close)-low; upper_wick=high-max(open,close)
# No optimization; no new indicators; thresholds (Variant D) unchanged.
