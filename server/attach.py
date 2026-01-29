#!/usr/bin/env python3
"""
Terminal Attach Client - Connect to a running TeleClaude session from the terminal.
Like 'tmux attach' but for TeleClaude shared sessions.
"""

import argparse
import asyncio
import os
import signal
import sys
import termios
import tty

try:
    import websockets
except ImportError:
    print("Error: websockets package required. Install with: pip install websockets")
    sys.exit(1)


class TerminalClient:
    """Client that attaches local terminal to remote PTY session."""

    # Ctrl+] is the detach key (ASCII 29, like telnet)
    DETACH_KEY = b'\x1d'

    def __init__(self, url: str):
        self.url = url
        self.ws = None
        self.original_termios = None
        self.running = False

    async def connect(self):
        """Connect to the WebSocket server."""
        try:
            self.ws = await websockets.connect(self.url)
            return True
        except Exception as e:
            print(f"Failed to connect: {e}")
            return False

    def setup_terminal(self):
        """Put terminal in raw mode."""
        if sys.stdin.isatty():
            self.original_termios = termios.tcgetattr(sys.stdin)
            tty.setraw(sys.stdin.fileno())

    def restore_terminal(self):
        """Restore terminal to original mode."""
        if self.original_termios:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self.original_termios)

    def get_terminal_size(self):
        """Get current terminal size."""
        try:
            size = os.get_terminal_size()
            return size.lines, size.columns
        except:
            return 24, 80

    async def send_resize(self):
        """Send terminal size to server."""
        rows, cols = self.get_terminal_size()
        await self.ws.send(f'{{"type":"resize","rows":{rows},"cols":{cols}}}')

    async def read_stdin(self):
        """Read from stdin and send to websocket."""
        loop = asyncio.get_event_loop()

        while self.running:
            try:
                # Read from stdin (non-blocking via asyncio)
                data = await loop.run_in_executor(None, sys.stdin.buffer.read1, 1024)
                if data:
                    # Check for detach key (Ctrl+])
                    if self.DETACH_KEY in data:
                        # Remove detach key from data
                        data = data.replace(self.DETACH_KEY, b'')
                        if data:
                            await self.ws.send(data)
                        self.running = False
                        break
                    await self.ws.send(data)
            except Exception as e:
                if self.running:
                    print(f"\r\nStdin error: {e}")
                break

    async def read_websocket(self):
        """Read from websocket and write to stdout."""
        try:
            async for message in self.ws:
                if isinstance(message, bytes):
                    sys.stdout.buffer.write(message)
                    sys.stdout.buffer.flush()
                else:
                    # Text message (JSON)
                    sys.stdout.buffer.write(message.encode())
                    sys.stdout.buffer.flush()
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:
            if self.running:
                print(f"\r\nWebSocket error: {e}")

    async def run(self):
        """Main run loop."""
        if not await self.connect():
            return

        self.running = True
        self.setup_terminal()

        # Handle SIGWINCH (terminal resize)
        def on_resize(signum, frame):
            asyncio.create_task(self.send_resize())

        if hasattr(signal, "SIGWINCH"):
            signal.signal(signal.SIGWINCH, on_resize)

        # Handle SIGINT gracefully
        def on_interrupt(signum, frame):
            self.running = False

        signal.signal(signal.SIGINT, on_interrupt)

        try:
            # Send initial size
            await self.send_resize()

            # Run input/output tasks
            stdin_task = asyncio.create_task(self.read_stdin())
            ws_task = asyncio.create_task(self.read_websocket())

            # Wait for either to finish
            done, pending = await asyncio.wait(
                [stdin_task, ws_task],
                return_when=asyncio.FIRST_COMPLETED
            )

            # Cancel remaining tasks
            for task in pending:
                task.cancel()

        finally:
            self.running = False
            self.restore_terminal()
            if self.ws:
                await self.ws.close()


def main():
    parser = argparse.ArgumentParser(
        description="Attach to a TeleClaude shared terminal session"
    )
    parser.add_argument(
        "--host",
        default="localhost",
        help="TeleClaude server host (default: localhost)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="TeleClaude server port (default: 8765)"
    )
    parser.add_argument(
        "--url",
        help="Full WebSocket URL (overrides host/port)"
    )

    args = parser.parse_args()

    if args.url:
        url = args.url
    else:
        url = f"ws://{args.host}:{args.port}/ws/terminal"

    print(f"Connecting to {url}...")
    print("Press Ctrl+] to detach (session keeps running)\n")

    client = TerminalClient(url)

    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        pass

    print("\r\nDetached from session.")


if __name__ == "__main__":
    main()
