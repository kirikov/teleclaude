#!/bin/bash

# TeleClaude - Background Start Script
# Runs at Claude Code session start via hook

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT=${TELECLAUDE_PORT:-8765}
WORKDIR=${TELECLAUDE_WORKDIR:-$(pwd)}
LOG_FILE="$SCRIPT_DIR/.teleclaude.log"
PID_FILE="$SCRIPT_DIR/.teleclaude.pid"

# Check if already running
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        # Already running, get ngrok URL
        NGROK_URL=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null | python3 -c "import sys, json; data = json.load(sys.stdin); tunnels = data.get('tunnels', []); print(tunnels[0]['public_url'] if tunnels else '')" 2>/dev/null || echo "")
        if [ -n "$NGROK_URL" ]; then
            echo "TeleClaude already running at: $NGROK_URL"
        else
            echo "TeleClaude already running at: http://localhost:$PORT"
        fi
        exit 0
    fi
fi

# Check if claude is available
if ! command -v claude &> /dev/null; then
    echo "Error: 'claude' command not found"
    exit 1
fi

# Setup virtual environment if needed
if [ ! -d "$SCRIPT_DIR/venv" ]; then
    python3 -m venv "$SCRIPT_DIR/venv" >> "$LOG_FILE" 2>&1
fi

source "$SCRIPT_DIR/venv/bin/activate"
pip install -q -r "$SCRIPT_DIR/requirements.txt" >> "$LOG_FILE" 2>&1

# Kill any stale processes
pkill -f "uvicorn server.main:app" 2>/dev/null || true
pkill -f "ngrok http $PORT" 2>/dev/null || true
sleep 1

# Start server in background
export TELECLAUDE_WORKDIR="$WORKDIR"
nohup "$SCRIPT_DIR/venv/bin/uvicorn" server.main:app --host 0.0.0.0 --port $PORT >> "$LOG_FILE" 2>&1 &
SERVER_PID=$!
echo $SERVER_PID > "$PID_FILE"

sleep 2

# Verify server started
if ! kill -0 $SERVER_PID 2>/dev/null; then
    echo "Error: Server failed to start. Check $LOG_FILE"
    exit 1
fi

# Start ngrok if available
if command -v ngrok &> /dev/null; then
    nohup ngrok http $PORT --log=stdout >> "$LOG_FILE" 2>&1 &
    sleep 3

    NGROK_URL=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null | python3 -c "import sys, json; data = json.load(sys.stdin); tunnels = data.get('tunnels', []); print(tunnels[0]['public_url'] if tunnels else '')" 2>/dev/null || echo "")

    if [ -n "$NGROK_URL" ]; then
        echo "$NGROK_URL" > "$SCRIPT_DIR/.teleclaude.url"
        echo "TeleClaude started at: $NGROK_URL"
    else
        echo "TeleClaude started at: http://localhost:$PORT (ngrok pending, check http://localhost:4040)"
    fi
else
    echo "TeleClaude started at: http://localhost:$PORT"
    echo "Install ngrok for remote access: brew install ngrok"
fi
