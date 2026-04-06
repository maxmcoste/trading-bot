#!/bin/bash
set -e

echo "============================================"
echo "  TradingBot — Mac Setup"
echo "============================================"
echo ""

# 1. Check Python
PYTHON=""
for cmd in python3.12 python3.11 python3; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "ERROR: Python 3.11+ is required. Install it via:"
    echo "  brew install python@3.12"
    exit 1
fi
echo "Using $PYTHON ($(${PYTHON} --version))"

# 2. Create venv
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "Creating virtual environment..."
$PYTHON -m venv venv
source venv/bin/activate
pip install --upgrade pip -q

echo "Installing dependencies..."
pip install -r requirements.txt -q
echo "Dependencies installed."

# 3. Interactive configuration
echo ""
echo "--- Broker Configuration ---"
echo "1) IBKR only (stocks/ETF)"
echo "2) Coinbase only (crypto)"
echo "3) Both"
read -p "Choose broker mode [1/2/3]: " broker_choice

case $broker_choice in
    1) BROKER_MODE="ibkr" ;;
    2) BROKER_MODE="coinbase" ;;
    *) BROKER_MODE="both" ;;
esac

IBKR_HOST="127.0.0.1"
IBKR_PORT="7497"
if [ "$BROKER_MODE" = "ibkr" ] || [ "$BROKER_MODE" = "both" ]; then
    read -p "IBKR host [$IBKR_HOST]: " input
    IBKR_HOST="${input:-$IBKR_HOST}"
    read -p "IBKR port (7497=paper, 7496=live) [$IBKR_PORT]: " input
    IBKR_PORT="${input:-$IBKR_PORT}"
fi

COINBASE_API_KEY=""
COINBASE_API_SECRET=""
if [ "$BROKER_MODE" = "coinbase" ] || [ "$BROKER_MODE" = "both" ]; then
    read -p "Coinbase API Key: " COINBASE_API_KEY
    read -sp "Coinbase API Secret: " COINBASE_API_SECRET
    echo ""
fi

read -p "Anthropic API Key: " ANTHROPIC_API_KEY
read -p "NewsAPI Key (optional, press Enter to skip): " NEWSAPI_KEY
read -p "Notification email (optional, press Enter to skip): " NOTIFY_EMAIL

# 4. Save secrets to macOS Keychain
echo ""
echo "Saving API keys to macOS Keychain..."
if [ -n "$ANTHROPIC_API_KEY" ]; then
    security add-generic-password -a tradingbot -s tradingbot-anthropic -w "$ANTHROPIC_API_KEY" -U 2>/dev/null || true
fi
if [ -n "$COINBASE_API_KEY" ]; then
    security add-generic-password -a tradingbot -s tradingbot-coinbase-key -w "$COINBASE_API_KEY" -U 2>/dev/null || true
fi
if [ -n "$COINBASE_API_SECRET" ]; then
    security add-generic-password -a tradingbot -s tradingbot-coinbase-secret -w "$COINBASE_API_SECRET" -U 2>/dev/null || true
fi
if [ -n "$NEWSAPI_KEY" ]; then
    security add-generic-password -a tradingbot -s tradingbot-newsapi -w "$NEWSAPI_KEY" -U 2>/dev/null || true
fi

# 5. Create .env
echo "Creating .env file..."
cat > .env << EOF
BROKER_MODE=$BROKER_MODE
IBKR_HOST=$IBKR_HOST
IBKR_PORT=$IBKR_PORT
IBKR_CLIENT_ID=1
COINBASE_API_KEY=$COINBASE_API_KEY
COINBASE_API_SECRET=$COINBASE_API_SECRET
PAPER_MODE=true
ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY
NEWSAPI_KEY=$NEWSAPI_KEY
NOTIFY_EMAIL=$NOTIFY_EMAIL
DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=8080
EOF

echo ".env created."

# 6. Create launchd plist
PLIST_PATH="$HOME/Library/LaunchAgents/com.tradingbot.agent.plist"
echo "Creating launchd agent..."
cat > "$PLIST_PATH" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.tradingbot.agent</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/caffeinate</string>
        <string>-i</string>
        <string>${SCRIPT_DIR}/venv/bin/python</string>
        <string>${SCRIPT_DIR}/main.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${SCRIPT_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${SCRIPT_DIR}/logs/launchd_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>${SCRIPT_DIR}/logs/launchd_stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:${SCRIPT_DIR}/venv/bin</string>
    </dict>
</dict>
</plist>
PLIST

# 7. Load agent
echo "Loading launchd agent..."
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"

# 8. Run paper test
echo ""
echo "Running paper trading test..."
python test_paper.py || echo "Test completed with warnings (check output above)"

# 9. Done
echo ""
echo "============================================"
echo "  Setup Complete!"
echo "============================================"
echo ""
echo "Dashboard: http://127.0.0.1:8080"
echo "Broker:    $BROKER_MODE"
echo "Mode:      PAPER (safe)"
echo ""
echo "To start manually:  cd $SCRIPT_DIR && source venv/bin/activate && python main.py"
echo "To stop:            launchctl unload $PLIST_PATH"
echo "Logs:               $SCRIPT_DIR/logs/"
echo ""
