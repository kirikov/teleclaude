#!/bin/bash

# TeleClaude - Start Script
# Starts the FastAPI server and ngrok tunnel

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT=${TELECLAUDE_PORT:-8765}
WORKDIR=${TELECLAUDE_WORKDIR:-$(pwd)}

echo "==================================="
echo "   TeleClaude - Remote Claude Code"
echo "==================================="
echo ""

# Check if claude is available
if ! command -v claude &> /dev/null; then
    echo "Error: 'claude' command not found. Please install Claude Code first."
    exit 1
fi

# Check if ngrok is available
SKIP_NGROK=""
if ! command -v ngrok &> /dev/null; then
    echo "Warning: 'ngrok' not found. Install it with: brew install ngrok"
    echo "Starting without ngrok tunnel..."
    SKIP_NGROK=1
fi

# Setup virtual environment if needed
if [ ! -d "$SCRIPT_DIR/venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$SCRIPT_DIR/venv"
fi

source "$SCRIPT_DIR/venv/bin/activate"

echo "Installing dependencies..."
pip install -q -r "$SCRIPT_DIR/requirements.txt"

# Kill any existing processes
pkill -f "uvicorn server.main:app" 2>/dev/null || true
pkill -f "ngrok http $PORT" 2>/dev/null || true

sleep 1

# Start the server
echo ""
echo "Starting server on port $PORT..."
export TELECLAUDE_WORKDIR="$WORKDIR"
uvicorn server.main:app --host 0.0.0.0 --port $PORT &
SERVER_PID=$!

sleep 2

# Check if server started
if ! kill -0 $SERVER_PID 2>/dev/null; then
    echo "Error: Server failed to start"
    exit 1
fi

echo "Server running at http://localhost:$PORT"

# Start ngrok if available
NGROK_PID=""
if [ -z "$SKIP_NGROK" ]; then
    echo ""
    echo "Starting ngrok tunnel..."
    ngrok http $PORT --log=stdout > /dev/null &
    NGROK_PID=$!

    sleep 3

    # Get ngrok URL
    NGROK_URL=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null | python3 -c "import sys, json; data = json.load(sys.stdin); tunnels = data.get('tunnels', []); print(tunnels[0]['public_url'] if tunnels else '')" 2>/dev/null || echo "")

    if [ -n "$NGROK_URL" ]; then
        echo ""
        echo "==================================="
        echo "   Access TeleClaude at:"
        echo "   $NGROK_URL"
        echo "==================================="
        echo ""
        echo "Share this URL to access Claude Code from anywhere!"
        echo "$NGROK_URL" > "$SCRIPT_DIR/.teleclaude.url"
    else
        echo "Warning: Could not get ngrok URL. Check http://localhost:4040"
    fi
fi

echo ""
echo "Working directory: $WORKDIR"
echo ""
echo "To attach from another terminal:"
echo "  cd $SCRIPT_DIR && source venv/bin/activate && python -m server.attach"
echo ""
echo "Press Ctrl+C to stop"
echo ""

# Handle shutdown
cleanup() {
    echo ""
    echo "Shutting down..."
    kill $SERVER_PID 2>/dev/null || true
    [ -n "$NGROK_PID" ] && kill $NGROK_PID 2>/dev/null || true
    rm -f "$SCRIPT_DIR/.teleclaude.pid" "$SCRIPT_DIR/.teleclaude.url"
    exit 0
}

trap cleanup INT TERM

# Wait for server
wait $SERVER_PID
