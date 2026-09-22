#!/bin/bash
# Setup script for Breeze Auto-Login

set -e

cd "$(dirname "$0")/.."

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                                                              ║"
echo "║     🔐 ICICI Breeze Auto-Login Setup                        ║"
echo "║                                                              ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# Activate virtual environment
echo "📦 Activating virtual environment..."
source .venv/bin/activate

# Install Playwright
echo "📦 Installing Playwright..."
pip install playwright

# Install Chromium browser
echo "🌐 Installing Chromium browser..."
playwright install chromium

echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "1. Verify your .env has these variables:"
echo "   - BREEZE_API_KEY"
echo "   - BREEZE_USER_ID"
echo "   - BREEZE_PASSWORD"
echo "   - TELEGRAM_BOT_TOKEN"
echo "   - TELEGRAM_HOME_CHANNEL"
echo ""
echo "2. Test the script:"
echo "   $ python3 breeze_auto_login.py --visible"
echo ""
echo "📚 Full guide: BREEZE_AUTO_LOGIN_GUIDE.md"
