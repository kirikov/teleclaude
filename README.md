# TeleClaude

A shared terminal session for Claude Code - like tmux, but accessible via web browser.

Multiple clients (terminal + webapp) can connect to the same Claude Code session and see/interact with it in real-time.

## Features

- **Shared PTY Session**: Real Claude Code running in a pseudo-terminal
- **Web Interface**: Access from any browser with full terminal emulation (xterm.js)
- **Terminal Attach**: Connect from multiple terminals like tmux
- **Real-time Sync**: All clients see the same session simultaneously
- **Remote Access**: Use ngrok to access from anywhere

## Architecture

```
                    ┌─────────────────────────┐
                    │   Claude Code (PTY)     │
                    │   Running in session    │
                    └───────────┬─────────────┘
                                │
                    ┌───────────┴─────────────┐
                    │    Session Manager      │
                    │   (broadcasts I/O)      │
                    └───────────┬─────────────┘
                ┌───────────────┼───────────────┐
                ↓               ↓               ↓
           Web Browser    Terminal #1    Terminal #2
           (xterm.js)     (attach)       (attach)
```

## Installation

```bash
# Clone or navigate to the project
cd teleclaude

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Optional: Install ngrok for remote access
brew install ngrok  # macOS
# or download from https://ngrok.com
```

## Usage

### Start the Server

```bash
cd /path/to/teleclaude
source venv/bin/activate

# Start with current directory as working dir
./start.sh

# Or start with a specific working directory
TELECLAUDE_WORKDIR=/path/to/your/project ./start.sh
```

The server will:
1. Start Claude Code in a PTY session
2. Launch the web server on port 8765
3. Start ngrok tunnel (if installed)
4. Display the access URLs

### Access via Web Browser

- **Local**: http://localhost:8765
- **Remote**: The ngrok URL displayed at startup (e.g., https://xxxx.ngrok-free.app)

### Attach from Terminal

Connect another terminal to the same session:

```bash
cd /path/to/teleclaude
source venv/bin/activate
python -m server.attach

# Or connect to a remote server
python -m server.attach --host your-server.com --port 8765
```

Press `Ctrl+C` to detach without killing the session.

### Manual Start (without script)

```bash
cd /path/to/teleclaude
source venv/bin/activate

# Set working directory for Claude Code
export TELECLAUDE_WORKDIR=/path/to/your/project

# Start server
uvicorn server.main:app --host 0.0.0.0 --port 8765

# In another terminal, start ngrok (optional)
ngrok http 8765
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TELECLAUDE_WORKDIR` | Current directory | Working directory for Claude Code |
| `TELECLAUDE_PORT` | 8765 | Server port |

### ngrok Setup (for remote access)

1. Create account at https://ngrok.com
2. Get your auth token from the dashboard
3. Configure ngrok:
   ```bash
   ngrok config add-authtoken YOUR_TOKEN
   ```

## Web Interface

The web interface provides:

- **Full terminal emulation** with xterm.js
- **Status bar** showing connection status and client count
- **Attach from Terminal** button with instructions
- **Restart Session** button to restart Claude Code

## Files

```
teleclaude/
├── server/
│   ├── __init__.py
│   ├── main.py          # FastAPI server
│   ├── pty_session.py   # PTY session manager
│   ├── attach.py        # Terminal attach client
│   └── static/
│       └── index.html   # Web UI
├── start.sh             # Start script
├── stop.sh              # Stop script
├── status.sh            # Status check
├── requirements.txt
└── README.md
```

## Troubleshooting

### Web UI shows "Connecting..." but doesn't connect

- Check if the server is running: `curl http://localhost:8765/api/status`
- Check server logs: `tail -f /tmp/teleclaude.log`
- Try accessing localhost directly instead of ngrok

### Can't type in the web terminal

- Click on the terminal to focus it
- Check browser console (F12) for errors
- Verify WebSocket connection in Network tab → WS

### Terminal attach doesn't work

- Ensure websockets package is installed: `pip install websockets`
- Check the server URL is correct
- Verify the server is running

### Claude Code doesn't start

- Ensure `claude` command is available in PATH
- Check if Claude Code is installed: `claude --version`
- Check server logs for errors

## Security Notes

- The server has no authentication - anyone with the URL can access your terminal
- Use ngrok's authentication features for production use
- Don't expose to public internet without proper security measures
- Consider running in a sandboxed environment

## License

MIT
