"""
VSCode Manager - Manages code-server instances for TeleClaude sessions.
"""

import asyncio
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class VSCodeInstance:
    """Represents a running code-server instance."""
    session_id: str
    port: int
    working_dir: str
    process: Optional[subprocess.Popen] = None
    started_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)

    def is_running(self) -> bool:
        """Check if the code-server process is still running."""
        if self.process is None:
            return False
        return self.process.poll() is None

    def touch(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = time.time()

    def idle_seconds(self) -> float:
        """Return seconds since last activity."""
        return time.time() - self.last_activity


class VSCodeManager:
    """Manages code-server instances for multiple sessions."""

    # Port range for code-server instances
    BASE_PORT = 8766
    MAX_INSTANCES = 10
    IDLE_TIMEOUT = 30 * 60  # 30 minutes

    def __init__(self, password: Optional[str] = None):
        self._instances: dict[str, VSCodeInstance] = {}
        self._password = password or os.environ.get("TELECLAUDE_PASSWORD", "")
        self._cleanup_task: Optional[asyncio.Task] = None

    def _find_available_port(self) -> int:
        """Find an available port for a new code-server instance."""
        used_ports = {inst.port for inst in self._instances.values()}
        for port in range(self.BASE_PORT, self.BASE_PORT + self.MAX_INSTANCES):
            if port not in used_ports:
                return port
        raise RuntimeError("No available ports for code-server")

    def _get_code_server_path(self) -> str:
        """Get the path to the code-server binary."""
        # Check common locations
        teleclaude_home = os.environ.get(
            "TELECLAUDE_HOME",
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )

        # Check local bin directory first
        local_bin = os.path.join(teleclaude_home, "bin", "code-server")
        if os.path.exists(local_bin):
            return local_bin

        # Check system path
        import shutil
        system_path = shutil.which("code-server")
        if system_path:
            return system_path

        raise FileNotFoundError(
            "code-server not found. Install with: teleclaude vscode-install"
        )

    def get_instance(self, session_id: str) -> Optional[VSCodeInstance]:
        """Get an existing code-server instance for a session."""
        instance = self._instances.get(session_id)
        if instance and instance.is_running():
            return instance
        return None

    def start(
        self,
        session_id: str,
        working_dir: str,
        base_path: Optional[str] = None
    ) -> VSCodeInstance:
        """Start a code-server instance for a session."""
        # Check if already running
        existing = self.get_instance(session_id)
        if existing:
            existing.touch()
            return existing

        # Find available port
        port = self._find_available_port()

        # Get code-server path
        code_server = self._get_code_server_path()

        # Build command
        base = base_path or f"/vscode/{session_id}"
        cmd = [
            code_server,
            "--bind-addr", f"127.0.0.1:{port}",
            "--auth", "password" if self._password else "none",
            "--disable-telemetry",
            "--disable-update-check",
            working_dir
        ]

        # Set environment
        env = os.environ.copy()
        if self._password:
            env["PASSWORD"] = self._password

        # Start process
        process = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True
        )

        # Create instance
        instance = VSCodeInstance(
            session_id=session_id,
            port=port,
            working_dir=working_dir,
            process=process
        )

        self._instances[session_id] = instance
        print(f"[VSCode] Started code-server for session '{session_id}' on port {port}")

        return instance

    def stop(self, session_id: str) -> bool:
        """Stop a code-server instance."""
        instance = self._instances.get(session_id)
        if not instance:
            return False

        if instance.process and instance.is_running():
            try:
                # Send SIGTERM to process group
                os.killpg(os.getpgid(instance.process.pid), signal.SIGTERM)
                instance.process.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                # Force kill if needed
                try:
                    os.killpg(os.getpgid(instance.process.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass

        del self._instances[session_id]
        print(f"[VSCode] Stopped code-server for session '{session_id}'")
        return True

    def stop_all(self) -> None:
        """Stop all code-server instances."""
        for session_id in list(self._instances.keys()):
            self.stop(session_id)

    def list_instances(self) -> list[dict]:
        """List all running code-server instances."""
        result = []
        for session_id, instance in self._instances.items():
            if instance.is_running():
                result.append({
                    "session_id": session_id,
                    "port": instance.port,
                    "working_dir": instance.working_dir,
                    "running": True,
                    "idle_seconds": int(instance.idle_seconds()),
                    "started_at": instance.started_at
                })
        return result

    async def cleanup_idle(self) -> None:
        """Stop instances that have been idle too long."""
        for session_id in list(self._instances.keys()):
            instance = self._instances.get(session_id)
            if instance and instance.idle_seconds() > self.IDLE_TIMEOUT:
                print(f"[VSCode] Stopping idle instance for session '{session_id}'")
                self.stop(session_id)

    async def start_cleanup_loop(self) -> None:
        """Start the background cleanup loop."""
        while True:
            await asyncio.sleep(60)  # Check every minute
            await self.cleanup_idle()

    def start_cleanup_task(self) -> None:
        """Start the cleanup task in the event loop."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self.start_cleanup_loop())

    def stop_cleanup_task(self) -> None:
        """Stop the cleanup task."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()


# Singleton instance
_vscode_manager: Optional[VSCodeManager] = None


def get_vscode_manager(password: Optional[str] = None) -> VSCodeManager:
    """Get or create the singleton VSCodeManager instance."""
    global _vscode_manager
    if _vscode_manager is None:
        _vscode_manager = VSCodeManager(password)
    return _vscode_manager
