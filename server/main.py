"""
TeleClaude - Shared Terminal Session for Claude Code
A web server that provides shared access to a Claude Code PTY session.
Like tmux, but accessible via terminal AND web browser.
Supports multiple named sessions.
"""

import asyncio
import json
import os
import uuid
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse

from .pty_session import PTYSession, SessionClient, SessionManager

# Working directory (can be configured)
WORKING_DIR = os.environ.get("TELECLAUDE_WORKDIR", os.getcwd())
# Session name (can be configured)
SESSION_NAME = os.environ.get("TELECLAUDE_SESSION", "default")
# Additional Claude arguments
CLAUDE_ARGS = os.environ.get("TELECLAUDE_CLAUDE_ARGS", "")

# Session manager singleton
session_manager = SessionManager()

# Active WebSocket connections for broadcasting
ws_connections: dict[str, WebSocket] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    # Startup: create session with configured name
    try:
        # Build command with args
        command = ["claude"]
        if CLAUDE_ARGS:
            import shlex
            command.extend(shlex.split(CLAUDE_ARGS))

        session_manager.get_or_create_session(SESSION_NAME, WORKING_DIR, command=command)
        print(f"Started Claude Code session '{SESSION_NAME}' in: {WORKING_DIR}")
        if CLAUDE_ARGS:
            print(f"Claude args: {CLAUDE_ARGS}")
    except Exception as e:
        print(f"Warning: Could not start session '{SESSION_NAME}': {e}")

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
        "current_session": SESSION_NAME,
        "session_running": session.is_running() if session else False,
        "connected_clients": session.get_client_count() if session else 0,
        "sessions": session_manager.list_sessions()
    }


@app.get("/api/sessions")
async def list_sessions():
    """List all available sessions."""
    return {
        "current": SESSION_NAME,
        "sessions": session_manager.list_sessions()
    }


@app.post("/api/sessions/{session_id}")
async def create_session(
    session_id: str,
    working_dir: str = Query(default=None),
    claude_args: str = Query(default=None)
):
    """Create a new session."""
    work_dir = working_dir or WORKING_DIR
    command = ["claude"]
    if claude_args:
        import shlex
        command.extend(shlex.split(claude_args))
    try:
        session_manager.get_or_create_session(session_id, work_dir, command=command)
        return {"status": "created", "session_id": session_id, "working_dir": work_dir, "claude_args": claude_args}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a session."""
    if session_id not in session_manager._sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    session_manager.cleanup_session(session_id)
    return {"status": "deleted", "session_id": session_id}


@app.post("/api/session/restart")
async def restart_session(session_id: str = Query(default=None)):
    """Restart a Claude Code session."""
    sid = session_id or SESSION_NAME
    session = session_manager.get_session(sid)
    work_dir = session.working_dir if session else WORKING_DIR
    session_manager.cleanup_session(sid)
    try:
        session_manager.get_or_create_session(sid, work_dir)
        return {"status": "restarted", "session_id": sid}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws/terminal")
async def websocket_terminal(websocket: WebSocket, session_id: str = Query(default=None)):
    """WebSocket endpoint for terminal access."""
    await websocket.accept()

    sid = session_id or SESSION_NAME
    session = session_manager.get_session(sid)
    if not session:
        session = session_manager.get_default_session()

    if not session or not session.is_running():
        await websocket.send_json({"type": "error", "message": f"No active session: {sid}"})
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
