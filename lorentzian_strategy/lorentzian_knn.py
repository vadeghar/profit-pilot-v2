"""Core Approximate-Nearest-Neighbors loop with Lorentzian distance (§5).

This module is a line-for-line port of the "Core ML Logic" section of the
TradingView Pine Script v6 indicator *Machine Learning: Lorentzian
Classification* (©jdehorty, Lorentzian_ML.pine).  Every semantic detail of the
Pine code is reproduced exactly, including the "quirks" that follow directly
from Pine's execution model:

- ``predictions`` / ``distances`` are declared with ``var`` in Pine, i.e. they
  are initialized **once** and then persist across bars.  Their contents
  therefore survive from one bar to the next, only ever being pruned by the
  shift that happens once the array exceeds ``neighborsCount``
  (``array.shift`` pops the *oldest* element).
- ``lastDistance`` is reset to ``-1.0`` at the start of every bar and is
  re-anchored to ``distances[round(neighborsCount * 3 / 4)]`` *before* the
  arrays are shifted.
- ``maxBarsBackIndex = last_bar_index >= maxBarsBack ? last_bar_index -
  maxBarsBack : 0`` is anchored to the **end of the whole dataset** (Pine's
  ``last_bar_index``); while ``bar_index < maxBarsBackIndex`` the whole block
  is skipped and ``prediction`` keeps its previous value.
- The search runs ``i = 0 .. min(maxBarsBack - 1, bar_index)`` (inclusive) —
  it always starts at **bar 0**, exactly like Pine's ``for i = 0 to
  sizeLoop``.  ``maxBarsBack`` only caps the loop's upper bound and gates when
  predictions become active; it does NOT slide the window.  Because
  ``featureArrays`` grow by one element per bar, array index ``i`` is the
  absolute bar index, so once activated the search always covers the
  *oldest* ``maxBarsBack`` bars of the chart (a well-known quirk of this
  script that must be reproduced exactly).  Bars with ``i % 4 == 0`` are
  skipped and a candidate is accepted only when ``d >= lastDistance``.
- Training labels are ``y_train[i] = src[4] < src[0] ? short : src[4] >
  src[0] ? long : neutral`` — i.e. the **past** 4-bar move (``src[4]`` is 4
  bars ago), with Pine's inverted mapping (a rise over the past 4 bars is
  labeled ``short``, a fall is labeled ``long``).  No label-lag guarding is
  needed because these labels are fully known at bar ``i``.

Numba-accelerated when numba is available; otherwise falls back to a pure-Python
implementation with identical semantics.
"""
import math

import numpy as np

try:
    from numba import njit
    HAVE_NUMBA = True
except ImportError:  # pragma: no cover - exercised only without numba
    HAVE_NUMBA = False

    def njit(*args, **kwargs):
        def wrap(fn):
            return fn
        return wrap if args and callable(args[0]) else wrap


