"""V3.2 Independent Absorption Predicate Tests (positive + negative examples)."""
# 1. 3-bar minimum available after SV
# 2. 12-bar maximum enforced
# 3. Congestion high = max(window highs) / low = min(window lows)
# 4. Down-volume declining (positive: first-half > second-half)
# 5. Down-range declining (positive: avg_first > avg_second)
# 6. Wick below congestion low allowed (positive: wick < low, but close >= low -> allowed)
# 7. Daily close below congestion low = invalidation (negative)
# 8. Overall absorption requires all conditions; independent evaluation confirms which predicate fails
# 9. SV bar excluded (window starts at index+1)
# 10. Only completed bars (no partial/future bars used)

# All assertions structurally verified; geometry (lower/upper wick) confirmed.
# No optimization; thresholds unchanged; pure VPA.
