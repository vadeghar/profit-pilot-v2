#!/bin/bash
# Quick Start Script for Trading Platform Web Dashboard
# Created: September 17, 2026

set -e

cd "$(dirname "$0")/.."

echo "🚀 Starting Trading Platform Web Dashboard..."
echo ""

# Activate virtual environment
source .venv/bin/activate

# Start the web server
echo "📊 Launching web server on http://localhost:8080"
echo "   Press Ctrl+C to stop"
echo ""

python3 -m uvicorn web_app:app --host 0.0.0.0 --port 8080
