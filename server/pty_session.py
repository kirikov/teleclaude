"""
PTY Session Manager - Manages a shared pseudo-terminal session.
Multiple clients (terminal, webapp) can connect to the same session.
"""

import asyncio
import fcntl
import os
import pty
import re
import select
import signal
import struct
import termios
import threading
import time
import urllib.request
import urllib.error
from typing import Callable, Optional
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SessionClient:
    """Represents a connected client."""
    id: str
    type: str  # 'terminal' or 'websocket'
    send_callback: Callable[[bytes], None]
    connected_at: datetime = field(default_factory=datetime.now)


class PTYSession:
    """
    Manages a pseudo-terminal session running Claude Code.
    Supports multiple connected clients for input/output.
    """

    def __init__(self, working_dir: str, command: list[str] = None):
        self.working_dir = working_dir
        self.command = command or ["claude"]

        self.master_fd: Optional[int] = None
        self.slave_fd: Optional[int] = None
        self.pid: Optional[int] = None

        self.clients: dict[str, SessionClient] = {}
        self.output_buffer: list[bytes] = []  # Recent output for new clients
        self.buffer_max_size = 500000  # Keep last ~500KB

        self.running = False
        self.read_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Notification settings
        self.ntfy_topic: Optional[str] = None
        self._notification_buffer = b""
        self._last_output_time = 0
        self._notification_timer: Optional[threading.Timer] = None
        self._last_notification_time = 0
        self._notification_cooldown = 3  # seconds between notifications

    def start(self) -> bool:
        """Start the PTY session with Claude Code."""
        if self.running:
            return True

        try:
            # Create pseudo-terminal
            self.master_fd, self.slave_fd = pty.openpty()

            # Fork process
            self.pid = os.fork()

            if self.pid == 0:
                # Child process
                os.close(self.master_fd)
                os.setsid()

                # Set up slave as controlling terminal
                os.dup2(self.slave_fd, 0)  # stdin
                os.dup2(self.slave_fd, 1)  # stdout
                os.dup2(self.slave_fd, 2)  # stderr

                if self.slave_fd > 2:
                    os.close(self.slave_fd)

                # Change to working directory
                os.chdir(self.working_dir)

                # Set environment
                env = os.environ.copy()
                env["TERM"] = "xterm-256color"
                env["COLORTERM"] = "truecolor"

                # Execute Claude
                os.execvpe(self.command[0], self.command, env)
            else:
                # Parent process
                os.close(self.slave_fd)
                self.slave_fd = None

                # Set non-blocking
                flags = fcntl.fcntl(self.master_fd, fcntl.F_GETFL)
                fcntl.fcntl(self.master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

                self.running = True

                # Start read thread
                self.read_thread = threading.Thread(target=self._read_loop, daemon=True)
                self.read_thread.start()

                return True

        except Exception as e:
            print(f"Failed to start PTY session: {e}")
            self.cleanup()
            return False

    def _read_loop(self):
        """Background thread that reads PTY output and broadcasts to clients."""
        while self.running and self.master_fd is not None:
            try:
                # Wait for data with timeout
                ready, _, _ = select.select([self.master_fd], [], [], 0.1)

                if ready:
                    try:
                        data = os.read(self.master_fd, 4096)
                        if data:
                            self._broadcast_output(data)
                        else:
                            # EOF - process exited
                            self.running = False
                            break
                    except OSError:
                        break

            except Exception as e:
                if self.running:
                    print(f"Read error: {e}")
                break

        self.running = False

    def _broadcast_output(self, data: bytes):
        """Send output to all connected clients and buffer it."""
        with self._lock:
            # Add to buffer
            self.output_buffer.append(data)

            # Trim buffer if too large
            total_size = sum(len(b) for b in self.output_buffer)
            while total_size > self.buffer_max_size and self.output_buffer:
                removed = self.output_buffer.pop(0)
                total_size -= len(removed)

            # Send to all clients
            for client in list(self.clients.values()):
                try:
                    client.send_callback(data)
                except Exception as e:
                    print(f"Failed to send to client {client.id}: {e}")

        # Handle notifications
        self._handle_notification_output(data)

    def _handle_notification_output(self, data: bytes):
        """Track output and send notifications when Claude is waiting."""
        if not self.ntfy_topic:
            return

        self._notification_buffer += data
        # Keep only last 2KB
        if len(self._notification_buffer) > 2048:
            self._notification_buffer = self._notification_buffer[-2048:]

        self._last_output_time = time.time()

        # Cancel existing timer
        if self._notification_timer:
            self._notification_timer.cancel()

        # Set timer to check after 2 seconds of idle
        self._notification_timer = threading.Timer(2.0, self._check_and_notify)
        self._notification_timer.daemon = True
        self._notification_timer.start()

    def _check_and_notify(self):
        """Send notification when Claude finishes outputting."""
        if not self.ntfy_topic:
            return

        # Check cooldown
        now = time.time()
        if now - self._last_notification_time < self._notification_cooldown:
            self._notification_buffer = b""
            return

        # Clean the output - remove ANSI escape codes
        try:
            text = self._notification_buffer.decode('utf-8', errors='ignore')
        except:
            self._notification_buffer = b""
            return

        # Remove ANSI codes and control characters
        clean_text = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)
        clean_text = re.sub(r'\x1b\][^\x07]*\x07', '', clean_text)  # OSC sequences
        clean_text = re.sub(r'[\x00-\x1f\x7f]', ' ', clean_text)  # Control chars
        clean_text = re.sub(r'\s+', ' ', clean_text).strip()

        if len(clean_text) < 5:
            self._notification_buffer = b""
            return

        # Determine notification type based on content patterns
        waiting_patterns = [
            (r'\?\s*$', 'Claude is asking a question'),
            (r'\[Y/n\]', 'Claude needs confirmation'),
            (r'\(y/n\)', 'Claude needs confirmation'),
            (r'proceed\?', 'Claude is asking to proceed'),
            (r'continue\?', 'Claude is asking to continue'),
            (r'select.*:', 'Claude is waiting for selection'),
            (r'choose.*:', 'Claude is waiting for your choice'),
            (r'enter.*:', 'Claude is waiting for input'),
        ]

        message = "Claude has an update"

        for pattern, msg in waiting_patterns:
            if re.search(pattern, clean_text, re.IGNORECASE):
                message = msg
                break

        self._send_notification(message)
        self._last_notification_time = now
        self._notification_buffer = b""

    def _send_notification(self, message: str):
        """Send notification via ntfy.sh."""
        if not self.ntfy_topic or not message:
            return

        try:
            url = f"https://ntfy.sh/{self.ntfy_topic}"
            data = message.encode('utf-8')
            req = urllib.request.Request(
                url,
                data=data,
                headers={'Title': 'TeleClaude'},
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                pass
            print(f"[Notification] Sent: {message[:50]}...")
        except Exception as e:
            print(f"[Notification] Failed to send: {e}")

    def set_ntfy_topic(self, topic: str):
        """Set the ntfy.sh topic for notifications."""
        self.ntfy_topic = topic if topic else None
        if topic:
            print(f"[Notification] Enabled for topic: {topic}")

    def write(self, data: bytes) -> bool:
        """Write input to the PTY (from any client)."""
        if not self.running or self.master_fd is None:
            return False

        try:
            os.write(self.master_fd, data)
            return True
        except OSError as e:
            print(f"Write error: {e}")
            return False

    def resize(self, rows: int, cols: int):
        """Resize the PTY window."""
        if self.master_fd is None:
            return

        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
        except Exception as e:
            print(f"Resize error: {e}")

    def add_client(self, client: SessionClient) -> bytes:
        """
        Add a client and return buffered output.
        Returns recent output so client can see history.
        """
        with self._lock:
            self.clients[client.id] = client
            # Return buffered output
            return b"".join(self.output_buffer)

    def remove_client(self, client_id: str):
        """Remove a client."""
        with self._lock:
            self.clients.pop(client_id, None)

    def get_client_count(self) -> int:
        """Get number of connected clients."""
        with self._lock:
            return len(self.clients)

    def is_running(self) -> bool:
        """Check if session is still running."""
        return self.running

    def cleanup(self):
        """Clean up resources."""
        self.running = False

        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except:
                pass
            self.master_fd = None

        if self.slave_fd is not None:
            try:
                os.close(self.slave_fd)
            except:
                pass
            self.slave_fd = None

        if self.pid:
            try:
                os.kill(self.pid, signal.SIGTERM)
                os.waitpid(self.pid, os.WNOHANG)
            except:
                pass
            self.pid = None

    def __del__(self):
        self.cleanup()


class SessionManager:
    """Manages multiple PTY sessions (for future multi-session support)."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._sessions = {}
            cls._instance._default_session = None
        return cls._instance

    def get_or_create_session(self, session_id: str, working_dir: str, command: list[str] = None) -> PTYSession:
        """Get existing session or create a new one."""
        if session_id not in self._sessions:
            session = PTYSession(working_dir, command=command)
            if session.start():
                self._sessions[session_id] = session
                if self._default_session is None:
                    self._default_session = session_id
            else:
                raise RuntimeError("Failed to start PTY session")
        return self._sessions[session_id]

    def get_session(self, session_id: str) -> Optional[PTYSession]:
        """Get a session by ID."""
        return self._sessions.get(session_id)

    def get_default_session(self) -> Optional[PTYSession]:
        """Get the default session."""
        if self._default_session:
            return self._sessions.get(self._default_session)
        return None

    def list_sessions(self) -> list[dict]:
        """List all sessions."""
        return [
            {
                "id": sid,
                "running": s.is_running(),
                "clients": s.get_client_count(),
                "working_dir": s.working_dir,
                "command": s.command
            }
            for sid, s in self._sessions.items()
        ]

    def cleanup_session(self, session_id: str):
        """Clean up a session."""
        if session_id in self._sessions:
            self._sessions[session_id].cleanup()
            del self._sessions[session_id]
            if self._default_session == session_id:
                self._default_session = None
