"""
TeleClaude - Shared Terminal Session for Claude Code
A web server that provides shared access to a Claude Code PTY session.
Like tmux, but accessible via terminal AND web browser.
Supports multiple named sessions.
"""

import asyncio
import hashlib
import json
import os
import secrets
import uuid
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query, Request, Depends, Cookie
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, StreamingResponse

from .pty_session import PTYSession, SessionClient, SessionManager
from .vscode_manager import get_vscode_manager, VSCodeManager

# Working directory (can be configured)
WORKING_DIR = os.environ.get("TELECLAUDE_WORKDIR", os.getcwd())
# Session name (can be configured)
SESSION_NAME = os.environ.get("TELECLAUDE_SESSION", "default")
# Additional Claude arguments
CLAUDE_ARGS = os.environ.get("TELECLAUDE_CLAUDE_ARGS", "")
# Password protection (optional)
PASSWORD = os.environ.get("TELECLAUDE_PASSWORD", "")

# Valid auth tokens (in-memory store)
auth_tokens: set[str] = set()


def is_auth_required() -> bool:
    """Check if authentication is enabled."""
    return bool(PASSWORD)


def verify_password(password: str) -> bool:
    """Verify the provided password."""
    if not PASSWORD:
        return True
    return secrets.compare_digest(password, PASSWORD)


def generate_token() -> str:
    """Generate a new auth token."""
    token = secrets.token_urlsafe(32)
    auth_tokens.add(token)
    return token


def verify_token(token: Optional[str]) -> bool:
    """Verify an auth token."""
    if not PASSWORD:
        return True
    if not token:
        return False
    return token in auth_tokens


async def check_auth(request: Request, token: Optional[str] = Cookie(default=None, alias="teleclaude_token")):
    """Dependency to check authentication."""
    if not is_auth_required():
        return True

    # Check cookie token
    if token and verify_token(token):
        return True

    # Check query parameter token (for WebSocket)
    query_token = request.query_params.get("token")
    if query_token and verify_token(query_token):
        return True

    raise HTTPException(status_code=401, detail="Authentication required")

# Session manager singleton
session_manager = SessionManager()

# VS Code manager singleton
vscode_manager = get_vscode_manager(PASSWORD)

# Active WebSocket connections for broadcasting
ws_connections: dict[str, WebSocket] = {}

# HTTP client for reverse proxy
http_client: Optional[httpx.AsyncClient] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    global http_client

    # Startup: create HTTP client for reverse proxy
    http_client = httpx.AsyncClient(timeout=30.0)

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

    # Start VS Code cleanup task
    vscode_manager.start_cleanup_task()

    yield

    # Shutdown: cleanup all sessions
    for session_id in list(session_manager._sessions.keys()):
        session_manager.cleanup_session(session_id)

    # Shutdown: stop all VS Code instances
    vscode_manager.stop_cleanup_task()
    vscode_manager.stop_all()

    # Shutdown: close HTTP client
    if http_client:
        await http_client.aclose()


