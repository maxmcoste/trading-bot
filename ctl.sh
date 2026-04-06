#!/bin/bash
# ============================================================
#  TradingBot — Control Script
#  Usage:  ./ctl.sh  start | stop | restart | status | logs
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="$SCRIPT_DIR/.bot.pid"
LOGFILE="$SCRIPT_DIR/logs/trading_bot.log"
VENV="$SCRIPT_DIR/venv/bin/python"
PLIST="$HOME/Library/LaunchAgents/com.tradingbot.agent.plist"

# Use venv python if available, else system python3
if [ -x "$VENV" ]; then
    PYTHON="$VENV"
else
    PYTHON="python3"
fi

# ── Helpers ──────────────────────────────────────────────────

is_running() {
    if [ -f "$PIDFILE" ]; then
        pid=$(cat "$PIDFILE")
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        # Stale pidfile
        rm -f "$PIDFILE"
    fi
    return 1
}

get_pid() {
    cat "$PIDFILE" 2>/dev/null || echo ""
}

# ── Commands ─────────────────────────────────────────────────

do_start() {
    if is_running; then
        echo "Bot is already running (PID $(get_pid))."
        exit 0
    fi

    mkdir -p "$SCRIPT_DIR/logs"

    echo "Starting TradingBot..."
    cd "$SCRIPT_DIR"
    nohup caffeinate -i "$PYTHON" main.py >> "$LOGFILE" 2>&1 &
    pid=$!
    echo "$pid" > "$PIDFILE"

    # Wait a moment and check it didn't crash immediately
    sleep 2
    if kill -0 "$pid" 2>/dev/null; then
        echo "Bot started (PID $pid)."
        echo "Dashboard: http://127.0.0.1:8080"
        echo "Logs:      $LOGFILE"
    else
        rm -f "$PIDFILE"
        echo "ERROR: Bot failed to start. Check $LOGFILE"
        exit 1
    fi
}

do_stop() {
    if ! is_running; then
        echo "Bot is not running."
        return 0
    fi

    pid=$(get_pid)
    echo "Stopping TradingBot (PID $pid)..."

    # Graceful SIGTERM first
    kill "$pid" 2>/dev/null

    # Wait up to 10 seconds for graceful shutdown
    for i in $(seq 1 10); do
        if ! kill -0 "$pid" 2>/dev/null; then
            rm -f "$PIDFILE"
            echo "Bot stopped."
            return 0
        fi
        sleep 1
    done

    # Force kill if still alive
    echo "Forcing shutdown..."
    kill -9 "$pid" 2>/dev/null || true
    rm -f "$PIDFILE"
    echo "Bot killed."
}

do_restart() {
    echo "Restarting TradingBot..."
    do_stop
    sleep 1
    do_start
}

do_status() {
    if is_running; then
        pid=$(get_pid)
        uptime_info=$(ps -o etime= -p "$pid" 2>/dev/null | xargs)
        mem=$(ps -o rss= -p "$pid" 2>/dev/null | awk '{printf "%.1f MB", $1/1024}')
        echo "Bot is RUNNING"
        echo "  PID:     $pid"
        echo "  Uptime:  ${uptime_info:-unknown}"
        echo "  Memory:  ${mem:-unknown}"
        echo "  Log:     $LOGFILE"
        echo "  Dashboard: http://127.0.0.1:8080"

        # Try to hit the health endpoint
        health=$(curl -s --max-time 2 http://127.0.0.1:8080/api/health 2>/dev/null || echo "")
        if echo "$health" | grep -q '"ok"'; then
            echo "  Health:  OK"
        else
            echo "  Health:  Dashboard not responding"
        fi
    else
        echo "Bot is STOPPED"
    fi
}

do_logs() {
    if [ ! -f "$LOGFILE" ]; then
        echo "No log file found at $LOGFILE"
        exit 1
    fi
    echo "Tailing $LOGFILE (Ctrl+C to stop)..."
    echo "---"
    tail -f "$LOGFILE"
}

# ── Main ─────────────────────────────────────────────────────

case "${1:-}" in
    start)   do_start   ;;
    stop)    do_stop    ;;
    restart) do_restart ;;
    status)  do_status  ;;
    logs)    do_logs    ;;
    *)
        echo "TradingBot Control"
        echo ""
        echo "Usage: $0 {start|stop|restart|status|logs}"
        echo ""
        echo "  start    Start the bot (with caffeinate to prevent sleep)"
        echo "  stop     Graceful shutdown (SIGTERM, then SIGKILL after 10s)"
        echo "  restart  Stop + start"
        echo "  status   Show PID, uptime, memory, dashboard health"
        echo "  logs     Tail the live log file"
        exit 1
        ;;
esac
