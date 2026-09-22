#!/bin/bash
# ==============================================================================
# Trading Strategy Execution Platform - Strategy & Backtest Runner Script
# Based on Final Architecture v2 (NSE / NFO / MCX)
# ==============================================================================

set -e

PLATFORM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$PLATFORM_DIR:$PYTHONPATH"

# Prefer the project virtualenv interpreter, fall back to system python3
if [ -x "$PLATFORM_DIR/.venv/bin/python" ]; then
    PYTHON="$PLATFORM_DIR/.venv/bin/python"
else
    PYTHON="python3"
fi

# Colors for terminal output
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

print_banner() {
    echo -e "${CYAN}============================================================${NC}"
    echo -e "${CYAN}   QUANT TRADING STRATEGY EXECUTION PLATFORM (v2.0 ARCH)   ${NC}"
    echo -e "${CYAN}============================================================${NC}"
}

usage() {
    print_banner
    echo -e "Usage:"
    echo -e "  $0 backtest <strategy_id|strategy_name> [options]"
    echo -e "  $0 list"
    echo -e "  $0 run <strategy_id|strategy_name> [instrument]"
    echo -e "  $0 web [port]"
    echo -e ""
    echo -e "Examples:"
    echo -e "  ${GREEN}$0 backtest ema_crossover${NC}"
    echo -e "  ${GREEN}$0 backtest rsi --instrument NSE:BANKNIFTY --timeframe 15m${NC}"
    echo -e "  ${GREEN}$0 backtest breakout --capital 200000${NC}"
    echo -e "  ${GREEN}$0 list${NC}"
    echo -e "  ${GREEN}$0 web 8080${NC}"
    exit 1
}

if [ $# -eq 0 ]; then
    usage
fi

CMD="$1"
shift

case "$CMD" in
    backtest)
        if [ $# -eq 0 ]; then
            echo -e "${RED}Error: Strategy ID or Name required for backtest!${NC}"
            echo -e "Available strategies: ema_crossover, rsi, breakout, mcx_trend_rider, equity_swing_vcp, index_oi_momentum"
            exit 1
        fi
        STRAT="$1"
        shift
        print_banner
        echo -e "${GREEN}>>> Launching Backtest for: $STRAT${NC}"
        "$PYTHON" "$PLATFORM_DIR/main.py" backtest "$STRAT" "$@"
        ;;

    list)
        print_banner
        "$PYTHON" "$PLATFORM_DIR/main.py" strategy list
        ;;

    run|start)
        STRAT="${1:-ema_crossover}"
        INST="${2:-NSE:NIFTY}"
        print_banner
        echo -e "${GREEN}>>> Starting live/paper strategy daemon: $STRAT on $INST${NC}"
        "$PYTHON" "$PLATFORM_DIR/main.py" start &
        PID=$!
        sleep 1
        "$PYTHON" "$PLATFORM_DIR/main.py" strategy start "$STRAT" --instrument "$INST"
        wait $PID
        ;;

    web|dashboard)
        PORT="${1:-8080}"
        print_banner
        echo -e "${GREEN}>>> Starting QuantPulse Web App & Interactive Dashboard on port $PORT...${NC}"
        exec "$PYTHON" -m uvicorn web_app:app --app-dir "$PLATFORM_DIR" --host 0.0.0.0 --port "$PORT"
        ;;

    *)
        "$PYTHON" "$PLATFORM_DIR/main.py" "$CMD" "$@"
        ;;
esac
