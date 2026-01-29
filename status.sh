#!/bin/bash

# TeleClaude - Status Script

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/.teleclaude.pid"
URL_FILE="$SCRIPT_DIR/.teleclaude.url"
PORT=${TELECLAUDE_PORT:-8765}

echo "TeleClaude Status"
echo "================="

# Check server
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Server: Running (PID $PID)"
    else
        echo "Server: Not running (stale PID file)"
    fi
else
    # Check if running without PID file
    if pgrep -f "uvicorn server.main:app" > /dev/null; then
        echo "Server: Running (no PID file)"
    else
        echo "Server: Not running"
    fi
fi

# Check local access
if curl -s -o /dev/null -w "%{http_code}" "http://localhost:$PORT" 2>/dev/null | grep -q "200"; then
    echo "Local:  http://localhost:$PORT (accessible)"
else
    echo "Local:  http://localhost:$PORT (not accessible)"
fi

# Check ngrok
if [ -f "$URL_FILE" ]; then
    SAVED_URL=$(cat "$URL_FILE")
    echo "Remote: $SAVED_URL"
else
    # Try to get from ngrok API
    NGROK_URL=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null | python3 -c "import sys, json; data = json.load(sys.stdin); tunnels = data.get('tunnels', []); print(tunnels[0]['public_url'] if tunnels else '')" 2>/dev/null || echo "")
    if [ -n "$NGROK_URL" ]; then
        echo "Remote: $NGROK_URL"
    else
        echo "Remote: No ngrok tunnel"
    fi
fi
