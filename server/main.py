"""
TeleClaude - Shared Terminal Session for Claude Code
A web server that provides shared access to a Claude Code PTY session.
Like tmux, but accessible via terminal AND web browser.
"""

import asyncio
import json
import os
import uuid
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse

from .pty_session import PTYSession, SessionClient, SessionManager

# Working directory (can be configured)
WORKING_DIR = os.environ.get("TELECLAUDE_WORKDIR", os.getcwd())

# Session manager singleton
session_manager = SessionManager()

# Active WebSocket connections for broadcasting
ws_connections: dict[str, WebSocket] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    # Startup: create default session
    try:
        session_manager.get_or_create_session("default", WORKING_DIR)
        print(f"Started Claude Code session in: {WORKING_DIR}")
    except Exception as e:
        print(f"Warning: Could not start default session: {e}")

    yield

    # Shutdown: cleanup all sessions
    for session_id in list(session_manager._sessions.keys()):
        session_manager.cleanup_session(session_id)


app = FastAPI(title="TeleClaude", description="Shared Claude Code Terminal", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the web terminal UI."""
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/status")
async def get_status():
    """Get server and session status."""
    session = session_manager.get_default_session()
    return {
        "working_dir": WORKING_DIR,
        "session_running": session.is_running() if session else False,
        "connected_clients": session.get_client_count() if session else 0,
        "sessions": session_manager.list_sessions()
    }


@app.post("/api/session/restart")
async def restart_session():
    """Restart the Claude Code session."""
    session_manager.cleanup_session("default")
    try:
        session_manager.get_or_create_session("default", WORKING_DIR)
        return {"status": "restarted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws/terminal")
async def websocket_terminal(websocket: WebSocket):
    """WebSocket endpoint for terminal access."""
    await websocket.accept()

    session = session_manager.get_default_session()
    if not session or not session.is_running():
        await websocket.send_json({"type": "error", "message": "No active session"})
        await websocket.close()
        return

    client_id = str(uuid.uuid4())[:8]
    ws_connections[client_id] = websocket

    # Queue for sending data to websocket
    output_queue: asyncio.Queue[bytes] = asyncio.Queue()

    # Capture the event loop for thread-safe callbacks
    loop = asyncio.get_running_loop()

    def on_output(data: bytes):
        """Callback when PTY produces output."""
        try:
            loop.call_soon_threadsafe(output_queue.put_nowait, data)
        except Exception as e:
            print(f"[WS {client_id}] Queue error: {e}")

    # Register client
    client = SessionClient(
        id=client_id,
        type="websocket",
        send_callback=on_output
    )

    # Get buffered output (history)
    history = session.add_client(client)

    # Send initial history
    if history:
        await websocket.send_bytes(history)

    async def send_output():
        """Task to send PTY output to websocket."""
        try:
            while True:
                data = await output_queue.get()
                await websocket.send_bytes(data)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[WS {client_id}] Send error: {e}")

    # Start output sender task
    sender_task = asyncio.create_task(send_output())

    try:
        while True:
            message = await websocket.receive()

            # Handle binary data (terminal input from xterm.js)
            if "bytes" in message:
                session.write(message["bytes"])

            # Handle text data (JSON commands or plain text)
            elif "text" in message:
                text = message["text"]
                try:
                    data = json.loads(text)
                    if data.get("type") == "resize":
                        session.resize(data.get("rows", 24), data.get("cols", 80))
                    elif data.get("type") == "input":
                        session.write(data.get("data", "").encode())
                except json.JSONDecodeError:
                    # Plain text input - send directly
                    session.write(text.encode())

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        sender_task.cancel()
        session.remove_client(client_id)
        ws_connections.pop(client_id, None)


@app.get("/api/attach-command")
async def get_attach_command():
    """Get the command to attach from terminal."""
    return {
        "command": f"python -m server.attach",
        "description": "Run this command in another terminal to attach to the same session"
    }


# Mount static files
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8765)