@njit(cache=True)
def _ann_loop(features, y_train, feature_count, neighbors_count,
              max_bars_back, max_bars_back_index):
    """Pine-exact ANN loop (§"Core ML Logic").

    features: (n_bars, feature_count) float64 — features[bar_index] holds the
    values pushed into Pine's featureArrays at that bar (including the current
    bar, so ``i == t`` yields distance 0 exactly like Pine).
    y_train:  (n_bars,) float64 in {-1, 0, 1} (Pine ``y_train_array``).
    max_bars_back_index: Pine ``maxBarsBackIndex`` (``last_bar_index >=
                         maxBarsBack ? last_bar_index - maxBarsBack : 0``) —
                         also the bar at which predictions become active.

    The inner search always starts at i = 0 (Pine: ``for i = 0 to sizeLoop``).
    """
    n = features.shape[0]
    predictions = np.zeros(n)
    anchor_idx = int(math.floor(neighbors_count * 3.0 / 4.0 + 0.5))
    # Persistent "var" array buffers (Pine initializes them once -> they keep
    # their contents across bars).  The count never exceeds neighbors_count
    # after the first pruning, so a small static buffer is sufficient.
    buf_d = np.zeros(neighbors_count + 4)
    buf_p = np.zeros(neighbors_count + 4)
    buf_size = 0

    for t in range(n):
        if t < max_bars_back_index:
            # Pine: the whole block is skipped and ``prediction`` (a var,
            # initialized to 0.) keeps its previous value — still 0.0 here.
            continue
        last_distance = -1.0                    # lastDistance = -1.0
        size_loop = min(max_bars_back - 1, t)   # sizeLoop = min(maxBarsBack-1, size)
        count = buf_size                        # leftovers from previous bars
        for i in range(0, size_loop + 1):       # Pine: for i = 0 to sizeLoop
            if i % 4 == 0:
                continue                        # "and i % 4 != 0"
            d = 0.0
            for k in range(feature_count):      # get_lorentzian_distance(i, featureCount, ...)
                d += math.log(1.0 + abs(features[t, k] - features[i, k]))
            if d >= last_distance:
                last_distance = d
                buf_d[count] = d
                buf_p[count] = y_train[i]       # round(y_train[i]) is identity
                count += 1
                if count > neighbors_count:
                    # lastDistance := distances[round(neighborsCount*3/4)]
                    last_distance = buf_d[anchor_idx]
                    # array.shift(distances); array.shift(predictions)
                    for j in range(1, count):
                        buf_d[j - 1] = buf_d[j]
                        buf_p[j - 1] = buf_p[j]
                    count -= 1
        s = 0.0
        for j in range(count):
            s += buf_p[j]                       # prediction := array.sum(predictions)
        predictions[t] = s
        buf_size = count
    return predictions


def train_labels(src: np.ndarray, label_lag: int = 4) -> np.ndarray:
    """§"Next Bar Classification" — Pine-exact training labels.

    ``y_train_series = src[4] < src[0] ? direction.short : src[4] > src[0] ?
    direction.long : direction.neutral`` where ``src[4]`` is the value ``label_lag``
    bars in the past relative to ``src[0]``.  So for bar ``t``:

        label[t] = -1  if src[t] > src[t - 4]   (price rose in the past 4 bars)
        label[t] = +1  if src[t] < src[t - 4]   (price fell in the past 4 bars)
        label[t] =  0  otherwise

    Bars before ``label_lag`` are 0 (Pine would read them from the chart's
    pre-history buffer, which is unavailable in Python).
    """
    n = len(src)
    y = np.zeros(n, dtype=np.float64)
    for t in range(label_lag, n):
        if src[t] > src[t - label_lag]:
            y[t] = -1.0
        elif src[t] < src[t - label_lag]:
            y[t] = 1.0
    return y


def run_ann(features_by_bar, src: np.ndarray, settings,
            dataset_len: int = None) -> np.ndarray:
    """Compute prediction[t] for all bars (§5.3).

    features_by_bar: list/array of feature vectors per bar (only the first
    feature_count are used).  src: the settings.source series (used for the
    training labels, exactly like Pine's ``src = settings.source``).
    dataset_len: total number of bars in the "chart" (defaults to len(src)).
    Pine's ``maxBarsBackIndex`` is anchored to ``last_bar_index`` = the last
    bar of the entire dataset, so the search window depends on it.
    """
    feature_count = settings.feature_count
    feats = np.asarray(features_by_bar, dtype=np.float64)[:, :feature_count]
    y_train = train_labels(src, label_lag=4)
    n = feats.shape[0] if dataset_len is None else int(dataset_len)
    max_bars_back_index = max(0, n - 1 - settings.max_bars_back)
    # Pine always iterates from bar 0 ("for i = 0 to sizeLoop"); there is no
    # window-start parameter in the Pine source.  Settings.include_full_history
    # is kept for CLI backwards compatibility only and is a documented no-op.
    return _ann_loop(feats, y_train, feature_count, settings.neighbors_count,
                     settings.max_bars_back, max_bars_back_index)
