"""Orchestration: load -> features -> filters -> kernel -> ANN -> signals -> backtest."""
import numpy as np
import pandas as pd

from .config import Settings
from .data_loader import load_data, describe_data, check_no_gaps, source_series
from .features import compute_features
from .filters import combined_filter, trend_filters
from .kernels import kernel_signals
from .lorentzian_knn import run_ann
from .signals import generate_signals, entry_exit_signals
from .backtest import simulate, report
from .bollinger import compute_bollinger


def run_pipeline(settings: Settings, df: pd.DataFrame = None, verbose: bool = True) -> dict:
    settings.validate()
    if df is None:
        df = load_data(settings)
    if verbose:
        print(describe_data(df, settings))
        gaps = check_no_gaps(df)
        if gaps:
            print(f"Note: {gaps} index gap(s) in data (informational).")

    features = compute_features(df, settings)
    feat_matrix = np.column_stack([features[f"f{i+1}"].to_numpy()
                                   for i in range(settings.feature_count)])
    src = source_series(df, settings.source)
    close = df["close"].astype(float)

    # §5.3 ANN loop — src = settings.source is used for the training labels and
    # the kernel regression (exactly like Pine's `src = settings.source`).
    prediction = pd.Series(
        run_ann(feat_matrix, src.to_numpy(), settings), index=df.index, name="prediction"
    )

    filter_all = combined_filter(df, settings)
    kernels = kernel_signals(src, settings)
    trend = trend_filters(df, settings)

    signals = generate_signals(prediction, filter_all)
    entries = entry_exit_signals(signals, kernels, trend, settings)

    results = simulate(close, entries, settings.use_worst_case)
    bollinger = compute_bollinger(df, settings)

    if verbose:
        print(report(results["metrics"]))

    return {
        "data": df, "features": features, "prediction": prediction,
        "filter_all": filter_all, "kernels": kernels, "signals": signals,
        "entries": entries, "bollinger": bollinger,
        "metrics": results["metrics"], "trades": results["trades"],
        "equity": results["equity"], "settings": settings,
    }
