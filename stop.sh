#!/bin/bash

# TeleClaude - Stop Script

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/.teleclaude.pid"
PORT=${TELECLAUDE_PORT:-8765}

echo "Stopping TeleClaude..."

# Kill server by PID file
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID" 2>/dev/null && echo "Server stopped (PID $PID)"
    fi
    rm -f "$PID_FILE"
fi

# Kill any remaining processes
pkill -f "uvicorn server.main:app" 2>/dev/null && echo "Killed uvicorn processes"
pkill -f "ngrok http $PORT" 2>/dev/null && echo "Killed ngrok processes"

# Clean up
rm -f "$SCRIPT_DIR/.teleclaude.url"

echo "TeleClaude stopped"
