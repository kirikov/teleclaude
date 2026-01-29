# TeleClaude

A shared terminal session for Claude Code - like tmux, but accessible via web browser and mobile.

Multiple clients (terminal + webapp) can connect to the same Claude Code session and see/interact with it in real-time.

## Features

- **Shared PTY Session**: Real Claude Code running in a pseudo-terminal
- **Web Interface**: Access from any browser with full terminal emulation (xterm.js)
- **Terminal Attach**: Connect from multiple terminals like tmux
- **Real-time Sync**: All clients see the same session simultaneously
- **Remote Access**: Use ngrok to access from anywhere
- **Multiple Sessions**: Run multiple Claude sessions and switch between them
- **Mobile Support**: Mobile-friendly UI with touch controls
- **Push Notifications**: Get notified when Claude needs your attention

## Quick Start

```bash
# Install globally (one time)
ln -sf /path/to/teleclaude/teleclaude /usr/local/bin/teleclaude

# Start TeleClaude in any directory
teleclaude start

# Start with Claude args (e.g., resume last conversation)
teleclaude start -- --resume

# Create additional sessions
teleclaude new -s myproject ~/myproject
teleclaude new -s api ~/api -- --resume

# List sessions
teleclaude sessions

# Attach from another terminal
teleclaude attach
teleclaude attach -s myproject
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `teleclaude start [dir]` | Start server with Claude Code |
| `teleclaude start -- --resume` | Start and resume last conversation |
| `teleclaude new -s NAME [dir]` | Create new session on running server |
| `teleclaude attach` | Attach to session from terminal |
| `teleclaude attach -s NAME` | Attach to specific session |
| `teleclaude sessions` | List all running sessions |
| `teleclaude status` | Show server status |
| `teleclaude url` | Show ngrok URL |
| `teleclaude stop` | Stop the server |

### Options

| Option | Description |
|--------|-------------|
| `-s, --session NAME` | Session name (default: 'default') |
| `-p, --port PORT` | Port number (default: 8765) |
| `-- [args]` | Pass arguments to Claude Code |

## Mobile Notifications

TeleClaude can notify you when Claude needs your attention (asking questions, waiting for confirmation, etc.)

### Option 1: Browser Notifications

1. Open TeleClaude in your mobile browser
2. Tap the **🔕** button in the header
3. Allow notifications when prompted
4. The button changes to **🔔** when enabled
5. You'll get notifications when Claude needs input (even if the tab is in background)

### Option 2: ntfy.sh Push Notifications (Recommended for Mobile)

[ntfy.sh](https://ntfy.sh) sends real push notifications to your phone, even when the browser is closed.

**Setup:**

1. **Install ntfy app on your phone:**
   - iOS: [App Store](https://apps.apple.com/app/ntfy/id1625396347)
   - Android: [Play Store](https://play.google.com/store/apps/details?id=io.heckel.ntfy)

2. **Subscribe to a topic:**
   - Open the ntfy app
   - Tap **+** to add a subscription
   - Enter a unique topic name, e.g., `teleclaude-john-secret123`
   - (Keep it private - anyone with the topic name can send you notifications)

3. **Configure TeleClaude:**
   - Open TeleClaude in your browser
   - Tap the **📱** button in the header
   - Enter the SAME topic name: `teleclaude-john-secret123`
   - Click OK

4. **Test it:**
   ```bash
   # Send a test notification
   curl -d "Test from TeleClaude" https://ntfy.sh/teleclaude-john-secret123
   ```

**How it works:**
- TeleClaude monitors Claude's output for patterns like `?`, `[Y/n]`, `proceed?`
- When Claude appears to be waiting for input, a notification is sent
- Notifications only fire when the browser tab is NOT focused

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
                ↓
           Push Notifications
           (ntfy.sh / Browser)
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

# Make CLI globally available
ln -sf $(pwd)/teleclaude /usr/local/bin/teleclaude

# Optional: Install ngrok for remote access
brew install ngrok  # macOS
```

## Web Interface

The web interface provides:

- **Full terminal emulation** with xterm.js
- **Session selector** to switch between sessions
- **Mobile toolbar** with arrow keys, Esc, Tab, etc.
- **Notification buttons**: 🔕 (browser) and 📱 (ntfy.sh)
- **Attach info** with terminal attach instructions
- **Restart Session** button

### Mobile Controls

On mobile devices, a toolbar appears at the bottom with:

| Button | Function |
|--------|----------|
| ↑Scr / ↓Scr | Scroll terminal history |
| Esc | Escape key |
| Tab | Tab key |
| ▲ ▼ ◀ ▶ | Arrow keys (for Claude prompts) |
| ^C | Ctrl+C |
| ⏎ | Enter |

## Terminal Attach

Connect to a running session from any terminal:

```bash
# Attach to default session
teleclaude attach

# Attach to specific session
teleclaude attach -s myproject

# Attach to remote server
teleclaude attach https://xxxx.ngrok-free.app
```

**Detach:** Press `Ctrl+]` to detach without stopping the session.

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TELECLAUDE_HOME` | Script location | TeleClaude installation directory |
| `TELECLAUDE_PORT` | 8765 | Server port |

### ngrok Setup (for remote access)

1. Create account at https://ngrok.com
2. Get your auth token from the dashboard
3. Configure ngrok:
   ```bash
   ngrok config add-authtoken YOUR_TOKEN
   ```

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
├── teleclaude           # CLI script
├── requirements.txt
└── README.md
```

## Troubleshooting

### Notifications not working

- **Browser notifications**: Make sure you allowed notifications when prompted
- **ntfy.sh**: Verify the topic name matches exactly in both the app and TeleClaude
- **Test ntfy directly**: `curl -d "test" https://ntfy.sh/your-topic`

### Mobile UI issues

- **Buttons hidden by Dynamic Island**: The UI should auto-adjust; try refreshing
- **Keyboard covers input**: Use the mobile toolbar buttons instead

### Web UI shows "Connecting..." but doesn't connect

- Check if the server is running: `teleclaude status`
- Check server logs for errors
- Try accessing localhost directly instead of ngrok

### Terminal attach doesn't work

- Ensure websockets package is installed: `pip install websockets`
- Use `Ctrl+]` to detach (not `Ctrl+C`)

## Security Notes

- The server has no authentication - anyone with the URL can access your terminal
- Use ngrok's authentication features for production use
- Keep your ntfy.sh topic name private
- Don't expose to public internet without proper security measures

## License

MIT