app = FastAPI(title="TeleClaude", description="Shared Claude Code Terminal", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the web terminal UI."""
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/auth/status")
async def auth_status(token: Optional[str] = Cookie(default=None, alias="teleclaude_token")):
    """Check if authentication is required and if user is authenticated."""
    required = is_auth_required()
    authenticated = verify_token(token) if required else True
    return {
        "auth_required": required,
        "authenticated": authenticated
    }


@app.post("/api/auth/login")
async def login(request: Request):
    """Authenticate with password."""
    try:
        body = await request.json()
        password = body.get("password", "")
    except:
        raise HTTPException(status_code=400, detail="Invalid request body")

    if not verify_password(password):
        raise HTTPException(status_code=401, detail="Invalid password")

    token = generate_token()
    response = JSONResponse({"status": "ok", "token": token})
    response.set_cookie(
        key="teleclaude_token",
        value=token,
        httponly=True,
        samesite="strict",
        max_age=86400 * 7  # 7 days
    )
    return response


@app.post("/api/auth/logout")
async def logout(token: Optional[str] = Cookie(default=None, alias="teleclaude_token")):
    """Logout and invalidate token."""
    if token and token in auth_tokens:
        auth_tokens.discard(token)

    response = JSONResponse({"status": "ok"})
    response.delete_cookie(key="teleclaude_token")
    return response


@app.get("/api/status")
async def get_status(auth: bool = Depends(check_auth)):
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
async def list_sessions(auth: bool = Depends(check_auth)):
    """List all available sessions."""
    return {
        "current": SESSION_NAME,
        "sessions": session_manager.list_sessions()
    }


@app.post("/api/sessions/{session_id}")
async def create_session(
    session_id: str,
    working_dir: str = Query(default=None),
    claude_args: str = Query(default=None),
    auth: bool = Depends(check_auth)
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
async def delete_session(session_id: str, auth: bool = Depends(check_auth)):
    """Delete a session."""
    if session_id not in session_manager._sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    session_manager.cleanup_session(session_id)
    return {"status": "deleted", "session_id": session_id}


@app.post("/api/notifications/ntfy")
async def set_ntfy_topic(topic: str = Query(default=""), session_id: str = Query(default=None), auth: bool = Depends(check_auth)):
    """Set ntfy.sh topic for push notifications."""
    sid = session_id or SESSION_NAME
    session = session_manager.get_session(sid)
    if not session:
        session = session_manager.get_default_session()

    if not session:
        raise HTTPException(status_code=404, detail="No active session")

    session.set_ntfy_topic(topic if topic else None)
    return {
        "status": "ok",
        "session_id": sid,
        "ntfy_topic": topic if topic else None,
        "enabled": bool(topic)
    }


@app.get("/api/notifications/ntfy")
async def get_ntfy_topic(session_id: str = Query(default=None), auth: bool = Depends(check_auth)):
    """Get current ntfy.sh topic."""
    sid = session_id or SESSION_NAME
    session = session_manager.get_session(sid)
    if not session:
        session = session_manager.get_default_session()

    topic = session.ntfy_topic if session else None
    return {
        "session_id": sid,
        "ntfy_topic": topic,
        "enabled": bool(topic)
    }


@app.post("/api/session/restart")
async def restart_session(session_id: str = Query(default=None), auth: bool = Depends(check_auth)):
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
async def websocket_terminal(websocket: WebSocket, session_id: str = Query(default=None), token: str = Query(default=None)):
    """WebSocket endpoint for terminal access."""
    # Check authentication before accepting
    if is_auth_required() and not verify_token(token):
        await websocket.close(code=4001, reason="Authentication required")
        return

    await websocket.accept()

    # Determine which session to connect to
    sid = session_id or SESSION_NAME

    # Get the specific session - don't fallback to avoid mixing outputs
    session = session_manager.get_session(sid)

    # Only fallback to default if no session_id was specified
    if not session and not session_id:
        session = session_manager.get_default_session()
        if session:
            # Find the actual session ID for this session
            for s_id, s in session_manager._sessions.items():
                if s is session:
                    sid = s_id
                    break

    if not session:
        await websocket.send_json({"type": "error", "message": f"Session not found: {sid}"})
        await websocket.close()
        return

    if not session.is_running():
        await websocket.send_json({"type": "error", "message": f"Session not running: {sid}"})
        await websocket.close()
        return

    print(f"[WS] Client connecting to session: {sid}")

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
                # Log to verify correct session routing
                if len(message["bytes"]) < 20:
                    print(f"[WS] Input from {client_id} to session {sid}: {message['bytes']!r}")
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
        print(f"[WS] Client {client_id} disconnected from session: {sid}")
    except Exception as e:
        print(f"[WS] Error for client {client_id} on session {sid}: {e}")
    finally:
        sender_task.cancel()
        session.remove_client(client_id)
        ws_connections.pop(client_id, None)
        print(f"[WS] Client {client_id} removed from session: {sid}")


@app.get("/api/attach-command")
async def get_attach_command():
    """Get the command to attach from terminal."""
    return {
        "command": f"python -m server.attach",
        "description": "Run this command in another terminal to attach to the same session"
    }


# ========== VS Code API Endpoints ==========

@app.post("/api/vscode/{session_id}")
async def start_vscode(session_id: str, auth: bool = Depends(check_auth)):
    """Start code-server for a session."""
    # Get the session's working directory
    session = session_manager.get_session(session_id)
    if not session:
        session = session_manager.get_default_session()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    work_dir = session.working_dir

    try:
        instance = vscode_manager.start(session_id, work_dir)
        return {
            "status": "running",
            "session_id": session_id,
            "port": instance.port,
            "working_dir": work_dir,
            "url": f"/vscode/{session_id}/"
        }
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/vscode/{session_id}")
async def stop_vscode(session_id: str, auth: bool = Depends(check_auth)):
    """Stop code-server for a session."""
    if vscode_manager.stop(session_id):
        return {"status": "stopped", "session_id": session_id}
    else:
        return {"status": "not_running", "session_id": session_id}


@app.get("/api/vscode/{session_id}/status")
async def vscode_status(session_id: str, auth: bool = Depends(check_auth)):
    """Get code-server status for a session."""
    instance = vscode_manager.get_instance(session_id)
    if instance:
        return {
            "status": "running",
            "session_id": session_id,
            "port": instance.port,
            "working_dir": instance.working_dir,
            "idle_seconds": int(instance.idle_seconds()),
            "url": f"/vscode/{session_id}/"
        }
    else:
        return {"status": "stopped", "session_id": session_id}


@app.get("/api/vscode")
async def list_vscode_instances(auth: bool = Depends(check_auth)):
    """List all running code-server instances."""
    return {"instances": vscode_manager.list_instances()}


# ========== VS Code Reverse Proxy ==========

async def proxy_to_vscode(request: Request, session_id: str, path: str = ""):
    """Proxy requests to code-server."""
    instance = vscode_manager.get_instance(session_id)
    if not instance:
        raise HTTPException(status_code=503, detail="VS Code not running for this session")

    # Update activity timestamp
    instance.touch()

    # Build target URL
    target_url = f"http://127.0.0.1:{instance.port}/{path}"
    if request.query_params:
        target_url += f"?{request.query_params}"

    # Get request body if present
    body = await request.body() if request.method in ["POST", "PUT", "PATCH"] else None

    # Forward headers (excluding host)
    headers = dict(request.headers)
    headers.pop("host", None)

    try:
        response = await http_client.request(
            method=request.method,
            url=target_url,
            headers=headers,
            content=body,
            follow_redirects=False
        )

        # Handle redirects - rewrite Location header
        response_headers = dict(response.headers)
        if "location" in response_headers:
            location = response_headers["location"]
            # Rewrite absolute URLs to go through proxy
            if location.startswith(f"http://127.0.0.1:{instance.port}"):
                location = location.replace(
                    f"http://127.0.0.1:{instance.port}",
                    f"/vscode/{session_id}"
                )
                response_headers["location"] = location

        # Remove hop-by-hop headers
        for header in ["transfer-encoding", "connection", "keep-alive"]:
            response_headers.pop(header, None)

        return StreamingResponse(
            content=response.iter_bytes(),
            status_code=response.status_code,
            headers=response_headers,
            media_type=response.headers.get("content-type")
        )
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="VS Code server not responding")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.api_route("/vscode/{session_id}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def vscode_proxy(request: Request, session_id: str, path: str = ""):
    """Reverse proxy to code-server."""
    # Check auth via cookie or query param
    token = request.cookies.get("teleclaude_token") or request.query_params.get("token")
    if is_auth_required() and not verify_token(token):
        raise HTTPException(status_code=401, detail="Authentication required")

    return await proxy_to_vscode(request, session_id, path)


@app.api_route("/vscode/{session_id}", methods=["GET"])
async def vscode_proxy_root(request: Request, session_id: str):
    """Redirect to add trailing slash for code-server root."""
    return JSONResponse(
        status_code=307,
        content={"detail": "Redirecting"},
        headers={"Location": f"/vscode/{session_id}/"}
    )


# ========== VS Code WebSocket Proxy ==========

@app.websocket("/vscode/{session_id}/{path:path}")
async def vscode_websocket_proxy(websocket: WebSocket, session_id: str, path: str = ""):
    """WebSocket proxy to code-server."""
    # Check auth
    token = websocket.query_params.get("token")
    if is_auth_required() and not verify_token(token):
        await websocket.close(code=4001, reason="Authentication required")
        return

    instance = vscode_manager.get_instance(session_id)
    if not instance:
        await websocket.close(code=4004, reason="VS Code not running")
        return

    await websocket.accept()
    instance.touch()

    # Connect to code-server WebSocket
    target_url = f"ws://127.0.0.1:{instance.port}/{path}"
    if websocket.query_params:
        target_url += f"?{websocket.query_params}"

    try:
        async with httpx.AsyncClient() as client:
            # Use websockets library for proper WebSocket proxying
            import websockets
            async with websockets.connect(target_url) as ws_target:
                async def forward_to_target():
                    try:
                        while True:
                            data = await websocket.receive()
                            if "text" in data:
                                await ws_target.send(data["text"])
                            elif "bytes" in data:
                                await ws_target.send(data["bytes"])
                    except WebSocketDisconnect:
                        pass

                async def forward_from_target():
                    try:
                        async for message in ws_target:
                            instance.touch()
                            if isinstance(message, str):
                                await websocket.send_text(message)
                            else:
                                await websocket.send_bytes(message)
                    except websockets.exceptions.ConnectionClosed:
                        pass

                await asyncio.gather(forward_to_target(), forward_from_target())
    except Exception as e:
        print(f"[VSCode WS] Error: {e}")
    finally:
        try:
            await websocket.close()
        except:
            pass


# Mount static files
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8765)
