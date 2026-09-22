"""Streamlit UI (§1.1, §15.6) — optional. Run: streamlit run lorentzian_strategy/app.py"""
import pandas as pd

import streamlit as st

from .config import Settings, TIMEFRAMES, SOURCES, MA_TYPES, DATA_PROVIDERS
from .data_loader import load_data, describe_data
from .main import run_pipeline
from .backtest import report


def main():
    st.set_page_config(page_title="Lorentzian Classification ML", layout="wide")
    st.title("Lorentzian Classification ML Strategy")

    with st.sidebar:
        st.header("Data")
        provider = st.selectbox("Data Source", DATA_PROVIDERS, index=DATA_PROVIDERS.index("breeze"))
        ticker = st.text_input("Ticker / Symbol", "NSE:NIFTY")
        timeframe = st.selectbox("Timeframe", TIMEFRAMES, index=4)  # default "1h"
        csv_path = st.text_input("CSV path (if source=csv)", "")
        max_bars_back = st.number_input("Max Bars Back", 10, 20000, 2000, step=50)
        neighbors = st.number_input("Neighbors Count", 1, 100, 8)
        feature_count = st.selectbox("Feature Count", [2, 3, 4, 5], index=3)
        source_price = st.selectbox("Price Source", SOURCES, index=3)
        st.header("Filters")
        use_vol = st.checkbox("Volatility Filter", True)
        use_regime = st.checkbox("Regime Filter", True)
        regime_threshold = st.slider("Regime Threshold", -10.0, 10.0, -0.1, 0.1)
        use_adx = st.checkbox("ADX Filter")
        adx_threshold = st.slider("ADX Threshold", 0.0, 100.0, 20.0)
        use_ema = st.checkbox("EMA Filter")
        ema_period = st.number_input("EMA Period", 2, 1000, 200, disabled=not use_ema)
        use_sma = st.checkbox("SMA Filter")
        sma_period = st.number_input("SMA Period", 2, 1000, 200, disabled=not use_sma)
        st.header("Kernel")
        use_kernel = st.checkbox("Kernel Filter", True)
        use_kernel_smoothing = st.checkbox("Kernel Smoothing")
        h = st.number_input("h (lookback)", 3, 50, 8)
        r = st.number_input("r (relative weight)", 0.25, 25.0, 8.0)
        x = st.number_input("x (regression level)", 2, 25, 25)
        lag = st.selectbox("lag", [1, 2], index=1)
        st.header("Bollinger Bands")
        use_bb = st.checkbox("Enable Bollinger Bands", False)
        bb_len = st.number_input("Length", 1, 500, 19, disabled=not use_bb)
        bb_mult = st.number_input("Multiplier", 0.01, 10.0, 2.36, disabled=not use_bb)
        bb_off = st.number_input("Offset", 0, 100, 0, disabled=not use_bb)
        bb_ma = st.selectbox("MA Type", MA_TYPES, disabled=not use_bb)
        bb_bg = st.checkbox("Show Background", False, disabled=not use_bb)
        bb_up = st.checkbox("Show Upper Band", False, disabled=not use_bb)
        bb_lo = st.checkbox("Show Lower Band", False, disabled=not use_bb)

        loaded_ok = st.session_state.get("loaded_ok", False)
        run_btn = st.button("Run Backtest", disabled=not loaded_ok)

    settings = Settings(
        source=source_price, neighbors_count=int(neighbors), max_bars_back=int(max_bars_back),
        feature_count=int(feature_count),
        use_dynamic_exits=st.session_state.get("use_dyn", False),
        timeframe=timeframe, data_provider=provider, ticker=ticker,
        csv_path=csv_path or None,
        use_volatility_filter=use_vol, use_regime_filter=use_regime,
        regime_threshold=regime_threshold,
        use_adx_filter=use_adx, adx_threshold=adx_threshold,
        use_ema_filter=use_ema, ema_period=int(ema_period),
        use_sma_filter=use_sma, sma_period=int(sma_period),
        use_kernel_filter=use_kernel, use_kernel_smoothing=use_kernel_smoothing,
        h=int(h), r=float(r), x=int(x), lag=int(lag),
        use_bollinger_bands=use_bb, bollinger_length=int(bb_len),
        bollinger_mult=float(bb_mult), bollinger_offset=int(bb_off),
        bollinger_ma_type=bb_ma, bollinger_show_background=bb_bg,
        bollinger_show_upper=bb_up, bollinger_show_lower=bb_lo,
    )

    # Timeframe/data changes force a fresh load + full pipeline re-run (§1.1)
    load_key = (provider, ticker, timeframe, csv_path, int(max_bars_back))
    if st.session_state.get("load_key") != load_key:
        st.session_state["load_key"] = load_key
        st.session_state["loaded_ok"] = False
        st.session_state["df"] = None
        try:
            settings.validate()
            df = load_data(settings)
            st.session_state["df"] = df
            st.session_state["loaded_ok"] = True
            st.info(describe_data(df, settings))
        except Exception as e:
            st.error(f"Data load failed: {e}")

    if run_btn and st.session_state.get("loaded_ok"):
        with st.spinner("Running full pipeline (features → filters → kernel → KNN → signals → backtest)…"):
            results = run_pipeline(settings, df=st.session_state["df"], verbose=False)
        st.subheader("Backtest Report")
        st.text(report(results["metrics"]))
        c1, c2 = st.columns(2)
        with c1:
            st.line_chart(results["equity"])
        with c2:
            price = results["data"]["close"]
            st.line_chart(price)
            bb = results.get("bollinger")
            if bb is not None:
                overlay = pd.DataFrame({"close": price})
                if bb_up:
                    overlay["upper"] = bb["upper"]
                if bb_lo:
                    overlay["lower"] = bb["lower"]
                overlay["basis"] = bb["basis"]
                st.line_chart(overlay)


if __name__ == "__main__":
    main()
