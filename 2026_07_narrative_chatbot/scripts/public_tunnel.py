"""A public HTTPS address for the Web UI, through a Cloudflare quick tunnel.

``cloudflared tunnel --url http://127.0.0.1:<port>`` needs no account: it
prints a random ``https://<words>.trycloudflare.com`` address that forwards
to that local port for as long as the process runs.  The Web UI gives the
tunnel a port of its own, so it can tell tunnelled requests apart from ones
made on this PC (both come from 127.0.0.1); see ``web_server``.

``cloudflared`` is looked up on PATH, then in the project's ``.tools/``
(gitignored), e.g.::

    curl -fsSL -o .tools/cloudflared \\
      https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
    chmod +x .tools/cloudflared
"""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_CLOUDFLARED = PROJECT_ROOT / ".tools" / "cloudflared"
URL_PATTERN = re.compile(rb"https://[a-z0-9-]+\.trycloudflare\.com")
START_TIMEOUT_SEC = 60.0
STOP_TIMEOUT_SEC = 10.0


def find_cloudflared() -> str | None:
    found = shutil.which("cloudflared")
    if found:
        return found
    return str(LOCAL_CLOUDFLARED) if LOCAL_CLOUDFLARED.is_file() else None


class QuickTunnel:
    """One ``cloudflared`` quick tunnel to a local port."""

    def __init__(self, executable: str) -> None:
        self.executable = executable
        self.process: asyncio.subprocess.Process | None = None
        self.url: str | None = None
        self._drain: asyncio.Task | None = None

    async def start(self, port: int, timeout: float = START_TIMEOUT_SEC) -> str:
        """Start the tunnel and return its public address."""
        self.process = await asyncio.create_subprocess_exec(
            # Some networks (seen on this lab's own Wi-Fi) block the
            # outbound QUIC/UDP cloudflared defaults to; its own
            # connectivity precheck then recommends HTTP/2 but does not
            # actually switch to it, so cloudflared just retries QUIC
            # forever and the tunnel never comes up. Force HTTP/2 instead.
            self.executable, "tunnel", "--no-autoupdate", "--protocol", "http2",
            "--url", f"http://127.0.0.1:{port}",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            self.url = await asyncio.wait_for(self._read_url(), timeout)
        except BaseException:
            await self.stop()
            raise
        # cloudflared keeps logging; keep its pipe from filling up.
        self._drain = asyncio.create_task(self._discard_output())
        return self.url

    async def _read_url(self) -> str:
        assert self.process is not None and self.process.stderr is not None
        while True:
            line = await self.process.stderr.readline()
            if not line:
                raise RuntimeError("cloudflared が公開アドレスを出す前に終了しました")
            match = URL_PATTERN.search(line)
            if match:
                return match.group(0).decode()

    async def _discard_output(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        while await self.process.stderr.readline():
            pass

    async def wait(self) -> int | None:
        """Until cloudflared exits (the public address is then gone); its
        exit code."""
        process = self.process
        return None if process is None else await process.wait()

    async def stop(self) -> None:
        """End the tunnel: the public address stops working."""
        process, self.process = self.process, None
        if self._drain is not None:
            self._drain.cancel()
            self._drain = None
        if process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), STOP_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
